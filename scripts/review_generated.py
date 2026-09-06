"""
review_generated.py — local-only esmini review pass over already-generated
scenarios, no LLM connection needed.

scripts/run_all.py cycles through all 19 reports with esmini viewing and a review
menu, but it calls run_agent() per report, which needs a live LLM
connection at run time -- that only works from a machine that can reach
the HPC vLLM server, which can't also open a local esmini window. This
script is the other half: generate everything remotely first
(scripts/hpc_live_llm_verification.py all, on HPC), pull the resulting
data/stage4_generated/*/*.xosc files locally via git, then just watch them here
-- no LLM needed for that part at all.

Deliberately has no feedback ([f]) option, unlike run_all.py's menu --
run_feedback_iteration() needs the LLM this script doesn't have access to.

Usage:
    python3 scripts/review_generated.py                  # all 19, in report order
    python3 scripts/review_generated.py crossing_05 crossing_06   # only these
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "src"))

from report_loader import load_reports

ESMINI_BIN = "/home/chimo/tools/esmini/esmini-demo/bin/esmini"
OUTPUT_BASE = PROJECT_DIR / "data" / "stage4_generated"
RESULTS_FILE = PROJECT_DIR / "data" / "review_results.json"
W = 70


def _launch_esmini(xosc_path: Path) -> None:
    cmd = [ESMINI_BIN, "--osc", str(xosc_path), "--window", "60", "60", "800", "600"]
    env = os.environ.copy()
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    print(f"\n{'─' * W}")
    print(f"  Launching esmini...")
    print(f"{'─' * W}")
    subprocess.run(cmd, env=env)
    print(f"  esmini closed.")


def _load_results() -> list[dict]:
    if RESULTS_FILE.exists():
        try:
            return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_result(entry: dict) -> None:
    results = _load_results()
    results = [r for r in results if r.get("scenario_id") != entry["scenario_id"]]
    results.append(entry)
    RESULTS_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


def _review_one(idx: int, total: int, scenario_id: str, scenario_type: str) -> dict:
    print(f"\n{'═' * W}")
    print(f"  Report {idx}/{total}: {scenario_id}  ({scenario_type})")
    print(f"{'═' * W}")

    xosc_path = OUTPUT_BASE / scenario_id / f"{scenario_id}.xosc"
    if not xosc_path.exists():
        print(f"\n  ⚠ Not generated locally yet: {xosc_path}")
        print("  Skipping -- pull it from the HPC run first (see the HPC quickstart notes).")
        entry = {
            "scenario_id": scenario_id,
            "scenario_type": scenario_type,
            "status": "missing_file",
            "note": None,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _save_result(entry)
        return entry

    _launch_esmini(xosc_path)

    note: str | None = None
    while True:
        print()
        print(f"  {'─' * (W - 2)}")
        print("  [r]  Rewatch the simulation")
        print("  [n]  Add a note for yourself (not sent anywhere)")
        print("  [s]  Skip -- move to next without confirming")
        print("  [ok] Confirm -- looks right, move to next")
        print(f"  {'─' * (W - 2)}")
        try:
            choice = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "ok"

        if choice == "ok":
            status = "reviewed"
            break
        if choice == "s":
            status = "skipped"
            break
        if choice == "r":
            _launch_esmini(xosc_path)
            continue
        if choice == "n":
            try:
                note = input("  Note: ").strip() or note
            except (EOFError, KeyboardInterrupt):
                pass
            continue
        if choice == "f":
            print(
                "  Feedback isn't available in this local-only review pass -- "
                "it needs a live LLM connection. Use run_all.py/run.py from a "
                "machine with LLM_BASE_URL reachable instead."
            )
            continue
        print("  Unrecognized choice.")

    entry = {
        "scenario_id": scenario_id,
        "scenario_type": scenario_type,
        "status": status,
        "note": note,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save_result(entry)
    print(f"  Saved ({status}).")
    return entry


def main() -> None:
    reports = load_reports()
    if len(sys.argv) > 1:
        wanted = set(sys.argv[1:])
        reports = [r for r in reports if r[0] in wanted]
        missing = wanted - {r[0] for r in reports}
        if missing:
            print(f"  Unknown scenario id(s), skipping: {', '.join(sorted(missing))}")
    total = len(reports)
    entries: list[dict] = []

    for i, (scenario_id, _report_text, scenario_type) in enumerate(reports, start=1):
        entries.append(_review_one(i, total, scenario_id, scenario_type))
        if i < total:
            try:
                input(f"\n  Press Enter to continue to next report...")
            except (EOFError, KeyboardInterrupt):
                pass

    n_reviewed = sum(1 for e in entries if e["status"] == "reviewed")
    n_skipped = sum(1 for e in entries if e["status"] == "skipped")
    n_missing = sum(1 for e in entries if e["status"] == "missing_file")

    print(f"\n{'═' * W}")
    print(f"  SUMMARY")
    print(f"{'─' * W}")
    print(f"  {n_reviewed}/{total} reviewed and confirmed")
    if n_skipped:
        print(f"  {n_skipped}/{total} skipped without confirming")
    if n_missing:
        print(f"  {n_missing}/{total} not generated locally yet:")
        for e in entries:
            if e["status"] == "missing_file":
                print(f"    - {e['scenario_id']}")
    notes = [e for e in entries if e.get("note")]
    if notes:
        print(f"\n  Notes left:")
        for e in notes:
            print(f"    [{e['scenario_id']}] {e['note']}")
    print(f"  Results saved to {RESULTS_FILE}")
    print(f"{'═' * W}\n")


if __name__ == "__main__":
    main()
