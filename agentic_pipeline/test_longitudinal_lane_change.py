"""
test_longitudinal_lane_change.py — offline regression gate for
_generate_longitudinal_openscenario (generate_scenario.py), fully offline,
no LLM/network call.

Live-verified bugs this guards against (longitudinal_01/02, "very weird
simulation and positions of the car and cyclist!! only the template was
right"):
1. This scenario type used to silently fall through to the "turning"
   conflict's trajectory model, making the motor vehicle execute an
   unwarranted ~90-degree turn instead of traveling straight (both actors
   are on the same road, same direction -- there is no turn).
2. The cyclist's default starting distance (unrelated to its own real
   speed) meant it needed to travel far more distance than its real speed
   could cover in the scenario's time budget -- and a bug in the first
   attempt at fixing this (a guard that skipped recomputing an actor's
   start position whenever its "natural" travel time looked large) left
   the cyclist 161m away from the car at the scripted impact moment.

Usage:
    python3 test_longitudinal_lane_change.py
"""
from __future__ import annotations

import math
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_scenario import _generate_longitudinal_openscenario

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  FAIL  {label}: {detail}")


def _longitudinal_data() -> dict:
    # Mirrors longitudinal_01's real, live-verified-buggy configuration:
    # cyclist far away (200m) and slow (4.25 m/s), car close-ish and fast
    # (10 m/s), a flat conflict_s_m target, and only a 10s time budget.
    return {
        "source": {"source_id": "test_longitudinal_lane_change"},
        "classification": {"scenario_type": "longitudinal"},
        "conflict": {"collision_description": "test fixture"},
        "participants": [
            {"id": "cyclist_1", "class": "cyclist", "type": "bicycle",
             "maneuver": "change_lane_left_to_right"},
            {"id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "go_straight"},
        ],
        "generated_simulation_parameters": {
            "opendrive": {
                "road_length_m": 500,
                "primary_heading_rad": 0.0,
                "motor_lane_width_m": 3.5,
                "bike_lane_width_m": 1.25,
            },
            "openscenario": {
                "simulation_duration_s": 10.0,
                "conflict": {"conflict_s_m": 250.0},
                "actors": {
                    "cyclist_1": {
                        "vehicle_category": "bicycle",
                        "initial_road_id": 1,
                        "initial_lane_id": 1,
                        "initial_s_m": 50.0,
                        "initial_speed_mps": 4.25,
                    },
                    "car_1": {
                        "vehicle_category": "car",
                        "initial_road_id": 1,
                        "initial_lane_id": -1,
                        "initial_s_m": 225.0,
                        "initial_speed_mps": 10.0,
                    },
                },
            },
        },
    }


def test_motor_vehicle_never_turns():
    data = _longitudinal_data()
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "scenario.xosc"
        _generate_longitudinal_openscenario(data, output_path, "straight_road.xodr")
        tree = ET.parse(output_path)
        headings = [
            float(v.find("Position/WorldPosition").get("h"))
            for traj in tree.getroot().iter("Trajectory") if "Motor" in traj.get("name")
            for v in traj.iter("Vertex")
        ]
    check("motor vehicle's heading never changes (straight line, no turn)",
          max(headings) - min(headings) < 1e-6, headings)


def test_cyclist_and_car_meet_at_the_same_point():
    data = _longitudinal_data()
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "scenario.xosc"
        _generate_longitudinal_openscenario(data, output_path, "straight_road.xodr")
        tree = ET.parse(output_path)
        pts = {}
        for traj in tree.getroot().iter("Trajectory"):
            verts = list(traj.iter("Vertex"))
            pts[traj.get("name")] = [
                (float(v.get("time")), float(v.find("Position/WorldPosition").get("x")),
                 float(v.find("Position/WorldPosition").get("y")))
                for v in verts
            ]
    motor_impact = next(p[-2] for name, p in pts.items() if "Motor" in name)
    cyclist_impact = next(p[-2] for name, p in pts.items() if "Cyclist" in name)
    gap = math.hypot(motor_impact[1] - cyclist_impact[1], motor_impact[2] - cyclist_impact[2])
    check("motor and cyclist impact points coincide (not 161m apart)",
          gap < 0.5, f"motor={motor_impact}, cyclist={cyclist_impact}, gap={gap:.2f}m")


def test_cyclist_never_needs_implausible_speed():
    data = _longitudinal_data()
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "scenario.xosc"
        _generate_longitudinal_openscenario(data, output_path, "straight_road.xodr")
        tree = ET.parse(output_path)
        for traj in tree.getroot().iter("Trajectory"):
            if "Cyclist" not in traj.get("name"):
                continue
            verts = list(traj.iter("Vertex"))[:-1]
            pts = [(float(v.get("time")), float(v.find("Position/WorldPosition").get("x")),
                    float(v.find("Position/WorldPosition").get("y"))) for v in verts]
    speeds = [
        math.hypot(x1 - x0, y1 - y0) / (t1 - t0)
        for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:])
    ]
    check("cyclist's speed stays close to its own real configured speed (not ~22 m/s)",
          max(speeds) <= 4.25 * 1.15, f"speeds={[round(s, 2) for s in speeds]}, configured=4.25 m/s")


def main() -> None:
    test_motor_vehicle_never_turns()
    test_cyclist_and_car_meet_at_the_same_point()
    test_cyclist_never_needs_implausible_speed()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all longitudinal lane-change checks (offline)")


if __name__ == "__main__":
    main()
