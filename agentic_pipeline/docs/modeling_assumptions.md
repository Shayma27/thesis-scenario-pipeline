# Modeling assumptions

Three deliberate scope decisions for the bachelor thesis. These are not bugs
— they are documented simplifications, applied consistently across the
pipeline. Only two OpenDRIVE templates exist and may be used:
`templates/straight_road.xodr` and `templates/intersection_4way.xodr`. No
other template file is introduced by any of the three assumptions below.

---

## Assumption 1 — `straight_road.xodr` reinterpreted as one-way for
## "longitudinal" scenarios

### What the file actually contains

`templates/straight_road.xodr` has a single `<laneSection>` with, from the
center lane (`id="0"`, a marking-only lane with no width) outward on each
side:

| side | lane id | type | width |
|---|---|---|---|
| left | `1` | driving | 3.07 m |
| left | `2` | biking | 1.25 m |
| left | `3` | shoulder | 1.68 m |
| left | `4` | border | 6.00 m |
| right | `-1` | driving | 3.07 m |
| right | `-2` | biking | 1.25 m |
| right | `-3` | shoulder | 1.68 m |
| right | `-4` | border | 6.00 m |

This is a standard **two-way road with exactly one driving lane per
direction** (lane `1` and lane `-1`), each with its own adjacent bike lane
(`2` / `-2`). It is *not* a multi-lane one-way road, and no such file
exists in `templates/` — the task explicitly rules out sourcing or creating
one. (`intersection_4way.xodr`'s four straight approach roads use the same
one-lane-per-direction pattern, plus a sidewalk lane per side that
`straight_road.xodr` doesn't have — see Assumption 2.)

### The reinterpretation

For scenarios classified `"longitudinal"` only (covers both overtaking and
lane-change reports — `extract_scenario.py`'s `scenario_type` definitions),
`straight_road.xodr` continues to be used unmodified, but lane `1` and lane
`-1` are treated as two **same-direction parallel lanes** instead of
opposing carriageways. Nothing in the `.xodr` file itself changes — this is
purely how the pipeline interprets and places actors on it, and it is
scoped to `scenario_type == "longitudinal"`:

- `complete_parameters.py`'s `_road_position_lane_id()` only returns the
  real lane id `1` for an explicit "leftmost lane" report position when
  `scenario_type == "longitudinal"`; every other scenario type keeps
  "leftmost" pinned to lane `-1` (the innermost lane on the participant's
  own side), so `intersection_4way.xodr`-based scenarios never place an
  actor on what is, there, a real opposing lane.
- `generate_scenario.py`'s `_world_position_from_lane_s()` previously used
  `abs(lane_id)` to compute the lateral trajectory offset, which collapsed
  lane `1` and lane `-1` onto the *same* y-coordinate (both are "lane index
  1"). It now uses the lane id's sign to place lane `1` on the real
  positive-t side and lane `-1` on the real negative-t side — the template's
  actual, physically adjacent geometry either side of the center-lane
  marking — so a lane change between them shows up as a real lateral move
  instead of a point on top of itself. This is a strict generalization:
  every other scenario type only ever uses negative lane ids, so their
  trajectories are numerically unchanged.

This directly represents `manual_classification_reference.md` report
**longitudinal_02** (Markgrafendamm): cyclist starts on the bike lane
(`-2`), changes left into the adjacent driving lane (`-1`) — already
representable without the reinterpretation, since bike lane and driving
lane sit on the same side. It is **required** for report
**longitudinal_01** (Alt-Biesdorf): the cyclist starts on "den linken der
drei Fahrstreifen" and changes to "den äußerst rechten Fahrstreifen" — two
literal driving lanes, which only exist in the template as lane `1`
(nominally opposing) and lane `-1`.

### Remaining simplification

`straight_road.xodr` has **one** real driving lane per direction, not
three. Report longitudinal_01's "linken der drei Fahrstreifen" /
"äußersten rechten Fahrstreifen" is mapped to the template's two available
driving lanes (`1` and `-1`) via the reinterpretation above — the exact
reported lane count/index ("2nd of three") is **not** modeled precisely.
This is a known, accepted limitation, not something the pipeline attempts
to fix by inventing extra lanes.

---

## Assumption 2 — cyclist position: extract from text, default only when
## unspecified

### What the templates can geometrically represent

Both templates model exactly **one** cycling facility per direction: a
lane of type `biking`, width **1.25 m**, directly beside the driving lane,
separated only by a `solid` road mark — no `<height>` element (unlike the
sidewalk lanes, see below), i.e. no curb or vertical separation. 1.25 m
matches the ERA 2010 FGSV **Schutzstreifen** width standard referenced in
project history. This is a painted, on-carriageway lane — it is **not** a
`getrennter Radweg` (physically separated cycle track), which would need a
curb/vertical offset that neither template models for the bike lane. It can
reasonably stand in for a generic "bike facility off the main traffic lane"
in a pinch, but not for a genuinely separated track.

`intersection_4way.xodr` additionally has a `sidewalk`-type lane per side
(width 2.0 m, with `<height>` elements — a real curb), which
`straight_road.xodr` does not have at all (its outer lanes are `shoulder`
and `border`, not `sidewalk`). Because the cyclist-position policy
(`cyclist_lateral_position` / `initial_lane_id`) has to work the same way
regardless of which template a report ends up on, and `straight_road.xodr`
has no sidewalk lane to place a cyclist on, a **Gehweg position is treated
as unrepresentable at the policy level for both templates** — see the
table below — rather than being representable on one template and silently
falling back on the other.

**Summary of what the position policy can place a cyclist on:**

| facility as described in text | representable? | lane used |
|---|---|---|
| `bike_lane` (Schutzstreifen/Radfahrschutzstreifen) | yes — exact match | the template's biking lane |
| `roadway_mixed` (Nebenfahrbahn) / explicit driving-lane language | yes | the driving lane |
| `separated_cycle_track` (baulich getrennter Radweg) | **no** — no curbed/separated lane modeled | falls back to the biking lane |
| `shared_foot_cycle_path` (gemeinsamer Geh-/Radweg) | **no** — no distinct shared-path lane in the position policy | falls back to the biking lane |
| `sidewalk` (Gehweg) | **no** — not wired into the position policy on either template (and doesn't exist in `straight_road.xodr` at all) | falls back to the biking lane |
| `median_strip` (Mittelstreifen) | **no** — a median/central refuge strip has no OpenDRIVE counterpart in either template | falls back to the biking lane |
| not mentioned | — | default: the biking lane |

### Per-report position table

Source: `docs/manual_classification_reference.md`'s 19 reports (numbered
1–19 there; `scenario_id` below follows the naming already established in
`docs/topology_detection_report.md`).

| scenario_id | position as described in text | representable? | lane used |
|---|---|---|---|
| turning_01 (Salvador-Allende-Str.) | "baulich getrennter Radweg" → separated_cycle_track | **no — flagged** | biking lane (fallback) |
| turning_02 (Mollstraße) | not specified | — | biking lane (default) |
| turning_03 (Gutschmidtstraße) | "Radverkehrsfuhrt" names the collision spot, not the cyclist's riding facility — not specified | — | biking lane (default) |
| turning_04 (Spandauer Damm) | not specified | — | biking lane (default) |
| turning_05 (Kiefholzstraße) | not specified | — | biking lane (default) |
| turning_06 (Schönhauser Straße) | not specified | — | biking lane (default) |
| turning_07 (Malteserstraße) | "baulich von der Fahrbahn getrennter Radweg" → separated_cycle_track | **no — flagged** | biking lane (fallback) |
| turning_08 (Reinickendorfer Straße) | not specified (cyclist ran a red light; Assumption 3 — signal state itself is not modeled either way) | — | biking lane (default) |
| turning_09 (Müggelheimer Damm) | "gemeinsamer Geh- und Radweg" → shared_foot_cycle_path | **no — flagged** | biking lane (fallback) |
| crossing_01 (Mühlenstraße) | cyclist enters from "Gehweg" (sidewalk) | **no — flagged** | biking lane (fallback) |
| crossing_02 (Rathausstraße) | not specified | — | biking lane (default) |
| crossing_03 (Müggelheimer Damm/Waldnesselweg) | not specified | — | biking lane (default) |
| crossing_04 (Landsberger Allee) | "vom begrünten Mittelstreifen kommend" → median_strip | **no — flagged** | biking lane (fallback) |
| crossing_05 (Storkower Straße) | not specified | — | biking lane (default) |
| crossing_06 (Oranienburger Straße) | "Radfahrschutzstreifen" → bike_lane | **yes** | biking lane (exact match) |
| crossing_07 (Luisenplatz) | not specified | — | biking lane (default) |
| crossing_08 (Unter den Eichen) | "rechten Fahrstreifen der Nebenfahrbahn" → roadway_mixed | **yes** | driving lane |
| longitudinal_01 (Alt-Biesdorf) | explicit driving-lane language ("linken der drei Fahrstreifen" → "äußersten rechten Fahrstreifen"), not a bike facility | **yes, via Assumption 1's reinterpretation** (see remaining simplification above) | driving lanes `1` → `-1` |
| longitudinal_02 (Markgrafendamm) | "Schutzstreifen" → bike_lane, then "nach links in den ... rechten Fahrstreifen" | **yes** — bike lane and driving lane are already adjacent on the same side | bike lane `-2` → driving lane `-1` |

**Flagged/unrepresentable: 5 of 19** (turning_01, turning_07, turning_09,
crossing_01, crossing_04). All five fall back to the template's biking
lane and are recorded per-scenario in `missing_parameters` with
`source: "unrepresentable_bike_facility_geometry"` (see
`complete_parameters._flag_unrepresentable_bike_facility`) — they are not
silently misrepresented as an exact match.

### Implementation

- `extract_scenario.py`: the extraction schema gained
  `participants[].road_position` (explicit "on which numbered driving
  lane" language, e.g. "den linken/äußerst rechten Fahrstreifen") and
  `road_context.bike_facility_position` (explicit facility side), both
  `null` unless the report text says so explicitly. `bike_facility_type`
  already existed and is unchanged.
- `complete_parameters.py`'s `_cyclist_lane()` (and, symmetrically,
  `_motor_lane()`) now check the participant's own `road_position` first,
  then fall back to `bike_facility_type` (bike lane vs. driving lane vs.
  unrepresentable-so-bike-lane), then to the template's default bike lane
  only when nothing is specified at all. `osm_enrichment.py`'s
  `_apply_cyclist_position_policy()` / `_apply_cyclist_lane_id()` already
  implemented the equivalent report-text-first priority for OSM-enriched
  runs; it is unchanged by this task (see the separate, out-of-scope
  Agent 2/Agent 3 ordering issue noted below).

---

## Assumption 3 — traffic lights are not modeled

Any mention of a traffic light / signal state (LSA, "Rotlicht",
"rote/grüne Ampel") in a report is extracted for other fields (e.g.
`traffic_rule_status`) as before, but is never itself captured or used to
influence the generated scenario. Removed:

- `extract_scenario.py`: the `road_context.traffic_light_present` field
  and the "LSA" extraction rule that fed it.
- `osm_enrichment.py`: the Overpass `highway=traffic_signals` node query,
  `_extract_road_context()`'s collection of signal nodes, the
  `traffic_signals_nearby` context field, and `_apply_osm_context()`'s
  merge of that into `road_context.traffic_light_present`.
- `pipeline.py`: the `traffic_light_present` / `traffic_signals_nearby`
  values returned from the `extract_scenario` and `query_osm` tool
  results, the extraction-summary print line, and the `query_osm` tool's
  "traffic signal presence" description text.

This field was already dead weight before removal — nothing in
`generate_road.py` or `generate_scenario.py` ever read
`traffic_light_present` to place an OpenDRIVE `<signal>` or gate any
timing/behavior; both templates' `<signals>` blocks are empty. Removing
the extraction/merge logic makes that explicit instead of leaving an
unused, confusing field in the enriched JSON.

---

## Out of scope

Per task instructions, this pass does **not** fix the separate Agent
2/Agent 3 ordering issue: `osm_enrichment.py`'s `_apply_cyclist_lane_id()`
runs before `complete_parameters.py` has created the `actors["cyclist_1"]`
entry, so it is currently a no-op and `complete_parameters._cyclist_lane()`
is the function actually deciding `initial_lane_id` in practice (confirmed
above). That ordering fix is tracked separately.
