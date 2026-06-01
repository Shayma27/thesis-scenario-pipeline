"""
run.py — interactive entry point for the agentic scenario pipeline.

Usage:
    python3 run.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# ── ensure this directory is on sys.path so pipeline.py finds its siblings ──
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import run_agent, run_feedback_iteration, OUTPUT_BASE

ESMINI_BIN = "/home/chimo/tools/esmini/esmini-demo/bin/esmini"
MAX_FEEDBACK_ITERATIONS = 5
W = 70


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[äÄ]", "ae", text)
    text = re.sub(r"[öÖ]", "oe", text)
    text = re.sub(r"[üÜ]", "ue", text)
    text = re.sub(r"ß", "ss", text)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")[:40]


def _auto_scenario_id(report_text: str) -> str:
    today = date.today().strftime("%Y%m%d")
    first_line = report_text.strip().split("\n")[0]
    m = re.search(
        r"([A-ZÄÖÜ][a-zA-ZäöüÄÖÜß\-]+(?:str(?:aße|\.)?|weg|allee|damm|platz|gasse))",
        first_line,
    )
    street = _slugify(m.group(1)) if m else "scenario"
    return f"{today}_{street}"


def _read_report() -> str:
    print("=" * W)
    print("  AGENTIC SCENARIO PIPELINE")
    print("  Convert a German Berlin police report → OpenDRIVE + OpenSCENARIO")
    print("=" * W)
    print()
    print("Paste the German police report text below.")
    print("When done, enter a blank line followed by END on its own line,")
    print("or press Ctrl+D (EOF).")
    print()

    lines: list[str] = []
    try:
        while True:
            line = input()
            if line.strip() == "END":
                break
            lines.append(line)
    except EOFError:
        pass

    return "\n".join(lines).strip()


def _read_scenario_id(report_text: str) -> str:
    auto = _auto_scenario_id(report_text)
    print()
    print(f"  Auto-generated scenario ID:  {auto}")
    try:
        answer = input("  Press Enter to use it, or type a custom ID:  ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    return answer if answer else auto


def _launch_esmini(xosc_path: Path) -> None:
    """Launch esmini and block until the window is closed."""
    cmd = [
        ESMINI_BIN,
        "--osc", str(xosc_path),
        "--window", "60", "60", "800", "600",
        "--fixed_timestep", "0.05",
    ]
    env = os.environ.copy()
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"

    print(f"\n{'─' * W}")
    print(f"  Launching esmini...")
    print(f"  LIBGL_ALWAYS_SOFTWARE=1 {' '.join(cmd)}")
    print(f"{'─' * W}")

    subprocess.run(cmd, env=env)

    print(f"  esmini closed.")


def main() -> None:
    report_text = _read_report()
    if not report_text:
        print("No report text provided. Exiting.")
        sys.exit(1)

    scenario_id = _read_scenario_id(report_text)

    # ── Run the full pipeline automatically, no human interruption ────────
    print()
    result = run_agent(report_text, scenario_id)

    if not result.get("valid"):
        print("\n  Pipeline did not produce a valid scenario. Cannot visualize.")
        sys.exit(1)

    xosc = Path(result["xosc_path"])
    state = result["state"]

    # ── esmini + feedback loop ─────────────────────────────────────────────
    for iteration in range(MAX_FEEDBACK_ITERATIONS + 1):
        _launch_esmini(xosc)

        print()
        print(f"  {'─' * (W - 2)}")
        try:
            feedback = input(
                "  Does the simulation match the report?\n"
                "  Press Enter if correct, or describe what is wrong: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            feedback = ""

        if not feedback:
            # User confirmed — done
            break

        if iteration >= MAX_FEEDBACK_ITERATIONS:
            print(f"\n  Max feedback iterations ({MAX_FEEDBACK_ITERATIONS}) reached. Saving current version.")
            break

        print(f"\n  Applying feedback [{iteration + 1}/{MAX_FEEDBACK_ITERATIONS}]...")
        fb_result = run_feedback_iteration(state, report_text, feedback)

        if not fb_result.get("success"):
            print(f"  ✗ Feedback error: {fb_result.get('error')}")
            print("  Retrying esmini with unchanged scenario...")
            continue

        xosc = Path(fb_result["xosc_path"])
        valid_tag = "✓ valid" if fb_result.get("valid") else "⚠ validation errors"
        print(f"  Regenerated ({valid_tag}): {xosc.name}")
        if fb_result.get("validation_errors"):
            for e in fb_result["validation_errors"]:
                print(f"    ✗  {e}")

    # ── Final summary ──────────────────────────────────────────────────────
    print()
    print("=" * W)
    print("  FINAL SCENARIO SAVED")
    print("-" * W)
    if state.xodr_path:
        print(f"  OpenDRIVE    : {state.xodr_path}")
    print(f"  OpenSCENARIO : {state.xosc_path}")
    output_dir = OUTPUT_BASE / scenario_id
    print(f"  Output dir   : {output_dir}/")
    print("=" * W)


if __name__ == "__main__":
    main()
