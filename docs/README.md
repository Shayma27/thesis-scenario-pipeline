# docs/

Reference material the code or a reader actually needs — not session notes
or drafts (those are kept outside the repo).

| File | Why it's here |
|---|---|
| `manual_classification_reference.md` | The 19 source police reports. Parsed at runtime by `utils/report_loader.py` — this is data the code depends on, not just documentation. |
| `modeling_assumptions.md` | The deliberate scope decisions behind the pipeline's design (e.g. why only two road templates exist), referenced throughout the code's own comments. |
| `gold_reference_audit.md` | How the hand-verified gold-reference answer key (`tests/gold_reference.py`) was built and independently cross-checked — the evidence behind the 19/19 extraction-agreement claim. |
| `topology_detection_report.md` | Per-report results of automatic road-topology detection, which decides which of the two OpenDRIVE templates each scenario uses. |
| `osm_audit_report.md` | Per-report manual verification of every OSM-derived claim against a real map — 9 of the 18 active reports have an explicit manual-review note. |
