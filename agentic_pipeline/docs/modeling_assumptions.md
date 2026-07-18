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
| `roadway_mixed` (Nebenfahrbahn) describing a genuine **separate parallel carriageway** (dual-carriageway boulevard, not extra lanes of one road) | **no** — neither template models a separated parallel carriageway, only one continuous roadway | falls back to the biking lane |
| not mentioned | — | default: the biking lane |

Note the last two rows both start from `bike_facility_type = "roadway_mixed"` text — the difference is what "Nebenfahrbahn" turns out to mean at that specific location once checked against a map. It is not something the extraction schema can currently distinguish on its own (see crossing_08 below), so this is handled as a scoped, per-report manual override rather than a change to how "roadway_mixed" is interpreted generally.

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
| crossing_08 (Unter den Eichen) | "rechten Fahrstreifen der Nebenfahrbahn" → roadway_mixed | **no — flagged** (see below) | biking lane (fallback) |
| longitudinal_01 (Alt-Biesdorf) | explicit driving-lane language ("linken der drei Fahrstreifen" → "äußersten rechten Fahrstreifen"), not a bike facility | **yes, via Assumption 1's reinterpretation** (see remaining simplification above) | driving lanes `1` → `-1` |
| longitudinal_02 (Markgrafendamm) | "Schutzstreifen" → bike_lane, then "nach links in den ... rechten Fahrstreifen" | **yes** — bike lane and driving lane are already adjacent on the same side | bike lane `-2` → driving lane `-1` |

**crossing_08 reclassified (this session):** originally listed as
representable — "roadway_mixed" was read as "cyclist rides the general
driving lane, no dedicated facility," same as crossing_08's own extraction
rule ("Nebenfahrbahn" = roadway_mixed) implies for any other report.
Manually re-verified against a satellite map: "Unter den Eichen" at this
location is a genuine dual carriageway (Hauptfahrbahn + Nebenfahrbahn) —
two physically separate parallel roadways with a median/verge between
them, not a single road with multiple lanes. **The report's "rechten
Fahrstreifen der Nebenfahrbahn" refers to this separate secondary
carriageway, which neither `straight_road.xodr` nor
`intersection_4way.xodr` models (both have only one continuous roadway
with an adjacent bike lane, no concept of a separated parallel
carriageway) — manually verified against satellite map, 2026-07-17.**
This is scoped to crossing_08 only (`complete_parameters.py`'s
`_CROSSING_08_OVERRIDE_SCENARIO_ID`) — it does not change how
"roadway_mixed" is interpreted for any other report, since for most
reports "Nebenfahrbahn"/"roadway_mixed" genuinely does just mean "ride the
general lane." Flagged under its own source label rather than the generic
bike-facility one, since the mismatch is about carriageway topology, not
cycling-facility geometry — see Implementation below.

**Flagged/unrepresentable: 6 of 19** (turning_01, turning_07, turning_09,
crossing_01, crossing_04, crossing_08). The first five fall back to the
template's biking lane and are recorded per-scenario in
`missing_parameters` with `source:
"unrepresentable_bike_facility_geometry"` (see
`complete_parameters._flag_unrepresentable_bike_facility`); crossing_08
falls back the same way but is recorded with `source:
"unrepresentable_carriageway_geometry"` (see
`complete_parameters._flag_unrepresentable_carriageway_geometry`). None
of the six are silently misrepresented as an exact match.

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
- `complete_parameters.py`'s `_cyclist_lane()` also carries a single-report
  manual override, keyed on `data["source"]["source_id"] ==
  "crossing_08"` (the stable id `report_loader.py` synthesizes for this
  report — same pattern already used for `osm_enrichment.py`'s
  `turning_06` heading override): when this report's `bike_facility_type`
  is `"roadway_mixed"`, it is treated as unrepresentable instead of "ride
  the driving lane," and flagged via the new
  `_flag_unrepresentable_carriageway_geometry()` /
  `source: "unrepresentable_carriageway_geometry"` (kept separate from
  `_flag_unrepresentable_bike_facility()` /
  `unrepresentable_bike_facility_geometry`, since this is a carriageway-
  topology mismatch, not a cycling-facility-geometry mismatch). No other
  report's `"roadway_mixed"` handling changes.

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

Per task instructions, this pass did **not** fix the separate Agent
2/Agent 3 ordering issue: `osm_enrichment.py`'s `_apply_cyclist_lane_id()`
runs before `complete_parameters.py` has created the `actors["cyclist_1"]`
entry, so it is currently a no-op and `complete_parameters._cyclist_lane()`
is the function actually deciding `initial_lane_id` in practice (confirmed
above).

**Update (later session):** the equivalent bug for the *motor vehicle's*
turning-lane assignment — `osm_enrichment._apply_turning_vehicle_lane_id()`
running during Agent 2 before Agent 3 created the actor entry it needed —
has since been fixed by moving that function into
`complete_parameters.py`, called right after the motor vehicle's actor
entry exists. See that commit for details.

