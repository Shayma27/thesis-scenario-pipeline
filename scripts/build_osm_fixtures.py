"""
build_osm_fixtures.py — generates data/stage2_osm_enriched/<scenario_id>.json for
all 19 reports: the frozen Agent-1 output in data/stage1_extracted/*.json, run through the
same OSM enrichment step the live pipeline uses, saved as a stable fixture.

Purpose: developing/testing a new Agent 3 (parameter completion) needs a
realistic "post-OSM, pre-Agent-3" input for all 19 reports, without paying
for a live OSM query (network-dependent, ~2 minutes, occasionally flaky)
every time. This script produces that fixture set once; regenerate it if
osm_enrichment.py changes in a way that affects its output, the same way
docs/topology_detection_report.md needs manual regeneration after a
topology-detection change.

These fixtures intentionally have no generated_simulation_parameters.
openscenario.actors — that's entirely Agent 3's job, not present yet at
this pipeline stage.

Usage:
    python3 scripts/build_osm_fixtures.py
Output:
    data/stage2_osm_enriched/<scenario_id>.json  (19 files)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from osm_enrichment import enrich_with_osm
from pipeline import _fill_location_query_fields
from provenance import check_agent1_preserved

INPUT_DIR = _PROJECT_ROOT / "data" / "stage1_extracted"
OUTPUT_DIR = _PROJECT_ROOT / "data" / "stage2_osm_enriched"
OSM_CACHE_DIR = _PROJECT_ROOT / "data" / "osm_cache"


def main() -> None:
    report_paths = sorted(INPUT_DIR.glob("*.json"))
    OUTPUT_DIR.mkdir(exist_ok=True)

    for path in report_paths:
        scenario_id = path.stem
        agent1_data = json.loads(path.read_text(encoding="utf-8"))

        working = json.loads(json.dumps(agent1_data))  # deep copy
        _fill_location_query_fields(
            working.setdefault("location", {}), working.get("participants", [])
        )
        enriched = enrich_with_osm(working, OSM_CACHE_DIR)
        check_agent1_preserved(agent1_data, enriched)

        out_path = OUTPUT_DIR / f"{scenario_id}.json"
        out_path.write_text(
            json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  wrote {out_path}")

    print(f"\n{len(report_paths)} fixtures written to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
