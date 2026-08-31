"""
test_junction_extended_start_provenance.py — offline regression gate
covering a provenance-consistency gap introduced while fixing junction
crossing timing (generate_scenario.py), fully offline, no LLM/network call.

Context: generate_scenario.py's junction-crossing generator can extend an
actor's initial_s_m farther back along its real road (see
_generate_straight_crossing_openscenario's "_extend_if_slack") so a
fast/close actor can drive continuously at its own real speed instead of
crawling or parking near the junction to stay synchronized with the other
actor. That mutates the SAME data dict complete_parameters.py already
wrote a missing_parameters provenance entry into for that actor's
initial_s_m -- if the entry weren't updated too, a caller that saves
data back to *.enriched.json after generation (pipeline.py's
run_agent() does exactly this) would end up with a provenance record
claiming one initial_s_m value while openscenario.actors holds a
different one -- exactly the class of bug test_constants_provenance.py's
"provenance value_used matches the actual final (post-clamp) value" guards
against for the _clamp_initial_s_to_real_road case, just via a different
code path this time.

Usage:
    python3 test_junction_extended_start_provenance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_scenario import _generate_straight_crossing_openscenario, _JUNCTION_XODR_NAME

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  FAIL  {label}: {detail}")


def _crossing_data() -> dict:
    # A fast, close car next to a slow, far cyclist -- exactly the regime
    # that triggers the car's initial_s_m to be extended.
    return {
        "source": {"source_id": "test_junction_extended_start_provenance"},
        "classification": {"scenario_type": "crossing"},
        "conflict": {"collision_description": "test fixture"},
        "participants": [
            {"id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "go_straight"},
            {"id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "go_straight"},
        ],
        "missing_parameters": [
            {
                "parameter": "car_1.initial_s_m",
                "value_used": 2.0,
                "source": "engineering_assumption",
                "reason": "Kinematic backward projection from the conflict point (pre-extension).",
            },
        ],
        "generated_simulation_parameters": {
            "opendrive": {
                "road_length_m": 100,
                "primary_heading_rad": -1.5707963267948966,
                "secondary_heading_rad": 3.141592653589793,
                "motor_lane_width_m": 3.5,
                "bike_lane_width_m": 1.25,
                "primary_has_bike_facility": True,
            },
            "openscenario": {
                "simulation_duration_s": 10.0,
                "conflict": {"conflict_time_s": 4.0},
                "actors": {
                    "cyclist_1": {
                        "vehicle_category": "bicycle",
                        "initial_road_id": 0,
                        "initial_lane_id": -1,
                        "initial_s_m": 29.83,
                        "initial_speed_mps": 4.25,
                    },
                    "car_1": {
                        "vehicle_category": "car",
                        "initial_road_id": 1,
                        "initial_lane_id": -1,
                        "initial_s_m": 2.0,
                        "initial_speed_mps": 22.22,
                    },
                },
            },
        },
    }


def test_extended_initial_s_m_matches_updated_provenance_entry():
    import tempfile
    data = _crossing_data()
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "scenario.xosc"
        _generate_straight_crossing_openscenario(data, output_path, _JUNCTION_XODR_NAME)

    final_value = data["generated_simulation_parameters"]["openscenario"]["actors"]["car_1"]["initial_s_m"]
    entry = next(
        (m for m in data.get("missing_parameters", []) if m.get("parameter") == "car_1.initial_s_m"),
        None,
    )
    check("car_1.initial_s_m actually got extended past its original 2.0m (sanity check on test setup)",
          final_value > 2.0, final_value)
    check("car_1.initial_s_m still has exactly one missing_parameters entry (updated in place, not duplicated)",
          sum(1 for m in data.get("missing_parameters", []) if m.get("parameter") == "car_1.initial_s_m") == 1,
          data.get("missing_parameters"))
    check("the provenance entry's value_used matches the actual final initial_s_m used for generation",
          entry is not None and entry["value_used"] == final_value,
          (entry, final_value))


def main() -> None:
    test_extended_initial_s_m_matches_updated_provenance_entry()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all junction extended-start provenance checks (offline)")


if __name__ == "__main__":
    main()
