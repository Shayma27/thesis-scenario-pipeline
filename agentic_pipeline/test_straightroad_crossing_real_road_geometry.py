"""
test_straightroad_crossing_real_road_geometry.py — offline regression gate
for _generate_straight_crossing_openscenario's non-junction
(straight_road.xodr) branch, fully offline, no LLM/network call.

Live-verified bug this guards against (crossing_01 "all wrong", crossing_04
"car doing that weird trajectory... coming from nowhere"): both actors used
to be placed via a fully synthetic s/t coordinate system (_world_from_
road_s_t assumes a road CENTERED at the origin) with no relationship to
straight_road.xodr's real, authored geometry (confirmed directly from the
.xodr file: the real road starts at (0,0), heading 0, extends to (500,0)).
Computed positions could land 100+ meters off the real modeled pavement.
Separately, the roles were backwards from both this pipeline's own
documented convention and the report's own extracted semantics (e.g.
crossing_04's conflict_mechanism is "cyclist_crosses_vehicle_path_from_
median") -- the car should drive on the real road, the cyclist crosses
into it from the side, not the other way around.

Usage:
    python3 test_straightroad_crossing_real_road_geometry.py
"""
from __future__ import annotations

import math
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
    # Mirrors crossing_04's real, live-verified-buggy configuration.
    return {
        "source": {"source_id": "test_straightroad_real_road"},
        "classification": {"scenario_type": "crossing"},
        "conflict": {"collision_description": "test fixture", "conflict_s_m": 200.0},
        "participants": [
            {"id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "enter_roadway"},
            {"id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "go_straight"},
        ],
        "generated_simulation_parameters": {
            "opendrive": {
                "road_length_m": 500,
                "primary_heading_rad": 1.5707963267948966,
                "secondary_heading_rad": -2.697862,
                "motor_lane_width_m": 3.5,
                "bike_lane_width_m": 1.25,
                "primary_has_bike_facility": True,
            },
            "openscenario": {
                "simulation_duration_s": 10.0,
                "conflict": {"conflict_time_s": 4.0, "conflict_s_m": 200.0},
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


def test_car_teleport_is_within_the_real_road_bounds_and_heading():
    data = _crossing_data()
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "s.xosc"
        _generate_straight_crossing_openscenario(data, output_path, "straight_road.xodr")
        tree = ET.parse(output_path)
        car_wp = None
        for private in tree.getroot().iter("Private"):
            if private.get("entityRef") == "car_1":
                car_wp = next(private.iter("TeleportAction")).find(".//WorldPosition")
    check("car's teleport x is within the real road's real 0-500m span",
          car_wp is not None and 0.0 <= float(car_wp.get("x")) <= 500.0,
          car_wp.attrib if car_wp is not None else None)
    check("car's teleport heading matches the real road's own tangent (0 or pi), not a synthetic angle",
          car_wp is not None and min(abs(float(car_wp.get("h"))), abs(abs(float(car_wp.get("h"))) - math.pi)) < 1e-6,
          car_wp.attrib if car_wp is not None else None)


def test_cyclist_starts_a_plausible_short_distance_from_the_impact_point():
    # Distance from the IMPACT point, not from the car's teleport -- the
    # car can legitimately be extended far back along the real road to
    # match a shared real-speed approach time (same principle as the
    # junction branch's "extend if slack"), so its teleport position isn't
    # the right reference point for "is the cyclist's crossing distance
    # plausible".
    data = _crossing_data()
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "s.xosc"
        _generate_straight_crossing_openscenario(data, output_path, "straight_road.xodr")
        tree = ET.parse(output_path)
        cyclist_wp = None
        impact_xy = None
        for private in tree.getroot().iter("Private"):
            if private.get("entityRef") != "cyclist_1":
                continue
            cyclist_wp = next(private.iter("TeleportAction")).find(".//WorldPosition")
        for traj in tree.getroot().iter("Trajectory"):
            if "Cyclist" not in traj.get("name"):
                continue
            last_moving_vertex = list(traj.iter("Vertex"))[-2]
            wp = last_moving_vertex.find("Position/WorldPosition")
            impact_xy = (float(wp.get("x")), float(wp.get("y")))
    dist = math.hypot(float(cyclist_wp.get("x")) - impact_xy[0], float(cyclist_wp.get("y")) - impact_xy[1])
    check("cyclist starts a plausible 'crossing from the median' distance from the impact point (not 100+m)",
          dist < 40.0, f"distance={dist:.1f}m")


def test_car_and_cyclist_meet_at_the_same_point():
    data = _crossing_data()
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "s.xosc"
        _generate_straight_crossing_openscenario(data, output_path, "straight_road.xodr")
        tree = ET.parse(output_path)
        pts = {}
        for traj in tree.getroot().iter("Trajectory"):
            verts = list(traj.iter("Vertex"))
            pts[traj.get("name")] = [
                (float(v.get("time")), float(v.find("Position/WorldPosition").get("x")),
                 float(v.find("Position/WorldPosition").get("y")))
                for v in verts
            ]
    names = list(pts.keys())
    a, b = pts[names[0]][-2], pts[names[1]][-2]
    gap = math.hypot(a[1] - b[1], a[2] - b[2])
    check("car and cyclist impact points coincide", gap < 0.1, f"gap={gap:.2f}m")


def main() -> None:
    test_car_teleport_is_within_the_real_road_bounds_and_heading()
    test_cyclist_starts_a_plausible_short_distance_from_the_impact_point()
    test_car_and_cyclist_meet_at_the_same_point()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all straight-road crossing real-road-geometry checks (offline)")


if __name__ == "__main__":
    main()
