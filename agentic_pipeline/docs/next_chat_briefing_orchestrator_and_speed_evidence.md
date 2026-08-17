Handoff briefing for a new Claude Code session — post-orchestrator-removal,
live re-verification pending

This session is running out of context. This file is the complete
handoff — read it fully before doing anything. Two other handoff docs
exist in this same folder (`next_chat_briefing_parameter_completion.md`,
`next_chat_briefing_agent3_verification.md`) — they're historical records
of earlier sessions, superseded by this one for anything they disagree
on, but still useful background on the thesis and why earlier decisions
were made. Don't rewrite them.

## Thesis context

**Title**: "Generation of Car-Cyclist Accident Scenarios Using Foundation
Models" — a pipeline using an LLM to generate car-cyclist accident
scenarios in ASAM OpenSCENARIO 1.3 format from real Berlin police crash
reports, for ADAS validation. Simulated via `scenariogeneration`,
demonstrated in esmini/DYNA4.

Supervisor's explicit expectation (verbal, not just written
Aufgabenstellung): **real use of AI to fill missing parameters** — not a
token/decorative LLM call. This has shaped every redesign decision across
multiple sessions now.

**User context**: not a senior software engineer — a bachelor's thesis
student under real time pressure (a supervisor meeting was postponed
from "today" to "tomorrow" partway through this session), works late,
gets emotionally overwhelmed under stress, explicitly does NOT want
things glossed over or "failed silently" — wants concrete evidence over
reassurance, and will push back hard (correctly) if a claim isn't
verified. Communicates in a mix of English/German, often terse/fast-typed
with caps for emphasis. Respond with real verification (run the check, cite
the line, show the diff), not narrative confidence.

## Current architecture (as of this session's end, commit `c176e1c`)

```
Agent 1 (extract_scenario.py)
  — the ONLY LLM call in the entire pipeline that reads raw report text
  — semantic extraction: scenario_type, participants, maneuvers,
    directions, headings, road_position, bike_facility, conflict_mechanism,
    collision_description
  — AS OF THIS SESSION: also extracts speed_evidence/speed_evidence_quote
    per participant (see below) — one more field from the same call, not
    a second call
  — frozen*, gold-verified — *"frozen" means don't touch without explicit
    permission; it WAS touched this session, deliberately, with permission
        ↓
Agent 2 (osm_enrichment.py)
  — deterministic OSM/Nominatim/Overpass enrichment, no LLM, never has been
        ↓
Agent 3 (complete_parameters.py + speed_estimation.py)
  — AS OF THIS SESSION: fully deterministic, ZERO LLM calls, zero network
    calls of any kind
  — reads Agent 1's already-grounded speed_evidence directly and
    concretizes it into an actual km/h value with a monotonic
    consistency check
        ↓
generate_scenario.py — writes .xosc/.xodr, deterministic
        ↓
pipeline.py run_agent()
  — AS OF THIS SESSION: five deterministic sequential Python calls
    (extract_scenario -> query_osm -> complete_parameters ->
    generate_scenario -> validate_and_fix), NOT an LLM tool-calling loop
  — run_feedback_iteration() (separate function, for human free-text
    feedback after watching a simulation) STILL uses an LLM deliberately
    — interpreting unpredictable human sentences is genuinely variable
    input, unlike the fixed five-step sequence. User explicitly flagged
    they're "not satisfied with it" and it needs revisiting — NOT done
    this session, just noted.
```

**Precise, load-bearing claim for the thesis**: exactly one LLM call
happens anywhere in this pipeline, per report — Agent 1's extraction.
Everything else is deterministic. This is a real, verified reduction from
earlier in this same session (which briefly had Agent 3 making its own
second LLM call, and always had an LLM-driven orchestrator).

## What changed this session, in order (all commits pushed to
## `origin/main`, `Shayma27/thesis-scenario-pipeline`, current HEAD `c176e1c`)

1. **`f379d3c`** — Fixed a stale `bike_lane_width_m` dead-code fallback
   (2.0m → 1.25m, matching real template geometry). Minor.

