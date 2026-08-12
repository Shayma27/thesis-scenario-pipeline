Briefing for a new Claude Code session — Agent 3 (Parameter Completion) → LLM-based

I'm continuing a bachelor's thesis project: an agentic pipeline that converts
raw German-language Berlin police accident reports (car/truck vs. cyclist
collisions) into ASAM OpenDRIVE + OpenSCENARIO 1.3 simulation files, via
`scenariogeneration`. The pipeline has three conceptual stages, referred to
as Agent 1/2/3. This is a continuation of prior work — read the files below
before proposing anything; don't start from a blank assumption of the
architecture.

## Current state of the pipeline (as of this handoff)

- **Agent 1 — `extract_scenario.py`** (semantic extraction via a local LLM,
  `Llama 3.1 8B Instruct` served through vLLM). **Frozen. Gold-verified
  19/19 against `gold_reference.py`/`test_semantic_correctness.py`. Do not
  modify this file or its prompt/schema** unless I explicitly ask — it took
  a full session to get to 19/19 and I don't want it touched incidentally.
  Its output for all 19 reference reports is saved untouched in `input/*.json`
  — that's the frozen corpus everything downstream is built/tested against.

- **Agent 2 — `osm_enrichment.py`** (OSM/Nominatim/Overpass enrichment).
  Just went through a full hardening pass this session: fixed multiple real
  geocoding bugs (verified against actual OSM tag data, not just asserted),
  added 9 documented manual topology overrides, fixed a lane-placement
  correctness bug, fixed a matching trajectory-offset bug, and added a
  structural invariant + test guaranteeing it can never silently overwrite
  an Agent-1 field. See `docs/thesis_section_osm_enrichment.md` for the full
  writeup — read it, it explains a lot of pipeline-wide conventions and
  constraints that still apply to Agent 3. This stage is stable now; don't
  casually modify it, but it's not frozen the way Agent 1 is.

- **Agent 3 — `complete_parameters.py`** (parameter completion). **This is
  what I want to work on next, and it's the most important part of my
  thesis.** Right now it is **pure deterministic Python** — flat defaults
  from `defaults.py` (e.g. a car's turning speed is always the same
  constant), simple per-scenario-type formulas, no LLM involved at all. I
  want to rebuild/extend this stage to use an LLM instead of blind
  deterministic defaults. Read `complete_parameters.py` in full before
  proposing anything — you need to know exactly what it currently fills in
  and how, since any replacement needs to at least cover the same ground.

## Why an LLM here, and what "better" means

