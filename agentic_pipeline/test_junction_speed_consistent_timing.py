"""
test_junction_speed_consistent_timing.py — offline regression gate for
_generate_straight_crossing_openscenario's junction-template approach
timing (generate_scenario.py), fully offline, no LLM/network call.

Live-verified history of this bug (2026-08-28 visual review, several
passes): the trajectory's waypoint times used to be fixed offsets from a
flat conflict_time_s constant, ignoring each actor's own initial_speed_mps
and real distance entirely. Three attempted fixes were live/user-rejected
before landing on the current design:
  1. Original: crossing_03's car (real speed 22.2 m/s, short real distance)
     crawled at ~1.8 m/s for most of the approach then jumped to ~8.3 m/s
     in the last 0.3s -- reported verbatim as "waehrend collision, der pkw
     erhoehte geschwindigkeit, sollte aber normalerweise bremsen" (during
     the collision the car increased speed, but should normally brake).
  2. Drive-then-hold-near-the-junction: removed the jump but had the car
     visibly freeze in place close to the crash site for several seconds --
     rejected on sight as equally unrealistic.
  3. One constant speed for the whole approach: no jump, no freeze, but
     whenever the two actors' real distance/speed ratios differed sharply
     (a car spawned close & fast next to a cyclist spawned far & slow --
     the report data's own numbers), the close/fast actor had to crawl the
     ENTIRE approach ("very langsam", "car is waiting for the bike" --
     crossing_02/03/05/06/07/08 second review round).
  4. Current design: an actor with kinematic slack stays PARKED at its own
     real starting position until the exact moment it needs to start
     driving, then drives continuously at its own EXACT real
     initial_speed_mps the rest of the way -- never crawling, never
     freezing near the crash site, never exceeding its own real speed.

Usage:
    python3 test_junction_speed_consistent_timing.py
"""
from __future__ import annotations

import math
import sys
import tempfile
import xml.etree.ElementTree as ET
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


def _crossing_data(cyclist_s, cyclist_speed, car_s, car_speed) -> dict:
    return {
        "source": {"source_id": "test_junction_speed_consistent_timing"},
        "classification": {"scenario_type": "crossing"},
        "conflict": {"collision_description": "test fixture"},
        "participants": [
            {"id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "go_straight"},
            {"id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "go_straight"},
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


def _pre_impact_segment_speeds(output_path: Path, trajectory_name: str) -> list[float]:
    """Speed of every segment up to (not including) the post-impact hold --
    i.e. everything from t=0 to the vertex at distance 0 (the impact
    point itself, always the second-to-last vertex; the last vertex is the
    duration_s hold at the same position)."""
    tree = ET.parse(output_path)
    trajectory = next(
        t for t in tree.getroot().iter("Trajectory") if t.get("name") == trajectory_name
    )
    verts = list(trajectory.iter("Vertex"))[:-1]  # drop the post-impact hold vertex
    pts = []
    for v in verts:
        t = float(v.get("time"))
        wp = v.find("Position/WorldPosition")
        pts.append((t, float(wp.get("x")), float(wp.get("y"))))
    speeds = []
    for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:]):
        dist = math.hypot(x1 - x0, y1 - y0)
        speeds.append(dist / (t1 - t0) if (t1 - t0) > 1e-9 else 0.0)
    return speeds


def test_fast_close_car_parks_then_drives_at_exact_real_speed():
    # crossing_03's real configuration: a fast car (22.2 m/s) very close to
    # the junction -- exactly the regime that used to crawl-then-jump, then
    # crawl-the-whole-way.
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "scenario.xosc"
        _generate_straight_crossing_openscenario(
            _crossing_data(cyclist_s=20.0, cyclist_speed=4.0, car_s=2.0, car_speed=22.22),
            output_path, _JUNCTION_XODR_NAME,
        )
        speeds = _pre_impact_segment_speeds(output_path, "CarStraightThroughIntersectionTrajectory")
    moving_speeds = [s for s in speeds if s > 0.5]
    check("fast, close car has a parked (near-zero speed) segment before it starts driving",
          speeds[0] < 0.5, f"segment speeds={[round(s, 2) for s in speeds]}")
    check("once moving, the car's speed matches its own real configured speed exactly",
          moving_speeds and max(moving_speeds) - min(moving_speeds) < 0.05
          and abs(moving_speeds[0] - 22.22) < 0.05,
          f"segment speeds={[round(s, 2) for s in speeds]}, configured=22.22 m/s")


def test_slow_far_cyclist_drives_continuously_at_its_real_speed():
    # crossing_05's actual real report values: a slow cyclist (4.25 m/s,
    # initial_s_m=29.83, duration_s=10.0) -- the determining/slowest actor,
    # so it should have no parked segment at all (no slack to spend).
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "scenario.xosc"
        _generate_straight_crossing_openscenario(
            _crossing_data(cyclist_s=29.83, cyclist_speed=4.25, car_s=10.0, car_speed=11.11),
            output_path, _JUNCTION_XODR_NAME,
        )
        speeds = _pre_impact_segment_speeds(output_path, "CyclistEnterIntersectionTrajectory")
    check("determining/slowest actor has no parked segment (moves from t=0)",
          speeds[0] > 0.5, f"segment speeds={[round(s, 2) for s in speeds]}")
    check("slow, far cyclist's speed stays close to its own real configured speed throughout",
          all(abs(s - 4.25) < 0.6 for s in speeds),
          f"segment speeds={[round(s, 2) for s in speeds]}, configured=4.25 m/s")


def test_no_actor_ever_exceeds_its_own_real_speed():
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "scenario.xosc"
        _generate_straight_crossing_openscenario(
            _crossing_data(cyclist_s=29.83, cyclist_speed=4.25, car_s=2.0, car_speed=22.22),
            output_path, _JUNCTION_XODR_NAME,
        )
        cyclist_speeds = _pre_impact_segment_speeds(output_path, "CyclistEnterIntersectionTrajectory")
        car_speeds = _pre_impact_segment_speeds(output_path, "CarStraightThroughIntersectionTrajectory")
    # A small (~5%) tolerance on the long entry-road segment specifically:
    # its two endpoints are connected by a straight chord for this
    # measurement, but the real entry road can have slight curvature
    # between them (see _curve_markers' docstring -- only the connector
    # portion is densely sampled, since the entry approach is "near-
    # straight" but not always perfectly so), so chord-length/time slightly
    # over-estimates true arc-length speed. This is a measurement artifact
    # of straight-chord sampling, not a real speed violation -- esmini's
    # own FollowTrajectoryAction interpolates the same way.
    check("cyclist never exceeds its own real configured speed (~5% chord/arc tolerance)",
          max(cyclist_speeds) <= 4.25 * 1.05, f"cyclist segment speeds={[round(s, 2) for s in cyclist_speeds]}")
    check("car never exceeds its own real configured speed (~5% chord/arc tolerance)",
          max(car_speeds) <= 22.22 * 1.05, f"car segment speeds={[round(s, 2) for s in car_speeds]}")


def main() -> None:
    test_fast_close_car_parks_then_drives_at_exact_real_speed()
    test_slow_far_cyclist_drives_continuously_at_its_real_speed()
    test_no_actor_ever_exceeds_its_own_real_speed()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all junction speed-consistent-timing checks (offline)")


if __name__ == "__main__":
    main()
