"""
test_straightroad_crossing_teleport_match.py — offline regression gate for
_generate_straight_crossing_openscenario's non-junction (straight_road.xodr)
branch, fully offline, no LLM/network call.

Live-verified bug this guards against (crossing_04, "car coming from
nowhere... it's supposed to go very normal in the driveway"): straight_road
.xodr has exactly one real road, so both cyclist_1 and car_1 sit on it
(initial_road_id=1 for both) -- but the trajectory renders a synthetic
"two roads crossing at an angle" abstraction (primary_heading for the
cyclist, secondary_heading for the car) unrelated to that one real road's
actual heading. The car's TeleportAction used to place it via LanePosition
(the real road, real heading), then its FollowTrajectoryAction immediately
jumped it to a WorldPosition along the fictional secondary_heading
direction instead -- a visible teleport-to-trajectory mismatch at t=0.

Usage:
    python3 test_straightroad_crossing_teleport_match.py
"""
from __future__ import annotations

import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_scenario import _generate_straight_crossing_openscenario

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  FAIL  {label}: {detail}")


def _crossing_data() -> dict:
    return {
        "source": {"source_id": "test_straightroad_crossing_teleport_match"},
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
                        "initial_s_m": 233.0,
                        "initial_speed_mps": 4.25,
                    },
                    "car_1": {
                        "vehicle_category": "car",
                        "initial_road_id": 1,
                        "initial_lane_id": -1,
                        "initial_s_m": 161.12,
                        "initial_speed_mps": 22.22,
                    },
                },
            },
        },
    }


def test_car_teleport_matches_trajectory_start():
    data = _crossing_data()
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "scenario.xosc"
        _generate_straight_crossing_openscenario(data, output_path, "straight_road.xodr")
        tree = ET.parse(output_path)

        teleport_wp = None
        for private in tree.getroot().iter("Private"):
            if private.get("entityRef") != "car_1":
                continue
            tp = next(private.iter("TeleportAction"))
            teleport_wp = tp.find(".//WorldPosition")

        traj_wp = None
        for traj in tree.getroot().iter("Trajectory"):
            if "Car" in traj.get("name"):
                traj_wp = next(traj.iter("Vertex")).find("Position/WorldPosition")

    check("car_1 has a WorldPosition teleport (not a LanePosition disconnected from the trajectory)",
          teleport_wp is not None, "no WorldPosition TeleportAction found for car_1")
    if teleport_wp is not None and traj_wp is not None:
        check("car_1's teleport position exactly matches its trajectory's t=0 point",
              (float(teleport_wp.get("x")), float(teleport_wp.get("y"))) ==
              (float(traj_wp.get("x")), float(traj_wp.get("y"))),
              (teleport_wp.attrib, traj_wp.attrib))


def test_cyclist_teleport_matches_trajectory_start():
    data = _crossing_data()
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "scenario.xosc"
        _generate_straight_crossing_openscenario(data, output_path, "straight_road.xodr")
        tree = ET.parse(output_path)

        teleport_wp = None
        for private in tree.getroot().iter("Private"):
            if private.get("entityRef") != "cyclist_1":
                continue
            tp = next(private.iter("TeleportAction"))
            teleport_wp = tp.find(".//WorldPosition")

        traj_wp = None
        for traj in tree.getroot().iter("Trajectory"):
            if "Cyclist" in traj.get("name"):
                traj_wp = next(traj.iter("Vertex")).find("Position/WorldPosition")

    if teleport_wp is not None and traj_wp is not None:
        check("cyclist_1's teleport position exactly matches its trajectory's t=0 point",
              (float(teleport_wp.get("x")), float(teleport_wp.get("y"))) ==
              (float(traj_wp.get("x")), float(traj_wp.get("y"))),
              (teleport_wp.attrib, traj_wp.attrib))


def main() -> None:
    test_car_teleport_matches_trajectory_start()
    test_cyclist_teleport_matches_trajectory_start()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all straight-road crossing teleport/trajectory match checks (offline)")


if __name__ == "__main__":
    main()
