"""
test_constants_provenance.py — offline regression gate for
complete_parameters.py's provenance labeling of conflict_time_s and
initial_s_m, fully offline, no LLM/network call.

Covers two things:
1. trigger_time_s was dead code (set, never read anywhere in the
   codebase) -- confirmed removed, never appears in output.
2. conflict_time_s/initial_s_m now get a dedicated, specific
   missing_parameters entry instead of the one generic _note() reason
   shared across every actor field.

The initial_s_m tests specifically guard against a real bug found and
fixed while writing this: the dedicated provenance entry must be
recorded with the actor's FINAL post-clamp initial_s_m
(_clamp_initial_s_to_real_road runs after the value is first computed,
and for "crossing" reports at the junction template the motor vehicle
sits on the much shorter secondary approach while the pre-clamp formula
is based on the primary road's length -- clamping is a real, structural
correction there, not a rare edge case). Recording the pre-clamp value
would silently desync the provenance record from what's actually in the
generated file.

Usage:
    python3 test_constants_provenance.py
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from complete_parameters import complete_parameters

INPUT_DIR = Path(__file__).resolve().parent / "input_osm_enriched"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  FAIL  {label}: {detail}")


def _load(scenario_id: str) -> dict:
    return json.loads((INPUT_DIR / f"{scenario_id}.json").read_text(encoding="utf-8"))


def _find_missing(result: dict, parameter: str) -> dict | None:
    return next(
        (m for m in result.get("missing_parameters", []) if m["parameter"] == parameter),
        None,
    )


def test_trigger_time_s_is_gone():
    for scenario_id in ("crossing_03", "turning_01", "longitudinal_01"):
        data = _load(scenario_id)
        result = complete_parameters(data)
        check(f"{scenario_id}: trigger_time_s never appears in output",
              "trigger_time_s" not in json.dumps(result), scenario_id)


def test_conflict_time_s_gets_specific_provenance():
    data = _load("crossing_03")
    result = complete_parameters(data)
    entry = _find_missing(result, "conflict.conflict_time_s")
    check("conflict_time_s has a dedicated missing_parameters entry", entry is not None, result.get("missing_parameters"))
    if entry:
        check("conflict_time_s reason is specific, not the generic _note() text",
              "approach duration" in entry["reason"].lower(), entry)
        check("conflict_time_s source is engineering_assumption",
              entry["source"] == "engineering_assumption", entry)


def test_initial_s_m_provenance_matches_final_value_after_clamp():
    # The exact bug this guards against: force a slow car speed so the
    # pre-clamp kinematic formula (based on the primary road's length)
    # produces an offset that exceeds the real, much shorter secondary
    # road the car actually starts on -- clamping must fire, and the
    # provenance record must reflect the clamped value, not the
    # pre-clamp one.
    data = _load("crossing_03")
    data["template_used"] = "intersection_4way.xodr"
    data["topology"] = {"topology": "junction"}
    actors = data.setdefault("generated_simulation_parameters", {}).setdefault(
        "openscenario", {}
    ).setdefault("actors", {})
    actors["car_1"] = {"initial_speed_mps": 2.0}  # slow -> large pre-clamp offset

    result = complete_parameters(data)
    final_value = result["generated_simulation_parameters"]["openscenario"]["actors"]["car_1"]["initial_s_m"]
    entry = _find_missing(result, "car_1.initial_s_m")
    check("car_1.initial_s_m has a dedicated missing_parameters entry", entry is not None, result.get("missing_parameters"))
    if entry:
        check("clamping actually fired (final value is well below the naive pre-clamp offset)",
              final_value < 20.0, final_value)
        check("provenance value_used matches the actual final (post-clamp) value, not a stale pre-clamp one",
              entry["value_used"] == final_value, (entry["value_used"], final_value))


def test_initial_s_m_reason_is_branch_specific():
    # turning_01 uses the motor-vehicle "turning" branch (-20m offset).
    data = _load("turning_01")
    result = complete_parameters(data)
    motor_id = next(
        p["id"] for p in data["participants"] if p["class"] == "motor_vehicle"
    )
    entry = _find_missing(result, f"{motor_id}.initial_s_m")
    check(f"{motor_id}.initial_s_m has a dedicated entry", entry is not None, result.get("missing_parameters"))
    if entry:
        check("reason mentions the 20m turning offset specifically",
              "20m" in entry["reason"], entry)
        check("reason is not the old generic _note() text",
              "not specified in report or OSM" not in entry["reason"], entry)


def main() -> None:
    test_trigger_time_s_is_gone()
    test_conflict_time_s_gets_specific_provenance()
    test_initial_s_m_provenance_matches_final_value_after_clamp()
    test_initial_s_m_reason_is_branch_specific()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all constants-provenance checks (offline)")


if __name__ == "__main__":
    main()
