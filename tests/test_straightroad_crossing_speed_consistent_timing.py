"""
test_straightroad_crossing_speed_consistent_timing.py — offline regression
gate for _generate_straight_crossing_openscenario's non-junction
(straight_road.xodr) branch, fully offline, no LLM/network call.

Live-verified bug this guards against: found by an automated self-check
sweep, not a user report -- crossing_01's cyclist jumped 3.69->6.00 m/s and
crossing_04's car dropped 22.69->8.33 m/s right at impact, both real (if
moderate) speed discontinuities from a fixed-distance/fixed-time final
approach segment (1.8m/2.5m in the last 0.3s) layered on top of a flat
impact_time_s cruise -- the same class of bug already fixed for the
junction branch, just not yet applied here since these two reports'
numbers happened to be close enough to real speed not to be flagged in
visual review.

Usage:
    python3 test_straightroad_crossing_speed_consistent_timing.py
"""
from __future__ import annotations

import math
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from generate_scenario import _generate_straight_crossing_openscenario

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  FAIL  {label}: {detail}")


def _crossing_data(cyclist_s, cyclist_speed, car_s, car_speed) -> dict:
    return {
        "source": {"source_id": "test_straightroad_speed"},
        "classification": {"scenario_type": "crossing"},
        "conflict": {"collision_description": "test fixture"},
        "participants": [
            {"id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "go_straight"},
            {"id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "go_straight"},
        ],
        "generated_simulation_parameters": {
            "opendrive": {
                "road_length_m": 300,
                "primary_heading_rad": 0.0,
                "secondary_heading_rad": -1.5707963267948966,
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
                        "initial_road_id": 1,
                        "initial_lane_id": -2,
                        "initial_s_m": cyclist_s,
                        "initial_speed_mps": cyclist_speed,
                    },
                    "car_1": {
                        "vehicle_category": "car",
                        "initial_road_id": 1,
                        "initial_lane_id": -1,
                        "initial_s_m": car_s,
                        "initial_speed_mps": car_speed,
                    },
                },
            },
        },
    }


def _pre_impact_speeds(output_path, trajectory_name):
    tree = ET.parse(output_path)
    traj = next(t for t in tree.getroot().iter("Trajectory") if t.get("name") == trajectory_name)
    verts = list(traj.iter("Vertex"))[:-1]
    pts = [(float(v.get("time")), float(v.find("Position/WorldPosition").get("x")),
            float(v.find("Position/WorldPosition").get("y"))) for v in verts]
    return [
        math.hypot(x1 - x0, y1 - y0) / (t1 - t0)
        for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:])
        if (t1 - t0) > 1e-9
    ]


def test_car_speed_never_jumps_right_before_impact():
    # crossing_04's real configuration: fast car, cyclist far enough away
    # to dominate the shared timing -- used to produce a 22.69->8.33 m/s
    # drop in the final 0.3s.
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "s.xosc"
        _generate_straight_crossing_openscenario(
            _crossing_data(cyclist_s=233.0, cyclist_speed=4.25, car_s=161.12, car_speed=22.22),
            output_path, "straight_road.xodr",
        )
        speeds = [s for s in _pre_impact_speeds(output_path, "CarStraightThroughIntersectionTrajectory") if s > 0.3]
    check("car's non-parked speed is constant right up to impact (no late jump/drop)",
          len(speeds) >= 1 and max(speeds) - min(speeds) < 0.05,
          f"speeds={[round(s, 2) for s in speeds]}")


def test_cyclist_speed_never_jumps_right_before_impact():
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "s.xosc"
        _generate_straight_crossing_openscenario(
            _crossing_data(cyclist_s=233.0, cyclist_speed=4.25, car_s=205.56, car_speed=11.11),
            output_path, "straight_road.xodr",
        )
        speeds = [s for s in _pre_impact_speeds(output_path, "CyclistEnterIntersectionTrajectory") if s > 0.3]
    check("cyclist's non-parked speed is constant right up to impact (no late jump)",
          len(speeds) >= 1 and max(speeds) - min(speeds) < 0.05,
          f"speeds={[round(s, 2) for s in speeds]}")


def main() -> None:
    test_car_speed_never_jumps_right_before_impact()
    test_cyclist_speed_never_jumps_right_before_impact()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all straight-road crossing speed-consistent-timing checks (offline)")


if __name__ == "__main__":
    main()
