"""
test_turning_perpendicular_streets.py — offline regression gate for
generate_openscenario's "turning" junction branch, fully offline, no
LLM/network call.

Live-verified bug this guards against (direct user correction, 2026-08-30):
the motor vehicle and cyclist were both placed on entry road 0 (the SAME
real street, different lanes) -- but a real "turning" conflict (e.g.
turning_08: a car on Reinickendorfer Strasse turning left into
Pankstrasse, hit by a cyclist) has the two actors approaching from
PERPENDICULAR real streets meeting at the junction, not the same street's
two lanes side by side. Fixed by placing the motor on entry road 1 (the
same real "secondary approach" the crossing generator's car uses) while
the cyclist stays on entry road 0. This also incidentally fixed a tension
between "showing a real turn" and "having the paths actually meet" for
turn_left maneuvers (turning_08's own gap dropped from 2.37m to 0.03m).

Usage:
    python3 test_turning_perpendicular_streets.py
"""
from __future__ import annotations

import math
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from generate_scenario import generate_openscenario, _JUNCTION_XODR_NAME

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  FAIL  {label}: {detail}")


def _turning_data(motor_maneuver: str) -> dict:
    return {
        "source": {"source_id": "test_turning_perpendicular_streets"},
        "classification": {"scenario_type": "turning"},
        "conflict": {"collision_description": "test fixture", "conflict_s_m": 46.83},
        "participants": [
            {"id": "truck_1", "class": "motor_vehicle", "type": "car", "maneuver": motor_maneuver},
            {"id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "go_straight"},
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
                "conflict": {"conflict_time_s": 4.0, "conflict_s_m": 46.83},
                "actors": {
                    "truck_1": {
                        "vehicle_category": "car",
                        "initial_road_id": 0,
                        "initial_lane_id": -1,
                        "initial_s_m": 26.83,
                        "initial_speed_mps": 10.0,
                    },
                    "cyclist_1": {
                        "vehicle_category": "bicycle",
                        "initial_road_id": 0,
                        "initial_lane_id": -2,
                        "initial_s_m": 9.37,
                        "initial_speed_mps": 4.25,
                    },
                },
            },
        },
    }


def _gap_and_heading_spread(motor_maneuver: str) -> tuple[float, float]:
    data = _turning_data(motor_maneuver)
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "s.xosc"
        generate_openscenario(data, output_path, _JUNCTION_XODR_NAME)
        tree = ET.parse(output_path)
        pts = {}
        for traj in tree.getroot().iter("Trajectory"):
            verts = list(traj.iter("Vertex"))
            pts[traj.get("name")] = [
                (float(v.get("time")), float(v.find("Position/WorldPosition").get("x")),
                 float(v.find("Position/WorldPosition").get("y")),
                 float(v.find("Position/WorldPosition").get("h")))
                for v in verts
            ]
    names = list(pts.keys())
    cyc = pts[names[1]] if "Cyclist" in names[1] else pts[names[0]]
    mot = pts[names[0]] if "Cyclist" in names[1] else pts[names[1]]
    impact_t = cyc[-2][0]
    mot_at_impact = next(p for p in mot if abs(p[0] - impact_t) < 0.05)
    gap = math.hypot(mot_at_impact[1] - cyc[-2][1], mot_at_impact[2] - cyc[-2][2])
    headings = [p[3] for p in mot[:-1]]
    return gap, max(headings) - min(headings)


def test_turn_left_has_tight_gap_and_visible_turn():
    gap, spread = _gap_and_heading_spread("turn_left")
    check("turn_left motor's collision gap is tight (perpendicular streets actually meet)",
          gap < 1.0, f"gap={gap:.2f}m")
    check("turn_left motor shows real heading rotation before impact",
          spread > 0.1, f"heading spread={spread:.3f} rad")


def test_turn_right_has_tight_gap_and_visible_turn():
    gap, spread = _gap_and_heading_spread("turn_right")
    check("turn_right motor's collision gap is tight",
          gap < 1.0, f"gap={gap:.2f}m")
    check("turn_right motor shows real heading rotation before impact",
          spread > 0.1, f"heading spread={spread:.3f} rad")


def main() -> None:
    test_turn_left_has_tight_gap_and_visible_turn()
    test_turn_right_has_tight_gap_and_visible_turn()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all turning perpendicular-streets checks (offline)")


if __name__ == "__main__":
    main()
