"""
test_junction_trajectory_teleport_alignment.py — offline regression gate for
_generate_straight_crossing_openscenario's junction-template trajectory
start point (generate_scenario.py), fully offline, no LLM/network call.

Live-verified bug this guards against (2026-08-28 visual review, crossing_05,
note: "links abbiegen is correct but the collision time needs to be fixed"):
the FollowTrajectoryAction's t=0 waypoint for both the cyclist and the car
was computed from a synthetic "distance before impact" formula
(road_length_m / 2 - initial_s_m) left over from the old straight-road/
line-intersection abstraction. Once the junction path started using real
intersection_4way.xodr connector-road geometry, that synthetic value no
longer matched the actor's real distance along the real path -- verified
directly for crossing_05: synthetic cyclist d0 was 17.0 m vs a real distance
of 37.4 m (20+ m off), and the car's was off by 34+ m. That mismatch put the
FollowTrajectoryAction's first waypoint tens of meters away from where the
TeleportAction (LanePosition, using the actor's real road/s) actually placed
the entity -- an initial jump, and a trajectory that then has to cover the
wrong distance in the fixed impact_time_s window, which is exactly what
"collision time needs to be fixed" describes.

Usage:
    python3 test_junction_trajectory_teleport_alignment.py
"""
from __future__ import annotations

import math
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_scenario import (
    _generate_straight_crossing_openscenario,
    _JUNCTION_XODR_NAME,
    _JUNCTION_CONNECTORS,
    _junction_template_path,
    _parse_xodr_road_geometry,
    _parse_xodr_lane_offset,
    _road_world_point,
    _cyclist_lateral_offset,
    _osc_params,
    _actor_params,
)

_ODR_PARAMS = {
    "road_length_m": 100,
    "primary_heading_rad": -1.5707963267948966,
    "secondary_heading_rad": 3.141592653589793,
    "motor_lane_width_m": 3.5,
    "bike_lane_width_m": 1.25,
    "primary_has_bike_facility": True,
}


def _lateral_offsets(data: dict) -> tuple[float, float]:
    """Reproduces _generate_straight_crossing_openscenario's own
    cyclist_offset/car_offset computation exactly, from the same data dict
    passed to the generator -- so this test can never silently drift from
    what the real code actually does."""
    odr_params = data["generated_simulation_parameters"]["opendrive"]
    osc_params = _osc_params(data)
    car_actor = _actor_params(data, "car_1")
    cyclist_offset = _cyclist_lateral_offset(odr_params, osc_params)
    car_offset = -float(odr_params.get("motor_lane_width_m", 3.5)) * (
        abs(int(car_actor["initial_lane_id"])) - 0.5
    )
    return cyclist_offset, car_offset

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  FAIL  {label}: {detail}")


def _crossing_data(cyclist_s: float, car_s: float, cyclist_maneuver: str = "go_straight") -> dict:
    return {
        "source": {"source_id": "test_junction_trajectory_teleport_alignment"},
        "classification": {"scenario_type": "crossing"},
        "conflict": {"collision_description": "test fixture"},
        "participants": [
            {"id": "cyclist_1", "class": "cyclist", "type": "bicycle",
             "maneuver": cyclist_maneuver},
            {"id": "car_1", "class": "motor_vehicle", "type": "car",
             "maneuver": "go_straight"},
        ],
        "generated_simulation_parameters": {
            "opendrive": dict(_ODR_PARAMS),
            "openscenario": {
                "simulation_duration_s": 12.0,
                "conflict": {"conflict_time_s": 6.0},
                "actors": {
                    "cyclist_1": {
                        "vehicle_category": "bicycle",
                        "initial_road_id": 0,
                        "initial_lane_id": -1,
                        "initial_s_m": cyclist_s,
                        "initial_speed_mps": 4.0,
                    },
                    "car_1": {
                        "vehicle_category": "car",
                        "initial_road_id": 1,
                        "initial_lane_id": -1,
                        "initial_s_m": car_s,
                        "initial_speed_mps": 8.0,
                    },
                },
            },
        },
    }


def _first_waypoint_xy(output_path: Path, trajectory_name: str) -> tuple[float, float]:
    tree = ET.parse(output_path)
    trajectory = next(
        t for t in tree.getroot().iter("Trajectory") if t.get("name") == trajectory_name
    )
    first = next(iter(trajectory.iter("WorldPosition")))
    return float(first.get("x")), float(first.get("y"))


