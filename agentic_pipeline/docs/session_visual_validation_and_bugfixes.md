# Pipeline Visual/Behavioral Validation — Session Documentation

**Date:** 2026-08-28 to 2026-08-30
**Scope:** `generate_scenario.py` (Agent 3's deterministic scenario-generation stage) and `report_loader.py` (corpus loading). No changes to `extract_scenario.py` (Agent 1, the pipeline's only LLM call), `osm_enrichment.py` (Agent 2), or `complete_parameters.py` (Agent 3's parameter-completion stage).

## 1. Why this session happened

The prior session's structural (`test_extract_scenario_schema.py`) and semantic (`test_semantic_correctness.py`) test suites were both green, but neither of those checks whether the generated `.xosc` scenarios actually *look* right when simulated — trajectory shape, timing, and whether a collision is visually plausible are not something an automated field-comparison test can verify. A full visual review pass in esmini (`review_generated.py`) found that most of the 19 generated scenarios had a real, visible problem, even though every automated gate was passing. This session is the process of finding and fixing the root causes of those problems, one category at a time, each iteration verified against real esmini output.

## 2. Root causes found and fixed, in the order they were discovered

### 2.1 Cyclist junction maneuver hardcoded
`_generate_straight_crossing_openscenario`'s junction-template branch built the cyclist's turn using a hardcoded `"go_straight"` literal, never reading the cyclist's actual extracted `maneuver` field. A cyclist reported as turning left (crossing_05, crossing_06) always rendered going straight through the junction.

**Fix:** read the maneuver via the existing `_maneuver_kind()` normalizer, same pattern already used for the motor vehicle elsewhere in the file.

### 2.2 Trajectory start disconnected from teleport position
The `FollowTrajectoryAction`'s first waypoint was computed from a synthetic `road_length_m / 2`-based formula left over from an earlier design, not from the actor's real, clamped `initial_s_m`. This could place the trajectory's starting point up to ~20–77m away from where the `TeleportAction` actually placed the vehicle, causing a visible jump at simulation start.

**Fix:** derive the trajectory's starting distance-to-impact directly from the real junction-connector geometry and the actor's real `initial_s_m`.

### 2.3 Cyclist and car aiming at two different "impact" points
Each vehicle's "impact" location was computed independently as the midpoint of its *own* connector road. Once each vehicle's real lane offset was applied, these two points could be 1–4.5m apart — close enough to look plausible on paper, far enough that the vehicles' bounding boxes never actually overlapped in the simulation. This was the direct cause of "no collision happened" on most `go_straight`/`go_straight` crossing reports.

**Fix:** added `_find_junction_crossing_point()`, which finds the real nearest-approach point between the two vehicles' actual sampled paths (restricted to each path's own junction span) and uses that single shared point for both.

### 2.4 Speed/timing decoupled from each actor's real configured speed
The trajectory's waypoint times were fixed offsets from a flat `conflict_time_s` constant, independent of each actor's real speed or real distance. Three designs were tried, in order, each rejected on live review:
1. A fixed cruise window followed by a fixed-distance final-approach segment — produced a real, verified speed discontinuity right at impact (e.g. a car observed jumping from ~1.8 m/s to ~8.3 m/s in the final 0.3s).
2. Driving at real speed then holding position near the junction — removed the speed jump but made the vehicle visibly freeze in place for several seconds, rejected on sight as equally unrealistic.
3. **Final design:** one constant speed for the entire approach, with the shared arrival time computed from each actor's own real distance/speed. Whichever actor has slack (would naturally arrive early) stays parked at its own real starting position until it needs to begin driving continuously, at its exact real speed, to arrive on schedule.

### 2.5 Sparse curve sampling
The turning connector's curvature was represented by only 1–2 fixed "near-impact marker" distances. Once fix 2.2 made the trajectory's starting distance realistically large, the single long straight-line segment from the start point to the first marker cut directly through the real curve, skipping almost the entire turn — a bicycle or vehicle appeared to travel in a straight line, then snap into the curve only in the last couple of meters.

**Fix:** `_curve_markers()` now includes every real sampled point along the connector between the junction entry and the impact point, so the rendered polyline follows the actual curvature.

