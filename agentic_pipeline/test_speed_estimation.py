"""
test_speed_estimation.py — regression gate for Agent 3's speed estimation
(speed_estimation.py), fully offline, no network of any kind.

As of the second architecture iteration (see speed_estimation.py's module
docstring), this module makes no LLM call and reads no conflict text at
all — it only reads participant.speed_evidence/speed_evidence_quote,
already classified and grounded by Agent 1 (extract_scenario.py). These
tests construct that already-extracted shape directly rather than mocking
an LLM response. Checks the contract this module promises: the grounded
envelope fires correctly with no evidence present, a valid classification
is concretized deterministically and used, a direction-inconsistent
classification is rejected rather than trusted, a malformed/unrecognized
relation falls back cleanly, a parked vehicle never even looks at
evidence, and — the regression case added after live-model verification
found a real defect — a "clearly faster" classification never produces a
value below the grounded baseline.

(Agent 1's own extraction-time grounding/backfill — rejecting a
fabricated quote, filling a verified-dropped pattern — is tested
separately in test_speed_evidence_backfill.py, since that's now where
that responsibility actually lives.)

Usage:
    python3 test_speed_estimation.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import speed_estimation
from speed_estimation import (
    CYCLIST_NOMINAL_KMH,
    EBIKE_LEGAL_CEILING_KMH,
    MOTOR_RILSA_APPROACH_KMH,
    MOTOR_STVO_INNERORTS_KMH,
    estimate_actor_speed,
)

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  FAIL  {label}: {detail}")


def _data_with_evidence(actor_id: str, relation: str | None, quote: str = "") -> dict:
    """Build the minimal data shape estimate_actor_speed reads — a
    participants list with one actor carrying already-grounded
    speed_evidence, exactly the shape Agent 1 produces."""
    participant = {"id": actor_id}
    if relation is not None:
        participant["speed_evidence"] = relation
        participant["speed_evidence_quote"] = quote
    return {"participants": [participant]}


def test_parked_ignores_speed_evidence_entirely():
    data = _data_with_evidence("car_1", "clearly_faster_than_context", "raste")
    speed_mps, entry = estimate_actor_speed(data, "car_1", "car", is_parked=True)
    check("parked vehicle: speed is exactly 0.0", speed_mps == 0.0, f"got {speed_mps}")
    check("parked vehicle: source is explicit_from_report", entry["source"] == "explicit_from_report", entry["source"])
    check("parked vehicle: ignores speed_evidence even if present", entry["source"] != "agent1_speed_evidence", entry)


def test_no_evidence_falls_back_to_grounded_default():
    data = _data_with_evidence("cyclist_1", None)
    speed_mps, entry = estimate_actor_speed(data, "cyclist_1", "bicycle", is_parked=False)
    expected = round(CYCLIST_NOMINAL_KMH / 3.6, 2)
    check("no evidence: falls back to grounded cyclist nominal", speed_mps == expected, f"got {speed_mps}, expected {expected}")
    check("no evidence: source is the grounded default, not agent1_speed_evidence", entry["source"] != "agent1_speed_evidence", entry["source"])


def test_actor_not_present_in_participants_falls_back_cleanly():
    # estimate_actor_speed is called with an actor_id that isn't in
    # data["participants"] at all (defensive: shouldn't happen in
    # practice, but must never crash).
    data = {"participants": []}
    speed_mps, entry = estimate_actor_speed(data, "car_1", "car", is_parked=False)
    check("actor missing from participants: does not crash, falls back", isinstance(speed_mps, float))
    check("actor missing from participants: source is grounded default", entry["source"] != "agent1_speed_evidence", entry["source"])


def test_valid_faster_classification_is_concretized_and_used():
    data = _data_with_evidence("car_1", "clearly_faster_than_context", "deutlich überhöhte Geschwindigkeit")
    speed_mps, entry = estimate_actor_speed(data, "car_1", "car", is_parked=False)
    check("valid classification: source is agent1_speed_evidence", entry["source"] == "agent1_speed_evidence", entry["source"])
    check("valid classification: evidence quote is carried through", "überhöhte" in entry["evidence_quote"], entry["evidence_quote"])
    check("valid classification: relation recorded", entry.get("qualitative_relation") == "clearly_faster_than_context", entry)
    check("valid classification: speed exceeds the un-narrowed default (a real shift happened)",
          speed_mps > MOTOR_STVO_INNERORTS_KMH / 3.6, f"got {speed_mps}")


def test_crossing_04_regression_speeding_never_produces_a_slower_value():
    # The exact live-verified bug: crossing_04's car was reported as
    # "speeding," yet the old numeric-narrowing design produced 25 km/h —
    # slower than the 40 km/h grounded RiLSA baseline. This must never
    # happen again for any "faster"-classified relation, regardless of
    # where the classification came from.
    baseline_mps = round(MOTOR_RILSA_APPROACH_KMH / 3.6, 2)
    for relation in ("faster_than_context", "clearly_faster_than_context"):
        data = _data_with_evidence("car_1", relation, "mit deutlich überhöhter Geschwindigkeit")
        data["classification"] = {"scenario_type": "crossing"}
        speed_mps, entry = estimate_actor_speed(data, "car_1", "car", is_parked=False)
        check(f"crossing_04 regression ({relation}): speed is above the grounded baseline, never below",
              speed_mps > baseline_mps, f"got {speed_mps} mps, baseline {baseline_mps} mps")
        check(f"crossing_04 regression ({relation}): source is agent1_speed_evidence, not silently falling back",
              entry["source"] == "agent1_speed_evidence", entry["source"])


def test_direction_inconsistent_classification_is_rejected():
    # A "faster" classification whose concretized value doesn't actually
    # exceed nominal must be rejected, not trusted — this is the safety
    # net the original design lacked entirely. Force an inconsistent
    # result by patching the concretizer directly.
    data = _data_with_evidence("car_1", "faster_than_context", "etwas schneller")
    with patch.object(speed_estimation, "_concretize_qualitative_relation", return_value=(1.0, "forced-inconsistent")):
        speed_mps, entry = estimate_actor_speed(data, "car_1", "car", is_parked=False)
    check("inconsistent classification: rejected, falls back to grounded default",
          entry["source"] != "agent1_speed_evidence", entry)


def test_unrecognized_relation_value_falls_back_defensively():
    # Defensive re-check: even though extract_scenario.py's own enum
    # sanitization should never let an invalid value through, this module
    # doesn't blindly trust it either — an unrecognized string must not
    # reach the concretizer.
    data = _data_with_evidence("car_1", "extremely_fast", "raste wie verrückt")
    speed_mps, entry = estimate_actor_speed(data, "car_1", "car", is_parked=False)
    check("unrecognized relation: falls back to grounded default, does not crash",
          entry["source"] != "agent1_speed_evidence", entry)


def test_ebike_envelope_uses_legal_ceiling():
    data = _data_with_evidence("cyclist_1", None)
    speed_mps, entry = estimate_actor_speed(data, "cyclist_1", "e_bike", is_parked=False)
    check("e-bike: nominal derived from empirical mean, within legal ceiling",
          speed_mps < EBIKE_LEGAL_CEILING_KMH / 3.6, f"got {speed_mps}")


def test_crossing_motor_vehicle_uses_rilsa_approach_speed():
    # No evidence -> grounded default. For a "crossing" scenario this must
    # be RiLSA's 40 km/h approach-speed figure, NOT the general
    # min(36, ceiling) formula used elsewhere — and NOT the old,
    # unverified "65% of the posted limit" rule.
    data = _data_with_evidence("car_1", None)
    data["classification"] = {"scenario_type": "crossing"}
    speed_mps, entry = estimate_actor_speed(data, "car_1", "car", is_parked=False)
    expected = round(MOTOR_RILSA_APPROACH_KMH / 3.6, 2)
    check("crossing motor vehicle: uses RiLSA's 40 km/h approach speed", speed_mps == expected, f"got {speed_mps}, expected {expected}")
    check("crossing motor vehicle: source is engineering_assumption", entry["source"] == "engineering_assumption", entry["source"])
    check("crossing motor vehicle: reason cites RiLSA", "RiLSA" in entry["reason"], entry["reason"])


def test_concretize_slower_relations_stay_below_nominal():
    envelope = speed_estimation._grounded_envelope("bicycle", None, is_crossing=False)
    for relation in ("slower_than_context", "clearly_slower_than_context", "stopped"):
        value, _note = speed_estimation._concretize_qualitative_relation(relation, envelope)
        check(f"concretize({relation}): stays below nominal", value < envelope["nominal_kmh"], f"got {value}")


def test_concretize_faster_relations_stay_above_nominal_even_when_ceiling_below_nominal():
    # The exact edge case documented in _concretize_qualitative_relation's
    # docstring: a "crossing" motor envelope where the OSM speed limit
    # (max_kmh) is BELOW the RiLSA-fixed nominal. "Faster" concretization
    # must still land above nominal by using safety_cap_kmh, not max_kmh.
    envelope = speed_estimation._grounded_envelope("car", 30.0, is_crossing=True)
    check("degenerate envelope: max_kmh really is below nominal_kmh (sanity check on the test setup)",
          envelope["max_kmh"] < envelope["nominal_kmh"], envelope)
    for relation in ("faster_than_context", "clearly_faster_than_context"):
        value, _note = speed_estimation._concretize_qualitative_relation(relation, envelope)
        check(f"concretize({relation}) with low OSM ceiling: still stays above nominal",
              value > envelope["nominal_kmh"], f"got {value}, nominal {envelope['nominal_kmh']}")


def main() -> None:
    test_parked_ignores_speed_evidence_entirely()
    test_no_evidence_falls_back_to_grounded_default()
    test_actor_not_present_in_participants_falls_back_cleanly()
    test_valid_faster_classification_is_concretized_and_used()
    test_crossing_04_regression_speeding_never_produces_a_slower_value()
    test_direction_inconsistent_classification_is_rejected()
    test_unrecognized_relation_value_falls_back_defensively()
    test_ebike_envelope_uses_legal_ceiling()
    test_crossing_motor_vehicle_uses_rilsa_approach_speed()
    test_concretize_slower_relations_stay_below_nominal()
    test_concretize_faster_relations_stay_above_nominal_even_when_ceiling_below_nominal()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all speed_estimation checks (offline, no network of any kind)")


if __name__ == "__main__":
    main()
