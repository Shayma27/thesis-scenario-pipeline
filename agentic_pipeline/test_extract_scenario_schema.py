"""
Test: Agent 1 (extract_scenario.py) output shape and downstream compatibility.

Runs entirely offline — no vLLM endpoint required — by stubbing the LLM
response with a hand-written, schema-correct JSON for the Salvador-Allende-
Str. turning report. Checks:
  1. extract_scenario() returns PURE LLM output: no osm_query/city/
     location_type/direction_references/generated_simulation_parameters/
     missing_parameters leak in from anywhere.
  2. source_id is always overridden from the script argument.
  3. pipeline._fill_location_query_fields() correctly derives the
     osm_query/city/osm_roads/direction_references/location_type fields
     pipeline.py's tool-calling loop needs, without mutating anything the
     LLM already produced.
  4. The result survives real (non-mocked) calls into
     osm_enrichment._build_location_queries() and
     complete_parameters.complete_parameters() without crashing.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── stub the openai package so this test needs neither network nor the
# openai pip package installed — only extract_scenario.py's own logic is
# under test here, not the real LLM call. ───────────────────────────────────
if "openai" not in sys.modules:
    fake_openai = types.ModuleType("openai")

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            pass

    fake_openai.OpenAI = _FakeOpenAI
    sys.modules["openai"] = fake_openai

import extract_scenario as es  # noqa: E402  (after the openai stub above)

SALVADOR_RAW = (
    "Der Fahrer wartete in dem von ihm geführten LKW auf der Fahrbahn der "
    "Salvador-Allende-Str. vor der Rotlicht abstrahlenden LSA. Als die LSA für seine "
    "Richtung grünes Licht abstrahlte, bog er nach rechts in den Müggelschlößchenweg ab "
    "und erfasste die neben ihm in gleicher Richtung geradeaus fahrende Radfahrerin. Sie "
    "befuhr den von der Fahrbahn baulich getrennten Radweg der Salvador-Allende-Str. in "
    "nördliche Richtung. Die LSA für Radfahrende strahlte grünes Wechsellicht ab. Die "
    "Radfahrerin und das Fahrrad wurden durch den rechts abbiegenden LKW vollständig "
    "überrollt."
)

FAKE_LLM_JSON = {
    "schema_version": "0.2",
    "source": {
        "dataset": "Berlin Police Reports",
        "source_id": "THIS_SHOULD_BE_OVERWRITTEN",
        "raw_text": SALVADOR_RAW,
    },
    "classification": {"scenario_type": "turning", "confidence": 1.0},
    "location": {
        "primary_road": "Salvador-Allende-Str",
        "secondary_road": "Müggelschlößchenweg",
        "house_number_reference": None,
    },
    "road_context": {"bike_facility_type": "separated_cycle_track", "bike_facility_position": None},
    "participants": [
        {
            "id": "truck_1", "class": "motor_vehicle", "type": "truck", "maneuver": "turn_right",
            "initial_direction": "north", "traffic_rule_status": None, "road_position": None,
        },
        {
            "id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "go_straight",
            "initial_direction": "north", "traffic_rule_status": None, "road_position": None,
        },
    ],
    "conflict": {
        "conflict_mechanism": "right_turn_across_separated_cycle_track",
        "heading_relation": "same_direction",
        "collision_happened": True,
        "collision_description": "A truck turning right ran over a cyclist riding straight on the adjacent separated cycle track.",
    },
    "evidence": {
        "location": "Salvador-Allende-Str. / Müggelschlößchenweg",
        "vehicle_maneuver": "bog er nach rechts ... ab",
        "cyclist_maneuver": "geradeaus fahrende Radfahrerin",
        "heading_relation": "in gleicher Richtung",
        "bike_facility": "baulich getrennten Radweg",
        "traffic_rule_status": None,
    },
}


def _stub_llm_response(monkeypatch_client) -> None:
    class _Message:
        content = json.dumps(FAKE_LLM_JSON)

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            return _Response()

    class _Chat:
        completions = _Completions()

    class _FakeClient:
        chat = _Chat()

    es.get_client = lambda: _FakeClient()


def main() -> None:
    _stub_llm_response(es)

    result = es.extract_scenario(SALVADOR_RAW, "right_turn_salvador_allende_1038")

    failures = []

    # 1. source_id always overridden
    if result["source"]["source_id"] != "right_turn_salvador_allende_1038":
        failures.append("source_id was not overridden from the script argument")

    # 2. pure LLM output — no Python-derived or simulator fields
    for leaked_field in ("osm_query", "city", "osm_roads", "direction_references", "location_type"):
        if leaked_field in result["location"]:
            failures.append(f"location.{leaked_field} leaked into extract_scenario() output")
    if "generated_simulation_parameters" in result:
        failures.append("generated_simulation_parameters leaked into extract_scenario() output")
    if "missing_parameters" in result:
        failures.append("missing_parameters leaked into extract_scenario() output")
    for dropped_field in ("date", "time"):
        if dropped_field in result["source"]:
            failures.append(f"source.{dropped_field} should have been dropped from the schema")

    # 3. pipeline.py's compatibility glue derives what it needs, correctly
    import pipeline as pl

    pl._fill_location_query_fields(result["location"], result["participants"])
    expected_query = "Salvador-Allende-Str / Müggelschlößchenweg, Berlin, Germany"
    if result["location"]["osm_query"] != expected_query:
        failures.append(f"osm_query mismatch: {result['location']['osm_query']!r}")
    if result["location"]["location_type"] != "intersection":
        failures.append(f"location_type mismatch: {result['location']['location_type']!r}")

    # pipeline.py's _tool_extract_scenario digest uses bracket access on these —
    # they must exist or the real tool-calling loop would KeyError.
    for path in (
        ("location", "primary_road"), ("location", "secondary_road"), ("location", "osm_query"),
        ("location", "location_type"), ("classification", "scenario_type"),
        ("classification", "confidence"), ("conflict", "conflict_mechanism"),
        ("conflict", "collision_happened"), ("road_context", "bike_facility_type"),
    ):
        section, key = path
        if key not in result[section]:
            failures.append(f"pipeline.py requires {section}.{key}, which is missing")

    # 4. real downstream functions run without crashing
    import osm_enrichment as oe
    queries = oe._build_location_queries(result)
    if not queries:
        failures.append("osm_enrichment._build_location_queries() produced no candidates")

    import complete_parameters as cp
    completed = cp.complete_parameters(result)
    actors = completed.get("generated_simulation_parameters", {}).get("openscenario", {}).get("actors", {})
    if set(actors) != {"truck_1", "cyclist_1"}:
        failures.append(f"complete_parameters() produced unexpected actors: {set(actors)}")

    print(f"\n{'═' * 70}")
    if failures:
        print(f"  FAILED — {len(failures)} issue(s)")
        for f in failures:
            print(f"    ✗ {f}")
        print(f"{'═' * 70}\n")
        sys.exit(1)
    else:
        print("  PASSED — extract_scenario.py is pure LLM output;")
        print("  pipeline.py glue + downstream agents all consume it cleanly.")
        print(f"{'═' * 70}\n")


if __name__ == "__main__":
    main()
