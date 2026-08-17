"""
Agentic Scenario Pipeline — core logic.

Converts a German Berlin police accident report into validated OpenDRIVE +
OpenSCENARIO simulation files through five fixed, deterministically
sequenced steps: extract_scenario -> query_osm -> complete_parameters ->
generate_scenario -> validate_and_fix.

run_agent() used to have an LLM decide the order of these five steps on
every call, even though the order never actually varied — SYSTEM_PROMPT
literally spelled it out as a "REQUIRED WORKFLOW" the model had to follow
verbatim every time. That's not a real sequencing decision, and live
testing showed it as a real cost: the model occasionally emitted
malformed tool-call JSON mid-sequence, burning retries for no benefit.
Removed — the five steps are now plain sequential Python calls to the
same underlying tool functions, no LLM involved in deciding what happens
next. The one LLM call left in the whole pipeline is inside
extract_scenario.py's own extraction (called from step 1) — the only
place that reads raw_text at all. See docs/modeling_assumptions.md and
speed_estimation.py's module docstring for the same reasoning applied
earlier to Agent 3.

run_feedback_iteration() keeps its own LLM call deliberately: interpreting
free-text human feedback ("move the cyclist closer") into a parameter
change is genuinely variable, unpredictable input a fixed script can't
handle — the kind of task an LLM is actually suited for, unlike walking a
checklist that never changes.

Entry point: run.py (at scenario_pipeline/ root).
Public API:  run_agent(report_text, scenario_id)
             run_feedback_iteration(state, report_text, user_feedback)
"""

from __future__ import annotations

import copy
import json
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

from llm_client import get_client, MODEL

import shutil

from extract_scenario import extract_scenario as _extract_scenario
from osm_enrichment import enrich_with_osm as _enrich_with_osm
from osm_enrichment import detect_topology as _detect_topology
from complete_parameters import complete_parameters as _complete_parameters
from template_selector import select_template as _select_template
from generate_scenario import generate_openscenario as _generate_openscenario
from validate_outputs import validate_generated_files as _validate_outputs
from provenance import check_agent1_preserved


OUTPUT_BASE = PROJECT_DIR / "output" / "agentic"
OSM_CACHE_DIR = PROJECT_DIR / "output" / "osm_cache"
MAX_RETRIES = 3

W = 70  # display width


# ── System prompts ────────────────────────────────────────────────────────────

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
        self.agent1_snapshot: dict | None = None
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
    print(f"  │  Collision  {col}  ·  heading: {conflict.get('heading_relation')}")
    print(f"  │  Bike infra {road.get('bike_facility_type')}")
    print(f"  └{'─' * (W - 2)}")


# ── Tool implementations ──────────────────────────────────────────────────────

def _fill_location_query_fields(location: dict, participants: list) -> None:
    """extract_scenario.py's LLM output is pure semantic extraction — no OSM
    query, no city constant, no location_type. This pipeline's tool-calling
    loop needs an osm_query string to pass to the next tool (query_osm) and a
    location_type for its own digest, so that glue lives here, at the one
    place that actually consumes it, not inside the extraction agent itself.
    """
    primary = location.get("primary_road")
    secondary = location.get("secondary_road")
    house_number = location.get("house_number_reference")

    location["city"] = "Berlin"

    if primary and secondary:
        location["osm_query"] = f"{primary} / {secondary}, Berlin, Germany"
    elif primary:
        location["osm_query"] = f"{primary}, Berlin, Germany"
    else:
        location["osm_query"] = None
    location["osm_roads"] = [road for road in (primary, secondary) if road]

    directions = []
    for participant in participants:
        direction = participant.get("initial_direction")
        if direction and direction not in directions:
            directions.append(direction)
    location["direction_references"] = directions

    if secondary:
        location["location_type"] = "intersection"
    elif house_number:
        location["location_type"] = "midblock"
    else:
        location["location_type"] = None


