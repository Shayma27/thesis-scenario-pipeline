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
19-report corpus (utils.report_loader.load_reports()).
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "src"))

from utils import llm_client
from pipeline import run_agent
from utils.report_loader import load_reports

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


def step3_speed_evidence_extraction_raw() -> None:
    print(f"\n{'=' * W}\nSTEP 3 — raw extract_scenario() call for crossing_04\n{'=' * W}")
    # Speed evidence is no longer a separate Agent 3 LLM call at all (see
    # speed_estimation.py's module docstring for why that design was
    # retired) — it's extracted directly by Agent 1's own extraction call,
    # the only LLM call left in this pipeline that reads raw_text. This
    # step calls extract_scenario() directly (bypassing the tool-calling
    # loop) on crossing_04's real report text and prints exactly what
    # came back for speed_evidence/speed_evidence_quote, to see the real
    # traceback if something fails instead of a swallowed exception deep
    # inside the tool loop.
    from extract_scenario import extract_scenario

    report_text = (
        "Eine Rad fahrende Person querte an einer Querungshilfe unachtsam die "
        "stadteinwärts führende Richtungsfahrbahn der Landsberger Allee vom "
        "begrünten Mittelstreifen kommend nach Norden. Dabei wurde sie von "
        "einer Pkw fahrenden Person ungebremst erfasst, die auf der "
        "Landsberger Allee Richtung Westen mit deutlich überhöhter "
        "Geschwindigkeit fuhr."
    )
    try:
        extracted = extract_scenario(report_text, "crossing_04_diagnostic")
        print("extract_scenario() returned OK. Participants:")
        for p in extracted.get("participants", []):
            print(
                f"  {p.get('id')}: speed_evidence={p.get('speed_evidence')!r}"
                f"  quote={p.get('speed_evidence_quote')!r}"
            )
    except Exception:
        print("extract_scenario() FAILED — full traceback:")
        traceback.print_exc()


def step4_full_pipeline(scenario_ids: list[str]) -> None:
    print(f"\n{'=' * W}\nSTEP 4 — full pipeline run_agent() on real reports\n{'=' * W}")
    reports = {sid: text for sid, text, _ in load_reports()}
    out_dir = PROJECT_DIR / "data" / "hpc_live_llm_verification"
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
    step3_speed_evidence_extraction_raw()
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
