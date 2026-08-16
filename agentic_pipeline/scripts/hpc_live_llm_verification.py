"""
hpc_live_llm_verification.py — run this ON THE HPC GPU NODE (or frontend02
with the job's node reachable), with a live vLLM server up. Requires
LLM_BASE_URL pointed at the actual node running the job (see step 1's
output — if it prints a connection error, that's your answer already).

Usage:
    python3 scripts/hpc_live_llm_verification.py [scenario_id ...]

With no arguments, runs the three queued diagnostics plus a full
run_agent() pass on crossing_04 (explicit "speeding car" language),
turning_01 (no explicit speed language), and longitudinal_01. Pass
specific scenario_ids to run only those.
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
            # Manually inline the guarded section
            response = client.chat.completions.create(
                model=llm_client.MODEL,
                messages=[
                    {"role": "system", "content": (
                        "You estimate plausible vehicle speed ranges for "
                        "accident reconstruction from report text. You never "
                        "invent a number unsupported by the text — you only "
                        "narrow a given range when the text supports it, or "
                        "say the text gives no evidence."
                    )},
                    {"role": "user", "content": (
                        "A traffic-engineering pipeline is reconstructing a German police "
                        f"accident report as a simulation. For the car (car_1), the "
                        f"grounded default speed range is {envelope['min_kmh']}-"
                        f"{envelope['max_kmh']} km/h (nominal {envelope['nominal_kmh']} "
                        "km/h). Based ONLY on the report text below, does it give "
                        "evidence that this specific vehicle was faster or slower than "
                        "that default? If yes, narrow the range and quote the relevant "
                        "phrase. If the report says nothing about this vehicle's speed, "
                        "say so — do not invent a narrower range without textual "
                        "evidence.\n\n"
                        f"collision_description: {conflict.get('collision_description')}\n"
                        f"severity_text: {conflict.get('severity_text')}\n"
                        f"conflict_mechanism: {conflict.get('conflict_mechanism')}\n\n"
                        "Respond as JSON only: "
                        '{"knowledge_status": "report_qualitative_signal" or '
                        '"insufficient_evidence", '
                        '"speed_range_kmh": {"min": <float>, "max": <float>}, '
                        '"rationale": "<one sentence, quote the report text if applicable>"}'
                    )},
                ],
                temperature=0.0,
                max_tokens=300,
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
            if e.get("logical_range_kmh"):
                print(f"      llm range: {e['logical_range_kmh']} km/h  clamped={e.get('clamped_to_safety_cap', False)}")

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
    scenario_ids = args if args else ["crossing_04", "turning_01", "longitudinal_01"]
    step4_full_pipeline(scenario_ids)
    print(f"\n{'=' * W}\nDONE — paste this entire output back for review.\n{'=' * W}")


if __name__ == "__main__":
    main()
