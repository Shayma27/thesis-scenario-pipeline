"""
test_speed_evidence_backfill.py — offline regression gate for Agent 1's
speed-evidence extraction safety net: _validate_speed_evidence_grounding()
and _backfill_speed_evidence() (extract_scenario.py), fully offline, no
LLM call.

Checks two distinct live-verified failure modes, both against
participant.speed_evidence/speed_evidence_quote (the per-participant
fields the LLM's own extraction now produces directly, as part of its one
raw_text-reading call):

1. _backfill_speed_evidence: crossing_03's "mit deutlich überhöhter
   Geschwindigkeit" was silently dropped by the LLM (unlike the
   structurally identical phrase in crossing_04, which it did capture) —
   the deterministic backfill closes exactly that gap, correctly
   attributes it to the motor vehicle in both cases, never fires on
   reports with no such phrase, and never fires when attribution would be
   ambiguous.

2. _validate_speed_evidence_grounding: an LLM classification with a
   fabricated quote (one that doesn't actually appear in raw_text — the
   same failure mode verified live in turning_03, where Agent 3's old
   classifier claimed "stopped" quoting the word "stopped," present
   nowhere in the source text) must be rejected, not trusted, regardless
   of which stage produces it.

Usage:
    python3 test_speed_evidence_backfill.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_scenario import _backfill_speed_evidence, _validate_speed_evidence_grounding

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


def _participant(data: dict, pid: str) -> dict:
    return next(p for p in data["participants"] if p["id"] == pid)


# ── _backfill_speed_evidence ─────────────────────────────────────────────

def test_crossing_03_gap_is_backfilled():
    # The exact live-verified gap: raw_text says "mit deutlich überhöhter
    # Geschwindigkeit" for car_1. speed_evidence is stripped from the
    # loaded fixture before backfill runs, rather than relying on the
    # fixture to naturally lack it: since input/crossing_03.json was
    # regenerated via a live extraction run, the LLM now correctly
    # classifies this field on its own (verified live, see
    # hpc_live_llm_verification.py) -- the fixture no longer naturally
    # lacks speed_evidence, which is the desired outcome, not something to
    # route around. Stripping it isolates _backfill_speed_evidence's own
    # behavior regardless of what the live LLM currently produces.
    data = _load_fixture("crossing_03")
    for participant in data["participants"]:
        participant.pop("speed_evidence", None)
        participant.pop("speed_evidence_quote", None)
    check("crossing_03 fixture (speed_evidence stripped): no speed_evidence before backfill (sanity check on the test setup)",
          "speed_evidence" not in _participant(data, "car_1"), _participant(data, "car_1"))
    _backfill_speed_evidence(data)
    car = _participant(data, "car_1")
    check("crossing_03: speed_evidence backfilled", car.get("speed_evidence") is not None, car)
    check("crossing_03: classified as clearly_faster_than_context", car.get("speed_evidence") == "clearly_faster_than_context", car)
    check("crossing_03: quote is grounded (present in raw_text)",
          car.get("speed_evidence_quote", "").lower() in data["source"]["raw_text"].lower(), car)
    cyclist = _participant(data, "cyclist_1")
    check("crossing_03: cyclist not misattributed", cyclist.get("speed_evidence") is None, cyclist)


def test_crossing_04_still_attributes_to_car():
    # crossing_04's cyclist is mentioned earlier in the report than the
    # car — confirms the backfill still attributes correctly despite that.
    # speed_evidence stripped for the same reason as crossing_03 above.
    data = _load_fixture("crossing_04")
    for participant in data["participants"]:
        participant.pop("speed_evidence", None)
        participant.pop("speed_evidence_quote", None)
    _backfill_speed_evidence(data)
    car = _participant(data, "car_1")
    check("crossing_04: speed_evidence backfilled", car.get("speed_evidence") is not None, car)
    check("crossing_04: classified as clearly_faster_than_context", car.get("speed_evidence") == "clearly_faster_than_context", car)
    cyclist = _participant(data, "cyclist_1")
    check("crossing_04: cyclist not misattributed", cyclist.get("speed_evidence") is None, cyclist)


def test_no_speed_language_never_fires():
    # None of the other 17 real reports mention "überhöht" at all --
    # confirms the backfill never invents evidence where there is none.
    for scenario_id in ("turning_01", "crossing_01", "longitudinal_01"):
        data = _load_fixture(scenario_id)
        before = {p["id"]: p.get("speed_evidence") for p in data["participants"]}
        _backfill_speed_evidence(data)
        after = {p["id"]: p.get("speed_evidence") for p in data["participants"]}
        check(f"{scenario_id}: no 'überhöht' language -> speed_evidence untouched",
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
    check("ambiguous attribution: car_1.speed_evidence stays unset",
          "speed_evidence" not in data["participants"][0], data["participants"][0])
    check("ambiguous attribution: cyclist_1.speed_evidence stays unset",
          "speed_evidence" not in data["participants"][1], data["participants"][1])


# ── _validate_speed_evidence_grounding ───────────────────────────────────

def test_fabricated_quote_is_rejected():
    # The exact live-verified bug at the Agent 3 layer (turning_03: the
    # model classified the e-bike as "stopped" quoting the word "stopped,"
    # which appears nowhere in the actual text — the report says it was
    # "going straight," moving). Same check, now guarding Agent 1's own
    # LLM-produced speed_evidence directly.
    data = {
        "source": {"raw_text": "A truck turning right struck an e-bike going straight at a cycle crossing."},
        "participants": [
            {"id": "cyclist_1", "class": "cyclist", "type": "e_bike",
             "speed_evidence": "stopped", "speed_evidence_quote": "stopped"},
        ],
    }
    _validate_speed_evidence_grounding(data)
    p = data["participants"][0]
    check("fabricated quote: speed_evidence nulled", p["speed_evidence"] is None, p)
    check("fabricated quote: speed_evidence_quote nulled too", p["speed_evidence_quote"] is None, p)


def test_genuinely_grounded_quote_survives():
    data = {
        "source": {"raw_text": "Eine Pkw fahrende Person befuhr den Müggelheimer Damm mit deutlich überhöhter Geschwindigkeit."},
        "participants": [
            {"id": "car_1", "class": "motor_vehicle", "type": "car",
             "speed_evidence": "clearly_faster_than_context", "speed_evidence_quote": "überhöhter Geschwindigkeit"},
        ],
    }
    _validate_speed_evidence_grounding(data)
    p = data["participants"][0]
    check("grounded quote: speed_evidence survives validation", p["speed_evidence"] == "clearly_faster_than_context", p)
    check("grounded quote: quote unchanged", p["speed_evidence_quote"] == "überhöhter Geschwindigkeit", p)


def test_empty_quote_with_relation_is_rejected():
    # A relation without any quote at all is exactly as ungrounded as a
    # fabricated one -- must be rejected the same way.
    data = {
        "source": {"raw_text": "Eine Pkw fahrende Person befuhr den Müggelheimer Damm."},
        "participants": [
            {"id": "car_1", "class": "motor_vehicle", "type": "car",
             "speed_evidence": "faster_than_context", "speed_evidence_quote": ""},
        ],
    }
    _validate_speed_evidence_grounding(data)
    p = data["participants"][0]
    check("empty quote: speed_evidence nulled", p["speed_evidence"] is None, p)


def main() -> None:
    test_crossing_03_gap_is_backfilled()
    test_crossing_04_still_attributes_to_car()
    test_no_speed_language_never_fires()
    test_ambiguous_attribution_never_fires()
    test_fabricated_quote_is_rejected()
    test_genuinely_grounded_quote_survives()
    test_empty_quote_with_relation_is_rejected()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all speed-evidence extraction checks (offline)")


if __name__ == "__main__":
    main()
