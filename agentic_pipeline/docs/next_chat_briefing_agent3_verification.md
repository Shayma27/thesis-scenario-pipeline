Handoff briefing for a new Claude Code session — Agent 3 verification + remaining gaps

I'm continuing a bachelor's thesis project (see full brief below). The previous
session ran out of context mid-work. This file is the complete handoff — read
it fully before doing anything.

## Thesis context

**Title**: "Generation of Car-Cyclist Accident Scenarios Using Foundation
Models" — a pipeline that uses a foundation model (LLM) to generate
car-cyclist accident scenarios in ASAM OpenSCENARIO 1.3 format from real
Berlin police crash reports, for ADAS validation. Simulated via
`scenariogeneration`, demonstrated in esmini/DYNA4.

My supervisor's explicit expectation, given verbally, not just from the
written Aufgabenstellung: **real use of AI to fill missing parameters** — not
a token/decorative LLM call. This directly shaped the redesign described
below: earlier in the previous session I scoped Agent 3's LLM role too
narrowly (one field, gated so tightly it rarely did anything visible); the
scope was corrected to "Agent 3 fills any genuine gap between what's known
after template selection and what OpenSCENARIO generation needs" — using an
LLM specifically where there's real, genuine uncertainty, and deterministic
derivation (never faked) everywhere the templates' fixed geometry already
determines the answer.

## Pipeline architecture (unchanged from before this session)

Three stages: **Agent 1** (`extract_scenario.py`, semantic extraction via
local LLM Llama 3.1 8B Instruct/vLLM) → **Agent 2** (`osm_enrichment.py`, OSM
Nominatim/Overpass enrichment) → **Agent 3** (`complete_parameters.py` +
NEW `speed_estimation.py`, parameter completion) → `generate_scenario.py`
(writes the `.xosc`/`.xodr`).

- **Agent 1 is FROZEN, gold-verified 19/19** against `gold_reference.py`/
  `test_semantic_correctness.py`. Do not modify unless explicitly asked.
  **Agent 3 deliberately does NOT read raw report text** — only Agent 1's
  structured JSON. This was explicitly re-affirmed this session: giving
  Agent 3's LLM raw-text access was proposed and then explicitly rejected —
  it would create a second, uncoordinated, unverified text-extraction stage
  and undermine the entire point of Agent 1 being the single frozen,
  verified extraction stage. If a genuine extraction gap is ever found
  (e.g. a real directional clue in raw text that Agent 1's schema doesn't
  capture), the fix belongs in *extending Agent 1's schema* (with explicit
  permission, since it's frozen), never in giving Agent 3 its own text read.
- **Agent 2** is stable, hardened, not frozen. Several real bugs fixed in it
  this session (below).
- **Only two fixed OpenDRIVE templates**: `templates/straight_road.xodr`
  (real driving lane width 3.07 m, biking lane 1.25 m) and
  `templates/intersection_4way.xodr` (real driving lane width 3.5 m, biking
  1.25 m, sidewalk 2.0 m) — verified directly against the XML files. Both
  have exactly ONE real driving lane and ONE real biking lane per
  direction, always, at fixed lane IDs (±1 driving, ±2 biking). No new
  template may ever be introduced. See `docs/modeling_assumptions.md` for
  the full documented assumptions (1: longitudinal same-direction
  reinterpretation; 2: cycling-facility representability; 3: signal state
  never modeled).

## What changed this session (all committed and pushed to `origin/main`,
## latest commit `bf7e699`)

### 1. Topology/template resolution moved before Agent 3
`pipeline.py`'s `_tool_query_osm` now runs `detect_topology()` +
`select_template()` right after OSM enrichment (was previously deferred to
`_tool_generate_scenario`, after Agent 3 already ran and had to guess).
`data["topology"]`/`data["template_used"]` now exist before Agent 3 runs, so
it reads the *real* selected template's actual geometry (road length, lane
widths) directly from the `.xodr` file instead of synthetic placeholders.

### 2. Agent 3 speed estimation completely rebuilt (`speed_estimation.py`, new file)
Replaced `defaults.py`'s old flat, **uncited** `DEFAULT_SPEEDS_MPS` table
with a grounded, cited deterministic envelope, narrowed by an LLM **only**
when the report's own text gives genuine evidence:

