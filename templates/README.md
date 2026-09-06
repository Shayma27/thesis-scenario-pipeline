# OpenDRIVE Templates

Pre-validated `.xodr` road topology files used by the agentic pipeline,
instead of generating a new `.xodr` per scenario. Template choice is decided
primarily by `osm_enrichment.detect_topology()` (an OSM-based signal for "is
this location a straight midblock segment or a junction?"), with
`scenario_type` — one of `turning`, `crossing`, `longitudinal`, `other` — used
only where topology doesn't apply (`longitudinal` is a single-road maneuver
by definition) or as a fallback while a report's topology is still
`needs_manual_review`. See `template_selector.py` and
`docs/topology_detection_report.md`.

## Template files

| File | Topology | Derived from | Used for scenario types (fallback / when topology doesn't decide) |
|------|----------|--------------|------------------------|
| `intersection_4way.xodr` | 4-way intersection | esmini `fabriksgatan.xodr` [esmini v2.57.0] | `turning`, `crossing`, `other` |
| `straight_road.xodr` | Straight road 500 m | esmini `straight_500m.xodr` [esmini v2.57.0] | `longitudinal` |

## Bike lane modifications

Both active templates were modified to add a right-side biking lane adjacent to the
outermost driving lane on each approach road and each connecting road:

- **Width:** 1.25 m per ERA 2010
  [FGSV, *Empfehlungen für Radverkehrsanlagen*, 2010]
- **Type:** `type="biking"`
- **Shoulder/sidewalk lanes** renumbered outward to preserve adjacency order
- **Junction laneLinks** added for all right-side-incoming biking movements
  (`intersection_4way.xodr` only)

## Source attribution

- esmini v2.57.0, github.com/esmini/esmini (BSD 3-Clause)
- ERA 2010 bike lane width reference: FGSV, *Empfehlungen für Radverkehrsanlagen*, 2010

## Usage

Template selection is defined in `template_selector.py`. The selected template is
copied into each scenario's output directory and referenced by the generated `.xosc`
via the `<LogicFile filepath="...">` element.
