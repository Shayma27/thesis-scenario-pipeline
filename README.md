# Scenario Generation Pipeline

Converts manually prepared German accident narratives involving a motor vehicle and a cyclist into ASAM OpenSCENARIO files linked to OpenDRIVE road templates. The generated scenarios provide a basis for simulation-based investigation of car–cyclist conflicts and require visual review.
## How it works

Only **one step** in this pipeline calls a language model. Everything else —
map lookups, unit conversion, geometry, file generation, validation — is
plain deterministic Python.

```
German police report (text)
        │
        ▼
Stage 1 — extract_scenario.py       ◀── the only LLM call in the whole pipeline
        │  semantic JSON: who, what maneuver, where, how they relate
        ▼
Stage 2 — osm_enrichment.py          (deterministic — Nominatim + Overpass)
        │  real road-context information
        ▼
Stage 3 — complete_parameters.py     (deterministic)
        │  + speed_estimation.py       concrete simulation parameters: speeds, positions, lane IDs
        ▼
Stage 4 — generate_scenario.py       (deterministic)
        │  writes the .xosc (OpenSCENARIO) file; the .xodr (OpenDRIVE) road
        │  network is a pre-built template, just copied in, never generated
        ▼
Stage 5 — validate_outputs.py        (deterministic structural check)
        │
        ▼
  .xosc + .xodr  (pipeline's final output)
```

The pipeline ends with scenario generation and structural checks. Playback is a separate manual step: all 18 visually assessed scenarios reproduced the intended conflicts in esmini. In DYNA4, two longitudinal scenarios succeeded after file adaptations, while the tested intersection scenarios did not.

## Repository layout

```
├── src/                    the 5 pipeline stage modules — see src/README.md
├── utils/                  shared code used by src/, scripts/, and tests/ — see utils/README.md
├── scripts/                things you run — see scripts/README.md
├── tests/                  19 regression tests + fixtures — see tests/README.md
├── templates/              the 2 manually adapted OpenDRIVE road templates — see templates/README.md
├── data/                   per-stage snapshots of the 19-report corpus — see data/README.md
└── docs/                   reference material — see docs/README.md
```

Each folder has its own short README explaining exactly what's in it and why.

## HPC setup

See [HPC setup and connection guide](docs/hpc_setup.md) for SSH access,
starting the model server, current connection settings, and the recorded
thesis configuration.

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