2. **`6320bc5`** — Added `scripts/hpc_live_llm_verification.py`, the tool
   used all session to test against the real HPC-hosted Llama 3.1 8B
   model. Usage: `python3 scripts/hpc_live_llm_verification.py [scenario_id ... | all]`.

3. **`65dcd73`** — **First live-verified Agent 3 bug and fix.** Original
   design had Agent 3's `speed_estimation.py` ask its own LLM to BOTH
   classify AND numerically narrow a speed range. Live-tested on
   `crossing_04` ("deutlich überhöhter Geschwindigkeit" — clearly
   excessive speed): the model correctly read the evidence but the
   narrowed range's midpoint (25 km/h) was *slower* than the non-LLM
   default (40 km/h) — backwards. No source quantifies "clearly
   excessive" numerically, so asking an 8B model to invent a number was
   asking it to hallucinate a calibration nothing grounds. **Fix**:
   constrained the LLM to classify only (`stopped` /
   `clearly_slower_than_context` / `slower_than_context` /
   `approximately_contextual` / `faster_than_context` /
   `clearly_faster_than_context`), moved numeric concretization to
   deterministic Python (`_concretize_qualitative_relation`), added a
   direction-consistency check before any value can reach output.
   Grounded in SoVAR (Guo et al., ASE 2024) and the Extended Scenic DSL
   paper's LLM/formal-layer split.

4. **`196748d`** — **Second live-verified bug**, found running all 19
   reports live: 10/11 failures were the same root cause — the LLM had
   to *retype* the `osm_query` string as a tool-call argument, and is
   unreliable reproducing German special characters (`ß`) — `"Straße"` →
   `"Straöse"` etc. This silently-corrupted string then correctly tripped
   `check_agent1_preserved` (a safety net built earlier this project),
   which was doing its job, just not catching the bug it was built for.
   **Fix**: `_tool_query_osm` no longer writes the LLM's argument into
   state at all — `_build_location_queries()` never actually needed it,
   the value was already correctly, deterministically set by
   `_fill_location_query_fields()` from Agent 1's own `primary_road`/
   `secondary_road` fields.

5. **`bdd96b9`** — **Third live-verified bug**: `turning_03`'s e-bike was
   classified `stopped` with `evidence_quote: "stopped"` — a word that
   appears nowhere in the actual text (which says the e-bike was "going
   straight," i.e. moving). A non-empty quote string was being trusted as
   proof of grounding; it isn't. **Fix**: `_llm_speed_estimate` (at the
   time) rejects any quote that isn't an actual case-insensitive
   substring of the source text.

