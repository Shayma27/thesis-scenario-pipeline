"""
hpc_live_llm_verification.py — run this ON THE HPC GPU NODE (or frontend02
with the job's node reachable), with a live vLLM server up. Requires
LLM_BASE_URL pointed at the actual node running the job (see step 1's
output — if it prints a connection error, that's your answer already).

Usage:
    python3 scripts/hpc_live_llm_verification.py [scenario_id ... | all]

With no arguments, runs the three queued diagnostics plus a full
run_agent() pass on crossing_04 (explicit "speeding car" language),
turning_01 (no explicit speed language), and longitudinal_01. Pass
specific scenario_ids to run only those, or "all" to run the full
19-report corpus (report_loader.load_reports()).
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import llm_client
from pipeline import run_agent
from report_loader import load_reports

W = 78


def step1_connectivity() -> None:
    print(f"\n{'=' * W}\nSTEP 1 — connectivity\n{'=' * W}")
    print(f"LLM_BASE_URL = {llm_client.LLM_BASE_URL}")
    print(f"LLM_MODEL    = {llm_client.MODEL}")
    try:
        client = llm_client.get_client()
        models = client.models.list()
        print(f"models.list() OK: {[m.id for m in models.data]}")
    except Exception:
        print("models.list() FAILED:")
        traceback.print_exc()


def step2_raw_llm_call() -> None:
    print(f"\n{'=' * W}\nSTEP 2 — raw unguarded chat.completions.create()\n{'=' * W}")
    try:
        client = llm_client.get_client()
        response = client.chat.completions.create(
            model=llm_client.MODEL,
            messages=[
                {"role": "system", "content": "Reply with the single word: OK"},
                {"role": "user", "content": "ping"},
            ],
            temperature=0.0,
            max_tokens=10,
            timeout=30,
        )
        print(f"Response: {response.choices[0].message.content!r}")
    except Exception:
        print("Raw call FAILED — full traceback:")
        traceback.print_exc()


def step3_speed_estimate_raw() -> None:
    print(f"\n{'=' * W}\nSTEP 3 — raw _llm_speed_estimate() call (bypasses its own except)\n{'=' * W}")
    import speed_estimation

    envelope = speed_estimation._grounded_envelope("car", 50.0, is_crossing=True)
    conflict = {
        "collision_description": "A cyclist crossing from a green median strip was struck by a speeding car.",
        "severity_text": None,
        "conflict_mechanism": "cyclist_crosses_vehicle_path_from_median",
    }
    prompt_fn = speed_estimation._llm_speed_estimate
    try:
        client = llm_client.get_client()
        # Reimplement the body of _llm_speed_estimate without its try/except
        # to surface the real traceback instead of a bare None.
        import inspect
        print("Calling with real crossing_04 conflict text + car envelope...")
        result = None
        try:
            result = prompt_fn("car (car_1)", envelope, conflict)
        except Exception:
            pass
        print(f"_llm_speed_estimate() returned: {result}")
        if result is None:
            print("Returned None — re-running the same call WITHOUT the except to see why:")
            # Manually inline the guarded section (kept in sync with
            # speed_estimation._llm_speed_estimate's real prompt/schema —
            # the categorical classify-only design, not the old numeric
            # narrowing one it replaced after live testing found it
            # producing direction-inconsistent speeds).
            response = client.chat.completions.create(
                model=llm_client.MODEL,
                messages=[
                    {"role": "system", "content": (
                        "You classify qualitative speed evidence for accident "
                        "reconstruction from report text into a fixed set of "
                        "categories. You never output a number. You only "
                        "report evidence that is specifically about the named "
                        "vehicle — never about a different participant in the "
                        "same report."
                    )},
                    {"role": "user", "content": (
                        "A traffic-engineering pipeline is reconstructing a German police "
                        "accident report as a simulation. You are assessing ONLY the "
                        "car (car_1)'s speed — never any other participant's, even if "
                        "the report describes one.\n\n"
                        f"For reference only (do not return a number): this vehicle's "
                        f"grounded typical/legal speed context is centered around "
                        f"{envelope['nominal_kmh']} km/h.\n\n"
                        "Based ONLY on the report text below, classify what it says about "
                        "THIS vehicle's own speed relative to that context:\n"
                        '- "stopped": the report says this vehicle was stationary/had '
                        "stopped (distinct from being parked throughout).\n"
                        '- "clearly_slower_than_context": clearly, markedly slower than '
                        "typical.\n"
                        '- "slower_than_context": somewhat slower than typical.\n'
                        '- "approximately_contextual": the report addresses this vehicle\'s '
                        "speed and indicates it was roughly typical.\n"
                        '- "faster_than_context": somewhat faster than typical.\n'
                        '- "clearly_faster_than_context": clearly, markedly faster / '
                        'exceeded the appropriate speed (e.g. "überhöhte Geschwindigkeit", '
                        '"raste", "deutlich zu schnell").\n'
                        '- "unknown": the report says nothing about THIS vehicle\'s own '
                        "speed.\n\n"
                        "If your evidence quote is actually about a different participant, "
                        "or isn't specific to this vehicle's own speed, you MUST classify "
                        'as "unknown" — never let another participant\'s speed evidence '
                        "apply to this one.\n\n"
                        f"collision_description: {conflict.get('collision_description')}\n"
                        f"severity_text: {conflict.get('severity_text')}\n"
                        f"conflict_mechanism: {conflict.get('conflict_mechanism')}\n\n"
                        "Respond as JSON only: "
                        '{"knowledge_status": "report_qualitative_signal" or '
                        '"not_reported", '
                        '"qualitative_relation": "<one of the categories above>", '
                        '"evidence_quote": "<verbatim phrase from the report text, or '
                        'empty string if not_reported>"}'
                    )},
                ],
                temperature=0.0,
                max_tokens=200,
                response_format={"type": "json_object"},
                timeout=30,
            )
            print(f"Raw response content: {response.choices[0].message.content!r}")
    except Exception:
        print("Raw diagnostic call FAILED — full traceback:")
        traceback.print_exc()


def step4_full_pipeline(scenario_ids: list[str]) -> None:
    print(f"\n{'=' * W}\nSTEP 4 — full pipeline run_agent() on real reports\n{'=' * W}")
    reports = {sid: text for sid, text, _ in load_reports()}
    out_dir = PROJECT_DIR / "output" / "hpc_live_llm_verification"
    out_dir.mkdir(parents=True, exist_ok=True)

    for sid in scenario_ids:
        if sid not in reports:
            print(f"\n  !! unknown scenario_id: {sid} (skipping)")
            continue
        print(f"\n{'-' * W}\n  {sid}\n{'-' * W}")
        try:
            result = run_agent(reports[sid], sid)
        except Exception:
            print(f"  run_agent() raised for {sid}:")
            traceback.print_exc()
            continue

        state = result.get("state")
        data = state.data if state else None
        if not data:
            print(f"  No data produced for {sid}. valid={result.get('valid')}")
            continue

        missing = data.get("missing_parameters", [])
        speed_entries = [e for e in missing if e.get("parameter", "").endswith(".initial_speed_mps")]
        print(f"\n  Speed provenance for {sid}:")
        for e in speed_entries:
            print(f"    {e.get('parameter')}: {e.get('value_used')} m/s  source={e.get('source')}")
            if e.get("reason"):
                print(f"      reason: {e['reason']}")
            if e.get("qualitative_relation"):
                print(f"      relation: {e['qualitative_relation']}  quote: {e.get('evidence_quote', '')!r}")

        print(f"\n  xosc_path: {result.get('xosc_path')}")
        print(f"  valid: {result.get('valid')}")

        dump_path = out_dir / f"{sid}.full_state.json"
        dump_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Full state dumped to {dump_path}")


def main() -> None:
    args = sys.argv[1:]
    step1_connectivity()
    step2_raw_llm_call()
    step3_speed_estimate_raw()
    if args == ["all"]:
        scenario_ids = [sid for sid, _text, _stype in load_reports()]
    elif args:
        scenario_ids = args
    else:
        scenario_ids = ["crossing_04", "turning_01", "longitudinal_01"]
    step4_full_pipeline(scenario_ids)
    print(f"\n{'=' * W}\nDONE — paste this entire output back for review.\n{'=' * W}")


if __name__ == "__main__":
    main()
