THESIS MASTER CONTEXT
Last updated: 2026-09-05. Update this file whenever the pipeline or a
supervisor decision changes substantially — don't let it go stale.

TITLE
Generation of Car-Cyclist Accident Scenarios Using Foundation Models

OBJECTIVE
Design, implement, and evaluate a pipeline that uses a foundation model to
generate car-cyclist accident scenarios in standardized ASAM OpenSCENARIO
format, suitable for validating ADAS functions addressing car-cyclist
conflicts. Scenarios are parametrized using real-world accident data and
demonstrated in a simulation environment (esmini, DYNA4).

INPUT DATA
19 Berlin Police accident reports (car/truck vs. cyclist collisions),
German-language free text. All 19 have been manually verified field-by-field
against a hand-built gold-reference answer key
(gold_reference.py / docs/gold_reference_audit.md), independently
cross-checked by a second reviewer. 18 of the 19 form the active
generation/simulation corpus; 1 (turning_07, a parking-lot access conflict)
is excluded from generation because neither road template can represent
that topology, though it remains part of the 19-report extraction/gold set.

FOUNDATION MODEL
Llama 3.1 8B Instruct.
Self-hosted using vLLM on TU Berlin HPC.
Temperature 0, schema-constrained JSON output.
Used for exactly one step in the automated pipeline: semantic extraction
from report text (extract_scenario.py). No other automated pipeline stage
makes an LLM call.
(A second, optional, human-triggered LLM call exists — run_feedback_iteration()
in pipeline.py, reachable via the interactive [f] menu in run.py/run_all.py —
used only after a human watches a generated scenario and reports a specific
problem in German. It is not part of the automated run_agent() sequence.)

TARGET STANDARD
ASAM OpenSCENARIO 1.3.1 (entities, trajectories, timing) +
ASAM OpenDRIVE (static road network), generated via the `scenariogeneration`
Python library.

CURRENT PIPELINE
1. extract_scenario — the only automated LLM call; produces a structured
   JSON record of semantic facts only (no simulator-specific values).
2. query_osm — deterministic; enriches with real OpenStreetMap data
   (Nominatim geocoding, Overpass road tags) and selects one of two fixed
   OpenDRIVE templates based on detected topology.
3. complete_parameters — deterministic; turns semantic facts + map data into
   concrete numeric simulation parameters (speeds, positions, lane IDs),
   using cited traffic-engineering defaults where the report is silent.
4. generate_scenario — deterministic; writes the .xosc (OpenSCENARIO) and
   .xodr (OpenDRIVE) files.
5. validate_and_fix — deterministic structural check (do references resolve,
   are lane positions valid); retries generation on failure up to a fixed
   limit.
Orchestrated by pipeline.py's run_agent() as five fixed sequential steps
(not an LLM tool-calling loop — that was tried and removed; it always
executed the same order anyway).

ROAD NETWORK
Two fixed, hand-built OpenDRIVE templates, chosen deliberately over
per-report custom road reconstruction (out of scope for this thesis):
- straight_road.xodr — one real road, 500 m, for midblock locations.
- intersection_4way.xodr — a real 4-way junction with real connector-road
  geometry for each turn.
Both model exactly one real driving lane and one real biking lane per
direction, regardless of any report-stated or OSM-tagged lane count.

CONFIRMED DESIGN DECISIONS
- Exactly one LLM call per report in the automated pipeline; everything
  downstream is deterministic Python with zero LLM dependency.
- Stage 1's (extraction's) output is frozen once produced — a dedicated
  invariant check (provenance.py: check_agent1_preserved) raises if any
  downstream stage overwrites an already-populated extraction field.
- A second LLM pass to validate the first extraction (as some published
  pipelines do) was considered and rejected: doubles inference cost/latency
  on a resource-constrained shared HPC deployment, with no guarantee a
  second pass by the same model judges groundedness more reliably than
  deterministic text-substring checks.
- Speed defaults are grounded in cited sources, never invented: StVO §3
  Abs. 3 Nr. 1 (50 km/h innerorts default), RiLSA (36 km/h clearance speed,
  40 km/h approach speed), Schleinitz et al. 2014 (empirical cycling speed
  study, n=28). The LLM only classifies qualitative speed evidence into one
  of six fixed categories from report text; deterministic code turns that
  category into a number, checked for direction-consistency before use.
