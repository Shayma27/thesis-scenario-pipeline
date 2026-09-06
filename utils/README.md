# utils/

Shared code that isn't itself a pipeline stage — used by `src/`, `scripts/`,
and `tests/` alike.

| File | What it does |
|---|---|
| `llm_client.py` | The vLLM/OpenAI-compatible client pointed at the TU Berlin HPC endpoint. Configuration comes entirely from environment variables (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) — no secrets hardcoded. |
| `report_loader.py` | Parses the 19-report corpus (`docs/manual_classification_reference.md`) into `(scenario_id, report_text, scenario_type)` records. Used by the batch scripts and by the test suite — **not** by a live `run_agent()` call, which takes one report as an argument directly. |
