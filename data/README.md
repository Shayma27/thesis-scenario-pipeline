# data/

Per-stage snapshots of the 19-report corpus. **None of this is read or
written by a live run of the pipeline** (`pipeline.run_agent()` takes one
report as an argument and doesn't touch these folders) — it's all produced
by the batch/dev scripts in `scripts/`, and consumed by the test suite so
tests don't need a live LLM/OSM connection to run.

| Folder | Produced by | Contains |
|---|---|---|
| `stage1_extracted/` | `scripts/extract_all.py` | Stage 1 output for all 19 reports — the gold-verified extraction corpus (19/19 agreement, see root README). |
| `stage2_osm_enriched/` | `scripts/build_osm_fixtures.py` | Stage 2 output, a dev/test fixture. **Currently stale** relative to the live pipeline (built before a later fix to `osm_enrichment.py`'s heading calculation) — regenerate with `scripts/build_osm_fixtures.py` if this matters for a specific check. |
| `stage4_generated/` | `scripts/run_all.py` / `scripts/run.py` | The 18 final, confirmed scenarios. Each folder holds that scenario's full history: `*.agent1.json` (Stage 1, frozen), `*.osm_enriched.json` (after Stage 2 only), `*.enriched.json` (after Stage 3, the complete record the `.xosc` was built from), the generated `*.xosc`, and a copy of the `.xodr` template used. |
| `osm_cache/` | `osm_enrichment.py` (automatically) | Cached Nominatim/Overpass HTTP responses. Gitignored — a regenerable network cache, not source data. |
| `hpc_live_llm_verification/` | `scripts/hpc_live_llm_verification.py` | Output from the HPC connectivity smoke test. |