- **Motor vehicle**: ceiling = OSM `maxspeed_kmh` if known, else § 3 Abs. 3
  Nr. 1 StVO innerorts default (50 km/h, verified against statute text).
  Nominal (no report signal) = lower of that and RiLSA's 36 km/h
  conservative clearance reference (RiLSA 2015, FGSV, § 2.5.2, p. 21-26,
  ISBN 978-3-939715-91-7) — **except** for "crossing" scenarios, which use
  RiLSA's separate, real 40 km/h motor-vehicle **approach**-speed figure
  (§ 2.5.3, p. 26, official English translation FGSV-Nr. 321 E) instead —
  this replaced a previously-**unverified** "65% of posted limit" rule that
  Codex confirmed has no primary-source basis anywhere (checked against
  StVO, RiLSA, German intersection-approach-speed studies, AASHTO, ITE).
- **Cyclist**: nominal 15.3 km/h (4.25 m/s), range 12.3-18.1 km/h — empirical
  mean/15th-85th percentile, Schleinitz et al. 2014, "Pedelec-Naturalistic
  Cycling Study," UDV Forschungsbericht 27, TU Chemnitz, p. 80, n=28,
  ISBN 978-3-939163-50-3. Not Berlin-specific; not intersection-restricted.
- **E-bike**: nominal 17.4 km/h (4.83 m/s, same source), legal ceiling 25
  km/h (§ 1 Abs. 3 StVG / StVZO Pedelec motor-assist cutoff, verified).
- **Parked vehicle**: hard `0.0`, no LLM call ever, always.
- **LLM narrowing**: fires for any non-parked actor whenever the report has
  *any* conflict text (`collision_description`/`severity_text`/
  `conflict_mechanism` — not gated on literal "speeding" language; the LLM
  itself decides `knowledge_status: "report_qualitative_signal"` vs
  `"insufficient_evidence"`). Output is a narrowed `speed_range_kmh` +
  `rationale`, always clamped to a hard safety cap (an explicitly-labeled
  **engineering judgment**, not a citation — `MOTOR_SAFETY_CLAMP_FACTOR =
  1.6`, `CYCLIST_SAFETY_CAP_KMH = 40`, `EBIKE_SAFETY_CAP_KMH = 35`) before
  it can reach output. Any failure (unreachable LLM, malformed JSON, no
  evidence) falls back to the grounded default directly, offline,
  deterministic — the pipeline never depends on LLM availability.
- Fixed a real ordering bug found while wiring this in: speed used to be
  computed AFTER position math in some branches, meaning position and the
  eventually-written speed could disagree. Now speed is always settled
  before position.

