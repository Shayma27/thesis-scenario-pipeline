# OpenDRIVE Templates

Pre-validated `.xodr` road topology files used by the agentic pipeline.
The pipeline selects the appropriate template based on `scenario_type` extracted from
the accident report, instead of generating a new `.xodr` per scenario.

## Template files

| File | Topology | Used for scenario types |
|------|----------|------------------------|
| `intersection_4way.xodr` | 4-way intersection | `right_turn_conflict`, `left_turn_conflict`, `straight_crossing_conflict` |
| `tjunction.xodr` | T-junction | `priority_violation_conflict` |
| `straight_road.xodr` | Straight road | `lane_change_conflict`, `rear_end_conflict` |

## Placeholder status

The current files are temporary placeholders copied from an existing pipeline output.
Replace each file with a properly authored and validated template before production use:

- `intersection_4way.xodr` — should have 4 approach roads, proper junction geometry
- `tjunction.xodr` — should have 3 approach roads with priority-road setup
- `straight_road.xodr` — should be a simple straight road with bike lane

## Usage

Template selection is defined in `template_selector.py`.  The selected template is
copied into each scenario's output directory and referenced by the generated `.xosc`
via the `<LogicFile filepath="...">` element.
