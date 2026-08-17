"""
test_speed_evidence_backfill.py — offline regression gate for
extract_scenario._backfill_speed_evidence(), fully offline (pure Python,
no LLM call).

Checks the exact live-verified gap this function closes: crossing_03's
"mit deutlich überhöhter Geschwindigkeit" was silently dropped by the LLM
(unlike the structurally identical phrase in crossing_04, which it did
capture), and that the deterministic backfill correctly attributes the
evidence to the motor vehicle in both cases, never fires on reports with
no such phrase, and never fires when attribution would be ambiguous.

Usage:
    python3 test_speed_evidence_backfill.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_scenario import _backfill_speed_evidence

INPUT_DIR = Path(__file__).resolve().parent / "input"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  FAIL  {label}: {detail}")


def _load_fixture(scenario_id: str) -> dict:
    return json.loads((INPUT_DIR / f"{scenario_id}.json").read_text(encoding="utf-8"))


def test_crossing_03_gap_is_backfilled():
    # The exact live-verified gap: raw_text says "mit deutlich überhöhter
    # Geschwindigkeit" for car_1, but the frozen fixture's own conflict
    # dict (as the LLM actually produced it) has no severity_text at all.
    data = _load_fixture("crossing_03")
    check("crossing_03 fixture: no severity_text before backfill (sanity check on the test setup)",
          "severity_text" not in data.get("conflict", {}), data.get("conflict"))
    _backfill_speed_evidence(data)
    severity = data.get("conflict", {}).get("severity_text")
    check("crossing_03: severity_text backfilled", severity is not None, data.get("conflict"))
    check("crossing_03: attributed to the car, not the cyclist", severity and "car" in severity.lower(), severity)


def test_crossing_04_still_attributes_to_car():
    # crossing_04 already works today (collision_description captures the
    # speeding car) -- confirms the backfill doesn't misattribute this case
    # either, even though the cyclist is mentioned earlier in the report.
    data = _load_fixture("crossing_04")
    _backfill_speed_evidence(data)
    severity = data.get("conflict", {}).get("severity_text")
    check("crossing_04: severity_text backfilled", severity is not None, data.get("conflict"))
    check("crossing_04: attributed to the car, not the cyclist", severity and "car" in severity.lower(), severity)


def test_no_speed_language_never_fires():
    # None of the other 17 real reports mention "überhöht" at all --
    # confirms the backfill never invents evidence where there is none.
    for scenario_id in ("turning_01", "crossing_01", "longitudinal_01"):
        data = _load_fixture(scenario_id)
        before = data.get("conflict", {}).get("severity_text")
        _backfill_speed_evidence(data)
        after = data.get("conflict", {}).get("severity_text")
        check(f"{scenario_id}: no 'überhöht' language -> severity_text untouched",
              after == before, f"before={before!r} after={after!r}")


def test_ambiguous_attribution_never_fires():
    # Synthetic case: both a cyclist marker and a motor marker appear
    # BEFORE the "überhöht" match in the same sentence -- attribution is
    # genuinely ambiguous, so no backfill should happen at all, matching
    # the conservative "never invent" contract used throughout this file.
    data = {
        "source": {"raw_text": "Der Radfahrer und der Pkw-Fahrer waren beide mit überhöhter Geschwindigkeit unterwegs."},
        "participants": [
            {"id": "car_1", "class": "motor_vehicle", "type": "car"},
            {"id": "cyclist_1", "class": "cyclist", "type": "bicycle"},
        ],
        "conflict": {},
    }
    _backfill_speed_evidence(data)
    check("ambiguous attribution: severity_text stays unset",
          "severity_text" not in data["conflict"], data["conflict"])


def main() -> None:
    test_crossing_03_gap_is_backfilled()
    test_crossing_04_still_attributes_to_car()
    test_no_speed_language_never_fires()
    test_ambiguous_attribution_never_fires()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all speed-evidence backfill checks (offline)")


if __name__ == "__main__":
    main()
