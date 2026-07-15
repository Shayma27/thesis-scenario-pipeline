"""
run_all.py — batch scenario runner for the agentic pipeline.

Usage:
    python3 run_all.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import openai

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from pipeline import run_agent, run_feedback_iteration
from report_loader import load_reports as _load_reports

ESMINI_BIN = "/home/chimo/tools/esmini/esmini-demo/bin/esmini"
RESULTS_FILE = PROJECT_DIR / "results.json"
MAX_FEEDBACK_ITERATIONS = 5
W = 70


# ── Scenario registry ────────────────────────────────────────────────────────
# Reports are loaded from docs/manual_classification_reference.md (see report_loader.py).

_LABELS = {
    "turning": "Turning",
    "crossing": "Crossing",
    "longitudinal": "Longitudinal",
}


def _build_groups() -> list[tuple[str, str, list[tuple[str, str]]]]:
    reports = _load_reports()
    grouped: dict[str, list[tuple[str, str]]] = {}
    for scenario_id, report_text, scenario_type in reports:
        grouped.setdefault(scenario_type, []).append((scenario_id, report_text))
    return [
        (_LABELS[scenario_type], scenario_type, scenarios)
        for scenario_type, scenarios in grouped.items()
    ]


# (display label, scenario_type, scenario list)
GROUPS: list[tuple[str, str, list[tuple[str, str]]]] = _build_groups()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _launch_esmini(xosc_path: Path) -> None:
    cmd = [ESMINI_BIN, "--osc", str(xosc_path), "--window", "60", "60", "800", "600"]
    env = os.environ.copy()
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    print(f"\n{'─' * W}")
    print(f"  Launching esmini...")
    print(f"{'─' * W}")
    subprocess.run(cmd, env=env)
    print(f"  esmini closed.")


def _with_retry(fn, *args, **kwargs):
    """Call fn(*args, **kwargs), retrying on 429 RateLimitError."""
    while True:
        try:
            return fn(*args, **kwargs)
        except openai.RateLimitError:
            print("\n  Rate limit — waiting 60s...")
            time.sleep(60)


def _load_results() -> list[dict]:
    if RESULTS_FILE.exists():
        try:
            return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_result(entry: dict) -> None:
    results = _load_results()
    # Replace any existing entry for the same scenario_id
    results = [r for r in results if r.get("scenario_id") != entry["scenario_id"]]
    results.append(entry)
    RESULTS_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Per-scenario runner ───────────────────────────────────────────────────────

def _run_one(
    idx: int,
    total: int,
    scenario_id: str,
    report_text: str,
    scenario_type: str,
) -> dict:
    print(f"\n{'═' * W}")
    print(f"  Report {idx}/{total}: {scenario_id}")
    print(f"{'═' * W}")

    result = _with_retry(run_agent, report_text, scenario_id)

    if not result.get("valid") or not result.get("xosc_path"):
        print("\n  ✗ Pipeline failed — skipping visualisation.")
        entry = {
            "scenario_id": scenario_id,
            "scenario_type": scenario_type,
            "confidence": (
                result["state"].data.get("classification", {}).get("confidence")
                if result.get("state") and result["state"].data
                else None
            ),
            "feedback_given": False,
            "feedback_count": 0,
            "feedback_messages": [],
            "status": "pipeline_failed",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _save_result(entry)
        print("  Saved.")
        return entry

    xosc = Path(result["xosc_path"])
    state = result["state"]
    confidence = (
        state.data.get("classification", {}).get("confidence") if state.data else None
    )

    _launch_esmini(xosc)

    # ── Review loop ───────────────────────────────────────────────────────
    feedback_count = 0
    feedback_messages: list[str] = []

    while True:
        print()
        print(f"  {'─' * (W - 2)}")
        print("  [r]  Rewatch the simulation")
        print("  [f]  Give feedback to improve it")
        print("  [ok] Confirm — this matches the report")
        print(f"  {'─' * (W - 2)}")
        try:
            choice = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "ok"

        if choice == "ok":
            break

        if choice == "r":
            _launch_esmini(xosc)
            continue

        if choice == "f":
            if feedback_count >= MAX_FEEDBACK_ITERATIONS:
                print(
                    f"\n  Max feedback iterations ({MAX_FEEDBACK_ITERATIONS}) reached."
                    " Saving current version."
                )
                break
            try:
                feedback = input("  Describe what is wrong: ").strip()
            except (EOFError, KeyboardInterrupt):
                feedback = ""
            if not feedback:
                continue
            feedback_messages.append(feedback)
            feedback_count += 1
            print(f"\n  Applying feedback [{feedback_count}/{MAX_FEEDBACK_ITERATIONS}]...")
            fb_result = _with_retry(run_feedback_iteration, state, report_text, feedback)
            if not fb_result.get("success"):
                print(f"  ✗ Feedback error: {fb_result.get('error')}")
            else:
                xosc = Path(fb_result["xosc_path"])
                valid_tag = "✓ valid" if fb_result.get("valid") else "⚠ validation errors"
                print(f"  Regenerated ({valid_tag}): {xosc.name}")
                if fb_result.get("validation_errors"):
                    for e in fb_result["validation_errors"]:
                        print(f"    ✗  {e}")
            _launch_esmini(xosc)
            continue

    # ── Log result ────────────────────────────────────────────────────────
    entry = {
        "scenario_id": scenario_id,
        "scenario_type": scenario_type,
        "confidence": confidence,
        "feedback_given": feedback_count > 0,
        "feedback_count": feedback_count,
        "feedback_messages": feedback_messages,
        "status": "confirmed",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save_result(entry)
    print(f"\n  Saved.")
    return entry


# ── Group runner ──────────────────────────────────────────────────────────────

def _run_group(
    scenarios: list[tuple[str, str, str]],  # (scenario_id, report_text, scenario_type)
) -> None:
    total = len(scenarios)
    entries: list[dict] = []

    for i, (scenario_id, report_text, scenario_type) in enumerate(scenarios, start=1):
        entry = _run_one(i, total, scenario_id, report_text, scenario_type)
        entries.append(entry)

        if i < total:
            try:
                input(f"\n  Press Enter to continue to next report...")
            except (EOFError, KeyboardInterrupt):
                pass

    # ── Summary ───────────────────────────────────────────────────────────
    n_clean = sum(
        1 for e in entries if e["status"] == "confirmed" and e["feedback_count"] == 0
    )
    n_feedback = sum(
        1 for e in entries if e["status"] == "confirmed" and e["feedback_count"] > 0
    )
    n_failed = sum(1 for e in entries if e["status"] == "pipeline_failed")
    total_iters = sum(e["feedback_count"] for e in entries)

    print(f"\n{'═' * W}")
    print(f"  SUMMARY")
    print(f"{'─' * W}")
    print(f"  {n_clean}/{total} confirmed without feedback")
    if n_feedback:
        s = "s" if total_iters != 1 else ""
        print(f"  {n_feedback}/{total} required feedback ({total_iters} iteration{s})")
    if n_failed:
        print(f"  {n_failed}/{total} pipeline failures")
    print(f"  Results saved to {RESULTS_FILE}")
    print(f"{'═' * W}\n")


# ── Menu ──────────────────────────────────────────────────────────────────────

def _show_menu() -> None:
    total_all = sum(len(s) for _, _, s in GROUPS)
    print(f"\n{'═' * W}")
    print("  SCENARIO BATCH RUNNER")
    print(f"{'─' * W}")
    for i, (label, _, scenarios) in enumerate(GROUPS, start=1):
        print(f"  [{i}]  {label:<30} ({len(scenarios)})")
    print(f"  [all] Run all {total_all} scenarios")
    print(f"{'═' * W}")


def main() -> None:
    _show_menu()
    try:
        choice = input("\n  Select: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        sys.exit(0)

    if choice == "all":
        scenarios = [
            (sid, rt, gtype)
            for _, gtype, group in GROUPS
            for sid, rt in group
        ]
        _run_group(scenarios)
        return

    try:
        idx = int(choice) - 1
        if not (0 <= idx < len(GROUPS)):
            raise ValueError
    except ValueError:
        print("  Invalid choice.")
        sys.exit(1)

    _, scenario_type, group = GROUPS[idx]
    scenarios = [(sid, rt, scenario_type) for sid, rt in group]
    _run_group(scenarios)


if __name__ == "__main__":
    main()