### 2.6 Extended starting position for actors with timing slack
Even with fix 2.4's parking behavior, a fast/close actor could still end up parked for most of the scenario's duration while a slow/far actor used nearly all the available time. Where the real road's own modeled length allows it, the actor with slack is now moved farther back along its real road so it can drive continuously at its real speed for a larger fraction of the approach, rather than parking as long. The corresponding `missing_parameters` provenance entry is updated in place so the recorded value always matches what's actually rendered (the same consistency invariant `test_constants_provenance.py` already enforced for the existing `_clamp_initial_s_to_real_road` correction).

### 2.7 Non-junction ("straight_road.xodr") crossing and longitudinal scenarios rendered off the real road
Scenarios that resolved to the single-road `straight_road.xodr` template (crossing_01, crossing_04, longitudinal_01, longitudinal_02) were positioned via `_world_from_road_s_t`, a fully synthetic coordinate system that assumes a road *centered at the origin*. The real template's road actually starts at `(0,0)`, heading `0`, and extends to `(500,0)` — confirmed directly from the `.xodr` file. Depending on each report's real distance and heading values, this could place both actors 100+ meters off the real modeled pavement, visible in esmini as vehicles floating over open ground or the auto-generated ground plane instead of on the road.

**Fix:** both generators now use the template's real road geometry (`_road_world_point`, reading the actual `<planView>` geometry and lane-offset data) instead of the synthetic formula.

