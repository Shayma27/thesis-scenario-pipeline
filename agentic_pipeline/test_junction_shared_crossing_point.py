"""
test_junction_shared_crossing_point.py — offline regression gate for
_generate_straight_crossing_openscenario's shared impact-point computation
(generate_scenario.py), fully offline, no LLM/network call.

Live-verified bug this guards against (2026-08-28 visual review, second
pass after the teleport-alignment fix): crossing_02/03/07/08 all reported
"no collision happened" even though topology/positions were otherwise
right. Root cause, confirmed by direct measurement: the cyclist's and
car's "impact" point were each independently computed as the midpoint of
their OWN connector road -- once each vehicle's real lane offset is
applied, those two midpoints are NOT the same physical location (measured
1-4.5m apart for real report data, even for a plain go_straight/
go_straight crossing), which is generally too far for the two vehicles'
bounding boxes to ever overlap. The two paths' true nearest-approach point
was under 0.3m almost everywhere in the same data. _find_junction_crossing_
point replaces the "each vehicle's own midpoint" formula with the real
nearest-approach point between the two (already laterally-offset) paths.

Usage:
    python3 test_junction_shared_crossing_point.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_scenario import _junction_maneuver_samples, _find_junction_crossing_point, _path_point_at_distance

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  FAIL  {label}: {detail}")


def _gap_for(cyclist_maneuver, cyclist_offset, car_maneuver, car_offset):
    cyc_samples, cyc_s, cyc_e = _junction_maneuver_samples(0, cyclist_maneuver, cyclist_offset, approach_margin_m=30.0)
    car_samples, car_s, car_e = _junction_maneuver_samples(1, car_maneuver, car_offset, approach_margin_m=30.0)

    # Old formula: each vehicle's own connector midpoint, independently.
    old_cyc_dist = cyc_s + 0.5 * (cyc_e - cyc_s)
    old_car_dist = car_s + 0.5 * (car_e - car_s)
    old_cyc_pt = _path_point_at_distance(cyc_samples, old_cyc_dist)
    old_car_pt = _path_point_at_distance(car_samples, old_car_dist)
    old_gap = math.hypot(old_cyc_pt[0] - old_car_pt[0], old_cyc_pt[1] - old_car_pt[1])

    # New formula: shared real nearest-approach point.
    new_cyc_dist, new_car_dist = _find_junction_crossing_point(
        cyc_samples, (cyc_s, cyc_e), car_samples, (car_s, car_e)
    )
    new_cyc_pt = _path_point_at_distance(cyc_samples, new_cyc_dist)
    new_car_pt = _path_point_at_distance(car_samples, new_car_dist)
    new_gap = math.hypot(new_cyc_pt[0] - new_car_pt[0], new_cyc_pt[1] - new_car_pt[1])
    return old_gap, new_gap


def test_go_straight_vs_go_straight_with_realistic_offsets_now_actually_meet():
    # cyclist on a bike-lane offset, car on a driving-lane offset -- the
    # exact real-world configuration that produced "no collision happened"
    # for crossing_02/03/07/08.
    old_gap, new_gap = _gap_for("go_straight", -4.125, "go_straight", -1.75)
    check("old own-midpoint formula leaves a real, non-trivial gap (sanity check on the test setup)",
          old_gap > 1.0, f"old_gap={old_gap}")
    check("new shared-crossing-point formula closes the gap to well under a vehicle width",
          new_gap < 0.5, f"old_gap={old_gap}, new_gap={new_gap}")


def test_turn_left_cyclist_vs_go_straight_car_also_meet():
    old_gap, new_gap = _gap_for("turn_left", -4.125, "go_straight", -1.75)
    check("new shared-crossing-point formula also closes the gap for a turning cyclist",
          new_gap < 0.5, f"old_gap={old_gap}, new_gap={new_gap}")


def test_both_turning_also_meet():
    old_gap, new_gap = _gap_for("turn_left", -0.625, "turn_left", -1.75)
    check("new shared-crossing-point formula also closes the gap when both actors turn",
          new_gap < 0.5, f"old_gap={old_gap}, new_gap={new_gap}")


def main() -> None:
    test_go_straight_vs_go_straight_with_realistic_offsets_now_actually_meet()
    test_turn_left_cyclist_vs_go_straight_car_also_meet()
    test_both_turning_also_meet()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all junction shared-crossing-point checks (offline)")


if __name__ == "__main__":
    main()
