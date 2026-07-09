"""
Agentic Scenario Pipeline — core logic.

A vLLM-served Llama 3.1 8B Instruct model with function calling autonomously sequences
5 tools to convert a German Berlin police accident report into validated
OpenDRIVE + OpenSCENARIO simulation files.

Entry point: run.py (at scenario_pipeline/ root).
Public API:  run_agent(report_text, scenario_id)
             run_feedback_iteration(state, report_text, user_feedback)
"""

from __future__ import annotations

import json
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

PROJECT_DIR = Path(__file__).resolve().parent

from llm_client import get_client, MODEL, message_to_dict

import shutil

from extract_scenario import extract_scenario as _extract_scenario
from osm_enrichment import enrich_with_osm as _enrich_with_osm
from complete_parameters import complete_parameters as _complete_parameters
from template_selector import select_template as _select_template
from generate_scenario import generate_openscenario as _generate_openscenario
from validate_outputs import validate_generated_files as _validate_outputs


OUTPUT_BASE = PROJECT_DIR / "output" / "agentic"
OSM_CACHE_DIR = PROJECT_DIR / "output" / "osm_cache"
MAX_ITERATIONS = 25
MAX_RETRIES = 3

W = 70  # display width


# ── Tool schemas ──────────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "extract_scenario",
            "description": (
                "Extract structured scenario data from a raw German Berlin police accident "
                "report. ALWAYS call this first. Returns scenario_type, participants, "
                "location info (including osm_query), bike_facility_type, and conflict description."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "report_text": {
                        "type": "string",
                        "description": "The full raw German police report text.",
                    },
                    "scenario_id": {
                        "type": "string",
                        "description": "Unique scenario ID, e.g. right_turn_test_001.",
                    },
                },
                "required": ["report_text", "scenario_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_osm",
            "description": (
                "Query OpenStreetMap for real road data at the accident location. "
                "Returns actual lane counts, bike facility type and position, speed limit, "
                "and traffic signal presence. Call after extract_scenario using the "
                "osm_query string from the extraction result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "osm_query": {
                        "type": "string",
                        "description": (
                            "OSM geocoding query, e.g. "
                            "'Salvador-Allende-Str / Müggelschlößchenweg, Berlin, Germany'."
                        ),
                    },
                },
                "required": ["osm_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_parameters",
            "description": (
                "Fill in all remaining simulation parameters using scenario-type-aware "
                "defaults: actor speeds, initial lane IDs, starting positions, timing. "
                "Never overwrites values already set by OSM enrichment. "
                "Call after query_osm — no arguments needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_scenario",
            "description": (
                "Select the correct .xodr road template and generate the OpenSCENARIO (.xosc) "
                "script from the current scenario data. Returns file paths and success status. "
                "Call after complete_parameters. On retry after validation failure, pass "
                "parameter_overrides as a JSON string to fix specific errors."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "parameter_overrides": {
                        "type": "string",
                        "description": (
                            "Optional JSON string with parameter overrides. "
                            "Example: '{\"opendrive\": {\"motor_lane_count\": 1}}'"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_and_fix",
            "description": (
                "Validate the generated OpenDRIVE and OpenSCENARIO files. "
                "Checks road references, actor lane IDs, trajectory vertex counts, "
                "s-position bounds, and stop conditions. "
                "Returns valid (bool), errors list, warnings, and fix suggestions. "
                "If valid=false and retries_remaining>0, call generate_scenario with "
                "parameter_overrides to fix the errors, then call validate_and_fix again. "
                "Stop when valid=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# ── System prompts ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an autonomous scenario generation agent for autonomous driving safety research at TU Berlin.
Your task: process a German Berlin police accident report (car/truck vs cyclist) and produce
valid OpenDRIVE + OpenSCENARIO simulation files by calling the available tools.

REQUIRED WORKFLOW — follow this exact sequence:
1. extract_scenario(report_text, scenario_id) — always first
2. query_osm(osm_query) — use the osm_query from step 1's result
3. complete_parameters() — no arguments
4. generate_scenario() — no arguments on first attempt
5. validate_and_fix() — check the generated files

IF validate_and_fix returns valid=false AND retries_remaining > 0:
  - Analyze each error carefully
  - Call generate_scenario(parameter_overrides=...) with JSON fixes
  - Call validate_and_fix() again
  COMMON FIXES:
  - "missing lane X": adjust lane IDs (negative integers: -1=rightmost driving lane)
  - "outside road length": reduce initial_s_m to be within road_length_m (default 100m)
  - "no trajectories": regenerate without overrides first

STOP when validate_and_fix returns valid=true. Give a brief summary of what was generated.
STOP after 3 retries even if still invalid — explain what failed and why.

Be concise between tool calls. One or two sentences of reasoning is enough.
"""

FEEDBACK_SYSTEM_PROMPT = """\
You are reviewing a generated simulation against the original police report.
The user has watched the simulation and reported an issue.
Your task: adjust simulation parameters to fix the reported problem.

You will receive:
1. The original German police report
2. The current simulation parameters (JSON)
3. The user's feedback describing what looks wrong

IMPORTANT: The .xosc (scenario script) is regenerated on every feedback iteration.
The .xodr road network is a pre-validated template selected by scenario type — it is
NOT regenerated. Geometry parameters below affect actor world-coordinate calculations
(trajectory positions) in the .xosc, not the underlying road template.

Output ONLY a valid JSON object with the parameters to change. Use this structure:
{
  "opendrive": { ... opendrive parameter overrides ... },
  "openscenario": {
    "actors": {
      "<actor_id>": { ... actor parameter overrides ... }
    },
    "conflict": { ... conflict parameter overrides ... },
    "simulation_duration_s": <optional>
  }
}

Only include parameters that need to change. Do not wrap in markdown code blocks.

━━━ GEOMETRY PARAMETERS (affect actor trajectory positions in .xosc) ━━━
- opendrive.primary_road_lanes: number of driving lanes (integer, e.g. 1, 2, 3)
- opendrive.motor_lane_width_m: width of each driving lane in meters (e.g. 3.5)
- opendrive.road_length_m: total road length in meters (default 100)
- opendrive.primary_has_bike_facility: whether a bike lane/track exists (true/false)
- opendrive.primary_bike_facility_position: which side the bike facility is on ("right", "left", "both")
- opendrive.primary_bike_facility_type: type of bike facility ("separated_cycle_track", "bike_lane", "shared_lane", "none")
- opendrive.bike_lane_width_m: width of the bike lane/track in meters (e.g. 1.5, 2.0)

━━━ ACTOR PARAMETERS (affect .xosc scenario script) ━━━
- openscenario.actors.truck_1.initial_speed_mps: truck initial speed in m/s
- openscenario.actors.cyclist_1.initial_speed_mps: cyclist initial speed in m/s
- openscenario.actors.truck_1.initial_s_m: truck starting position along road in meters
- openscenario.actors.cyclist_1.initial_s_m: cyclist starting position along road in meters
- openscenario.actors.truck_1.initial_lane_id: truck lane (-1=rightmost driving lane, -2=next lane)
- openscenario.actors.cyclist_1.initial_lane_id: cyclist lane (-2=bike lane if present, -1=driving lane)

━━━ CONFLICT / TIMING PARAMETERS ━━━
- openscenario.conflict.conflict_s_m: road position (meters) where the conflict occurs
- openscenario.simulation_duration_s: total simulation time in seconds

━━━ EXAMPLE FEEDBACK → PARAMETER MAPPINGS ━━━
"die Fahrradspur fehlt"              → primary_has_bike_facility: true
"der Radweg ist auf der falschen Seite" → primary_bike_facility_position: "left" or "right"
"zu wenig Spuren"                    → primary_road_lanes: 2 or 3
"der Radweg ist baulich getrennt"    → primary_bike_facility_type: "separated_cycle_track"
"der LKW ist zu schnell"             → truck_1.initial_speed_mps: lower value
"der Fahrrad startet zu weit weg"    → cyclist_1.initial_s_m: higher value

Be concise and precise. Only change what the user's feedback indicates is wrong.
"""


# ── Agent state ───────────────────────────────────────────────────────────────

class AgentState:
    def __init__(self, scenario_id: str):
        self.scenario_id = scenario_id
        self.data: dict | None = None
        self.output_dir: Path = OUTPUT_BASE / scenario_id
        self.xodr_path: Path | None = None
        self.xosc_path: Path | None = None
        self.retry_count: int = 0
        self.log: list[dict] = []

    def record(self, event_type: str, payload: dict) -> None:
        self.log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **payload,
        })


# ── Display helpers ───────────────────────────────────────────────────────────

def _show_extraction_summary(data: dict) -> None:
    cls = data.get("classification", {})
    loc = data.get("location", {})
    conflict = data.get("conflict", {})
    road = data.get("road_context", {})
    participants = data.get("participants", [])

    print(f"\n  ┌─ Extraction result {'─' * (W - 22)}")
    print(f"  │  Type       {cls.get('scenario_type')}   (confidence: {cls.get('confidence')})")
    road_str = loc.get("primary_road") or "?"
    if loc.get("secondary_road"):
        road_str += f"  →  {loc.get('secondary_road')}"
    print(f"  │  Location   {road_str}")
    for p in participants:
        role = "Motor vehicle" if p.get("class") == "motor_vehicle" else "Cyclist      "
        print(f"  │  {role}  {p.get('id')}  ·  {p.get('type')}  ·  {p.get('maneuver')}")
    print(f"  │  Conflict   {conflict.get('conflict_mechanism')}")
    col = "yes" if conflict.get("collision_happened") else "no"
    print(f"  │  Collision  {col}  ·  severity: {conflict.get('severity_text')}")
    print(f"  │  Bike infra {road.get('bike_facility_type')}  ·  traffic light: {road.get('traffic_light_present')}")
    print(f"  └{'─' * (W - 2)}")


# ── Tool implementations ──────────────────────────────────────────────────────

def _tool_extract_scenario(state: AgentState, report_text: str, scenario_id: str) -> dict:
    print(f"  → Calling LLM ({MODEL}) for extraction...")
    extracted = _extract_scenario(report_text, scenario_id)
    state.data = extracted
    stype = extracted["classification"]["scenario_type"]
    conf = extracted["classification"]["confidence"]
    state.record("extract_scenario", {"scenario_type": stype, "confidence": conf})
    print(f"  ✓ Extracted: {stype}  (confidence: {conf})")
    return {
        "scenario_type": stype,
        "confidence": conf,
        "primary_road": extracted["location"]["primary_road"],
        "secondary_road": extracted["location"]["secondary_road"],
        "osm_query": extracted["location"]["osm_query"],
        "participants": [
            {"id": p["id"], "type": p["type"], "maneuver": p["maneuver"]}
            for p in extracted["participants"]
        ],
        "conflict_mechanism": extracted["conflict"]["conflict_mechanism"],
        "collision_happened": extracted["conflict"]["collision_happened"],
        "bike_facility_type": extracted["road_context"]["bike_facility_type"],
        "traffic_light_present": extracted["road_context"]["traffic_light_present"],
        "location_type": extracted["location"]["location_type"],
    }


def _tool_query_osm(state: AgentState, osm_query: str) -> dict:
    print(f"  → Querying OSM: {osm_query!r}")
    if state.data is None:
        return {"error": "extract_scenario must be called before query_osm"}

    state.data["location"]["osm_query"] = osm_query
    state.output_dir.mkdir(parents=True, exist_ok=True)

    enriched = _enrich_with_osm(state.data, OSM_CACHE_DIR)
    state.data = enriched

    ctx = enriched.get("osm_context", {})
    status = ctx.get("enrichment_status", "unknown")
    state.record("query_osm", {"status": status, "query": osm_query})
    print(f"  ✓ OSM status: {status}")

    result: dict = {
        "enrichment_status": status,
        "traffic_signals_nearby": ctx.get("traffic_signals_nearby"),
    }
    if "geocoded" in ctx:
        result["geocoded_location"] = ctx["geocoded"].get("display_name", "")
    if "bike_facility" in ctx:
        bf = ctx["bike_facility"]
        result["bike_facility"] = {
            "type": bf.get("type"),
            "position": bf.get("position"),
            "source": bf.get("source"),
        }
    if "derived" in ctx:
        result["derived_maxspeed_kmh"] = ctx["derived"].get("maxspeed_kmh")
    if "lane_count_evidence" in ctx:
        lce = ctx["lane_count_evidence"]
        result["lane_count_evidence"] = {
            "primary_used_count": lce.get("primary", {}).get("used_count"),
            "secondary_used_count": lce.get("secondary", {}).get("used_count"),
        }
    odr = enriched.get("generated_simulation_parameters", {}).get("opendrive", {})
    result["opendrive_params_after_osm"] = {k: v for k, v in odr.items() if v is not None}
    return result


def _tool_complete_parameters(state: AgentState) -> dict:
    print("  → Filling in simulation parameter defaults...")
    if state.data is None:
        return {"error": "extract_scenario must be called first"}

    state.data = _complete_parameters(state.data)
    state.record("complete_parameters", {})

    odr = state.data["generated_simulation_parameters"]["opendrive"]
    osc = state.data["generated_simulation_parameters"]["openscenario"]
    actors = osc.get("actors", {})
    print(f"  ✓ Parameters complete: geometry={odr.get('road_geometry')},  actors={list(actors)}")
    return {
        "road_geometry": odr.get("road_geometry"),
        "road_length_m": odr.get("road_length_m"),
        "motor_lane_count": odr.get("motor_lane_count", odr.get("primary_road_lanes")),
        "has_bike_facility": odr.get("primary_has_bike_facility"),
        "simulation_duration_s": osc.get("simulation_duration_s"),
        "actors": {
            aid: {
                "vehicle_category": a.get("vehicle_category"),
                "initial_road_id": a.get("initial_road_id"),
                "initial_lane_id": a.get("initial_lane_id"),
                "initial_s_m": a.get("initial_s_m"),
                "initial_speed_mps": a.get("initial_speed_mps"),
            }
            for aid, a in actors.items()
        },
        "missing_parameters_filled": len(state.data.get("missing_parameters", [])),
    }


def _tool_generate_scenario(state: AgentState, parameter_overrides: str | None = None) -> dict:
    print("  → Selecting template and generating OpenSCENARIO...")
    if state.data is None:
        return {"success": False, "error": "extract_scenario must be called first"}

    if parameter_overrides:
        try:
            overrides = json.loads(parameter_overrides)
            sim = state.data.setdefault("generated_simulation_parameters", {})
            for section, vals in overrides.items():
                if isinstance(vals, dict):
                    sim.setdefault(section, {}).update(vals)
                    print(f"  ✓ Applied override [{section}]: {vals}")
        except json.JSONDecodeError as exc:
            return {"success": False, "error": f"Invalid parameter_overrides JSON: {exc}"}

    state.output_dir.mkdir(parents=True, exist_ok=True)
    sid = state.data["source"]["source_id"]
    xosc_path = state.output_dir / f"{sid}.xosc"
    enriched_path = state.output_dir / f"{sid}.enriched.json"

    scenario_type = state.data.get("classification", {}).get("scenario_type", "")
    template_rel = _select_template(scenario_type)
    template_src = Path(__file__).resolve().parent / template_rel
    xodr_filename = Path(template_rel).name
    xodr_path = state.output_dir / xodr_filename
    shutil.copy2(template_src, xodr_path)

    try:
        _generate_openscenario(state.data, xosc_path, xodr_filename=xodr_filename)
        enriched_path.write_text(
            json.dumps(state.data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        state.xodr_path = xodr_path
        state.xosc_path = xosc_path
        state.record("generate_scenario", {
            "template": str(template_src),
            "xodr": str(xodr_path),
            "xosc": str(xosc_path),
        })
        print(f"  ✓ Template:   {xodr_filename}  →  {xodr_path.name}")
        print(f"  ✓ Generated:  {xosc_path.name}")
        return {
            "success": True,
            "xodr_path": str(xodr_path),
            "xosc_path": str(xosc_path),
            "enriched_json_path": str(enriched_path),
            "error": None,
        }
    except Exception as exc:
        state.record("generate_scenario_error", {"error": str(exc)})
        print(f"  ✗ Generation failed: {exc}")
        return {"success": False, "error": str(exc)}


def _tool_validate_and_fix(state: AgentState) -> dict:
    print("  → Validating generated files...")
    if state.xodr_path is None or state.xosc_path is None:
        return {
            "valid": False,
            "errors": ["generate_scenario must be called before validate_and_fix"],
            "warnings": [],
            "suggestions": [],
            "retry_count": state.retry_count,
            "retries_remaining": MAX_RETRIES,
        }

    state.retry_count += 1
    result = _validate_outputs(state.data, state.xodr_path, state.xosc_path)
    state.record("validate_and_fix", {
        "valid": result.ok,
        "errors": result.errors,
        "warnings": result.warnings,
        "retry_count": state.retry_count,
    })

    suggestions = []
    for err in result.errors:
        el = err.lower()
        if "missing lane" in el or "unknown lane" in el:
            suggestions.append(
                "Fix: set initial_lane_id to a valid lane. "
                "OpenDRIVE uses negative IDs: -1=rightmost driving, -2=bike lane if present."
            )
        if "outside road" in el or "s=" in el:
            suggestions.append("Fix: reduce initial_s_m to [0, road_length_m] (default 100m).")
        if "no trajectories" in el:
            suggestions.append("Fix: call generate_scenario before validate_and_fix.")

    print(f"  {'✓' if result.ok else '✗'} Validation: {'VALID' if result.ok else f'INVALID ({len(result.errors)} error(s))'}")
    for err in result.errors:
        print(f"    ✗  {err}")
    for w in result.warnings:
        print(f"    ⚠  {w}")

    return {
        "valid": result.ok,
        "errors": result.errors,
        "warnings": result.warnings,
        "suggestions": suggestions,
        "retry_count": state.retry_count,
        "retries_remaining": max(0, MAX_RETRIES - state.retry_count + 1),
    }


# ── Tool dispatcher ────────────────────────────────────────────────────────────

def _dispatch(state: AgentState, name: str, args: dict) -> dict:
    if name == "extract_scenario":
        return _tool_extract_scenario(state, **args)
    if name == "query_osm":
        return _tool_query_osm(state, **args)
    if name == "complete_parameters":
        return _tool_complete_parameters(state)
    if name == "generate_scenario":
        return _tool_generate_scenario(state, **args)
    if name == "validate_and_fix":
        return _tool_validate_and_fix(state)
    return {"error": f"Unknown tool: {name}"}


# ── Feedback loop helpers ──────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    """Extract first JSON object from LLM response text."""
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
    return None


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge overrides into base, preserving unmodified keys."""
    result = dict(base)
    for k, v in overrides.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def run_feedback_iteration(state: AgentState, report_text: str, user_feedback: str) -> dict:
    """
    Call the LLM with user feedback to get adjusted parameters,
    then regenerate and validate the scenario.
    Returns {success, xosc_path, xodr_path, overrides_applied, error}.
    """
    client = get_client()

    params_json = json.dumps(
        state.data.get("generated_simulation_parameters", {}),
        indent=2,
        ensure_ascii=False,
    )

    print(f"\n  ┌─ Feedback LLM call {'─' * (W - 22)}")
    print(f"  │  Feedback: {user_feedback[:80]}")

    messages = [
        {"role": "system", "content": FEEDBACK_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Original police report:\n{report_text}\n\n"
                f"Current simulation parameters:\n{params_json}\n\n"
                f"User feedback after watching simulation:\n{user_feedback}\n\n"
                "Output ONLY the JSON parameter_overrides to fix the issue."
            ),
        },
    ]

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.0,
        max_tokens=1024,
    )

    content = response.choices[0].message.content or ""
    print(f"  │  LLM response: {content[:200].strip()}")

    overrides = _extract_json(content)
    if overrides is None:
        print(f"  └─ ✗ Could not parse JSON from LLM response")
        return {"success": False, "error": f"LLM did not return valid JSON: {content[:300]}"}

    print(f"  │  Parsed overrides: {json.dumps(overrides, ensure_ascii=False)[:200]}")
    print(f"  └{'─' * (W - 2)}")

    # Deep-merge overrides into state data so actor sub-keys are preserved
    sim = state.data.get("generated_simulation_parameters", {})
    state.data["generated_simulation_parameters"] = _deep_merge(sim, overrides)

    gen_result = _tool_generate_scenario(state)
    if not gen_result.get("success"):
        return {"success": False, "error": gen_result.get("error")}

    val_result = _tool_validate_and_fix(state)

    return {
        "success": True,
        "valid": val_result.get("valid"),
        "xosc_path": str(state.xosc_path),
        "xodr_path": str(state.xodr_path),
        "overrides_applied": overrides,
        "validation_errors": val_result.get("errors", []),
    }


# ── Agent loop ─────────────────────────────────────────────────────────────────

def run_agent(report_text: str, scenario_id: str) -> dict:
    """
    Run the full agentic pipeline on one police report — no human interruption.
    Returns a result dict including the AgentState under key 'state'.
    """
    client = get_client()
    state = AgentState(scenario_id)

    print(f"\n{'═' * W}")
    print(f"  {scenario_id}")
    print(f"{'─' * W}")
    print(f"  {report_text[:120].strip()}...")
    print(f"{'═' * W}")

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Process this Berlin police accident report.\n"
                f"Scenario ID: {scenario_id}\n\n"
                f"Report:\n{report_text.strip()}"
            ),
        },
    ]

    iteration = 0
    final_valid = False
    final_summary: str | None = None

    while iteration < MAX_ITERATIONS:
        iteration += 1
        print(f"\n[Step {iteration}]")

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.0,
            max_tokens=768,
        )
        msg = response.choices[0].message

        if not getattr(msg, "tool_calls", None) and msg.content:
            raw_content = msg.content
            try:
                maybe_call = json.loads(raw_content)
                if not isinstance(maybe_call, dict):
                    maybe_call = None
            except Exception:
                maybe_call = None

            if maybe_call is None:
                maybe_call = _extract_json(raw_content)

            if not isinstance(maybe_call, dict):
                if "extract_scenario" in raw_content:
                    maybe_call = {
                        "name": "extract_scenario",
                        "parameters": {
                            "report_text": report_text.strip(),
                            "scenario_id": scenario_id,
                        },
                    }
                elif "query_osm" in raw_content:
                    maybe_call = {
                        "name": "query_osm",
                        "parameters": {
                            "osm_query": (state.data.get("location") or {}).get("osm_query", "") if state.data else "",
                        },
                    }
                elif "complete_parameters" in raw_content:
                    maybe_call = {"name": "complete_parameters", "parameters": {}}
                elif "generate_scenario" in raw_content:
                    maybe_call = {"name": "generate_scenario", "parameters": {}}
                elif "validate_and_fix" in raw_content:
                    maybe_call = {"name": "validate_and_fix", "parameters": {}}

            if isinstance(maybe_call, dict) and maybe_call.get("name"):
                fn_name = maybe_call.get("name")
                fn_args = maybe_call.get("parameters", maybe_call.get("arguments", {}))

                if isinstance(fn_args, str):
                    try:
                        fn_args = json.loads(fn_args)
                    except Exception:
                        fn_args = {}

                if not isinstance(fn_args, dict):
                    fn_args = {}

                fake_tool_calls = [
                    SimpleNamespace(
                        id=f"manual_tool_call_{iteration}",
                        type="function",
                        function=SimpleNamespace(
                            name=fn_name,
                            arguments=json.dumps(fn_args, ensure_ascii=False),
                        ),
                    )
                ]

                object.__setattr__(msg, "tool_calls", fake_tool_calls)
                object.__setattr__(msg, "content", None)

        if msg.content:
            print(f"  Agent: {textwrap.fill(msg.content.strip(), W - 10, subsequent_indent='          ')}")

        if not msg.tool_calls:
            final_summary = msg.content
            messages.append({"role": "assistant", "content": msg.content or ""})
            break

        messages.append(message_to_dict(msg))

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                fn_args = {}
            if not isinstance(fn_args, dict):
                fn_args = {}

            args_preview = ", ".join(f"{k}={repr(v)[:60]}" for k, v in fn_args.items())
            print(f"\n  ┌─ {fn_name}({args_preview})")

            state.record("tool_call", {"tool": fn_name, "args_keys": list(fn_args.keys())})

            try:
                tool_result = _dispatch(state, fn_name, fn_args)
            except Exception as exc:
                tool_result = {"error": f"Tool error: {exc}"}
                print(f"  ✗ {exc}")

            # Show extraction summary automatically (no human confirmation)
            if fn_name == "extract_scenario" and state.data:
                _show_extraction_summary(state.data)

            result_preview = json.dumps(tool_result, ensure_ascii=False)
            if len(result_preview) > 500:
                result_preview = result_preview[:500] + "  …"
            print(f"  └─ {result_preview}")

            state.record("tool_result", {
                "tool": fn_name,
                "result_keys": list(tool_result.keys()) if isinstance(tool_result, dict) else [],
            })

            if fn_name == "validate_and_fix" and isinstance(tool_result, dict) and tool_result.get("valid"):
                final_valid = True

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(tool_result, ensure_ascii=False),
            })

        if final_valid:
            summary_resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=0.0,
                max_tokens=256,
            )
            final_summary = summary_resp.choices[0].message.content or ""
            if final_summary:
                print(f"\n  {textwrap.fill(final_summary.strip(), W - 4, subsequent_indent='  ')}")
            break

        if state.retry_count > MAX_RETRIES:
            print(f"\n  Max retries ({MAX_RETRIES}) reached.")
            break

    # ── Save agent log ─────────────────────────────────────────────────────
    state.output_dir.mkdir(parents=True, exist_ok=True)
    log_path = state.output_dir / f"{scenario_id}.agent_log.json"
    log_path.write_text(
        json.dumps(
            {
                "scenario_id": scenario_id,
                "valid": final_valid,
                "iterations": iteration,
                "retries": state.retry_count,
                "final_summary": final_summary,
                "log": state.log,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # ── Final status ───────────────────────────────────────────────────────
    print(f"\n{'═' * W}")
    if final_valid:
        print(f"  ✓ VALID  —  {scenario_id}")
        print(f"{'─' * W}")
        print(f"  XODR  {state.xodr_path}")
        print(f"  XOSC  {state.xosc_path}")
    else:
        print(f"  ✗ INVALID  —  {scenario_id}")
    print(f"  Steps: {iteration}  |  Retries: {max(0, state.retry_count - 1)}")
    print(f"  Log:  {log_path}")
    print(f"{'═' * W}\n")

    return {
        "scenario_id": scenario_id,
        "valid": final_valid,
        "iterations": iteration,
        "retries": state.retry_count,
        "xodr_path": str(state.xodr_path) if state.xodr_path else None,
        "xosc_path": str(state.xosc_path) if state.xosc_path else None,
        "scenario_type": (
            state.data.get("classification", {}).get("scenario_type")
            if state.data else None
        ),
        "state": state,
    }
