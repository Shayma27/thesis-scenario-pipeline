"""Select the correct pre-validated .xodr template for a given scenario type.

Template choice is decided primarily by osm_enrichment.detect_topology() —
an OSM-based signal answering "is this location a straight midblock segment
or a junction?", automating what was previously a manual Google Maps/OSM
lookup. scenario_type (one of "turning", "crossing", "longitudinal", "other")
is the deciding input only where topology doesn't apply — "longitudinal" is
a single-road maneuver by definition, not a location-topology question — and
as a fallback while a report's topology is still "needs_manual_review".
"""

from pathlib import Path

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

# Fallback template per scenario_type, used only when topology detection
# hasn't conclusively decided (None or "needs_manual_review"). "turning" and
# "crossing" both need a real junction to happen at all, so the richer
# 4-way template is the sensible guess pending confirmation. "other" has no
# reliable topology prior (it covers everything not turning/crossing/
# longitudinal, from parked-vehicle dooring to unclear reports) — kept on
# the richer template too, since a straight road can't represent a junction
# but a junction template can still host a straight-line trajectory.
TEMPLATE_MAP: dict[str, str] = {
    "turning": "templates/intersection_4way.xodr",
    "crossing": "templates/intersection_4way.xodr",
    "longitudinal": "templates/straight_road.xodr",
    "other": "templates/intersection_4way.xodr",
}

_TOPOLOGY_TEMPLATE = {
    "midblock": "templates/straight_road.xodr",
    "4way_junction": "templates/intersection_4way.xodr",
}


def select_template(scenario_type: str, topology: str | None = None) -> str:
    """Return the relative path to the .xodr template.

    `topology` is the result of osm_enrichment.detect_topology(): one of
    "midblock", "4way_junction", "needs_manual_review", or None (topology
    detection wasn't run). Falls back to intersection_4way.xodr for an
    unrecognized scenario_type when topology doesn't decide it.
    """
    if scenario_type == "longitudinal":
        # Same-direction travel (overtaking or a lane change) is a
        # single-road maneuver by definition — topology detection isn't
        # meaningful here (the street names such a report mentions are
        # incidental landmarks along that one road, not a real
        # cross-street), so scenario_type alone decides this case.
        return TEMPLATE_MAP["longitudinal"]

    if topology in _TOPOLOGY_TEMPLATE:
        return _TOPOLOGY_TEMPLATE[topology]

    # topology is None or "needs_manual_review": fall back to the
    # scenario_type-based table rather than blocking generation.
    return TEMPLATE_MAP.get(scenario_type, "templates/intersection_4way.xodr")
