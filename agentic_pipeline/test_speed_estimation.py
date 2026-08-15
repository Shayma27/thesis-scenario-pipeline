"""
test_speed_estimation.py — regression gate for Agent 3's LLM-informed speed
estimation (speed_estimation.py), fully offline.

Checks the contract this module promises, not exact LLM output (which isn't
bit-identical run to run even at temperature 0): the grounded envelope fires
correctly with no LLM available, a valid narrowing response is clamped and
used, an out-of-range response gets clamped rather than trusted verbatim, a
malformed/failing response falls back cleanly, and a parked vehicle never
even attempts an LLM call.

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


def _unreachable(*_args, **_kwargs):
    raise ConnectionError("LLM server unreachable — test_speed_estimation.py runs offline")


def _fake_llm(status, rng=None, rationale="test rationale"):
    def _call(vehicle_label, envelope, conflict):
        result = {"knowledge_status": status, "rationale": rationale}
        if rng is not None:
            result["speed_range_kmh"] = rng
        return result
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
    check("LLM unreachable: source is the grounded default, not an LLM estimate", entry["source"] != "llm_speed_estimate", entry["source"])


def test_no_report_signal_skips_llm_call():
    with patch.object(speed_estimation, "_llm_speed_estimate", side_effect=AssertionError("LLM called with no report signal")):
        speed_mps, entry = estimate_actor_speed(
            {"conflict": {}}, "car_1", "car", is_parked=False
        )
    expected = round(min(speed_estimation.MOTOR_RILSA_CLEARANCE_KMH, MOTOR_STVO_INNERORTS_KMH) / 3.6, 2)
    check("no report signal: grounded default used, no LLM call attempted", speed_mps == expected, f"got {speed_mps}")


def test_valid_narrowing_is_used_and_clamped_into_envelope():
    fake = _fake_llm("report_qualitative_signal", {"min": 60.0, "max": 65.0}, "report says 'deutlich überhöhte Geschwindigkeit'")
    with patch.object(speed_estimation, "_llm_speed_estimate", side_effect=fake):
        speed_mps, entry = estimate_actor_speed(
            {"conflict": CONFLICT_WITH_SIGNAL}, "car_1", "car", is_parked=False
        )
    check("valid narrowing: source is llm_speed_estimate", entry["source"] == "llm_speed_estimate", entry["source"])
    check("valid narrowing: rationale is carried through", "deutlich" in entry["reason"], entry["reason"])
    check("valid narrowing: logical_range_kmh recorded", "logical_range_kmh" in entry, entry)
    # 60-65 km/h is within the StVO-default (50 km/h) * 1.6 safety cap (80 km/h) — unclamped
    check("valid narrowing: not flagged as clamped when within the safety cap", "clamped_to_safety_cap" not in entry, entry)
    check("valid narrowing: speed exceeds the un-narrowed default (a real shift happened)",
          speed_mps > MOTOR_STVO_INNERORTS_KMH / 3.6, f"got {speed_mps}")


def test_absurd_llm_range_gets_clamped_not_trusted():
    fake = _fake_llm("report_qualitative_signal", {"min": 150.0, "max": 200.0}, "hallucinated")
    with patch.object(speed_estimation, "_llm_speed_estimate", side_effect=fake):
        speed_mps, entry = estimate_actor_speed(
            {"conflict": CONFLICT_WITH_SIGNAL}, "car_1", "car", is_parked=False
        )
    cap_kmh = MOTOR_STVO_INNERORTS_KMH * speed_estimation.MOTOR_SAFETY_CLAMP_FACTOR
    check("absurd range: clamped, flagged as such", entry.get("clamped_to_safety_cap") is True, entry)
    check("absurd range: final speed never exceeds the hard safety cap",
          speed_mps <= round(cap_kmh / 3.6, 2) + 0.01, f"got {speed_mps}, cap {cap_kmh / 3.6}")


def test_malformed_llm_output_falls_back_cleanly():
    def _malformed(vehicle_label, envelope, conflict):
        return {"knowledge_status": "report_qualitative_signal"}  # missing speed_range_kmh
    with patch.object(speed_estimation, "_llm_speed_estimate", side_effect=_malformed):
        speed_mps, entry = estimate_actor_speed(
            {"conflict": CONFLICT_WITH_SIGNAL}, "cyclist_1", "bicycle", is_parked=False
        )
    # _llm_speed_estimate itself is responsible for validating its own output
    # before returning — this test exercises estimate_actor_speed()'s side of
    # the contract by simulating what a already-invalid upstream result
    # would do if it ever slipped through; the real validation is exercised
    # by the raw-JSON tests below.
    check("malformed (missing range): does not crash", isinstance(speed_mps, float))


def test_raw_llm_json_missing_fields_returns_none():
    class _FakeMessage:
        content = '{"knowledge_status": "report_qualitative_signal"}'  # no speed_range_kmh

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
        result = speed_estimation._llm_speed_estimate("car (car_1)", {"min_kmh": 0, "max_kmh": 50, "nominal_kmh": 36}, CONFLICT_WITH_SIGNAL)
    check("raw LLM JSON missing speed_range_kmh: returns None (triggers fallback)", result is None, result)


def test_raw_llm_json_negative_range_returns_none():
    class _FakeMessage:
        content = '{"knowledge_status": "report_qualitative_signal", "speed_range_kmh": {"min": -5, "max": 10}, "rationale": "x"}'

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
        result = speed_estimation._llm_speed_estimate("car (car_1)", {"min_kmh": 0, "max_kmh": 50, "nominal_kmh": 36}, CONFLICT_WITH_SIGNAL)
    check("raw LLM JSON negative min: returns None (triggers fallback)", result is None, result)


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
        return {"knowledge_status": "insufficient_evidence"}

    with patch.object(speed_estimation, "_llm_speed_estimate", side_effect=_track):
        estimate_actor_speed(data, "car_1", "car", is_parked=False)
    check("crossing report with speed language: LLM check is actually reached", called["hit"], "LLM was never called")


def main() -> None:
    test_parked_never_calls_llm()
    test_no_llm_reachable_falls_back_to_grounded_default()
    test_no_report_signal_skips_llm_call()
    test_valid_narrowing_is_used_and_clamped_into_envelope()
    test_absurd_llm_range_gets_clamped_not_trusted()
    test_malformed_llm_output_falls_back_cleanly()
    test_raw_llm_json_missing_fields_returns_none()
    test_raw_llm_json_negative_range_returns_none()
    test_ebike_envelope_uses_legal_ceiling()
    test_crossing_motor_vehicle_uses_rilsa_approach_speed()
    test_crossing_report_signal_now_reaches_llm()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("PASSED: all speed_estimation checks (offline, no LLM server reachable)")


if __name__ == "__main__":
    main()
