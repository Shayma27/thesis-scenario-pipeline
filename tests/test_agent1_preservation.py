"""
test_agent1_preservation.py — regression gate for the Agent-1 preservation
invariant, across all 19 reports in input/*.json.

Agent 1 (extract_scenario.py) is frozen and gold-verified (see
gold_reference.py / test_semantic_correctness.py). This test checks the rule
that protects that: once OSM enrichment (osm_enrichment.py) and parameter
completion (complete_parameters.py) run, they may fill in a field Agent 1
left null, but must never change a field Agent 1 already set.

Unlike test_semantic_correctness.py (is Agent 1's output correct) and
test_extract_scenario_schema.py (is Agent 1's output shaped correctly), this
test is about what happens to that output *after* Agent 1 — nothing here
checks OSM enrichment's own correctness, only that it never clobbers what
came before it.

Usage:
    python3 test_agent1_preservation.py
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import osm_enrichment
from complete_parameters import complete_parameters
from osm_enrichment import enrich_with_osm
from pipeline import _fill_location_query_fields
from provenance import Agent1FieldOverwritten, check_agent1_preserved

INPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "stage1_extracted"
OSM_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "osm_cache"


def _no_network(*_args, **_kwargs):
    raise URLError("network calls disabled in test_agent1_preservation.py — cache only")


def main() -> None:
    # This test checks a structural invariant (enrichment never overwrites an
    # Agent-1 field), not OSM enrichment's own correctness — it doesn't need
    # live data to be meaningful, and live Nominatim/Overpass calls make a
    # regression test slow and flaky. Cache hits still work; cache misses
    # fail fast into osm_enrichment.py's already-handled network-error path
    # instead of making a real request. speed_estimation.py makes no network
    # call of any kind (Agent 3's speed evidence is read straight from
    # Agent 1's already-grounded extraction — see its module docstring), so
    # no LLM patching is needed here at all.
    network_patch = patch.object(osm_enrichment, "urlopen", side_effect=_no_network)
    network_patch.start()
    try:
        _run()
    finally:
        network_patch.stop()


def _run() -> None:
    report_paths = sorted(INPUT_DIR.glob("*.json"))
    if not report_paths:
        print(f"No reports found in {INPUT_DIR}")
        sys.exit(1)

    failures: list[str] = []

    for path in report_paths:
        scenario_id = path.stem
        agent1_data = json.loads(path.read_text(encoding="utf-8"))

        # Mirror pipeline.py's _tool_extract_scenario: glue fields are added
        # to location, but nothing Agent 1 set is overwritten by them.
        working = copy.deepcopy(agent1_data)
        _fill_location_query_fields(
            working.setdefault("location", {}), working.get("participants", [])
        )
        try:
            check_agent1_preserved(agent1_data, working)
        except Agent1FieldOverwritten as exc:
            failures.append(f"[{scenario_id}] pipeline glue step: {exc}")
            continue

        try:
            enriched = enrich_with_osm(working, OSM_CACHE_DIR)
        except Exception as exc:  # noqa: BLE001 — a crash here is itself a failure to report
            failures.append(f"[{scenario_id}] enrich_with_osm raised: {exc}")
            continue
        try:
            check_agent1_preserved(agent1_data, enriched)
        except Agent1FieldOverwritten as exc:
            failures.append(f"[{scenario_id}] osm enrichment: {exc}")
            continue

        try:
            completed = complete_parameters(enriched)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"[{scenario_id}] complete_parameters raised: {exc}")
            continue
        try:
            check_agent1_preserved(agent1_data, completed)
        except Agent1FieldOverwritten as exc:
            failures.append(f"[{scenario_id}] parameter completion: {exc}")
            continue

        print(f"  OK  {scenario_id}")

    print()
    if failures:
        print(f"FAILED: {len(failures)} violation(s) of the Agent-1 preservation invariant")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print(f"PASSED: all {len(report_paths)} reports — no Agent-1 field was ever overwritten")


if __name__ == "__main__":
    main()
