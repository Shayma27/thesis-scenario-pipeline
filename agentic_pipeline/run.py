"""
run.py — interactive entry point for the agentic scenario pipeline.

Usage:
    python3 run.py
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

# ── ensure this directory is on sys.path so pipeline.py finds its siblings ──
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import run_agent, OUTPUT_BASE


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
    # look for a street name — anything before /, Ecke, or end of first line
    first_line = report_text.strip().split("\n")[0]
    m = re.search(r"([A-ZÄÖÜ][a-zA-ZäöüÄÖÜß\-]+(?:str(?:aße|\.)?|weg|allee|damm|platz|gasse))", first_line)
    street = _slugify(m.group(1)) if m else "scenario"
    return f"{today}_{street}"


def _read_report() -> str:
    print("=" * 70)
    print("  AGENTIC SCENARIO PIPELINE")
    print("  Convert a German Berlin police report → OpenDRIVE + OpenSCENARIO")
    print("=" * 70)
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
    answer = input("  Press Enter to use it, or type a custom ID:  ").strip()
    return answer if answer else auto


def _esmini_command(xosc_path: Path) -> str:
    return (
        f"esmini --window 60 60 1200 600 "
        f"--osc {xosc_path}"
    )


def main() -> None:
    report_text = _read_report()
    if not report_text:
        print("No report text provided. Exiting.")
        sys.exit(1)

    scenario_id = _read_scenario_id(report_text)

    print()
    result = run_agent(report_text, scenario_id, human_in_loop=True)

    print()
    if result.get("user_aborted"):
        print("Pipeline aborted. No files saved.")
        sys.exit(0)

    xosc = result.get("xosc_path")
    xodr = result.get("xodr_path")
    output_dir = OUTPUT_BASE / scenario_id

    print("=" * 70)
    print("  FILES SAVED")
    print("-" * 70)
    if xodr:
        print(f"  OpenDRIVE  : {xodr}")
    if xosc:
        print(f"  OpenSCENARIO: {xosc}")
    print(f"  Enriched JSON + agent log in: {output_dir}/")
    print()
    if xosc:
        print("  VISUALIZE WITH ESMINI")
        print("-" * 70)
        print(f"  {_esmini_command(Path(xosc))}")
    print("=" * 70)


if __name__ == "__main__":
    main()
