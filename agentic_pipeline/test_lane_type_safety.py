"""
test_lane_type_safety.py — regression gate for actor/lane-type correctness.

Both OpenDRIVE templates (templates/straight_road.xodr,
templates/intersection_4way.xodr) have exactly ONE real driving lane and
ONE real biking lane per direction — never more, regardless of what
primary_road_lanes/secondary_road_lanes/motor_lane_count (from OSM or the
report) claims. Before this test existed, complete_parameters.py's lane
arithmetic (-max(1, n), -(n+1), ...) assumed a lane existed at position n
from center, which silently placed actors on the template's border/
shoulder/sidewalk lane whenever a report's road had more than one real
lane (e.g. a right-turning car on a 2-lane road landed on lane -2, which is
the *biking* lane, not a second driving lane).

This test runs the real pipeline (extract -> glue -> OSM enrich -> complete
parameters -> topology -> template selection) for all 19 reports, then
checks each actor's initial_lane_id against the ACTUALLY SELECTED
template's real lane types:
    - a motor vehicle must always land on a "driving"-type lane.
    - a cyclist must land on "driving" or "biking" — both are legitimate
      (e.g. explicit road_position, or bike_facility_type="roadway_mixed"
      mean "ride the driving lane" on purpose) — but never anything else.

"sidewalk" is deliberately NOT in the cyclist's allowed set: no code path
currently tries to place a cyclist there (see Assumption 2 in
docs/modeling_assumptions.md — crossing_01's "rode out from the sidewalk"
report is a documented, intentional fallback to the bike lane, not a
sidewalk placement). If that's ever implemented, this test's allowed set
must change with it, deliberately — not silently.

Usage:
    python3 test_lane_type_safety.py
"""

from __future__ import annotations

import copy
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parent))

import osm_enrichment
import speed_estimation
from complete_parameters import complete_parameters
from generate_scenario import _resolve_road_id
from osm_enrichment import detect_topology, enrich_with_osm
from pipeline import _fill_location_query_fields
from template_selector import select_template

INPUT_DIR = Path(__file__).resolve().parent / "input"
OSM_CACHE_DIR = Path(__file__).resolve().parent / "output" / "osm_cache"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

ALLOWED_TYPES = {
    "motor_vehicle": {"driving"},
    "cyclist": {"driving", "biking"},
}


def _no_network(*_args, **_kwargs):
    raise URLError("network calls disabled in test_lane_type_safety.py — cache only")


def _no_llm(*_args, **_kwargs):
    raise ConnectionError("LLM calls disabled in test_lane_type_safety.py — offline only")


def _lane_types(xodr_filename: str, road_id: int) -> dict[int, str]:
    tree = ET.parse(TEMPLATE_DIR / xodr_filename)
    road_el = next(r for r in tree.getroot().findall("road") if r.get("id") == str(road_id))
    types: dict[int, str] = {}
    lanes_el = road_el.find("lanes")
    for lane_section in lanes_el.findall("laneSection"):
        for side in ("left", "center", "right"):
            side_el = lane_section.find(side)
            if side_el is None:
                continue
            for lane in side_el.findall("lane"):
                types[int(lane.get("id"))] = lane.get("type")
    return types


def _audit_one(scenario_id: str, agent1_data: dict) -> list[str]:
    problems = []
    data = copy.deepcopy(agent1_data)
    _fill_location_query_fields(data.setdefault("location", {}), data.get("participants", []))

    report_text = data.get("source", {}).get("raw_text", "")
    enriched = enrich_with_osm(data, OSM_CACHE_DIR)

    # Mirrors pipeline.py's _tool_query_osm: topology detection and template
    # selection now happen right after OSM enrichment, before complete_
    # parameters() (Agent 3) runs, so Agent 3 can work with the real
    # selected template's geometry instead of a placeholder — see
    # complete_parameters._real_primary_road_length_m's docstring.
    scenario_type = enriched.get("classification", {}).get("scenario_type", "")
    topology_result = detect_topology(report_text, scenario_id, cache_dir=OSM_CACHE_DIR)
    template_rel = select_template(scenario_type, topology_result["topology"])
    xodr_filename = Path(template_rel).name
    enriched["topology"] = topology_result
    enriched["template_used"] = xodr_filename

    completed = complete_parameters(enriched)
    is_crossing = scenario_type == "crossing"

    actors = completed.get("generated_simulation_parameters", {}).get("openscenario", {}).get("actors", {})
    for participant in completed.get("participants", []):
        pclass = participant.get("class")
        if pclass not in ALLOWED_TYPES:
            continue
        actor = actors.get(participant["id"])
        if not actor or "initial_lane_id" not in actor:
            continue

        is_secondary = is_crossing and pclass == "motor_vehicle"
        road_id = _resolve_road_id(xodr_filename, is_secondary_approach=is_secondary)
        lane_types = _lane_types(xodr_filename, road_id)
        lane_id = int(actor["initial_lane_id"])
        actual_type = lane_types.get(lane_id, "MISSING_LANE")

        if actual_type not in ALLOWED_TYPES[pclass]:
            problems.append(
                f"{participant['id']} ({pclass}) placed on lane {lane_id} of "
                f"{xodr_filename} road {road_id}, which is type "
                f"'{actual_type}' — allowed: {sorted(ALLOWED_TYPES[pclass])}"
            )
    return problems


def main() -> None:
    network_patch = patch.object(osm_enrichment, "urlopen", side_effect=_no_network)
    llm_patch = patch.object(speed_estimation, "get_client", side_effect=_no_llm)
    network_patch.start()
    llm_patch.start()
    try:
        _run()
    finally:
        network_patch.stop()
        llm_patch.stop()


def _run() -> None:
    report_paths = sorted(INPUT_DIR.glob("*.json"))
    failures: list[str] = []

    for path in report_paths:
        scenario_id = path.stem
        agent1_data = json.loads(path.read_text(encoding="utf-8"))
        try:
            problems = _audit_one(scenario_id, agent1_data)
        except Exception as exc:  # noqa: BLE001 — a crash is itself a failure to report
            failures.append(f"[{scenario_id}] raised: {exc}")
            continue
        if problems:
            for p in problems:
                failures.append(f"[{scenario_id}] {p}")
        else:
            print(f"  OK  {scenario_id}")

    print()
    if failures:
        print(f"FAILED: {len(failures)} lane-type violation(s)")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)

    print(f"PASSED: all {len(report_paths)} reports place every actor on a correctly-typed lane")


if __name__ == "__main__":
    main()
