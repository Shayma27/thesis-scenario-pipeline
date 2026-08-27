# Handoff briefing for a new Claude Code session — post-visual-review,
# real geometry/behavior bugs pending

This session is running out of context. This file is the complete
handoff — read it fully before doing anything. Three other handoff docs
exist in this same folder (`next_chat_briefing_parameter_completion.md`,
`next_chat_briefing_agent3_verification.md`,
`next_chat_briefing_orchestrator_and_speed_evidence.md`) — they're
historical records of earlier sessions, superseded by this one for
anything they disagree on, but still useful background on the thesis and
why earlier decisions were made. Don't rewrite them.

## Thesis context

**Title**: "Generation of Car-Cyclist Accident Scenarios Using Foundation
Models" — a pipeline using an LLM to generate car-cyclist accident
scenarios in ASAM OpenSCENARIO 1.3 format from real Berlin police crash
reports, for ADAS validation. Simulated via `scenariogeneration`,
demonstrated in esmini/DYNA4.

Supervisor's explicit expectation (verbal, not just written
Aufgabenstellung): **real use of AI to fill missing parameters** — not a
token/decorative LLM call. Supervisor also confirmed (email, 2026-08-04)
that the ego vehicle does **not** need to be reactive/responsive —
fixed-route collision scenarios are in scope for DYNA4 validation. As of
2026-08-26, supervisor's stance is "keep going, waiting for final
results."

**User context**: bachelor's thesis student under real time pressure,
works late, gets emotionally overwhelmed under stress, explicitly does
NOT want things glossed over or "failed silently" — wants concrete
evidence over reassurance, and will push back hard (correctly) if a claim
isn't verified. Communicates in a mix of English/German, often
terse/fast-typed with caps for emphasis, occasional typos. Respond with
real verification (run the check, cite the line, show the diff), not
narrative confidence. Corrects mistakes forcefully and is usually right
to. Appreciates honest "not yet confirmed" over optimistic rounding.
Under stress, benefits from short, direct answers first, detail after.

## Current architecture (as of this session's end, commit `<see below>`)

```
Agent 1 (extract_scenario.py)
  — the ONLY LLM call in the entire pipeline that reads raw report text
  — semantic extraction + speed_evidence/speed_evidence_quote per
    participant (added two sessions ago, live-verified)
  — frozen*, gold-verified — *"frozen" means don't touch without explicit
    permission
        ↓
Agent 2 (osm_enrichment.py)
  — deterministic OSM/Nominatim/Overpass enrichment, no LLM, never has been
        ↓
Agent 3 (complete_parameters.py + speed_estimation.py)
  — fully deterministic, ZERO LLM calls, zero network calls of any kind
  — reads Agent 1's already-grounded speed_evidence directly, concretizes
    into km/h with a monotonic consistency check
  — every flat constant (conflict_time_s, initial_s_m offsets) now has
    real citation or explicit engineering_assumption labeling (this
    session, see below) — no uncited numbers left in this file
        ↓
generate_scenario.py — writes .xosc/.xodr, deterministic
        ↓
pipeline.py run_agent()
  — five deterministic sequential Python calls, NOT an LLM tool-calling
    loop (orchestrator removed two sessions ago, live-verified)
  — run_feedback_iteration() (human free-text feedback after watching a
    simulation) STILL uses an LLM deliberately, but is currently
    DEPRIORITIZED/PAUSED — see "Deprioritized work" below
```

**Precise, load-bearing claim for the thesis**: exactly one LLM call
happens anywhere in this pipeline, per report — Agent 1's extraction.
Everything else is deterministic. This has been live-verified against
the real HPC-hosted Llama 3.1 8B model multiple times across sessions.

## What happened this session, in order

Starting state: previous session's handoff
(`next_chat_briefing_orchestrator_and_speed_evidence.md`) had just closed
out live re-verification of the orchestrator removal and speed-evidence
consolidation. This session did:

1. **Live HPC re-verification repeated + a real bug found and fixed**:
   running `scripts/hpc_live_llm_verification.py all` hit a context-length
   error (vLLM server capped at `--max-model-len 4096`, too small once
   the speed_evidence prompt consolidation grew Agent 1's system prompt —
   every single report failed identically). Fixed by bumping to `8192` in
   `/scratch/shayma27/llm-api/jobs/serve-llama31.sbatch` on HPC (not
   tracked in this git repo — if that sbatch file is ever regenerated
   from a stale copy, re-apply this). Re-verified: 19/19 valid, and
   `speed_evidence` for crossing_03/crossing_04 confirmed coming directly
   from the live model (`source=agent1_speed_evidence`), not the regex
   backfill (added a diagnostic print to `_backfill_speed_evidence` to
   prove this empirically — it never fired).

2. **Gold fixture regeneration** (`extract_all.py`, live): closed the 2
   previously-known `speed_evidence` gaps. Found one new mismatch
   (`crossing_02.car_1.maneuver`: live model said `go_straight`, old gold
   said `enter_roadway`) — traced through the actual dispatch code
   (`generate_scenario.py:_maneuver_kind()`,
   `complete_parameters.py`'s lane-selection logic) and confirmed BOTH
   values hit the identical code path (only `turn_left`/`turn_right` are
   ever special-cased) — zero functional effect either way. User
   determined on review that `go_straight` was actually correct (the old
   hand-classification was wrong) — `gold_reference.py` corrected.
   `test_semantic_correctness.py`: 19/19 clean.

3. **`car_path` robustness fix**: `_apply_lane_guided_maneuver_context`
   (osm_enrichment.py) used to require OSM `turn:lanes` tag corroboration
   *on top of* Agent 1's own `maneuver` field already saying `turn_left`,
   before it would set `car_path="turn_left_from_secondary_to_primary"`
   for a "crossing" scenario — OSM turn-lane tagging is sparse enough
   that this never fired for any of the 19 real reports. Fixed: Agent 1's
   `maneuver` field is now sufficient on its own; OSM data is corroborating
   evidence only, never a gate. **Zero changes to extract_scenario.py**
   (Agent 1 itself untouched). New test: `test_car_path_maneuver.py`.
   Doesn't change any of the current 19 reports' output (none have a
   crossing-type left-turning *car* — see item 8 below for the
   *cyclist*-turning case, which is different and still broken).

4. **Uncited flat constants labeled**: `conflict_time_s` (was completely
   unrecorded — lives on the `conflict` dict, `_note()` never saw it) and
   every `initial_s_m` offset branch (`-20m` turning, `-25m`
   longitudinal/other, `cs*0.2` cyclist non-crossing, the two kinematic
   crossing formulas) now get a specific `missing_parameters` entry,
   `source=engineering_assumption`, no fabricated citations (these are
   scene-staging choices, not claims about real crash geometry — same
   category as the file's existing `engineering_assumption` examples).
   `trigger_time_s` (`1.0`) turned out to be **dead code** — set in
   exactly 2 places, read nowhere in the whole codebase — removed rather
   than cited. Found and fixed a real bug in the first pass at this: the
   provenance entry for `initial_s_m` must be recorded with the actor's
   FINAL post-clamp value (`_clamp_initial_s_to_real_road` runs after —
   for "crossing" reports at the junction template, the motor vehicle
   sits on the much shorter secondary approach while `cs` is computed
   from the primary road's length, so clamping is a real, structural
   correction there, not a rare edge case). New test:
   `test_constants_provenance.py`.

5. **Literature review for "pipeline self-evaluation"** — user wants the
   pipeline to evaluate itself, not just a one-off manual study. Read
   **ARISE** (arXiv:2601.14743, IV 2026) in full: proposed an SCS-style
   LLM-judge modeled on their Semantic Conformity Score. User pushed back
   for more grounding; read **SAFE** (arXiv:2502.02025, ICSE 2026) in
   full — same exact task (accident reports → scenarios), more rigorous.
   **Key correction**: SAFE's own headline accuracy metric is a
   human-built oracle + deterministic field comparison (exactly this
   project's existing `gold_reference.py`/`test_semantic_correctness.py`
   methodology) — SAFE deliberately did NOT trust an LLM-judge for their
   strongest fidelity claim, using a 30-participant human study instead.
   Revised recommendation: don't add a new LLM-judge layer; instead
   borrow SAFE's ablation methodology (Table 7: measure each guardrail's
   real contribution by disabling it and re-running against gold
   reference) to turn "these safety nets help" into real numbers.
   Designed a full ablation plan (`extract_scenario.py`'s 16 deterministic
   post-processing guardrails, zero changes needed to the frozen file
   itself via monkeypatching module-level function references) —
   **then the user paused this**, feeling it was too complex for the
   time available, and asked to reconsider scope entirely.

6. **Scope pivot**: user decided to stop treating "build a self-evaluation
   layer" as required, and instead just validate what the pipeline
   already produces. Correctly noted (and I agreed, grounded in the SAFE
   reading) that the existing `gold_reference.py` methodology already
   matches the strongest paper in this exact domain's own primary
   evidence — no need to add more on top just to look more sophisticated.
   **Dropped the ablation plan entirely** (not resumed, not needed).

7. **`review_generated.py`** (new script) — the actual highest-value next
   check turned out to be simple: watch the generated scenarios in esmini
   and use human judgment, since trajectory/timing plausibility is
   fundamentally not something automated tests can check. Built as a
   trimmed, local-only copy of `run_all.py`'s review loop (reuses
   `_launch_esmini`/`_load_results`/`_save_result` patterns) with the
   `run_agent()`/LLM call removed — because `run_all.py` needs a live LLM
   connection (only reachable from inside the HPC cluster network) AND a
   local display (only available on the user's machine) simultaneously,
   which don't coexist. Design: generate everything on HPC first
   (`scripts/hpc_live_llm_verification.py all`, already proven), commit +
   push `output/agentic/`, pull locally, then watch with zero LLM/network
   needed at watch-time.

8. **First full visual review pass — THE ACTUAL FINDING THIS SESSION
   PRODUCED, read this carefully.** User ran `review_generated.py`
   against all 19 freshly-regenerated scenarios (commit `0a72fa1`) and
   left notes per report. **This was not a clean pass** — most reports
   have a real, visible problem. Verbatim notes, lightly cleaned up:

   | Report | Note |
   |---|---|
   | turning_01 | "alright but the bike continue going straight even after the collision. normally it has to stop at the collision" |
   | turning_03 | same as turning_01 |
   | turning_04 | same as turning_01 |
   | turning_05 | same as turning_01 |
   | turning_06 | same as turning_01 |
   | turning_07 | "all wrong! but this is supposed to be a parking access conflict scenario. the template is not suitable. we should drop the scenario" |
   | turning_08 | "wrong cyclist position. no collision happened. and the raw text scenario from the beginning didn't have that much info" |
   | crossing_01 | "all wrong and weird simulation" |
   | crossing_02 | "no collision happened. but right topology and scenario" |
   | crossing_03 | "could be okay if we adjust the time of collision. position is also not right, cyclist in the middle of the driveway" |
   | crossing_04 | "right template and cyclist position and direction but the car was very wrong coming from nowhere. it's supposed to go very normal in the driveway" |
   | crossing_05 | "and the bike was going in the driveway" |
   | crossing_06 | "no collision happened. and the turn left of the bike didn't happen" |
   | crossing_07 | "collision didn't happen but everything else was right" |
   | longitudinal_01 | "very weird simulation and positions of the car and cyclist!! only the template was right" |
   | longitudinal_02 | "same as longi1" |

   Full raw data (including timestamps, `status: "skipped"` for all —
   user didn't confirm any, correctly, since real problems were found):
   `review_results.json` (committed, repo root).

   **Root causes — one CONFIRMED, others need investigation**:
   - **CONFIRMED** (crossing_06's "turn left of the bike didn't happen"):
     `generate_scenario.py:741` (`_generate_straight_crossing_openscenario`),
     the cyclist's junction trajectory is generated with
     `_junction_maneuver_samples(0, "go_straight", ...)` — **the maneuver
     kind is a hardcoded literal string**, never reading
     `cyclist_p.get("maneuver")` at all. The `car_path` fix earlier this
     session (item 3) only handles a turning *car* in a crossing scenario
     — a turning *cyclist* (crossing_05's `turn_left`, crossing_06's
     `turn_left`) has never been supported by this code path. This is the
     same class of bug as the `car_path` fix, just on the other actor,
     never fixed because it was never live-rendered/watched until now.
   - **NOT YET INVESTIGATED** (needs code reading, not yet done this
     session): post-collision behavior not stopping (turning_01/03/04/05/06)
     — likely `generate_scenario.py`'s waypoint timeline for the "hold
     position after impact" logic not actually holding, or holding the
     wrong actor; "collision didn't happen" (crossing_02/06/07) — likely a
     timing/`conflict_time_s`/impact-point-computation issue, possibly
     interacting with item 4's constants-labeling changes or the
     `car_path`/cyclist-turn bug above; "car coming from nowhere"
     (crossing_04) — likely a spawn-position/`initial_s_m` or lane-offset
     issue specific to the straight-road (non-junction) template's
     crossing geometry; longitudinal_01/02 "very weird positions" — the
     `change_lane`/`change_lane_left_to_right` maneuver's trajectory
     generation, not yet examined at all this session.
   - **Scoping decision needed, not a bug**: turning_07's maneuver is
     `turn_right_into_parking` — neither `straight_road.xodr` nor
     `intersection_4way.xodr` can represent a parking-lot access
     geometry (both templates model exactly one real driving lane and one
     real biking lane per direction, no parking area — see
     `docs/modeling_assumptions.md`). User's suggestion ("drop the
     scenario") is reasonable and should be discussed/decided, not
     silently overridden.

## What's verified vs NOT — read this carefully

### Verified this session (real, not hand-waved)
- Live HPC re-verification: 19/19 structurally valid, speed_evidence
  correctly live-sourced (not backfilled) for crossing_03/04.
- `test_semantic_correctness.py`: 19/19 clean (after the crossing_02 gold
  correction).
- All other offline suites green: `test_agent1_preservation.py`,
  `test_lane_type_safety.py`, `test_extract_scenario_schema.py`,
  `test_speed_estimation.py`, `test_speed_evidence_backfill.py`,
  `test_car_path_maneuver.py`, `test_constants_provenance.py`.
- The crossing_06 cyclist-turn root cause (confirmed by direct code
  reading, not guessed).

### NOT verified — this is the actual state, don't round up
- **Visual/behavioral correctness of the generated scenarios is largely
  NOT working**, per the user's own direct observation across 19/19
  reports (0 confirmed, 19 skipped). This contradicts nothing in the
  "verified" list above — structural validity (`validate_outputs.py`)
  and semantic field correctness (`gold_reference.py`) were never claims
  about trajectory/timing/collision-triggering plausibility, and this is
  the first time anyone actually watched them. **This is the single
  highest-priority thing to fix now** — everything else this session did
  (constants labeling, car_path for cars, literature review) is real and
  worth keeping, but none of it is "final results" material until the
  scenarios actually look right.
- Only ONE of the ~6 distinct problem categories above has a confirmed
  root cause (crossing_06's hardcoded cyclist maneuver). The rest need
  real investigation — don't assume they're all the same bug, they
  visibly aren't (some are timing, some are positioning, some are
  template mismatch).

## Known, deliberately-NOT-done items, in priority order

1. **Fix the visual/behavioral bugs found in item 8 above** — this is now
   the top priority, full stop. Recommended approach: pick ONE
   category at a time (e.g. start with the confirmed cyclist-turn bug,
   since it's already root-caused — extend the `car_path`-style fix to
   the cyclist side of `_generate_straight_crossing_openscenario`), fix,
   regenerate that one report, re-watch it with `review_generated.py`
   before moving to the next category. Don't try to fix all 6 categories
   in one blind pass.
2. **Decide turning_07's fate** — ask the user directly whether to drop
   it from the 19-report corpus (their own suggestion) or find another
   way to represent it. Don't decide unilaterally.
3. **`run_feedback_iteration()`** — still paused/deprioritized (see item 6
   above and the full design plan that was written and then shelved — if
   resumed, a plan for it exists in this session's transcript: typed
   `edit_intent` status taxonomy, Python-enforced `unsupported` gate for
   geometry feedback since no parametric OpenDRIVE generator exists in
   this codebase, speed-evidence-conflict check reusing
   `speed_estimation.py`, transactional apply/rollback). Only revisit if
   the user decides human-in-the-loop feedback is staying in scope — this
   was explicitly uncertain last it came up.
4. **Ablation study** — designed in full, then explicitly dropped by the
   user for being too complex relative to its value right now. Don't
   resume unless the user brings it back up themselves.
5. Full systematic Layer-3 faithfulness evaluation, other extraction gaps
   beyond speed evidence — lower priority than item 1 now.

## Repo / environment notes

- `.venv/bin/python3`, NOT plain `python3`, for all offline test runs —
  plain `python3` doesn't have `openai` installed. Same on HPC: always
  `source ~/thesis-venv/bin/activate` first, or you get
  `ModuleNotFoundError: No module named 'openai'` (happened this
  session, easy to forget after a break).
- Git repo root is `/home/chimo/Shayma/Shayma` (one level above
  `agentic_pipeline/`).
- `output/agentic/` is NOT gitignored (only `output/osm_cache/` is) —
  the full 19-report generated output (`.xosc`/`.xodr`/`.enriched.json`/
  etc.) is now committed at `0a72fa1`, so both HPC and local checkouts
  have the same files without needing to regenerate.
- `review_results.json` (repo root) — the user's own visual-review notes
  per scenario, committed. Read it, don't just read the table above (the
  table is a cleaned-up summary made this session; the JSON has exact
  timestamps and the literal text).
- esmini binary: `/home/chimo/tools/esmini/esmini-demo/bin/esmini`
  (local machine only). `review_generated.py` (repo root) launches it
  per-report with a review menu (`[r]`ewatch/`[n]`ote/`[s]`kip/`[ok]`
  confirm) — rerun it after any fix to re-check the specific report(s)
  affected, no need to redo all 19 every time unless a shared/core
  function changed.
- HPC login chain, dual-partition GPU-queue trick, common failure modes:
  see `docs/hpc_quickstart.md` (still accurate, unchanged this session).
- Windows Terminal `historySize` was bumped to max (32767) in an earlier
  session after repeated scrollback-loss complaints — should already be
  fixed, mention if scrollback issues recur.

## Working style notes for this user

- Wants real verification, not confident-sounding prose. Always run the
  actual check before claiming something works.
- Corrects mistakes forcefully and is usually right to.
- Explicitly does not want frozen/gold-verified Agent 1
  (`extract_scenario.py`) code touched without asking first — always
  propose before implementing anything touching it, even when a real gap
  is found. (Every fix this session that could avoid touching it, did —
  e.g. the `car_path` fix used monkeypatching-style zero-touch design
  where possible; extend that discipline to the item 8 fixes too where
  the bug is actually in `generate_scenario.py`/`osm_enrichment.py`, not
  Agent 1.)
- Appreciates being told the honest state plainly, including "this is
  not yet confirmed" rather than optimistic rounding — this is exactly
  what item 8 above is: an honest, unflattering, but real result.
- Under time/stress pressure, benefits from short, direct answers first,
  detail after — lead with the answer, not the reasoning.
- Is willing to reconsider and simplify scope when a plan feels too
  complex (see item 6, the ablation-study pivot) — don't resist this,
  help find the right-sized next step instead.