- The ego (motor) vehicle does NOT need to be reactive/responsive to the
  cyclist — supervisor-confirmed scope (see SUPERVISOR DECISIONS below).
  Fixed-route, deterministic collision scenarios are sufficient.
- turning_07 is permanently excluded from the generation/simulation corpus
  (parking-lot topology, no template fits); stays in the 19-report
  extraction/gold-reference set.
- Every non-report-derived value in the output carries a provenance label
  (explicit_from_report / osm_tag / engineering_assumption / legal_default)
  recording its source — continuously tested against the actual output
  (test_constants_provenance.py).

OPEN QUESTIONS
- DYNA4 demonstration: never yet attempted. DYNA4 doesn't run on a personal
  laptop, so the first test happens live at the university with the
  supervisor's help. Recommended scenario: turning_01 (truck turning right
  into a cyclist on a separated cycle track, real 4-way junction, fully
  automatic resolution, clean esmini review).
- Literature review: 4 named papers/techniques (CrashAgent, the base Scenic
  language, TRACE, "Traffic Scenario Orchestration via Constraint
  Satisfaction") are known leads from a prior corpus review but have no
  full citation yet — pending a literature-search session with paper
  access (see docs/codex_literature_todo.md for the exact prompt to use).
  Already fully resolved: Safa/Jiang/Zheng's Extended Scenic DSL pipeline
  (arXiv:2602.20644v1), SoVAR (Guo et al., ASE 2024, DOI
  10.1145/3691620.3695037). Partially resolved (arXiv ID only, need full
  author/title): SAFE (arXiv:2502.02025), ARISE (arXiv:2601.14743).
- Dataset Analysis chapter (Aufgabenstellung Task 2): the
  turning/crossing/longitudinal classification scheme is a project-internal
  design choice, not yet tied to a cited, established accident taxonomy.
  An NHTSA dataset comparison has not yet been done.
- heading_reference (an extraction schema field capturing a "toward X"
  destination phrase) has no downstream consumer yet — retained for
  traceability only.
- 9 of the 18 active reports rely on a manually reviewed topology override
  rather than fully automatic resolution (each labeled with its specific
  basis — a re-verified fact, an accepted best-fit between the only two
  templates, or an explicitly flagged best guess).
- One report (crossing_05) has a collision location that even manual map
  review could not resolve with certainty.

EVALUATION
- Extraction (Stage 1): 19/19 reports in full field-level agreement with
  the manually verified, independently cross-checked gold reference.
- Generation/simulation corpus: 18 of 19 reports (1 excluded, see INPUT
  DATA).
- esmini: all 18 active reports individually watched and confirmed correct
  by the user — trajectories, collision points, and timing match their
  source reports. A final automated geometry sweep (speed discontinuities,
  impact-point gaps) across all 18 found zero issues.
- DYNA4: not yet performed (see OPEN QUESTIONS).
- Automated regression coverage: 19 test files covering extraction schema
  validity, semantic correctness against gold reference, provenance
  consistency, lane-type safety, and per-scenario-type trajectory/geometry
  correctness. All passing as of last verification (2026-09-04).

SUPERVISOR DECISIONS
- Supervisor: Esra.
- Confirmed by email, 2026-08-04: the ego (motor) vehicle does NOT need to
  be reactive/responsive to the cyclist; fixed-route collision scenarios
  are sufficient scope for DYNA4 validation.
- Explicit verbal expectation (repeated across multiple meetings, not just
  the written Aufgabenstellung): genuine, substantive use of AI to fill
  missing/uncertain parameters — not a token/decorative LLM call. This has
  directly shaped pipeline design (e.g., why speed evidence classification
  stays in the LLM step while numeric concretization is deterministic).
- As of 2026-08-26, supervisor's stance: "keep going, waiting for final
  results."
- Aufgabenstellung explicitly requires demonstration in both esmini and
  DYNA4.

HOW TO USE THIS FILE
Paste this whole file at the start of a new AI chat with:
"Here is the current master context of my thesis. Treat this as
authoritative. Do not contradict it unless I explicitly tell you something
has changed."
Update it yourself (or ask an assistant with verified repo access to update
it) whenever the pipeline changes substantially, a supervisor decision is
made, or an open question gets resolved — this file is only useful if it
stays current.
