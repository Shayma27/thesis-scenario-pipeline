"""
report_loader.py — parses docs/manual_classification_reference.md into report records.

Replaces the previous hardcoded/external data source for run_all.py.
Each record is (scenario_id, report_text, scenario_type), matching the tuple shape
run_agent()/run_feedback_iteration() (report_text, scenario_id) and run_all.py's
bookkeeping (scenario_type) already expect.
"""

from __future__ import annotations

import re
from pathlib import Path

REFERENCE_FILE = Path(__file__).resolve().parent / "docs" / "manual_classification_reference.md"

_SECTION_TO_TYPE = {
    "TURNING": "turning",
    "CROSSING": "crossing",
    "LONGITUDINAL": "longitudinal",
}

_ENTRY_RE = re.compile(
    r"\d+-Datum:\s*(?P<meta>.*?)\n(?P<narrative>.*?)(?=\n\s*\n\d+-Datum:|\Z)",
    re.DOTALL,
)

def load_reports() -> list[tuple[str, str, str]]:
    """Parse manual_classification_reference.md into (scenario_id, report_text, scenario_type).

    The source file has no scenario_id field, so IDs are synthesized as
    "{scenario_type}_{NN}" (NN = 1-based position within its section), since
    nothing in the reports themselves is a reliable, fabrication-free identifier.
    """
    text = REFERENCE_FILE.read_text(encoding="utf-8")

    sections = re.split(r"\n## ", text)
    records: list[tuple[str, str, str]] = []

    for section in sections[1:]:  # sections[0] is the title before the first "## "
        header, _, body = section.partition("\n")
        scenario_type = _SECTION_TO_TYPE.get(header.strip())
        if scenario_type is None:
            continue

        for position, match in enumerate(_ENTRY_RE.finditer(body), start=1):
            meta = match.group("meta").strip()
            narrative = " ".join(
                line.strip() for line in match.group("narrative").strip().splitlines()
            )
            report_text = f"Datum: {meta} {narrative}"
            scenario_id = f"{scenario_type}_{position:02d}"
            records.append((scenario_id, report_text, scenario_type))

    return records