A prior review (an external Codex-based audit, referenced in earlier
sessions) suggested this design direction for Agent 3: **report facts →
validated OSM constraints → deterministic geometry → constraint solving →
documented ranges/defaults**, citing the SoVAR paper's approach of
representing genuinely missing accident information as **structured
constraints and generalizable parameterization**, rather than inventing one
concrete value and presenting it as fact. That's the spirit of what I want:
not "guess a number," but reason about what's actually knowable from the
report + OSM data, what's genuinely uncertain, and represent that
honestly — likely via the same `missing_parameters` provenance pattern
already used throughout the pipeline (`{parameter, value_used, source,
reason}` — see `osm_enrichment.py`/`complete_parameters.py` for the existing
convention; reuse it, don't invent a parallel one).

## Hard constraints — do not violate these, they caused real bugs this session

1. **Both OpenDRIVE templates have exactly one real driving lane and one
   real biking lane per direction, at fixed lane IDs — always.** Never more,
   regardless of what a report or OSM implies about lane count. The bug just
   fixed this session was exactly this: lane-ID arithmetic that scaled with
   a reported lane count, silently placing actors on sidewalk/border lanes
   that aren't driving lanes. If the new Agent 3 reasons about "which lane,"
   it must resolve to one of the template's real lanes, never a synthesized
   one. See `docs/modeling_assumptions.md`, Assumption 1, and
   `test_lane_type_safety.py` (keep this test passing).

2. **There are two separate position representations that must stay
   consistent**: the actor's "teleport" (`initial_lane_id`/`initial_road_id`/
   `initial_s_m`, resolved via the template's real OpenDRIVE geometry) and
   its "trajectory" (a separately-computed `WorldPosition` polyline in
   `generate_scenario.py`). A second bug this session was exactly these two
   disagreeing. Any parameter that affects position needs to flow into both
   consistently.

3. **Only two templates exist**: `templates/straight_road.xodr` (a two-way
   road, reinterpreted as same-direction lanes only for `scenario_type ==
   "longitudinal"`) and `templates/intersection_4way.xodr` (a real 4-way
   junction). No new template may be introduced — this is a fixed project
   constraint, not a current limitation to solve. `template_selector.py`
   picks between them.

4. **`docs/modeling_assumptions.md`'s three assumptions are deliberate,
   documented simplifications** — not bugs to "fix" by inventing new
   geometry: (1) the longitudinal same-direction reinterpretation, (2) only
   one representable cycling-facility geometry (a flush painted lane, not a
   curbed/separated track — anything else falls back to it, flagged), (3)
   traffic-light/signal state is never extracted or modeled, even if the
   report mentions it (verified this holds even under direct temptation in
   Agent 1's prompt). If the new Agent 3's LLM reasoning produces something
   outside these bounds, it should degrade to the representable fallback and
   record why — not silently violate them.

5. **`validate_outputs.py` only checks that a referenced lane ID exists on
   the road — not its type.** Don't trust it to catch a class-of-bug like
   #1 above; write a dedicated test if you introduce a new failure mode,
   the way `test_lane_type_safety.py` was added this session.

## Working conventions from this session — please continue these

- **Investigate and verify before fixing.** Every fix this session was
  checked against real, independent ground truth (actual OSM tag data
  fetched directly, or the user's own map inspection) before being called
  done — not just asserted. Do the same here: if the LLM-based Agent 3
  produces a value, have a way to sanity-check it against something real,
  not just "the LLM said so."
- **Prefer general, root-cause fixes over narrow patches.** When a
  per-report override truly is necessary, label explicitly whether it's a
  verified fact, a pragmatic best-available choice, or an unverified best
  guess — never blur these together (see `_MANUAL_TOPOLOGY_OVERRIDES` in
  `osm_enrichment.py` for the pattern).
- **Every change needs a regression test**, ideally fast and offline (no
  live network/LLM dependency for CI). Existing suite to keep green:
  `test_extract_scenario_schema.py`, `test_semantic_correctness.py`,
  `test_agent1_preservation.py`, `test_lane_type_safety.py`. An LLM-based
  Agent 3 will need its own test strategy — probably checking structural
  bounds/consistency rather than exact values, since LLM output won't be
  bit-identical run to run even at temperature 0 in principle. Think about
  this explicitly before implementing, and propose it to me.
- **Reason and propose before implementing.** Read the relevant code first,
  explain what you found and what you're proposing, and get my confirmation
  before writing a lot of code — especially for the initial design of how
  the LLM fits into this stage (what it decides vs. what stays
  deterministic, prompt/schema structure, how uncertainty is represented).
  I've been happy with this pattern this session; don't skip straight to
  code.
- **Don't remove or fight OSM enrichment.** It was evaluated for removal
  this session and kept, on real evidence — read
  `docs/thesis_section_osm_enrichment.md` §2 before re-litigating that.

## Key files, in the order I'd read them

1. `docs/modeling_assumptions.md` — the 3 hard constraints above, in detail.
2. `complete_parameters.py` — the current deterministic Agent 3. Know it
   cold before proposing changes.
3. `defaults.py` — the flat constants Agent 3 currently uses.
4. `generate_scenario.py` — what actually consumes Agent 3's output
   (`initial_lane_id`, `initial_road_id`, `initial_s_m`, `initial_speed_mps`,
   `vehicle_category` per actor; `road_length_m`, `motor_lane_width_m`,
   `bike_lane_width_m`, `primary_heading_rad`, `secondary_heading_rad`,
   `primary_has_bike_facility`, `cyclist_lateral_position`, `car_path`,
   `simulation_duration_s`, `conflict.conflict_time_s`/`conflict_s_m`).
5. `docs/thesis_section_osm_enrichment.md` — what Agent 2 now provides, and
   the pipeline-wide conventions (provenance labeling, override taxonomy).
6. `input/*.json` — the 19-report frozen corpus to build and test against.
7. `template_selector.py`, `validate_outputs.py` — for context on template
   selection and what structural validation does (and doesn't) check.
8. `llm_client.py` — how the pipeline currently talks to the local vLLM
   server, for reference on connecting Agent 3 to an LLM the same way.

## Environment note

No local LLM server was reachable in the sandbox used for the OSM hardening
session — check whether one is available to you before assuming you can run
live LLM calls end-to-end. If not, say so explicitly rather than fabricating
results.

## What I want from you first

Don't start implementing. Read the files above, then tell me: what should
the LLM in Agent 3 actually decide (vs. stay deterministic), what would its
input/output schema look like, and how would you represent genuine
uncertainty (ranges? confidence? multiple candidate values?) consistent with
the SoVAR-inspired direction above. I'll confirm the design before we build
anything.