**Update (yet another later session):** the cyclist-side instance above
(`_apply_cyclist_lane_id()`) has also been moved into
`complete_parameters.py`, called right after `_cyclist_lane()` sets the
cyclist's actor entry. Unlike the motor-vehicle fix, this one could
**not** simply mirror the same "unconditional overwrite" pattern:
`_apply_cyclist_lane_id()`'s own lane-selection logic predates Assumption
2 entirely (no representability flagging, no crossing_08 override, and
its own "nothing matched" fallback is the driving lane, not the bike
lane — the exact hardcoded-default behavior Assumption 2 replaced).
Direct comparison against all 19 reports showed 15 of 19 would have
silently regressed to the driving lane under an unconditional overwrite.
So its write is now guarded the same way every other field in
`complete_parameters()` already is (`_setd`-style — only if not already
set): `_cyclist_lane()` runs first and remains authoritative; the moved
function's guard clause is now evaluated against a real actor instead of
always firing, but its write only actually takes effect (and is only
recorded in `missing_parameters`) on the reports where its formula
happens to agree with `_cyclist_lane()`'s. See that commit for the exact
per-report comparison.

**Update (readiness audit before HPC testing):** a general audit of
`agentic_pipeline/` (pyflakes + a real, offline end-to-end generation run
of all 19 reference reports — not just the parameter-completion logic in
isolation) turned up three more issues, all now fixed:

1. **A third instance of the same Agent 2/Agent 3 ordering bug.**
   `osm_enrichment._apply_osm_context()` derived `car_1`'s
   `initial_speed_mps` for "crossing" reports from a nearby OSM `maxspeed`
   tag (65% of the limit, as an intersection-approach speed), gated on
   `"car_1" in actors` — always `False`, same root cause as the other two.
   Unlike the cyclist-lane case, nothing in `complete_parameters.py`
   already computes an equivalent value this could conflict with, so it
   was moved the same way as the turning-vehicle fix: now
   `complete_parameters._apply_osm_derived_crossing_speed()`, called
   before the generic per-type/maneuver speed default's `_setd()`, reading
   the already-computed `maxspeed_kmh` from `data["osm_context"]["derived"]`
   rather than re-deriving it. The original hardcoded `"car_1"` (not the
   actual actor id) is preserved unchanged — every "crossing" report in
   this corpus uses a car.
2. **Wrong `initial_road_id` for every actor on `straight_road.xodr`.**
   `complete_parameters.py` always set `initial_road_id = 0` for
   non-crossing actors — correct for `intersection_4way.xodr` (its
   primary approach really is road `0`), but `straight_road.xodr`'s one
   real `<road>` element has `id="1"`, not `0` (verified directly against
   the template file). This affects every `"longitudinal"` report (always
   `straight_road.xodr`) and any other report whose topology resolves to
   `"midblock"` instead of `"4way_junction"` — several turning/crossing
   reports do, per the topology table above. `complete_parameters.py`
   can't get this right on its own: template selection (and the topology
   detection it depends on) happens later, in `pipeline.py`'s
   `_tool_generate_scenario`, after `complete_parameters()` already ran.
   Fixed at the one place with definitive knowledge of the actually
   -selected template — `generate_scenario.py`'s `_resolve_road_id()`,
   called from both `generate_openscenario()` and
   `_generate_straight_crossing_openscenario()` right before building each
   actor's `LanePosition`.
3. **Unbounded `initial_s_m` against `intersection_4way.xodr`'s real,
   short roads.** `complete_parameters.py` computes `initial_s_m` against
   a synthetic 100 m road — fine for `straight_road.xodr` (500 m, always
   comfortably larger), but `intersection_4way.xodr`'s individual roads
   are real and geometrically varied (the secondary approach is only
   ~16.9 m). An unclamped `initial_s_m` from the synthetic assumption
   could exceed a short real road's actual length, failing OpenDRIVE
   lane-position validation. Fixed alongside (2), in the same two call
   sites: `generate_scenario._clamp_initial_s_to_real_road()` parses the
   real selected road's length directly from the template file (reusing
   the existing `_parse_xodr_road_geometry()`/`_road_total_length()`
   helpers `_junction_maneuver_samples()` already relies on for the
   trajectory itself) and clamps to it — the same defensive philosophy
   `_junction_maneuver_samples()` already applies internally
   (`approach_m = min(approach_margin_m, entry_length)`), now applied
   consistently to the teleport's starting position too.

Verified via a genuine end-to-end run (real templates copied, real
`.xodr`/`.xosc` files written, real `validate_outputs.py` checks — not
just the parameter-completion functions in isolation) for all 19
reference reports against their actual topology results from
`docs/topology_detection_report.md`: all 19 now produce valid output.
Before these three fixes, `longitudinal_01`/`longitudinal_02` and
`crossing_02`/`03`/`06`/`07`/`08` (the ones resolving to
`intersection_4way.xodr`'s real junction) failed validation.

A few pre-existing, unrelated dead-code/style items were also cleaned up
in passing (unused local variables and one unused import flagged by
pyflakes) — cosmetic only, no behavior change. `test_feedback_geometry.py`'s
fixture JSON (`output/agentic/20260602_mueggelschloesschenweg/...`) is
stale — `scenario_type: "right_turn_conflict"` predates the 4-category
taxonomy migration (commit 715d56b) — noted but not regenerated, since it
requires a live LLM run and doesn't affect the pipeline itself.