def _expected_teleport_xy(
    entry_road_id: int, maneuver_kind: str, s_m: float, t_offset_m: float
) -> tuple[float, float]:
    """The real teleported world position, plus the same rigid
    entry-road-to-connector seam correction _junction_maneuver_samples
    applies (a deliberate, small, constant shift -- see that function's
    "genuine small modeling seam" comment -- not the bug this test guards
    against, so it must be replicated here rather than ignored)."""
    xodr_path = _junction_template_path()
    entry_segs = _parse_xodr_road_geometry(xodr_path, entry_road_id)
    entry_lo = _parse_xodr_lane_offset(xodr_path, entry_road_id)
    connector_id = _JUNCTION_CONNECTORS[entry_road_id][maneuver_kind]
    connector_segs = _parse_xodr_road_geometry(xodr_path, connector_id)
    connector_lo = _parse_xodr_lane_offset(xodr_path, connector_id)

    x, y, _heading = _road_world_point(entry_segs, s_m, t_offset_m, entry_lo)
    entry_end_x, entry_end_y, _ = _road_world_point(entry_segs, 0.0, t_offset_m, entry_lo)
    conn_start_x, conn_start_y, _ = _road_world_point(connector_segs, 0.0, t_offset_m, connector_lo)
    dx, dy = conn_start_x - entry_end_x, conn_start_y - entry_end_y
    return x + dx, y + dy


def test_cyclist_trajectory_starts_at_its_teleported_position():
    # cyclist_1 teleports via LanePosition(road_id=0, s=cyclist_s) -- the
    # FollowTrajectoryAction's t=0 point must land close to that same real
    # road position, not tens of meters off along a wrong synthetic offset.
    cyclist_s = 25.0
    data = _crossing_data(cyclist_s, car_s=10.0)
    cyclist_offset, _car_offset = _lateral_offsets(data)
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "scenario.xosc"
        _generate_straight_crossing_openscenario(data, output_path, _JUNCTION_XODR_NAME)
        actual_x, actual_y = _first_waypoint_xy(output_path, "CyclistEnterIntersectionTrajectory")
    expected_x, expected_y = _expected_teleport_xy(0, "go_straight", cyclist_s, t_offset_m=cyclist_offset)
    dist = math.hypot(actual_x - expected_x, actual_y - expected_y)
    check("cyclist trajectory t=0 point is close to its real teleported position",
          dist < 1.0,
          f"expected~=({expected_x:.2f},{expected_y:.2f}), actual=({actual_x:.2f},{actual_y:.2f}), dist={dist:.2f}m")


def test_car_trajectory_starts_at_its_teleported_position():
    car_s = 40.0
    data = _crossing_data(cyclist_s=10.0, car_s=car_s)
    _cyclist_offset, car_offset = _lateral_offsets(data)
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "scenario.xosc"
        _generate_straight_crossing_openscenario(data, output_path, _JUNCTION_XODR_NAME)
        actual_x, actual_y = _first_waypoint_xy(output_path, "CarStraightThroughIntersectionTrajectory")
    expected_x, expected_y = _expected_teleport_xy(1, "go_straight", car_s, t_offset_m=car_offset)
    dist = math.hypot(actual_x - expected_x, actual_y - expected_y)
    check("car trajectory t=0 point is close to its real teleported position",
          dist < 1.0,
          f"expected~=({expected_x:.2f},{expected_y:.2f}), actual=({actual_x:.2f},{actual_y:.2f}), dist={dist:.2f}m")


def test_cyclist_far_from_junction_still_aligns():
    # A larger s (farther back on the entry road) is the exact regime where
    # the old synthetic road_length_m/2-based formula diverged most from the
    # real distance -- guard the larger-offset case specifically.
    cyclist_s = 60.0
    data = _crossing_data(cyclist_s, car_s=10.0, cyclist_maneuver="turn_left")
    cyclist_offset, _car_offset = _lateral_offsets(data)
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "scenario.xosc"
        _generate_straight_crossing_openscenario(data, output_path, _JUNCTION_XODR_NAME)
        actual_x, actual_y = _first_waypoint_xy(output_path, "CyclistEnterIntersectionTrajectory")
    expected_x, expected_y = _expected_teleport_xy(0, "turn_left", cyclist_s, t_offset_m=cyclist_offset)
    dist = math.hypot(actual_x - expected_x, actual_y - expected_y)
    check("far-from-junction turning cyclist's trajectory still starts at its real position",
          dist < 1.0,
          f"expected~=({expected_x:.2f},{expected_y:.2f}), actual=({actual_x:.2f},{actual_y:.2f}), dist={dist:.2f}m")


def main() -> None:
    test_cyclist_trajectory_starts_at_its_teleported_position()
    test_car_trajectory_starts_at_its_teleported_position()
    test_cyclist_far_from_junction_still_aligns()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all junction trajectory/teleport alignment checks (offline)")


if __name__ == "__main__":
    main()