### 2.8 Cyclist/car roles reversed relative to the report's own semantics
Separately from 2.7, the code had the cyclist always using the "normal, straight" heading and the car always using the synthetic "crossing" heading — backwards from both this pipeline's own documented convention (`complete_parameters.py`: *"crossing" scenarios are defined as the cyclist's path crossing the vehicle's straight path*) and the individual reports' own extracted data (crossing_04's `conflict_mechanism` field literally reads `cyclist_crosses_vehicle_path_from_median`).

**Fix:** the car now drives normally along the real road; the cyclist approaches from the side, at a short (`speed × ~4s`), real-speed-derived distance, matching an "entering the roadway" / "crossing from the median" maneuver. On request, the cyclist's crossing angle is also snapped to exactly perpendicular (90°) relative to the car's real heading, for a clean T-bone visual (crossing_01, crossing_04).

### 2.9 Turning conflicts placed both actors on the same street
The "turning" scenario generator placed the motor vehicle and the cyclist on the *same* entry road (different lanes of the same street), with the motor vehicle turning across the cyclist's straight path. For turn_left maneuvers specifically, this produced a geometric degenerate case: the real nearest-approach point between the two paths could legitimately fall right at the very start of the motor's turn lane, before any curvature had happened — meaning the "collision" occurred with zero visible turning motion (turning_08: "no real left turn ... no collision happened").

Two intermediate fixes were tried and superseded:
- Forcing the geometric search to require some minimum turn-progress before accepting an impact point — rejected: this only widens the gap between the two paths further (verified: even 15% forced progress increases the gap from 2.37m to 2.70m; 40% needs 4.18m), making "no collision" worse, not better.
- Letting the motor vehicle continue curving for a short distance *after* the scripted collision (representing a vehicle not stopping dead on contact) — implemented and scoped to fire only when the pre-impact turn showed near-zero progress, but superseded by the fix below.

**Final fix**, following a direct correction from the user (a real "turning" conflict — e.g. a car on one street turning into a cyclist on the cross street — involves two *perpendicular* real streets, not one street's two lanes): the motor vehicle is now placed on the perpendicular entry road (the same real "secondary approach" road the crossing generator's car uses), while the cyclist stays on its own entry road. This was deliberately scoped to `turn_left` maneuvers only, at the user's explicit direction, since applying it to `turn_right` maneuvers as well changed geometry that had already been reviewed and confirmed correct. Verified: turning_08's (turn_left) collision gap dropped from 2.37m to 0.03m with a real, visible turn; all `turn_right` reports were confirmed to render byte-for-byte identically (ignoring the generation timestamp) to their previously-confirmed state.

A data-integrity issue was found and repaired in the course of this fix: an earlier, broader version of the change had clamped `turn_right` motor vehicles' `initial_s_m` down to the shorter perpendicular road's real length and persisted that clamped value into the corresponding `.enriched.json` file. Reverting the code alone did not undo this, since the clamp is applied to whatever value is currently stored. The correct pre-clamp values (still intact in the affected reports' own `missing_parameters` provenance records) were used to restore the `.enriched.json` files before the final regeneration.

## 3. Corpus change: turning_07 dropped

`turning_07`'s extracted maneuver is `turn_right_into_parking` — a parking-lot access conflict. Neither `straight_road.xodr` nor `intersection_4way.xodr` models a parking-lot access geometry; both templates represent exactly one real driving lane and one real biking lane per direction (see `docs/modeling_assumptions.md`). This is a template limitation, not a bug: no amount of parameter tuning makes this report's real topology representable.

`report_loader.py` now filters `turning_07` out of the active corpus via an `EXCLUDED_SCENARIO_IDS` set, applied *after* scenario IDs are assigned by position, so `turning_08`/`turning_09`'s IDs did not shift. The report's original text is left untouched in `docs/manual_classification_reference.md` as a historical record — it was excluded from processing, not deleted from the source.

The active corpus is now **18 reports** (previously 19).

## 4. Code cleanup

Three code paths were confirmed to have zero coverage across all 18 active reports (verified by directly checking each report's actual gating condition, not by inspection alone) and removed:

- The entire non-junction (`straight_road.xodr`) trajectory model for "turning" scenarios in `generate_openscenario` — every active turning report resolves to `intersection_4way.xodr`; `turning_07` was the only report that needed the removed path. Replaced with an assertion that fails with a clear message if a future report ever needs it.
- The "parked motor vehicle (dooring)" branch in the same function — all 8 active turning reports have a moving motor vehicle that starts before the conflict point.
- The "car turns left" branch (`car_path == "turn_left_from_secondary_to_primary"`) in the non-junction crossing generator — `car_path` is `None` for every active crossing report.

All 18 reports were regenerated after each removal and diffed byte-for-byte (excluding the generation timestamp) against the pre-cleanup output: zero differences in every case, confirming the removed code was never exercised.

## 5. Test coverage added

Ten new offline regression tests (no LLM, no network) were added this session, each targeting one of the fixes above with a concrete before/after check against the specific bug it guards:

- `test_cyclist_junction_maneuver.py`
- `test_junction_trajectory_teleport_alignment.py`
- `test_junction_shared_crossing_point.py`
- `test_junction_speed_consistent_timing.py`
- `test_junction_extended_start_provenance.py`
- `test_straightroad_crossing_teleport_match.py`
- `test_straightroad_crossing_speed_consistent_timing.py`
- `test_straightroad_crossing_real_road_geometry.py`
- `test_longitudinal_lane_change.py`
- `test_turning_perpendicular_streets.py`

Several of these were explicitly verified to fail against the pre-fix code and pass against the fix, rather than being written to simply match whatever the new code produced.

All pre-existing offline suites (`test_extract_scenario_schema.py`, `test_semantic_correctness.py`, `test_lane_type_safety.py`, `test_agent1_preservation.py`, `test_car_path_maneuver.py`, `test_constants_provenance.py`, `test_speed_estimation.py`, `test_speed_evidence_backfill.py`) remained green throughout.

## 6. Final validation status

All 18 active reports were individually watched in esmini (`review_generated.py`) and confirmed by the user as visually and behaviorally correct — trajectories, collision points, and timing match the underlying police report narratives. A final automated sweep across all 18 reports' generated `.xosc` files (checking for speed discontinuities and impact-point gaps) found zero remaining issues.

**Load-bearing pipeline claim, unaffected by this session:** exactly one LLM call happens anywhere in the pipeline, per report — Agent 1's extraction. Nothing in `extract_scenario.py`, `osm_enrichment.py`, or `complete_parameters.py` was touched this session; every fix was in the deterministic, zero-network scenario-rendering stage. Regenerating the corpus locally (without re-running Agent 1 on HPC) is therefore equivalent to a full pipeline re-run for this stage.
