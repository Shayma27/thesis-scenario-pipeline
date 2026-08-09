"""
Test: Agent 1 (extract_scenario.py) output shape and downstream compatibility.

Runs entirely offline — no vLLM endpoint required — by stubbing the LLM
response. Checks:
  1. extract_scenario() returns PURE LLM output: no osm_query/city/
     location_type/direction_references/generated_simulation_parameters/
     missing_parameters/evidence/heading_relation/traffic_rule_status leak
     in from anywhere.
  2. source_id/raw_text are always Python-set from the function arguments,
     never trusted from the LLM.
  3. Invalid (non-enum) values the model sometimes invents — e.g. "same as
     final", "torstraße" for initial_direction — get sanitized to null.
  4. scenario_type=turning is enforced whenever the motor vehicle's own
     maneuver is a turn, even if the model self-contradicts and picks a
     different scenario_type (a real failure seen in a live batch run).
  5. pipeline._fill_location_query_fields() correctly derives what
     pipeline.py's tool-calling loop needs.
  6. The result survives real (non-mocked) calls into
     osm_enrichment._build_location_queries() and
     complete_parameters.complete_parameters() without crashing.
"""

from __future__ import annotations

import copy
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

# Note: no "raw_text" here — the LLM is no longer asked to reproduce it,
# extract_scenario() injects it from the report_text argument instead.
FAKE_LLM_JSON = {
    "schema_version": "0.2",
    "source": {
        "dataset": "Berlin Police Reports",
        "source_id": "THIS_SHOULD_BE_OVERWRITTEN",
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
            "initial_direction": "north", "road_position": None,
        },
        {
            "id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "go_straight",
            "initial_direction": "north", "road_position": None,
        },
    ],
    "conflict": {
        "conflict_mechanism": "right_turn_across_separated_cycle_track",
        "collision_happened": True,
        "collision_description": "A truck turning right ran over a cyclist riding straight on the adjacent separated cycle track.",
    },
}


def _stub_llm_response(fake_json: dict) -> None:
    class _Message:
        content = json.dumps(fake_json)

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


def test_structure_and_downstream_compat() -> list[str]:
    _stub_llm_response(FAKE_LLM_JSON)
    result = es.extract_scenario(SALVADOR_RAW, "right_turn_salvador_allende_1038")

    failures = []

    # source_id/raw_text always Python-set, never trusted from the LLM
    if result["source"]["source_id"] != "right_turn_salvador_allende_1038":
        failures.append("source_id was not overridden from the script argument")
    if result["source"]["raw_text"] != SALVADOR_RAW:
        failures.append("raw_text was not set from the report_text argument")

    # pure LLM output — no Python-derived or simulator fields
    for leaked_field in ("osm_query", "city", "osm_roads", "direction_references", "location_type"):
        if leaked_field in result["location"]:
            failures.append(f"location.{leaked_field} leaked into extract_scenario() output")
    if "generated_simulation_parameters" in result:
        failures.append("generated_simulation_parameters leaked into extract_scenario() output")
    if "missing_parameters" in result:
        failures.append("missing_parameters leaked into extract_scenario() output")
    if "evidence" in result:
        failures.append("evidence leaked into extract_scenario() output")
    if "heading_relation" in result.get("conflict", {}):
        failures.append("conflict.heading_relation leaked into extract_scenario() output")
    for dropped_field in ("date", "time"):
        if dropped_field in result["source"]:
            failures.append(f"source.{dropped_field} should have been dropped from the schema")
    for participant in result["participants"]:
        if "traffic_rule_status" in participant:
            failures.append(f"{participant['id']}.traffic_rule_status leaked into extract_scenario() output")

    # pipeline.py's compatibility glue derives what it needs, correctly
    import pipeline as pl

    pl._fill_location_query_fields(result["location"], result["participants"])
    expected_query = "Salvador-Allende-Str / Müggelschlößchenweg, Berlin, Germany"
    if result["location"]["osm_query"] != expected_query:
        failures.append(f"osm_query mismatch: {result['location']['osm_query']!r}")
    if result["location"]["location_type"] != "intersection":
        failures.append(f"location_type mismatch: {result['location']['location_type']!r}")

    # pipeline.py's _tool_extract_scenario digest uses bracket access on these —
    # they must exist or the real tool-calling loop would KeyError.
    for section, key in (
        ("location", "primary_road"), ("location", "secondary_road"), ("location", "osm_query"),
        ("location", "location_type"), ("classification", "scenario_type"),
        ("classification", "confidence"), ("conflict", "conflict_mechanism"),
        ("conflict", "collision_happened"), ("road_context", "bike_facility_type"),
    ):
        if key not in result[section]:
            failures.append(f"pipeline.py requires {section}.{key}, which is missing")

    # real downstream functions run without crashing
    import osm_enrichment as oe
    queries = oe._build_location_queries(result)
    if not queries:
        failures.append("osm_enrichment._build_location_queries() produced no candidates")

    import complete_parameters as cp
    completed = cp.complete_parameters(result)
    actors = completed.get("generated_simulation_parameters", {}).get("openscenario", {}).get("actors", {})
    if set(actors) != {"truck_1", "cyclist_1"}:
        failures.append(f"complete_parameters() produced unexpected actors: {set(actors)}")

    return failures


