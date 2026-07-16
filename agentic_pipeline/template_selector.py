"""Select the correct pre-validated .xodr template for a given scenario type.

Template choice used to be inferred purely from scenario_type (a lookup
table, below). It's now decided primarily by osm_enrichment.detect_topology()
— an OSM-based signal answering "is this location a straight midblock
segment or a junction?", automating what was previously a manual Google
Maps/OSM lookup. scenario_type remains the deciding input only where
topology doesn't apply (dooring is excluded outright; lane_change_conflict
is a single-road maneuver by definition, not a location topology question)
and as a fallback while a report's topology is still "needs_manual_review".
"""

from pathlib import Path

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

TEMPLATE_MAP: dict[str, str | None] = {
    "right_turn_conflict":         "templates/intersection_4way.xodr",
    "left_turn_conflict":          "templates/intersection_4way.xodr",
    "straight_crossing_conflict":  "templates/intersection_4way.xodr",
    "priority_violation_conflict": "templates/intersection_4way.xodr",
    "lane_change_conflict":        "templates/straight_road.xodr",
    "dooring":                     None,
    "unknown":                     "templates/intersection_4way.xodr",
}

_TOPOLOGY_TEMPLATE = {
    "midblock": "templates/straight_road.xodr",
    "4way_junction": "templates/intersection_4way.xodr",
}


def select_template(scenario_type: str, topology: str | None = None) -> str:
    """Return the relative path to the .xodr template.

    `topology` is the result of osm_enrichment.detect_topology(): one of
    "midblock", "4way_junction", "needs_manual_review", or None (topology
    detection wasn't run). Raises ValueError for scenario types excluded by
    design (e.g. dooring). Falls back to intersection_4way.xodr for unknown
    types when topology doesn't decide it.
    """
    if scenario_type == "dooring":
        raise ValueError(
            "Scenario type 'dooring' excluded by design "
            "(dooring not supported in esmini)."
        )
    if scenario_type == "lane_change_conflict":
        # A lane change is a single-road maneuver by definition — topology
        # detection isn't meaningful here (the street names a report
        # mentions are incidental landmarks along that one road, not a
        # real cross-street), so scenario_type alone decides this case.
        return TEMPLATE_MAP["lane_change_conflict"]

    if topology in _TOPOLOGY_TEMPLATE:
        return _TOPOLOGY_TEMPLATE[topology]

    # topology is None or "needs_manual_review": fall back to the previous
    # scenario_type-based assumption rather than blocking generation.
    return TEMPLATE_MAP.get(scenario_type, "templates/intersection_4way.xodr")
