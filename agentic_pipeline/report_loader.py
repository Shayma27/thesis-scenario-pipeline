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

# Dropped from the active corpus, 2026-08-28: turning_07 describes a
# turn-right-into-parking-lot-access conflict ("turn_right_into_parking").
# Neither straight_road.xodr nor intersection_4way.xodr models a parking
# lot access geometry (both templates have exactly one real driving lane
# and one real biking lane per direction, no parking area — see
# docs/modeling_assumptions.md), so no amount of parameter tuning can make
# this report's real topology representable. User decision, not a code
# fix — write this up as a corpus limitation in the thesis rather than
# silently forcing it onto an unsuitable template. The report's text stays
# in manual_classification_reference.md (an unaltered historical record);
# it's filtered out here instead of deleted so turning_08/turning_09's
# scenario_ids (position-based) don't shift.
EXCLUDED_SCENARIO_IDS = {"turning_07"}


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
            # The date/time meta line is deliberately dropped here: Agent 1 no
            # longer extracts source.date/source.time (see extract_scenario.py),
            # so fusing it onto the narrative was pure noise sent to the LLM —
            # worse, run-on with no punctuation ("...10:38 Uhr Der Fahrer..."),
            # which measurably affected scenario_type classification in testing.
            report_text = " ".join(
                line.strip() for line in match.group("narrative").strip().splitlines()
            )
            scenario_id = f"{scenario_type}_{position:02d}"
            if scenario_id in EXCLUDED_SCENARIO_IDS:
                continue
            records.append((scenario_id, report_text, scenario_type))

    return records
