# Scenario Generation Pipeline

Converts a German Berlin police accident report (car/truck vs. cyclist) into a
standardized **ASAM OpenSCENARIO + OpenDRIVE** scenario, ready to play back in
[esmini](https://github.com/esmini/esmini) and validate against ADAS functions
that address car-cyclist conflicts.

## Not an "AI agent" pipeline

Only **one step** in this pipeline calls a language model. Everything else —
map lookups, unit conversion, geometry, file generation, validation — is
plain deterministic Python that produces the same output every time for the
same input:

```
German police report (text)
        │
        ▼
Stage 1 — extract_scenario.py       ◀── the only LLM call in the whole pipeline
        │  semantic JSON: who, what maneuver, where, how they relate
        ▼
Stage 2 — osm_enrichment.py          (deterministic — Nominatim + Overpass, no LLM)
        │  real road geometry, lane counts, headings, topology
        ▼
Stage 3 — complete_parameters.py     (deterministic, zero network)
        │  + speed_estimation.py       concrete simulation parameters: speeds, positions, lane IDs
        ▼
Stage 4 — generate_scenario.py       (deterministic, zero network)
        │  writes the .xosc (OpenSCENARIO) file; the .xodr (OpenDRIVE) road
        │  network is a pre-built template, just copied in, never generated
        ▼
Stage 5 — validate_outputs.py        (deterministic structural check)
        │
        ▼
     esmini                          (external C++ simulator — plays the .xosc/.xodr)
```

You'll still see the word "Agent" here and there in the code (a leftover
naming convention: `AgentState`, `run_agent()`, the `"agent1_speed_evidence"`
field in the extraction schema). It refers to *stage* in that older sense,
not to an autonomous LLM agent — stages 2–5 involve no model call at all,
and stage 1's LLM call is a single, schema-constrained extraction request,
not an agentic loop.

Stage 5 is a one-shot check, not a retry-until-correct loop: it verifies the
generated `.xosc`/`.xodr` pair is structurally sound — every actor referenced
in the story actually exists, every actor's starting lane/road actually
exists in the road network, trajectory timestamps never go backwards, files
are well-formed XML. Since stages 1–5 are all deterministic, a failure here
means a bug in an earlier stage, not something a second attempt would fix
without changing anything — so there's no retry loop to explain.

## Repository layout

```
├── src/                    the 5 pipeline stage modules — see src/README.md
├── utils/                  shared code used by src/, scripts/, and tests/ — see utils/README.md
├── scripts/                things you run — see scripts/README.md
├── tests/                  19 regression tests + fixtures — see tests/README.md
├── templates/              the 2 hand-built OpenDRIVE road templates — see templates/README.md
├── data/                   per-stage snapshots of the 19-report corpus — see data/README.md
└── docs/                   reference material — see docs/README.md
```

Each folder has its own short README explaining exactly what's in it and why.

## Requirements

```bash
pip install -r requirements.txt   # scenariogeneration, openai
```

The LLM client (`utils/llm_client.py`) talks to a vLLM server hosting Llama
3.1 8B Instruct, configured entirely through environment variables:

```bash
export LLM_BASE_URL="http://gpu026:8000/v1"   # default shown
export LLM_API_KEY="EMPTY"                     # default shown
export LLM_MODEL="llama31"                     # default shown
```

esmini itself is a separate, external binary (not part of this repo) — see
[esmini](https://github.com/esmini/esmini).

## Running it

```bash
# One report, interactively, with esmini playback and a feedback loop
python3 scripts/run.py

# The full 18-report corpus, batch, with esmini review per scenario
python3 scripts/run_all.py

# Stage 1 only, batch — useful for reviewing extraction before running
# OSM enrichment / parameter completion / generation on top of it
python3 scripts/extract_all.py
```

## Running the tests

```bash
python3 tests/test_semantic_correctness.py   # or any other tests/test_*.py
```

Most tests run fully offline against the frozen `data/` snapshots. The one
exception is `tests/test_feedback_geometry.py`, which needs a live LLM
connection (it exercises the feedback-correction loop, not the main pipeline).

## Status

- **Extraction (Stage 1):** 19/19 reports in full field-level agreement with
  the manually verified, independently cross-checked gold reference.
- **Generation/simulation corpus:** 18 of 19 reports. The 19th describes a
  parking-lot access conflict that neither of the two OpenDRIVE templates can
  represent, so it's excluded from generation (it stays in the extraction/gold
  set — see `data/stage1_extracted/`).
- **esmini:** all 18 active reports individually watched and confirmed
  correct by the thesis author; a final automated geometry sweep across all
  18 found zero issues.
- **DYNA4:** not yet performed.
