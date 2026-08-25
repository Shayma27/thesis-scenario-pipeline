"""
test_car_path_maneuver.py — offline regression gate for
_apply_lane_guided_maneuver_context (osm_enrichment.py), fully offline, no
LLM/network call.

Live-verified gap this guards against: for "crossing" scenarios, car_path
used to be set to "turn_left_from_secondary_to_primary" only when car_1's
maneuver said turn_left AND OSM turn:lanes tag data corroborated it (car_1
on the report's leftmost motor lane, matched OSM approach's first tagged
turn lane containing "left"). OSM turn:lanes tagging is sparse in
practice, so this never fired for any of the 19 real reports even when a
report unambiguously described a left-turning car — Agent 1's own
gold-verified maneuver field is now sufficient on its own, with OSM
lane data kept only as corroborating evidence in the provenance record.

Usage:
    python3 test_car_path_maneuver.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from osm_enrichment import _apply_lane_guided_maneuver_context

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  FAIL  {label}: {detail}")


def _crossing_report(car_maneuver: str, road_position=None) -> dict:
    return {
        "classification": {"scenario_type": "crossing"},
        "participants": [
            {"id": "car_1", "class": "motor_vehicle", "type": "car",
             "maneuver": car_maneuver, "road_position": road_position},
            {"id": "cyclist_1", "class": "cyclist", "type": "bicycle",
             "maneuver": "go_straight"},
        ],
    }


def test_turn_left_maneuver_sets_car_path_with_no_osm_evidence():
    # The exact live-verified gap: maneuver alone says turn_left, no OSM
    # turn:lanes data at all -- car_path must still be set.
    data = _crossing_report("turn_left", road_position=None)
    context = {}
    _apply_lane_guided_maneuver_context(data, context)
    osc_params = data.get("generated_simulation_parameters", {}).get("openscenario", {})
    check("turn_left maneuver with zero OSM evidence still sets car_path",
          osc_params.get("car_path") == "turn_left_from_secondary_to_primary", osc_params)
    check("lane_guided_maneuver source is agent1_maneuver, not an OSM inference",
          context.get("lane_guided_maneuver", {}).get("source") == "agent1_maneuver", context)
    check("osm_corroborates is False when no OSM turn:lanes evidence is present",
          context.get("lane_guided_maneuver", {}).get("evidence", {}).get("osm_corroborates") is False,
          context)


def test_turn_left_into_parking_also_matches():
    # maneuver values like "turn_left_into_parking" contain "turn_left" as
    # a substring and should match the same way "turn_left" alone does.
    data = _crossing_report("turn_left_into_parking")
    context = {}
    _apply_lane_guided_maneuver_context(data, context)
    osc_params = data.get("generated_simulation_parameters", {}).get("openscenario", {})
    check("turn_left_into_parking sets car_path",
          osc_params.get("car_path") == "turn_left_from_secondary_to_primary", osc_params)


def test_go_straight_maneuver_never_sets_car_path():
    data = _crossing_report("go_straight", road_position="leftmost_motor_lane")
    context = {
        "lane_count_evidence": {
            "secondary": {"selected_segments": [{"turn_lanes": "left|through"}]}
        }
    }
    _apply_lane_guided_maneuver_context(data, context)
    osc_params = data.get("generated_simulation_parameters", {}).get("openscenario", {})
    check("go_straight maneuver never sets car_path, even with OSM turn-lane evidence present",
          "car_path" not in osc_params, osc_params)


def test_non_crossing_scenario_type_never_sets_car_path():
    data = _crossing_report("turn_left")
    data["classification"]["scenario_type"] = "turning"
    context = {}
    _apply_lane_guided_maneuver_context(data, context)
    osc_params = data.get("generated_simulation_parameters", {}).get("openscenario", {})
    check("non-crossing scenario_type never sets car_path (that path is handled elsewhere)",
          "car_path" not in osc_params, osc_params)


def test_osm_corroboration_recorded_when_present():
    data = _crossing_report("turn_left", road_position="leftmost_motor_lane")
    context = {
        "lane_count_evidence": {
            "secondary": {"selected_segments": [{"turn_lanes": "left|through"}]}
        }
    }
    _apply_lane_guided_maneuver_context(data, context)
    evidence = context.get("lane_guided_maneuver", {}).get("evidence", {})
    check("osm_corroborates is True when leftmost_motor_lane + OSM left turn-lane tag both present",
          evidence.get("osm_corroborates") is True, evidence)


def main() -> None:
    test_turn_left_maneuver_sets_car_path_with_no_osm_evidence()
    test_turn_left_into_parking_also_matches()
    test_go_straight_maneuver_never_sets_car_path()
    test_non_crossing_scenario_type_never_sets_car_path()
    test_osm_corroboration_recorded_when_present()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all car_path/lane-guided-maneuver checks (offline)")


if __name__ == "__main__":
    main()