6. **`995a727`** — Found (via the user's own careful question — "what if
   they use another word, not just 'überhöht'?") that `crossing_03`'s
   raw text has the exact same "mit deutlich überhöhter Geschwindigkeit"
   phrasing as `crossing_04`, but Agent 1's `collision_description` for
   `crossing_03` dropped it — same wording, inconsistent LLM extraction,
   not a text-matching gap. Added `_backfill_speed_evidence` +
   `_validate_speed_evidence_grounding` to `extract_scenario.py`,
   mirroring the file's own existing pattern for compass/heading fields
   (`_validate_direction_grounding`/`_backfill_initial_direction`) —
   deterministic regex safety net for the ONE verified pattern, not a
   general extractor.

7. **`90775d2`** — **The big consolidation.** User (correctly, per a
   detailed external Codex/literature review — see below) pushed back:
   a keyword-only regex safety net has a hard recall ceiling; a *second*
   Agent-3 LLM call re-reading Agent 1's own lossy summary couldn't fix a
   gap that happens upstream of it. Moved `speed_evidence`/
   `speed_evidence_quote` extraction directly into Agent 1's ONE raw-text-
   reading call (reusing the same category vocabulary, `crossing_04` is
   now one of three few-shot examples), kept the same two-function safety
   net (validate-grounding + backfill-the-one-verified-gap), now guarding
   extraction directly. `speed_estimation.py` (Agent 3) now makes **zero**
   LLM/network calls — reads Agent 1's already-grounded field, concretizes
   deterministically. `gold_reference.py` got real `speed_evidence`
   expectations for `crossing_03`/`crossing_04` (the only 2 of 19 reports
   with any speed-related vocabulary at all — verified by scanning all 19
   raw texts programmatically, not assumed).

8. **`c176e1c`** — **Orchestrator removed.** `run_agent()`'s LLM
   tool-calling loop always executed the same fixed five-step sequence —
   `SYSTEM_PROMPT` literally said "REQUIRED WORKFLOW — follow this exact
   sequence" — not a real decision. Live traces (from before fix #4)
   showed it as a real cost: malformed tool-call JSON, wasted retries.
   Replaced with plain sequential Python calls to the same `_tool_*`
   functions. `run_feedback_iteration()` untouched (still LLM-based,
   deliberately — see architecture section above). Verified offline:
   mocked the one extraction call, ran the whole real deterministic
   sequence, produced valid output. `run_all.py`/`run.py`/the HPC script
   all still work (return-dict shape unchanged).

## The Codex/literature review (important — read before touching Agent 3/1 framing again)

An external Codex review (against the user's own local literature
corpus — SoVAR, Extended Scenic DSL, CrashAgent, Scenic, TRACE, SAFE,
ARISE, Traffic Scenario Orchestration via Constraint Satisfaction, and
others) evaluated the redesigned architecture and found it well-supported
by the literature — LLM at the semantic-extraction boundary, deterministic
constraints/templates/solvers for concretization is the dominant pattern
in the strongest papers. Key corrections it made to how this should be
described (all now reflected in the architecture above and worth
preserving in the thesis write-up):

- **Don't say** "Agent 3 uses an LLM to fill missing parameters." Agent 3
  is fully deterministic now.
- **Don't say** "AI reconstructs the missing actual speed." No LLM
  recovers a real, unmeasured physical quantity — nobody can, it was
  never recorded. The correct framing: AI extracts available *qualitative
  evidence* (a genuine semantic-representation gap it does fill);
  unresolved numerical uncertainty is completed through documented,
  cited, or explicitly-labeled-as-assumption deterministic values (a
  *missing-measurement* gap it does NOT fill, and shouldn't claim to).
- **Precise, defensible sentence to use**: "The parameter-completion
  module does not attempt to recover unobserved accident parameters as
  facts. It produces simulation assumptions with explicit provenance and
  uncertainty status, while enforcing consistency between qualitative
  report evidence and selected numerical parameters."
- Prefer "hybrid LLM-assisted reconstruction pipeline" or "three-stage
  neuro-symbolic pipeline" over an unqualified "three-agent pipeline" —
  the latter risks implying three reasoning models when there's now one.
- The now-removed orchestrator LLM was never a "real" AI contribution
  worth counting — it walked a fixed checklist. Its removal this session
  is consistent with, not a retreat from, the literature's guidance.

## What's verified vs NOT — read this carefully, this is the actual state

### Verified offline (real, not hand-waved)
- All 5 core offline test suites pass: `test_extract_scenario_schema.py`,
  `test_agent1_preservation.py` (19/19), `test_lane_type_safety.py`
  (19/19), `test_speed_estimation.py` (26 checks), `test_speed_evidence_backfill.py`
  (9 checks, covers both the grounding-validator and the backfill).
- `test_semantic_correctness.py`: **17/19 clean, 2 known, explained
  failures** (`crossing_03`/`crossing_04`'s `speed_evidence` — the
  frozen `input/*.json` fixtures predate this field; this is an honest,
  correct signal, not a bug — see below for what would close it).
- Full offline pipeline (`complete_parameters` → `generate_scenario` →
  `validate_outputs`) run directly against all 19 real
  `input_osm_enriched/*.json` fixtures: 19/19 produce valid `.xosc`/`.xodr`.
- The new deterministic `run_agent()` sequence: verified offline with a
  mocked extraction step (real `crossing_04` fixture data), ran the real
  5-step sequence, produced valid output, matching what the old
  LLM-orchestrated version did.
- Speed-evidence backfill/grounding functions: directly unit-tested
  against real `crossing_03`/`crossing_04` raw text.

### Verified LIVE against the real HPC 8B model (Llama 3.1 8B Instruct via vLLM)
- The OLD (numeric-narrowing) Agent 3 design: live-tested, found broken
  (bug #3 above), fixed.
- The FIXED (categorical) Agent 3 design, **while it still made its own
  second LLM call**: live-tested on all 19 reports, 19/19 valid, found
  bugs #4 and #5 above via that same run, both fixed.
- **NOT yet live-tested**: the consolidated design where `speed_evidence`
  comes from Agent 1's own extraction call (commit `90775d2`) — this has
  only been verified offline (mocked/direct function tests). Does the
  live 8B model actually classify `speed_evidence` correctly as part of
  its normal extraction call, without needing the backfill to rescue it?
  Unknown until tested live.
- **NOT yet live-tested at all**: the deterministic orchestrator removal
  (commit `c176e1c`). Everything from before this commit was tested
  through the OLD LLM-orchestrated `run_agent()`. Need to confirm the new
  sequential version behaves identically against the real model, not just
  offline mocks.

### This is the single highest-priority next step
Get the HPC vLLM job running again and run:
```bash
cd ~/thesis-scenario-pipeline
git pull origin main
# ... get a live node, export LLM_BASE_URL/LLM_API_KEY (see HPC section below) ...
python3 agentic_pipeline/scripts/hpc_live_llm_verification.py all
```
Check specifically: (a) does `crossing_04`/`crossing_03`'s car get
`speed_evidence=clearly_faster_than_context` directly from Agent 1's
extraction (provenance should read `source=agent1_speed_evidence`, not
fall back to `engineering_assumption`); (b) does the whole thing still
work now that there's no LLM deciding step order — look for any behavior
difference in the trace format (it's different now — `[Step N] tool_name`
without an LLM "Agent:" narration line — that's expected, not a bug);
(c) still 19/19 valid.

## Known, deliberately-NOT-done items (in priority order)

1. **Live re-verification of items above** — highest priority, blocks
   trusting either of this session's two biggest changes.
2. **Regenerate `input/*.json` gold fixtures for real** via a live
   extraction run, to close the 2 known `test_semantic_correctness.py`
   failures properly (currently just documented-as-expected, not fixed).
   User was asked if they wanted this done now; deferred, not decided
   against — ask again.
3. **`run_feedback_iteration()`** — user explicitly said "I'm not
   satisfied with it" and flagged it needs fixing when we get to that
   step. No specifics given yet on what's wrong with it — ask the user
   directly what's bothering them about it before touching it.
4. **`car_path` robustness** (from the original, earlier-session
   briefing, still unaddressed) — derive from Agent 1's own gold-verified
   `maneuver` field instead of a fragile OSM-turn-lane-tag chain that
   currently never fires for any of the 19 real reports.
5. **Uncited flat constants in `complete_parameters.py`** (also
   carried over, unaddressed): `initial_s_m` offsets (`-20m` turning,
   `-25m` other, `cs * 0.2` cyclist non-crossing), timing constants
   (`conflict_time_s=4.0`, `trigger_time_s=1.0`) — flagged during an
   earlier "fill every gap" audit, never given the same citation/
   engineering_assumption-labeling treatment speed got.
6. **Full Layer-3 faithfulness evaluation** — discussed at length
   (a 5-layer evaluation framework: technical validity / contract
   compliance / faithfulness-against-manual-ground-truth / ablation /
   downstream execution), only informally spot-checked (the `crossing_03`
   discovery WAS effectively a Layer-3 finding). A real systematic version
   — manually annotate all 19 reports' actual speed evidence, compare
   against what the live model produces — would be strong thesis material
   and hasn't been built as a reusable script yet.
7. Whether any of the other 17 reports (beyond `crossing_03`/`04`) have
   a different kind of extraction gap (not speed-related) not yet
   systematically re-checked since the last fixes landed.

## HPC access — the practical part

**Login chain** (all interactive, OTP required every time — key-based
auth was investigated and ruled out, this cluster's gateway requires
password+OTP regardless of SSH key presence):
```bash
ssh shayma27@sshgate.tu-berlin.de   # password + OTP
ssh gateway.hpc.tu-berlin.de         # password, lands on frontend02
source ~/thesis-venv/bin/activate
cd ~/thesis-scenario-pipeline && git pull origin main
```

**Getting the vLLM server up**:
```bash
squeue -u $USER   # check if a job is already running
# if not:
sbatch /scratch/shayma27/llm-api/jobs/serve-llama31.sbatch
watch -n 10 squeue -u $USER   # wait for ST=R, Ctrl+C once it flips
```
Give it a minute after `R` to actually finish loading the model (SLURM
`R` only means the job started, not that vLLM is serving yet).

**Pointing the client at it**:
```bash
cat /scratch/shayma27/llm-api/server-node.txt
cat /scratch/shayma27/llm-api/server-port.txt
export LLM_BASE_URL="http://$(cat /scratch/shayma27/llm-api/server-node.txt):$(cat /scratch/shayma27/llm-api/server-port.txt)/v1"
export LLM_API_KEY="$(cat ~/.secrets/vllm_api_key)"
curl -s "$LLM_BASE_URL/models" -H "Authorization: Bearer $LLM_API_KEY"   # should return real JSON with "llama31"
```
**Common failure mode seen repeatedly this session**: the user runs the
`cat`/export commands but the shell still shows the OLD `LLM_BASE_URL`
from a previous, now-expired job — always re-run the export after
confirming a NEW job is `R`, don't assume a stale exported value is still
valid. The `h200_short` SLURM partition (this cluster's fast-turnaround
H200 queue) jobs seem to have a limited walltime — jobs from earlier in
a session reliably expire ("Connection refused") after enough real time
has passed; always re-check `squeue` first, don't assume.

**There is also a second Claude Code instance** installed directly ON
`frontend02` itself (via nvm, set up earlier by the user in a separate
chat) — it has direct filesystem/GPU access, no SSH/OTP needed from
there. This local/remote session cannot reach it directly (no shared
agent registry). If HPC access is being difficult from here, the user
can ask that other instance directly instead of relaying through this one.

## Repo / environment notes

- `.venv/bin/python3`, NOT plain `python3`, for all offline test runs —
  plain `python3` doesn't have `openai` installed.
- Git repo root is `/home/chimo/Shayma/Shayma` (one level above
  `agentic_pipeline/`) — there's a large, unrelated set of pre-existing
  deleted files under `../scenario_pipeline/` and a modified `../log.txt`
  showing in `git status` at all times this session — this is leftover
  from a superseded earlier pipeline attempt, NOT something to touch or
  worry about, confirmed harmless and pre-existing at the start of this
  session.
- `output/agentic/feedback_geometry_test/*` shows as locally modified in
  git status — leftover from an offline smoke-test connection attempt
  earlier this session, harmless, unstaged, never committed.
- `docs/hpc_quickstart.md` exists but was created outside this
  conversation's visibility (present in git status as untracked from the
  very start) — check its contents before assuming it's stale or
  authoritative; it may predate or duplicate the HPC section above.

## Working style notes for this user

- Wants real verification, not confident-sounding prose. Always run the
  actual check before claiming something works. When you can't verify
  (no live LLM access, etc.), say so explicitly rather than inferring.
- Corrects mistakes forcefully and is usually right to — when the user
  pushes back ("but what if..."), actually re ground the claim before
  defending it; this session had at least two cases (the "concrete
  evidence" overclaim about the orchestrator, the keyword-only backfill
  limitation) where the user's pushback was correct and led to a better
  design.
- Explicitly does not want frozen/gold-verified Agent 1 code touched
  without asking first — always propose before implementing anything
  touching it, even when a real gap is found.
- Appreciates being told the honest state plainly, including "this is
  not yet confirmed" rather than optimistic rounding.
- Under time/stress pressure, benefits from short, direct answers first,
  detail after — lead with the answer, not the reasoning.
