"""
test_cyclist_junction_maneuver.py — offline regression gate for
_generate_straight_crossing_openscenario's cyclist trajectory (generate_scenario.py),
fully offline, no LLM/network call.

Live-verified bug this guards against (2026-08-26 visual review, crossing_06):
the cyclist's junction trajectory was built with
_junction_maneuver_samples(0, "go_straight", ...) — the maneuver kind was a
hardcoded literal, never reading cyclist_1's actual "maneuver" field at all.
A turn_left cyclist (crossing_05/06) rendered going straight through the
junction, so the reported turn never visibly happened. This mirrors the same
class of bug the car_path fix (test_car_path_maneuver.py) addressed for the
car side; this test covers the cyclist side of the same generator.

Usage:
    python3 test_cyclist_junction_maneuver.py
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from generate_scenario import _generate_straight_crossing_openscenario, _JUNCTION_XODR_NAME

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  FAIL  {label}: {detail}")


def _crossing_data(cyclist_maneuver: str) -> dict:
    return {
        "source": {"source_id": "test_cyclist_junction_maneuver"},
        "classification": {"scenario_type": "crossing"},
        "conflict": {"collision_description": "test fixture"},
        "participants": [
            {"id": "cyclist_1", "class": "cyclist", "type": "bicycle",
             "maneuver": cyclist_maneuver},
            {"id": "car_1", "class": "motor_vehicle", "type": "car",
             "maneuver": "go_straight"},
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
                "simulation_duration_s": 12.0,
                "conflict": {"conflict_time_s": 6.0},
                "actors": {
                    "cyclist_1": {
                        "vehicle_category": "bicycle",
                        "initial_road_id": 0,
                        "initial_lane_id": -1,
                        "initial_s_m": 20.0,
                        "initial_speed_mps": 4.0,
                    },
                    "car_1": {
                        "vehicle_category": "car",
                        "initial_road_id": 1,
                        "initial_lane_id": -1,
                        "initial_s_m": 20.0,
                        "initial_speed_mps": 8.0,
                    },
                },
            },
        },
    }


def _cyclist_headings(output_path: Path) -> list[float]:
    tree = ET.parse(output_path)
    trajectory = next(
        t for t in tree.getroot().iter("Trajectory")
        if t.get("name") == "CyclistEnterIntersectionTrajectory"
    )
    return [float(wp.get("h")) for wp in trajectory.iter("WorldPosition")]


def _cyclist_final_xy(output_path: Path) -> tuple[float, float]:
    tree = ET.parse(output_path)
    trajectory = next(
        t for t in tree.getroot().iter("Trajectory")
        if t.get("name") == "CyclistEnterIntersectionTrajectory"
    )
    last = list(trajectory.iter("WorldPosition"))[-1]
    return float(last.get("x")), float(last.get("y"))


def test_go_straight_cyclist_keeps_a_near_constant_heading(tmp_path):
    output_path = tmp_path / "go_straight.xosc"
    _generate_straight_crossing_openscenario(
        _crossing_data("go_straight"), output_path, _JUNCTION_XODR_NAME
    )
    headings = _cyclist_headings(output_path)
    spread = max(headings) - min(headings)
    check("go_straight cyclist heading barely changes across the junction",
          spread < 0.2, f"heading spread={spread}, headings={headings}")


def test_turn_left_cyclist_actually_turns():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "turn_left.xosc"
        _generate_straight_crossing_openscenario(
            _crossing_data("turn_left"), output_path, _JUNCTION_XODR_NAME
        )
        headings = _cyclist_headings(output_path)
        spread = max(headings) - min(headings)
        # A real turn must show up as a clear heading change -- before the
        # fix this was always ~0 (go_straight cyclist's spread is < 0.2,
        # asserted above) because "go_straight" was hardcoded regardless of
        # the maneuver field.
        check("turn_left cyclist heading changes noticeably more than the go_straight case",
              spread > 0.3, f"heading spread={spread}, headings={headings}")


def test_turn_left_and_go_straight_cyclist_exit_on_different_roads():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        straight_path = Path(tmp) / "go_straight.xosc"
        left_path = Path(tmp) / "turn_left.xosc"
        _generate_straight_crossing_openscenario(
            _crossing_data("go_straight"), straight_path, _JUNCTION_XODR_NAME
        )
        _generate_straight_crossing_openscenario(
            _crossing_data("turn_left"), left_path, _JUNCTION_XODR_NAME
        )
        straight_xy = _cyclist_final_xy(straight_path)
        left_xy = _cyclist_final_xy(left_path)
        dist = ((straight_xy[0] - left_xy[0]) ** 2 + (straight_xy[1] - left_xy[1]) ** 2) ** 0.5
        check("turn_left cyclist ends up on a visibly different road than go_straight",
              dist > 2.0, f"go_straight final={straight_xy}, turn_left final={left_xy}, dist={dist}")


def main() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        test_go_straight_cyclist_keeps_a_near_constant_heading(Path(tmp))
    test_turn_left_cyclist_actually_turns()
    test_turn_left_and_go_straight_cyclist_exit_on_different_roads()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all cyclist junction-maneuver checks (offline)")


if __name__ == "__main__":
    main()