def test_sanitizes_invalid_enum_values() -> list[str]:
    """Reproduces the real turning_01/turning_06 bugs: the model inventing a
    non-enum string ("same as final", "torstraße") for initial_direction
    instead of a compass word or null.
    """
    broken = copy.deepcopy(FAKE_LLM_JSON)
    broken["participants"][0]["initial_direction"] = "same as final"
    broken["participants"][1]["initial_direction"] = "torstraße"
    broken["road_context"]["bike_facility_position"] = "somewhere vague"
    _stub_llm_response(broken)
    result = es.extract_scenario(SALVADOR_RAW, "test_sanitize")

    failures = []
    if result["participants"][0]["initial_direction"] is not None:
        failures.append(
            f"invalid initial_direction 'same as final' was not sanitized to null, "
            f"got {result['participants'][0]['initial_direction']!r}"
        )
    if result["participants"][1]["initial_direction"] is not None:
        failures.append(
            f"invalid initial_direction 'torstraße' was not sanitized to null, "
            f"got {result['participants'][1]['initial_direction']!r}"
        )
    if result["road_context"]["bike_facility_position"] is not None:
        failures.append("invalid bike_facility_position was not sanitized to null")
    # valid enum values must survive untouched
    if result["participants"][0]["road_position"] is not None:
        failures.append("valid null road_position was incorrectly changed")
    return failures


def test_enforces_turning_definition() -> list[str]:
    """Reproduces the real turning_06 bug: the model extracted the motor
    vehicle's maneuver as turn_right (correct) but still classified
    scenario_type as crossing (self-contradiction). Python must force
    scenario_type=turning using the maneuver it already has.
    """
    contradictory = copy.deepcopy(FAKE_LLM_JSON)
    contradictory["classification"]["scenario_type"] = "crossing"
    assert contradictory["participants"][0]["maneuver"] == "turn_right"  # sanity check on the fixture
    _stub_llm_response(contradictory)
    result = es.extract_scenario(SALVADOR_RAW, "test_turning_enforced")

    failures = []
    if result["classification"]["scenario_type"] != "turning":
        failures.append(
            f"scenario_type was not forced to 'turning' despite motor_vehicle.maneuver=turn_right, "
            f"got {result['classification']['scenario_type']!r}"
        )
    return failures


def main() -> None:
    all_failures: list[tuple[str, str]] = []
    for name, test_fn in (
        ("structure_and_downstream_compat", test_structure_and_downstream_compat),
        ("sanitizes_invalid_enum_values", test_sanitizes_invalid_enum_values),
        ("enforces_turning_definition", test_enforces_turning_definition),
    ):
        for failure in test_fn():
            all_failures.append((name, failure))

    print(f"\n{'═' * 70}")
    if all_failures:
        print(f"  FAILED — {len(all_failures)} issue(s)")
        for test_name, f in all_failures:
            print(f"    ✗ [{test_name}] {f}")
        print(f"{'═' * 70}\n")
        sys.exit(1)
    else:
        print("  PASSED — extract_scenario.py output is clean, sanitized,")
        print("  self-consistent, and downstream agents consume it without crashing.")
        print(f"{'═' * 70}\n")


if __name__ == "__main__":
    main()
