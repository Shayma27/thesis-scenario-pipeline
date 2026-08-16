"""
test_speed_estimation.py — regression gate for Agent 3's LLM-informed speed
estimation (speed_estimation.py), fully offline.

Checks the contract this module promises, not exact LLM output (which isn't
bit-identical run to run even at temperature 0): the grounded envelope fires
correctly with no LLM available, a valid qualitative classification is
concretized deterministically and used, a direction-inconsistent
classification is rejected rather than trusted, a malformed/failing
response falls back cleanly, a parked vehicle never even attempts an LLM
call, and — the two regression cases added after live-model verification
found a real defect — a "clearly faster" classification never produces a
value below the grounded baseline, and evidence about one actor never
leaks onto a different actor.

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

CONFLICT_WITH_SIGNAL = {
    "collision_description": "A cyclist crossing from a green median strip was struck by a speeding car.",
    "severity_text": "serious",
    "conflict_mechanism": "cyclist_crosses_vehicle_path_from_median",
}
CONFLICT_NO_SIGNAL = {
    "collision_description": None,
    "severity_text": None,
    "conflict_mechanism": "right_turn_across_separated_cycle_track",
}
# The exact real report text that found the live bug this test suite now
# guards against: crossing_04, "struck by a speeding car" (deutlich
# überhöhter Geschwindigkeit).
CONFLICT_CROSSING_04 = {
    "collision_description": "A cyclist crossing from a green median strip was struck by a speeding car.",
    "severity_text": None,
    "conflict_mechanism": "cyclist_crosses_vehicle_path_from_median",
}


def _unreachable(*_args, **_kwargs):
    raise ConnectionError("LLM server unreachable — test_speed_estimation.py runs offline")


def _fake_llm(status, relation="unknown", quote="test quote"):
    def _call(vehicle_label, envelope, conflict):
        if status == "not_reported":
            return {"knowledge_status": "not_reported", "qualitative_relation": "unknown", "evidence_quote": ""}
        return {"knowledge_status": status, "qualitative_relation": relation, "evidence_quote": quote}
    return _call


failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  FAIL  {label}: {detail}")


def test_parked_never_calls_llm():
    with patch.object(speed_estimation, "_llm_speed_estimate", side_effect=AssertionError("LLM called for a parked vehicle")):
        speed_mps, entry = estimate_actor_speed(
            {"conflict": CONFLICT_WITH_SIGNAL}, "car_1", "car", is_parked=True
        )
    check("parked vehicle: speed is exactly 0.0", speed_mps == 0.0, f"got {speed_mps}")
    check("parked vehicle: source is explicit_from_report", entry["source"] == "explicit_from_report", entry["source"])


def test_no_llm_reachable_falls_back_to_grounded_default():
    with patch.object(speed_estimation, "get_client", side_effect=_unreachable):
        speed_mps, entry = estimate_actor_speed(
            {"conflict": CONFLICT_WITH_SIGNAL}, "cyclist_1", "bicycle", is_parked=False
        )
    expected = round(CYCLIST_NOMINAL_KMH / 3.6, 2)
    check("LLM unreachable: falls back to grounded cyclist nominal", speed_mps == expected, f"got {speed_mps}, expected {expected}")
    check("LLM unreachable: source is the grounded default, not an LLM estimate", entry["source"] != "llm_qualitative_signal", entry["source"])


def test_no_report_signal_skips_llm_call():
    with patch.object(speed_estimation, "_llm_speed_estimate", side_effect=AssertionError("LLM called with no report signal")):
        speed_mps, entry = estimate_actor_speed(
            {"conflict": {}}, "car_1", "car", is_parked=False
        )
    expected = round(min(speed_estimation.MOTOR_RILSA_CLEARANCE_KMH, MOTOR_STVO_INNERORTS_KMH) / 3.6, 2)
    check("no report signal: grounded default used, no LLM call attempted", speed_mps == expected, f"got {speed_mps}")


def test_valid_faster_classification_is_concretized_and_used():
    fake = _fake_llm("report_qualitative_signal", "clearly_faster_than_context", "deutlich überhöhte Geschwindigkeit")
    with patch.object(speed_estimation, "_llm_speed_estimate", side_effect=fake):
        speed_mps, entry = estimate_actor_speed(
            {"conflict": CONFLICT_WITH_SIGNAL}, "car_1", "car", is_parked=False
        )
    check("valid classification: source is llm_qualitative_signal", entry["source"] == "llm_qualitative_signal", entry["source"])
    check("valid classification: evidence quote is carried through", "deutlich" in entry["evidence_quote"], entry["evidence_quote"])
    check("valid classification: relation recorded", entry.get("qualitative_relation") == "clearly_faster_than_context", entry)
    check("valid classification: speed exceeds the un-narrowed default (a real shift happened)",
          speed_mps > MOTOR_STVO_INNERORTS_KMH / 3.6, f"got {speed_mps}")


def test_crossing_04_regression_speeding_never_produces_a_slower_value():
    # The exact live-verified bug: crossing_04's car was reported as
    # "speeding," yet the old numeric-narrowing design produced 25 km/h —
    # slower than the 40 km/h grounded RiLSA baseline. This must never
    # happen again for any "faster"-classified relation.
    data = {"classification": {"scenario_type": "crossing"}, "conflict": CONFLICT_CROSSING_04}
    baseline_mps = round(MOTOR_RILSA_APPROACH_KMH / 3.6, 2)
    for relation in ("faster_than_context", "clearly_faster_than_context"):
        fake = _fake_llm("report_qualitative_signal", relation, "mit deutlich überhöhter Geschwindigkeit")
        with patch.object(speed_estimation, "_llm_speed_estimate", side_effect=fake):
            speed_mps, entry = estimate_actor_speed(data, "car_1", "car", is_parked=False)
        check(f"crossing_04 regression ({relation}): speed is above the grounded baseline, never below",
              speed_mps > baseline_mps, f"got {speed_mps} mps, baseline {baseline_mps} mps")
        check(f"crossing_04 regression ({relation}): source is llm_qualitative_signal, not silently falling back",
              entry["source"] == "llm_qualitative_signal", entry["source"])


def test_direction_inconsistent_classification_is_rejected():
    # A "faster" classification whose concretized value doesn't actually
    # exceed nominal must be rejected, not trusted — this is the safety
    # net the old design lacked entirely. Force an inconsistent envelope
    # by patching the concretizer to return a value below nominal despite
    # a "faster" relation.
    fake = _fake_llm("report_qualitative_signal", "faster_than_context", "quote")
    with patch.object(speed_estimation, "_llm_speed_estimate", side_effect=fake), \
         patch.object(speed_estimation, "_concretize_qualitative_relation", return_value=(1.0, "forced-inconsistent")):
        speed_mps, entry = estimate_actor_speed(
            {"conflict": CONFLICT_WITH_SIGNAL}, "car_1", "car", is_parked=False
        )
    check("inconsistent classification: rejected, falls back to grounded default",
          entry["source"] != "llm_qualitative_signal", entry)


def test_actor_attribution_not_reported_falls_back_cleanly():
    # The other live-verified bug: a cyclist getting flagged with
    # knowledge_status=report_qualitative_signal even though the evidence
    # was actually about the car. The prompt now instructs the LLM to
    # return "not_reported" in that case — this test exercises the
    # fallback path that must fire when it does.
    fake = _fake_llm("not_reported")
    with patch.object(speed_estimation, "_llm_speed_estimate", side_effect=fake):
        speed_mps, entry = estimate_actor_speed(
            {"conflict": CONFLICT_CROSSING_04}, "cyclist_1", "bicycle", is_parked=False
        )
    expected = round(CYCLIST_NOMINAL_KMH / 3.6, 2)
    check("actor attribution: not_reported falls back to the cyclist's own grounded default",
          speed_mps == expected, f"got {speed_mps}, expected {expected}")
    check("actor attribution: source is not an LLM estimate", entry["source"] != "llm_qualitative_signal", entry["source"])


def test_malformed_llm_output_falls_back_cleanly():
    def _malformed(vehicle_label, envelope, conflict):
        return {"knowledge_status": "report_qualitative_signal"}  # missing qualitative_relation/evidence_quote
    with patch.object(speed_estimation, "_llm_speed_estimate", side_effect=_malformed):
        speed_mps, entry = estimate_actor_speed(
            {"conflict": CONFLICT_WITH_SIGNAL}, "cyclist_1", "bicycle", is_parked=False
        )
    # _llm_speed_estimate itself is responsible for validating its own output
    # before returning — this test exercises estimate_actor_speed()'s side of
    # the contract by simulating what an already-invalid upstream result
    # would do if it ever slipped through; the real validation is exercised
    # by the raw-JSON tests below.
    check("malformed (missing relation): does not crash", isinstance(speed_mps, float))


def test_raw_llm_json_missing_fields_returns_none():
    class _FakeMessage:
        content = '{"knowledge_status": "report_qualitative_signal"}'  # no qualitative_relation/evidence_quote

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    class _FakeCompletions:
        def create(self, **kwargs):
            return _FakeResponse()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    with patch.object(speed_estimation, "get_client", return_value=_FakeClient()):
        result = speed_estimation._llm_speed_estimate("car (car_1)", {"min_kmh": 0, "max_kmh": 50, "nominal_kmh": 36, "safety_cap_kmh": 80}, CONFLICT_WITH_SIGNAL)
    check("raw LLM JSON missing qualitative_relation: returns None (triggers fallback)", result is None, result)


def test_raw_llm_json_empty_evidence_quote_returns_none():
    class _FakeMessage:
        content = '{"knowledge_status": "report_qualitative_signal", "qualitative_relation": "clearly_faster_than_context", "evidence_quote": ""}'

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    class _FakeCompletions:
        def create(self, **kwargs):
            return _FakeResponse()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    with patch.object(speed_estimation, "get_client", return_value=_FakeClient()):
        result = speed_estimation._llm_speed_estimate("car (car_1)", {"min_kmh": 0, "max_kmh": 50, "nominal_kmh": 36, "safety_cap_kmh": 80}, CONFLICT_WITH_SIGNAL)
    check("raw LLM JSON empty evidence_quote: returns None (triggers fallback)", result is None, result)


def test_raw_llm_json_unrecognized_relation_returns_none():
    class _FakeMessage:
        content = '{"knowledge_status": "report_qualitative_signal", "qualitative_relation": "extremely_fast", "evidence_quote": "raste"}'

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    class _FakeCompletions:
        def create(self, **kwargs):
            return _FakeResponse()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    with patch.object(speed_estimation, "get_client", return_value=_FakeClient()):
        result = speed_estimation._llm_speed_estimate("car (car_1)", {"min_kmh": 0, "max_kmh": 50, "nominal_kmh": 36, "safety_cap_kmh": 80}, CONFLICT_WITH_SIGNAL)
    check("raw LLM JSON unrecognized relation: returns None (triggers fallback)", result is None, result)


def test_raw_llm_json_fabricated_quote_not_in_source_text_returns_none():
    # A well-formed relation and a non-empty quote aren't enough — the quote
    # must actually appear in the text the model was given. "raste" (raced)
    # never appears anywhere in CONFLICT_WITH_SIGNAL's text.
    class _FakeMessage:
        content = '{"knowledge_status": "report_qualitative_signal", "qualitative_relation": "clearly_faster_than_context", "evidence_quote": "raste"}'

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    class _FakeCompletions:
        def create(self, **kwargs):
            return _FakeResponse()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    with patch.object(speed_estimation, "get_client", return_value=_FakeClient()):
        result = speed_estimation._llm_speed_estimate("car (car_1)", {"min_kmh": 0, "max_kmh": 50, "nominal_kmh": 36, "safety_cap_kmh": 80}, CONFLICT_WITH_SIGNAL)
    check("raw LLM JSON fabricated quote (not in source text): returns None (triggers fallback)", result is None, result)


def test_turning_03_regression_fabricated_stopped_quote_rejected():
    # The exact live-verified bug: turning_03's report says the e-bike was
    # "going straight" (moving) into the intersection, but the LLM
    # classified it as "stopped" with evidence_quote "stopped" — a word
    # that appears nowhere in collision_description. Must fall back to the
    # grounded default, never claim a fabricated "stopped" as real evidence.
    conflict = {
        "collision_description": "A truck turning right struck an e-bike going straight at a cycle crossing.",
        "severity_text": None,
        "conflict_mechanism": "right_turn_across_cycle_crossing",
    }

    class _FakeMessage:
        content = '{"knowledge_status": "report_qualitative_signal", "qualitative_relation": "stopped", "evidence_quote": "stopped"}'

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    class _FakeCompletions:
        def create(self, **kwargs):
            return _FakeResponse()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    with patch.object(speed_estimation, "get_client", return_value=_FakeClient()):
        speed_mps, entry = estimate_actor_speed(
            {"conflict": conflict}, "cyclist_1", "e_bike", is_parked=False
        )
    check("turning_03 regression: fabricated 'stopped' quote rejected, falls back to grounded default",
          entry["source"] != "llm_qualitative_signal", entry)
    check("turning_03 regression: e-bike speed is not zeroed out by the fabricated evidence",
          speed_mps > 0, f"got {speed_mps}")


def test_ebike_envelope_uses_legal_ceiling():
    with patch.object(speed_estimation, "get_client", side_effect=_unreachable):
        speed_mps, entry = estimate_actor_speed(
            {"conflict": {}}, "cyclist_1", "e_bike", is_parked=False
        )
    check("e-bike: nominal derived from empirical mean, within legal ceiling",
          speed_mps < EBIKE_LEGAL_CEILING_KMH / 3.6, f"got {speed_mps}")


def test_crossing_motor_vehicle_uses_rilsa_approach_speed():
    # No report signal, no LLM reachable -> grounded default. For a
    # "crossing" scenario this must be RiLSA's 40 km/h approach-speed
    # figure, NOT the general min(36, ceiling) formula used elsewhere —
    # and NOT the old, unverified "65% of the posted limit" rule.
    data = {"classification": {"scenario_type": "crossing"}, "conflict": {}}
    with patch.object(speed_estimation, "get_client", side_effect=_unreachable):
        speed_mps, entry = estimate_actor_speed(data, "car_1", "car", is_parked=False)
    expected = round(MOTOR_RILSA_APPROACH_KMH / 3.6, 2)
    check("crossing motor vehicle: uses RiLSA's 40 km/h approach speed", speed_mps == expected, f"got {speed_mps}, expected {expected}")
    check("crossing motor vehicle: source is engineering_assumption", entry["source"] == "engineering_assumption", entry["source"])
    check("crossing motor vehicle: reason cites RiLSA", "RiLSA" in entry["reason"], entry["reason"])


def test_crossing_report_signal_now_reaches_llm():
    # Regression check for the masking bug found while building this: a
    # crossing report's own "speeding car"-type text used to never reach
    # the LLM step at all, because a separate function
    # (_apply_osm_derived_crossing_speed, since removed) claimed
    # initial_speed_mps first, unconditionally, whenever OSM had a speed
    # limit. Confirms the LLM path is actually reachable now for exactly
    # that case.
    data = {"classification": {"scenario_type": "crossing"}, "conflict": CONFLICT_WITH_SIGNAL}
    called = {"hit": False}

    def _track(vehicle_label, envelope, conflict):
        called["hit"] = True
        return {"knowledge_status": "not_reported", "qualitative_relation": "unknown", "evidence_quote": ""}

    with patch.object(speed_estimation, "_llm_speed_estimate", side_effect=_track):
        estimate_actor_speed(data, "car_1", "car", is_parked=False)
    check("crossing report with speed language: LLM check is actually reached", called["hit"], "LLM was never called")


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
    test_parked_never_calls_llm()
    test_no_llm_reachable_falls_back_to_grounded_default()
    test_no_report_signal_skips_llm_call()
    test_valid_faster_classification_is_concretized_and_used()
    test_crossing_04_regression_speeding_never_produces_a_slower_value()
    test_direction_inconsistent_classification_is_rejected()
    test_actor_attribution_not_reported_falls_back_cleanly()
    test_malformed_llm_output_falls_back_cleanly()
    test_raw_llm_json_missing_fields_returns_none()
    test_raw_llm_json_empty_evidence_quote_returns_none()
    test_raw_llm_json_unrecognized_relation_returns_none()
    test_raw_llm_json_fabricated_quote_not_in_source_text_returns_none()
    test_turning_03_regression_fabricated_stopped_quote_rejected()
    test_ebike_envelope_uses_legal_ceiling()
    test_crossing_motor_vehicle_uses_rilsa_approach_speed()
    test_crossing_report_signal_now_reaches_llm()
    test_concretize_slower_relations_stay_below_nominal()
    test_concretize_faster_relations_stay_above_nominal_even_when_ceiling_below_nominal()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all speed_estimation checks (offline, no LLM server reachable)")


if __name__ == "__main__":
    main()
