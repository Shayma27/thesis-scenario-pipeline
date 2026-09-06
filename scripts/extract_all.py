"""
extract_all.py — batch-run Agent 1 (extract_scenario.py) over all reports in
docs/manual_classification_reference.md, non-interactively.

Unlike run_all.py, this does NOT run the full pipeline (no OSM enrichment,
no parameter completion, no template/xosc generation, no esmini). It only
exercises the extraction stage, saving each result to input/<scenario_id>.json
— for reviewing Agent 1's output across the whole report corpus before
deciding whether to run anything downstream.

Usage:
    python3 extract_all.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import openai

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "src"))

from extract_scenario import extract_scenario
from utils.report_loader import load_reports

INPUT_DIR = PROJECT_DIR / "data" / "stage1_extracted"
W = 70


def _with_retry(fn, *args, **kwargs):
    """Call fn(*args, **kwargs), retrying on 429 RateLimitError."""
    while True:
        try:
            return fn(*args, **kwargs)
        except openai.RateLimitError:
            print("  Rate limit — waiting 60s...")
            time.sleep(60)


def main() -> None:
    reports = load_reports()
    total = len(reports)
    INPUT_DIR.mkdir(exist_ok=True)

    print(f"{'═' * W}")
    print(f"  Extracting {total} scenarios from manual_classification_reference.md")
    print(f"{'═' * W}")

    succeeded: list[str] = []
    failed: list[tuple[str, str]] = []

    for i, (scenario_id, report_text, scenario_type) in enumerate(reports, start=1):
        print(f"\n[{i}/{total}] {scenario_id} ({scenario_type})")
        try:
            result = _with_retry(extract_scenario, report_text, scenario_id)
        except Exception as exc:  # noqa: BLE001 — log and keep going through the batch
            print(f"  ✗ FAILED: {exc}")
            failed.append((scenario_id, str(exc)))
            continue

        output_path = INPUT_DIR / f"{scenario_id}.json"
        output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        cls = result.get("classification", {})
        loc = result.get("location", {})
        print(f"  ✓ scenario_type={cls.get('scenario_type')} confidence={cls.get('confidence')}")
        print(f"    {loc.get('primary_road')} / {loc.get('secondary_road')}")
        print(f"    Saved: {output_path}")
        succeeded.append(scenario_id)

    print(f"\n{'═' * W}")
    print(f"  SUMMARY: {len(succeeded)}/{total} succeeded")
    if failed:
        print(f"  {len(failed)} FAILED:")
        for sid, err in failed:
            print(f"    ✗ {sid}: {err}")
    print(f"  Output dir: {INPUT_DIR}")
    print(f"{'═' * W}")


if __name__ == "__main__":
    main()
