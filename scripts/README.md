# scripts/

Things you run. None of these contain pipeline logic — they call into
`src/pipeline.py` and report the result. See `src/README.md` for the actual
pipeline.

| File | What it's for |
|---|---|
| `run.py` | **The main demo.** Interactive: paste one report, watch it generate, view it in esmini, optionally give feedback in German to correct it. |
| `run_all.py` | Batch version of `run.py` over the full 18-report corpus, with an esmini review menu after each one. This is how every scenario in `data/stage4_generated/` was produced and confirmed. |
| `extract_all.py` | Runs **only** Stage 1 (extraction) over the corpus, non-interactively. Produced `data/stage1_extracted/` — useful for reviewing what the LLM extracted before trusting anything built on top of it. |
| `build_osm_fixtures.py` | One-off: runs Stage 2 (OSM enrichment) over the frozen Stage 1 corpus and saves the result. Produced `data/stage2_osm_enriched/`, a fixture so tests don't need a live OSM query every run. Re-run this if `osm_enrichment.py` changes. |
| `osm_audit_report.py` | Read-only audit: dumps every OSM-derived claim (geocoding, topology, bike facility, lane counts) per report into `docs/osm_audit_report.md`, for checking against a real map by hand. |
| `review_generated.py` | Re-watches already-generated scenarios in esmini without needing a live LLM connection — for a machine that can't also reach the HPC vLLM server. No feedback option (that needs the LLM). |
| `hpc_live_llm_verification.py` | Smoke test: confirms the vLLM endpoint on HPC actually responds before trusting a real run. |
