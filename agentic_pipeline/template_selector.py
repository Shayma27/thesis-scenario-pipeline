"""Select the correct pre-validated .xodr template for a given scenario type."""

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


def select_template(scenario_type: str) -> str:
    """Return the relative path to the .xodr template for *scenario_type*.

    Raises ValueError for scenario types excluded by design (e.g. dooring).
    Falls back to intersection_4way.xodr for unknown types.
    """
    template = TEMPLATE_MAP.get(scenario_type, "templates/intersection_4way.xodr")
    if template is None:
        raise ValueError(
            f"Scenario type '{scenario_type}' excluded by design "
            "(dooring not supported in esmini)."
        )
    return template