### 3. `defaults.py` deleted entirely
Went from ~10 constants (most uncited, two actively wrong for one of the
two templates) to 0. `DEFAULT_PARKING_ACCESS_S_M` was dead code (removed).
Road length + lane widths are now read directly from real template geometry
(`generate_scenario._real_lane_width_m`,
`complete_parameters._real_primary_road_length_m`/`_real_lane_widths_m`) —
this fixed a real, previously-unnoticed bug: the old flat 3.5 m motor-lane
default only matched `intersection_4way.xodr` (off by ~14% for
`straight_road.xodr`'s real 3.07 m), and the old 2.0 m bike-lane default
matched **neither** template's real 1.25 m. The two remaining genuine
staging/policy constants (`DEFAULT_SIMULATION_DURATION_S`,
`DEFAULT_CYCLIST_LATERAL_POSITION`) now live directly in
`generate_scenario.py` (the module that already needed both and that other
stages already import shared pieces from) — no separate defaults module.

### 4. Four real bugs found and fixed via a systematic "does Agent 3 close
### every gap" audit (prompted directly by asking "what does generate_scenario.py
### read with a silent fallback, and is that fallback ever actually wrong")

- **`generate_scenario._world_position_from_lane_s`**: branched on
  `motor_lane_count` instead of the fixed `lane_index` both templates
  always use (1=driving, 2=biking). Verified live: 4 reports
  (`turning_01/05/07/09`) have OSM `motor_lane_count=2`, which made a
  cyclist's `lane_index=2` incorrectly satisfy the driving-lane branch —
  its drawn trajectory was computed at the wrong lateral offset. Fixed to
  decide purely from `lane_index`, matching how `_cyclist_lateral_offset`
  (the crossing-scenario equivalent) was already fixed in an earlier
  session.
- **`osm_enrichment._approach_lane_count_evidence`**: heading selection was
  gated behind lane-count success, though they're independent facts about a
  road segment. Verified live against `crossing_02`'s real cached OSM data:
  every one of its 7 candidate segments has a real, consistent heading
  (~34.5°) and moves toward the target, but none has a usable `lanes` tag,
  so the heading was discarded for a reason unrelated to heading. Before
  fix: 0/8 "crossing" reports had any real heading. After: all 8 get a real
  primary heading, 4/8 (`crossing_02/06/07/08`) resolve both headings.
- **`osm_enrichment._apply_lane_context` (crossing branch) — a real,
  currently-active correctness bug, not just missing data**: Agent 1's
  `location.primary_road`/`secondary_road` (name-based — "which street is
  primary for geocoding") and `generate_scenario.py`'s
  `primary_heading_rad`/`secondary_heading_rad` (role-based — primary is
  literally used as the cyclist's own line of travel, secondary as the
  car's, matching `_resolve_road_id`'s "primary approach=cyclist, secondary
  approach=car") are two different concepts that were silently conflated.
  For a report naming only one street (no `secondary_road`), that road is
  ALWAYS the car's, by the very definition of "crossing" scenario_type
  (vehicle goes straight, cyclist's path crosses it at an angle) — verified
  against `crossing_01` and `crossing_04`'s actual report text. Before this
  fix, `crossing_04`'s **already-generated output** had its cyclist's
  trajectory computed along the road's own heading (west) instead of its
  real, roughly-north crossing direction — a live wrong-output bug. Now the
  resolved heading is assigned to the correct participant's slot.
- **`complete_parameters.py` — new deterministic (no LLM) fallback chain**
  for whichever heading is still unresolvable by OSM once there's no named
  road left to search: (1) the relevant participant's own extracted compass
  direction (`initial_direction` — Agent 1 field, 8-point compass), via a
  verified compass→radian conversion (calibrated and cross-checked directly
  against `crossing_04`'s real resolved heading before use — standard
  atan2(dy,dx) convention, 0=East, π/2=North, matching
  `osm_enrichment._road_heading_rad`'s own convention); (2) if that's null
  too, a perpendicular-to-the-known-heading geometric default, honestly
  labeled `source: "engineering_assumption"` (which of the two
  perpendicular sides is genuinely unknowable from the report — this
  mirrors an assumption `generate_scenario.py`'s own turning-scenario
  fallback already makes elsewhere). Verified: `crossing_01`'s cyclist
  heading (previously fully unfillable — no compass word, no OSM road) now
  resolves via the perpendicular fallback, correctly labeled, not
  fabricated as fact.

## Verified facts worth knowing before touching anything again

- Real template lane widths: `straight_road.xodr` driving=3.07m
  biking=1.25m; `intersection_4way.xodr` driving=3.5m biking=1.25m
  sidewalk=2.0m.
- OpenSCENARIO version: `scenariogeneration==0.16.5` installed, defaults to
  `_MINOR_VERSION=3` (confirmed via actual generated `.xosc`:
  `revMajor="1" revMinor="3"`) — already 1.3.x, matches target, nothing to
  change.
- OpenSCENARIO requires **m/s** for speed (verified against ASAM spec
  directly), not km/h — Python always does the one `/3.6` conversion,
  never the LLM.
- Topology resolution across the 19-report corpus: 14 resolve to
  `intersection_4way.xodr`, 5 to `straight_road.xodr`
  (`longitudinal_01/02`, `crossing_01/04`, `turning_07`).
- `car_path` (crossing motor-vehicle turn-vs-straight) currently never
  fires for any of the 19 real reports — not actively wrong today, but
  fragile (depends on a narrow OSM turn-lane-tag chain rather than Agent
  1's own reliable `maneuver` field). Flagged, not yet fixed.

## Test suite — run via `.venv/bin/python3`, NOT plain `python3`

Plain `python3` in this environment doesn't have `openai` installed;
`.venv/bin/python3` does (it's a `uv`-managed venv with its own
site-packages, symlinked interpreter — confirmed working). All five pass:
`test_extract_scenario_schema.py`, `test_semantic_correctness.py`,
`test_agent1_preservation.py`, `test_lane_type_safety.py` (all offline,
mocked network + mocked LLM client), `test_speed_estimation.py` (new, 20
checks, fully offline/mocked).

## Git / HPC state

Everything is committed and pushed to `origin/main` (GitHub repo
`Shayma27/thesis-scenario-pipeline`), latest commit `bf7e699`. The remote
URL has a personal access token embedded in it for HTTPS auth — noted, not
touched. On HPC: SSH via `sshgate.tu-berlin.de` → `gateway.hpc.tu-berlin.de`
→ `frontend02`, `thesis-venv` activated, repo cloned at
`~/thesis-scenario-pipeline` — **pull the latest commit first**
(`git pull origin main`), it was several commits behind at last check.

**Unfinished business, interrupted mid-debug**: a live vLLM Llama 3.1 8B
server was running on HPC (SLURM job `1876836`, `h200_short` partition,
node `gpu071`) and a direct sanity check of
`speed_estimation._llm_speed_estimate()` returned `None` — meaning it
silently failed somewhere (the function swallows all exceptions for
graceful production fallback, so the raw cause wasn't visible). Most likely
cause, not yet confirmed: `LLM_BASE_URL` env var still defaulting to
`llm_client.py`'s hardcoded `http://gpu026:8000/v1`, which doesn't match
`gpu071` where the job actually landed. Three diagnostics were queued and
never reported back:
1. `echo $LLM_BASE_URL` — is it actually set to `http://gpu071:8000/v1`?
2. `curl -s http://gpu071:8000/v1/models` — is the server actually up
   (SLURM `R` state only means the job started, not that vLLM finished
   loading)?
3. A raw, unguarded `client.chat.completions.create(...)` call (bypassing
   `_llm_speed_estimate`'s silent exception handling) to see the actual
   traceback instead of a bare `None`.

**This is the first thing to resume** — get the LLM narrowing path actually
verified against the real model (this has never been tested against a live
LLM, only mocked). Once working, the real verification pass is: run the
full pipeline on real reports (start with `crossing_04`, which has explicit
"struck by a speeding car" language) and manually check whether the LLM's
`knowledge_status`/`rationale`/narrowed range are actually defensible
against the report text — the same rigor `gold_reference.py` already
applied to Agent 1, not yet done for this new speed logic. I flagged
explicitly, before building any of this, that I expected the prompt/schema
to need at least one real iteration once tested against the actual 8B
model, the same way Agent 1 did.

## Explicitly flagged, NOT yet done (in rough priority order)

1. **Live-LLM verification** (above) — highest priority, blocks trusting
   any of the speed-estimation work for the thesis.
2. `car_path` robustness — derive from Agent 1's own gold-verified
   `maneuver` field directly instead of the fragile OSM-turn-lane-tag
   chain that never fires today.
3. Stale `bike_lane_width_m` fallback still reading the old wrong `2.0`
   default inside `generate_scenario._cyclist_lateral_offset` — dead code
   in practice (Agent 3 always sets the real value first) but inconsistent
   with the fix already made elsewhere; quick cleanup.
4. Uncited flat constants in `complete_parameters.py` never re-examined
   with the same scrutiny speed got: `initial_s_m` offsets (`-20m` turning,
   `-25m` other, `cs * 0.2` cyclist non-crossing), and timing constants
   (`conflict_time_s=4.0`, `trigger_time_s=1.0`). Found during the "fill
   every gap" audit, flagged, not yet addressed — same "cite or replace
   with real reasoning" treatment speed already got.
5. Whether any of the other 18 reports (beyond `crossing_01`) have a
   heading or other gap not yet covered by the fallback chain above — not
   systematically re-checked since the last two fixes landed.

## What I want from you first

Read this whole file, confirm you understand the current state, then help
resume item 1 (the HPC live-LLM debug) unless I say otherwise. Don't
re-litigate settled design decisions above (the narrow-LLM-scope-for-speed
redesign, the "Agent 3 never reads raw text" decision, the citation
sourcing) without new evidence — they were each argued through carefully
and I don't want to redo that work. Do keep the same working discipline
this session used throughout: verify against real data before claiming
something is fixed, run the full test suite after every change, and
propose before implementing anything non-trivial.
