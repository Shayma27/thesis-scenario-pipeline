# src/

The 5 pipeline stages, plus 4 modules they share. This is the actual
pipeline — everything here runs on every report. Orchestrated by
`pipeline.py`'s `run_agent()`, always in this order.

| File | Role | What it does |
|---|---|---|
| `extract_scenario.py` | Stage 1 | The only LLM call in the pipeline. Turns raw German report text into structured JSON (who was involved, what maneuver, where). |
| `osm_enrichment.py` | Stage 2 | Deterministic. Looks up the real road via OpenStreetMap (Nominatim + Overpass) — geometry, lane counts, headings. |
| `complete_parameters.py` | Stage 3 | Deterministic. Turns semantic facts + map data into concrete simulation numbers (speeds, positions, lane IDs), using cited traffic-engineering defaults where the report is silent. |
| `speed_estimation.py` | Stage 3 helper | Turns the LLM's qualitative speed classification (from Stage 1) into an actual km/h value, grounded in StVO/RiLSA/Schleinitz et al. — never invented. |
| `template_selector.py` | Stage 3/4 helper | Picks `straight_road.xodr` or `intersection_4way.xodr` based on the detected topology. |
| `generate_scenario.py` | Stage 4 | Writes the `.xosc` (OpenSCENARIO) file, positioning actors using the selected `.xodr` template's real geometry. **Does not generate the `.xodr` itself** — that's a pre-built template file, just copied into the output folder unchanged. |
| `validate_outputs.py` | Stage 5 | One-shot structural check on the generated `.xosc`/`.xodr` pair (valid XML, every actor/lane/road reference resolves, trajectory timestamps don't go backwards). No retry loop — the pipeline is deterministic, so a failure here means a bug upstream. |
| `pipeline.py` | orchestrator | Calls the 5 stages above in fixed order via `run_agent(report_text, scenario_id)`. Contains all the actual logic; has no command-line interface of its own — see `scripts/run.py` for that. |
| `provenance.py` | safety check | Guarantees Stage 1's output is never silently overwritten by a later stage — a field it already set can only be read, never changed. |

## `pipeline.py` vs. `scripts/run.py`

`pipeline.py` is a library — it defines `run_agent()` but doesn't run
anything by itself. `scripts/run.py` is the thin command-line wrapper that
actually calls `run_agent()`, reads a report from stdin, and opens esmini
afterward. You run `scripts/run.py`; `pipeline.py` is what it calls into.
