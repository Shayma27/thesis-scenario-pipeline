"""Select the correct pre-validated .xodr template for a given scenario type."""

from pathlib import Path

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

TEMPLATE_MAP: dict[str, str] = {
    "right_turn_conflict":         "intersection_4way.xodr",
    "left_turn_conflict":          "intersection_4way.xodr",
    "straight_crossing_conflict":  "intersection_4way.xodr",
    "priority_violation_conflict": "tjunction.xodr",
    "lane_change_conflict":        "straight_road.xodr",
    "rear_end_conflict":           "straight_road.xodr",
}

_FALLBACK = "intersection_4way.xodr"


def select_template(scenario_type: str) -> Path:
    """Return the absolute path to the .xodr template for *scenario_type*.

    Falls back to the 4-way intersection template for unknown types.
    """
    filename = TEMPLATE_MAP.get(scenario_type, _FALLBACK)
    return _TEMPLATE_DIR / filename