def _tool_extract_scenario(state: AgentState, report_text: str, scenario_id: str) -> dict:
    print(f"  → Calling LLM ({MODEL}) for extraction...")
    extracted = _extract_scenario(report_text, scenario_id)
    _fill_location_query_fields(
        extracted.setdefault("location", {}), extracted.get("participants", [])
    )
    state.data = extracted

    # Snapshot Agent 1's output before anything downstream (OSM enrichment,
    # parameter completion) can touch it. Later stages are checked against
    # this snapshot via provenance.check_agent1_preserved: they may fill in
    # fields Agent 1 left null, but must never change one it already set.
    state.agent1_snapshot = copy.deepcopy(extracted)
    state.output_dir.mkdir(parents=True, exist_ok=True)
    agent1_path = state.output_dir / f"{scenario_id}.agent1.json"
    agent1_path.write_text(
        json.dumps(extracted, indent=2, ensure_ascii=False), encoding="utf-8"
    )

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
        "location_type": extracted["location"]["location_type"],
    }


def _tool_query_osm(state: AgentState, osm_query: str) -> dict:
    if state.data is None:
        return {"error": "extract_scenario must be called before query_osm"}

    # osm_query is a required tool-call argument (the LLM must supply
    # something to invoke this tool), but the authoritative value already
    # lives in state.data["location"]["osm_query"] — built deterministically
    # by _fill_location_query_fields() from Agent 1's own primary_road/
    # secondary_road fields, not regenerated by the LLM. Live verification
    # against the real 8B model found it unreliable at reproducing German
    # special characters verbatim through its own token generation (e.g.
    # "Straße" -> "Straöse", "Gutschmidtstraße" -> "Gutschmidtstraöse") —
    # writing that regenerated string over the already-correct one used to
    # trip check_agent1_preserved() below (correctly, by that check's own
    # logic — it just wasn't meant to catch the LLM garbling its own
    # required argument). The LLM's osm_query argument is therefore never
    # written into state; the trusted stored value is used throughout.
    trusted_osm_query = state.data["location"].get("osm_query")
    print(f"  → Querying OSM: {trusted_osm_query!r}")
    state.output_dir.mkdir(parents=True, exist_ok=True)

    enriched = _enrich_with_osm(state.data, OSM_CACHE_DIR)
    check_agent1_preserved(state.agent1_snapshot, enriched)
    state.data = enriched

    # Snapshot the state right after OSM enrichment, before parameter
    # completion (Agent 3) touches it — distinct from both .agent1.json
    # (before any enrichment) and .enriched.json (written later, after
    # Agent 3 has also run). Gives a clean, inspectable middle checkpoint:
    # exactly what OSM alone contributed, with no actor data yet (that's
    # entirely Agent 3's job, not present at this point).
    sid = state.data["source"]["source_id"]
    osm_enriched_path = state.output_dir / f"{sid}.osm_enriched.json"
    osm_enriched_path.write_text(
        json.dumps(state.data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Topology detection and template selection used to happen later, in
    # _tool_generate_scenario — after complete_parameters() (Agent 3) had
    # already run and guessed at road geometry it had no way to get right
    # (see docs/modeling_assumptions.md's "Out of scope" history: this is
    # why generate_scenario.py grew _resolve_road_id()/
    # _clamp_initial_s_to_real_road() to silently patch Agent 3's guesses
    # afterward). detect_topology() only needs the report text and scenario
    # id — both already exist at this point, before OSM enrichment even
    # runs — so there's no reason to defer it. Moving it here lets Agent 3
    # work with the real selected template's actual geometry from the
    # start instead of a synthetic placeholder.
    scenario_type = state.data.get("classification", {}).get("scenario_type", "")
    report_text = state.data.get("source", {}).get("raw_text", "")
    topology_result = _detect_topology(report_text, sid, cache_dir=OSM_CACHE_DIR)
    state.data["topology"] = topology_result
    state.record("detect_topology", topology_result)
    print(
        f"  ✓ Topology: {topology_result['topology']}"
        f"  (streets={topology_result['streets']}, way_count={topology_result['way_count']})"
    )
    template_rel = _select_template(scenario_type, topology_result["topology"])
    state.data["template_used"] = Path(template_rel).name

    ctx = enriched.get("osm_context", {})
    status = ctx.get("enrichment_status", "unknown")
    notes = ctx.get("notes", [])
    state.record("query_osm", {"status": status, "query": trusted_osm_query, "notes": notes})
    print(f"  ✓ OSM status: {status}")
    for note in notes:
        print(f"  ⚠ OSM enrichment note: {note}")

    result: dict = {
        "enrichment_status": status,
    }
    if notes:
        result["notes"] = notes
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

    completed = _complete_parameters(state.data)
    check_agent1_preserved(state.agent1_snapshot, completed)
    state.data = completed
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
                if not isinstance(vals, dict):
                    continue
                if section == "actors":
                    # complete_parameters()'s tool response flattens actors to a
                    # top-level "actors" key (see _tool_complete_parameters), but
                    # the data generate_scenario.py actually reads lives one level
                    # deeper, under openscenario.actors (_osc_params/_actor_params
                    # in generate_scenario.py). Route there instead of writing to
                    # an "actors" key nothing ever reads.
                    osc = sim.setdefault("openscenario", {})
                    osc["actors"] = _deep_merge(osc.get("actors", {}), vals)
                else:
                    sim[section] = _deep_merge(sim.get(section, {}), vals)
                print(f"  ✓ Applied override [{section}]: {vals}")
        except json.JSONDecodeError as exc:
            return {"success": False, "error": f"Invalid parameter_overrides JSON: {exc}"}

    state.output_dir.mkdir(parents=True, exist_ok=True)
    sid = state.data["source"]["source_id"]
    xosc_path = state.output_dir / f"{sid}.xosc"
    enriched_path = state.output_dir / f"{sid}.enriched.json"

    # Topology detection and template selection now happen in _tool_query_osm,
    # right after OSM enrichment, so Agent 3 (complete_parameters()) can work
    # with the real selected template's geometry instead of a placeholder —
    # see that function's comment. Reuse what's already there; only
    # recompute as a defensive fallback for a caller that skipped that step
    # (e.g. an older saved state resumed from before this change).
    xodr_filename = state.data.get("template_used")
    if xodr_filename:
        topology_result = state.data.get("topology", {})
    else:
        scenario_type = state.data.get("classification", {}).get("scenario_type", "")
        report_text = state.data.get("source", {}).get("raw_text", "")
        topology_result = _detect_topology(report_text, sid, cache_dir=OSM_CACHE_DIR)
        state.data["topology"] = topology_result
        state.record("detect_topology", topology_result)
        template_rel = _select_template(scenario_type, topology_result["topology"])
        xodr_filename = Path(template_rel).name
        state.data["template_used"] = xodr_filename

    print(
        f"  ✓ Topology: {topology_result.get('topology', 'unknown')}"
        f"  (streets={topology_result.get('streets')}, way_count={topology_result.get('way_count')})"
    )
    template_src = Path(__file__).resolve().parent / "templates" / xodr_filename
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


def _build_deterministic_summary(state: AgentState) -> str:
    """Plain-Python recap of what was generated. Replaces the old closing
    LLM call, which only ever restated data already computed
    deterministically upstream — nothing in that summary required actual
    reasoning, so producing it doesn't need a model either."""
    data = state.data or {}
    stype = data.get("classification", {}).get("scenario_type", "?")
    mechanism = data.get("conflict", {}).get("conflict_mechanism", "?")
    roles = ", ".join(
        f"{p.get('id')} ({p.get('type')}, {p.get('maneuver')})"
        for p in data.get("participants", [])
    )
    odr = data.get("generated_simulation_parameters", {}).get("opendrive", {})
    osc = data.get("generated_simulation_parameters", {}).get("openscenario", {})
    actors = osc.get("actors", {})
    speeds = ", ".join(
        f"{aid}={a.get('initial_speed_mps')} m/s" for aid, a in actors.items()
    )
    return (
        f"{stype} scenario ({mechanism}). Participants: {roles}. "
        f"Road length {odr.get('road_length_m')} m, "
        f"duration {osc.get('simulation_duration_s')} s. "
        f"Initial speeds: {speeds}."
    )


def run_agent(report_text: str, scenario_id: str) -> dict:
    """
    Run the full pipeline on one police report — no human interruption.
    Returns a result dict including the AgentState under key 'state'.

    Five fixed steps, deterministic Python sequencing — see module
    docstring for why this replaced an LLM tool-calling loop that only
    ever executed the same order every time. The one LLM call anywhere in
    this function is inside _tool_extract_scenario (step 1).

    On a validation failure, retries regenerate deterministically — no
    LLM-improvised parameter_overrides. If generate_scenario/
    complete_parameters (already using real template geometry and cited
    defaults) still produces invalid output after MAX_RETRIES attempts,
    that's a real bug worth fixing at the source, not something to paper
    over with a guessed patch (run_feedback_iteration still exists for
    the genuinely different case of a human, not validate_and_fix,
    reporting something wrong).
    """
    state = AgentState(scenario_id)

    print(f"\n{'═' * W}")
    print(f"  {scenario_id}")
    print(f"{'─' * W}")
    print(f"  {report_text[:120].strip()}...")
    print(f"{'═' * W}")

    step = 0
    final_valid = False
    final_summary: str | None = None
    last_errors: list[str] = []

    step += 1
    print(f"\n[Step {step}] extract_scenario")
    try:
        extract_result = _tool_extract_scenario(
            state, report_text=report_text.strip(), scenario_id=scenario_id
        )
    except Exception as exc:
        extract_result = {"error": f"Tool error: {exc}"}

    if "error" in extract_result:
        last_errors = [extract_result["error"]]
    else:
        _show_extraction_summary(state.data)

        step += 1
        print(f"\n[Step {step}] query_osm")
        osm_query = (state.data.get("location") or {}).get("osm_query") or ""
        try:
            osm_result = _tool_query_osm(state, osm_query=osm_query)
        except Exception as exc:
            osm_result = {"error": f"Tool error: {exc}"}

        if "error" in osm_result:
            last_errors = [osm_result["error"]]
        else:
            step += 1
            print(f"\n[Step {step}] complete_parameters")
            try:
                _tool_complete_parameters(state)
            except Exception as exc:
                last_errors = [f"Tool error: {exc}"]
            else:
                attempt = 0
                while attempt < MAX_RETRIES and not final_valid:
                    attempt += 1
                    step += 1
                    print(f"\n[Step {step}] generate_scenario (attempt {attempt})")
                    gen_result = _tool_generate_scenario(state)
                    if not gen_result.get("success"):
                        last_errors = [gen_result.get("error", "generate_scenario failed")]
                        continue

                    step += 1
                    print(f"\n[Step {step}] validate_and_fix (attempt {attempt})")
                    val_result = _tool_validate_and_fix(state)
                    last_errors = val_result.get("errors", [])
                    if val_result.get("valid"):
                        final_valid = True

    if final_valid:
        final_summary = _build_deterministic_summary(state)
        print(f"\n  {textwrap.fill(final_summary, W - 4, subsequent_indent='  ')}")
    elif last_errors:
        final_summary = "Failed: " + "; ".join(last_errors)

    # ── Save agent log ─────────────────────────────────────────────────────
    state.output_dir.mkdir(parents=True, exist_ok=True)
    log_path = state.output_dir / f"{scenario_id}.agent_log.json"
    log_path.write_text(
        json.dumps(
            {
                "scenario_id": scenario_id,
                "valid": final_valid,
                "iterations": step,
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
        for err in last_errors:
            print(f"    ✗  {err}")
    print(f"  Steps: {step}  |  Retries: {max(0, state.retry_count - 1)}")
    print(f"  Log:  {log_path}")
    print(f"{'═' * W}\n")

    return {
        "scenario_id": scenario_id,
        "valid": final_valid,
        "iterations": step,
        "retries": state.retry_count,
        "xodr_path": str(state.xodr_path) if state.xodr_path else None,
        "xosc_path": str(state.xosc_path) if state.xosc_path else None,
        "scenario_type": (
            state.data.get("classification", {}).get("scenario_type")
            if state.data else None
        ),
        "state": state,
    }
