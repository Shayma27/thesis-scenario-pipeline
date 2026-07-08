# OpenDRIVE Templates

Pre-validated `.xodr` road topology files used by the agentic pipeline.
The pipeline selects the appropriate template based on `scenario_type` extracted from
the accident report, instead of generating a new `.xodr` per scenario.

## Template files

| File | Topology | Derived from | Used for scenario types |
|------|----------|--------------|------------------------|
| `intersection_4way.xodr` | 4-way intersection | esmini `fabriksgatan.xodr` [esmini v2.57.0] | `right_turn_conflict`, `left_turn_conflict`, `straight_crossing_conflict`, `priority_violation_conflict`, `unknown` |
| `straight_road.xodr` | Straight road 500 m | esmini `straight_500m.xodr` [esmini v2.57.0] | `lane_change_conflict` |
| `tjunction.xodr` | T-junction | placeholder — replace before production use | `priority_violation_conflict` (legacy) |

## Bike lane modifications

Both active templates were modified to add a right-side biking lane adjacent to the
outermost driving lane on each approach road and each connecting road:

- **Width:** 1.25 m per ERA 2010
  [FGSV, *Empfehlungen für Radverkehrsanlagen*, 2010]
- **Type:** `type="biking"`
- **Shoulder/sidewalk lanes** renumbered outward to preserve adjacency order
- **Junction laneLinks** added for all right-side-incoming biking movements
  (`intersection_4way.xodr` only)

## Excluded scenario types

| Scenario type | Reason |
|---------------|--------|
| `dooring` | No suitable static template — esmini cannot model a door-opening event [esmini v2.57.0] |

`select_template()` raises `ValueError` for `dooring`; the pipeline must handle this
and skip esmini scenario generation for such reports.

## Source attribution

- esmini v2.57.0, github.com/esmini/esmini (BSD 3-Clause)
- ERA 2010 bike lane width reference: FGSV, *Empfehlungen für Radverkehrsanlagen*, 2010

## Usage

Template selection is defined in `template_selector.py`. The selected template is
copied into each scenario's output directory and referenced by the generated `.xosc`
via the `<LogicFile filepath="...">` element.
