"""
test_semantic_correctness.py — semantic regression gate for the 19 reports.

Unlike test_extract_scenario_schema.py (structure, enum validity, downstream
compatibility against one mocked example), this compares the CURRENT
input/*.json files against gold_reference.py's manually-verified expected
values, checked field-by-field against each report's actual raw_text.

This tests structural/enum fields exactly (scenario_type, participant
type/maneuver/initial_direction/heading_reference/road_position, roads,
bike_facility_type/position, collision_happened) and free-text fields
(conflict_mechanism, collision_description) only for forbidden content
(signal-state leakage, unsupported facility claims) — exact-string matching
prose is not meaningful, paraphrase is expected and fine.

Usage:
    python3 test_semantic_correctness.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gold_reference import GOLD, SKIP, GLOBAL_FORBIDDEN_TERMS, BIKE_FACILITY_IMPLYING_TERMS
from report_loader import load_reports

INPUT_DIR = Path(__file__).resolve().parent / "input"


class Failure:
    def __init__(self, scenario_id, path, expected, actual, raw_text):
        self.scenario_id = scenario_id
        self.path = path
        self.expected = expected
        self.actual = actual
        self.raw_text = raw_text

    def __str__(self):
        return (
            f"  [{self.scenario_id}] {self.path}\n"
            f"      expected: {self.expected!r}\n"
            f"      actual:   {self.actual!r}\n"
            f"      raw_text: {self.raw_text}"
        )


def _check_field(failures, scenario_id, path, expected, actual, raw_text):
    if expected is SKIP or expected == SKIP:
        return
    if expected != actual:
        failures.append(Failure(scenario_id, path, expected, actual, raw_text))


def _check_forbidden(failures, scenario_id, path, text, forbidden_terms, raw_text):
    if not text:
        return
    lowered = text.lower()
    for term in forbidden_terms:
        if term.lower() in lowered:
            failures.append(Failure(
                scenario_id, path,
                expected=f"(must NOT contain {term!r})",
                actual=text,
                raw_text=raw_text,
            ))


def check_scenario(scenario_id: str) -> list[Failure]:
    failures: list[Failure] = []
    gold = GOLD.get(scenario_id)
    if gold is None:
        failures.append(Failure(scenario_id, "<gold_reference>", "an entry in GOLD", "missing", ""))
        return failures

    data_path = INPUT_DIR / f"{scenario_id}.json"
    if not data_path.exists():
        failures.append(Failure(scenario_id, "<file>", "file exists", "missing", ""))
        return failures
    data = json.loads(data_path.read_text(encoding="utf-8"))
    raw_text = data.get("source", {}).get("raw_text", "")

    _check_field(failures, scenario_id, "classification.scenario_type",
                 gold["scenario_type"], data.get("classification", {}).get("scenario_type"), raw_text)

    for field, expected in gold["location"].items():
        _check_field(failures, scenario_id, f"location.{field}",
                     expected, data.get("location", {}).get(field), raw_text)

    for field, expected in gold["road_context"].items():
        _check_field(failures, scenario_id, f"road_context.{field}",
                     expected, data.get("road_context", {}).get(field), raw_text)

    actual_participants = {p["id"]: p for p in data.get("participants", [])}
    for pid, expected_fields in gold["participants"].items():
        actual = actual_participants.get(pid)
        if actual is None:
            failures.append(Failure(scenario_id, f"participants.{pid}", "present", "MISSING", raw_text))
            continue
        for field, expected in expected_fields.items():
            _check_field(failures, scenario_id, f"participants.{pid}.{field}",
                         expected, actual.get(field), raw_text)

    _check_field(failures, scenario_id, "conflict.collision_happened",
                 gold["collision_happened"], data.get("conflict", {}).get("collision_happened"), raw_text)

    # free-text fields: forbidden-content checks only, no exact match
    conflict_mechanism = data.get("conflict", {}).get("conflict_mechanism", "") or ""
    collision_description = data.get("conflict", {}).get("collision_description", "") or ""
    free_text = f"{conflict_mechanism} {collision_description}"

    _check_forbidden(failures, scenario_id, "conflict.[mechanism+description]",
                      free_text, GLOBAL_FORBIDDEN_TERMS, raw_text)

    if gold["road_context"].get("bike_facility_type") is None:
        _check_forbidden(failures, scenario_id, "conflict.[mechanism+description] (bike facility)",
                          free_text, BIKE_FACILITY_IMPLYING_TERMS, raw_text)

    _check_forbidden(failures, scenario_id, "conflict.[mechanism+description] (scenario-specific)",
                      free_text, gold.get("extra_forbidden", []), raw_text)

    return failures


def main() -> None:
    scenario_ids = [sid for sid, _, _ in load_reports()]
    all_failures: list[Failure] = []
    for sid in scenario_ids:
        all_failures.extend(check_scenario(sid))

    total = len(scenario_ids)
    failed_ids = sorted({f.scenario_id for f in all_failures})

    print(f"\n{'═' * 78}")
    print(f"  SEMANTIC CORRECTNESS GATE — {total - len(failed_ids)}/{total} scenarios clean, "
          f"{len(all_failures)} field-level failure(s) across {len(failed_ids)} scenario(s)")
    print(f"{'═' * 78}")

    if all_failures:
        for f in all_failures:
            print(str(f))
            print()
        print(f"{'─' * 78}")
        print("  This does NOT test downstream/structural compatibility — see")
        print("  test_extract_scenario_schema.py for that. This tests whether the")
        print("  CURRENT input/*.json files match manually-verified semantic ground truth.")
        print(f"{'═' * 78}\n")
        sys.exit(1)
    else:
        print("  All 19 scenarios match the gold reference.")
        print(f"{'═' * 78}\n")


if __name__ == "__main__":
    main()
