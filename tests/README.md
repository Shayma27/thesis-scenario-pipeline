# tests/

19 regression tests, run individually (`python3 tests/test_name.py`), no
test runner required. All run fully offline against the frozen `data/`
snapshots — no LLM or network call — **except** `test_feedback_geometry.py`,
which needs a live LLM connection (it exercises the feedback-correction
loop, not the main pipeline).

`gold_reference.py` in this folder is not a test itself — it's the
hand-verified, independently cross-checked answer key that
`test_semantic_correctness.py` checks the extraction corpus against.

What's covered:
- **Extraction schema & semantics** — `test_extract_scenario_schema.py`,
  `test_semantic_correctness.py` (the 19/19 gold-reference agreement claim)
- **Provenance invariants** — `test_agent1_preservation.py`,
  `test_constants_provenance.py` (Stage 1's output is never overwritten;
  every non-report-derived value is correctly labeled by source)
- **Lane/geometry safety** — `test_lane_type_safety.py`,
  `test_car_path_maneuver.py`, `test_cyclist_junction_maneuver.py`,
  `test_longitudinal_lane_change.py`, `test_turning_perpendicular_streets.py`
- **Junction-specific geometry** — `test_junction_extended_start_provenance.py`,
  `test_junction_shared_crossing_point.py`,
  `test_junction_speed_consistent_timing.py`,
  `test_junction_trajectory_teleport_alignment.py`
- **Straight-road-specific geometry** — `test_straightroad_crossing_real_road_geometry.py`,
  `test_straightroad_crossing_speed_consistent_timing.py`,
  `test_straightroad_crossing_teleport_match.py`
- **Speed defaults** — `test_speed_estimation.py`, `test_speed_evidence_backfill.py`
- **Feedback loop** — `test_feedback_geometry.py` (needs a live LLM)
