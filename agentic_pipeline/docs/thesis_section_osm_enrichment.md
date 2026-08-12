# OSM Enrichment Stage (Agent 2) — Hardening, Verification, and Design Decisions

## 1. Purpose and Scope

The OSM enrichment stage (`osm_enrichment.py`) sits between the semantic
extraction stage (Agent 1, frozen and gold-verified — see the extraction
methodology section) and parameter completion (Agent 3). Its job is narrow
by design: take the location and road-context facts Agent 1 already
extracted from the report text, and enrich them with real map data —
geocoded coordinates, road topology (midblock vs. junction), lane counts,
posted speed limits, and cycling infrastructure — pulled live from
OpenStreetMap via two public APIs, Nominatim (geocoding) and Overpass
(raw tagged road/way data).

This section documents a substantial hardening pass on this stage:
several previously undiagnosed bugs affecting geocoding precision and
topology classification, a structural safety invariant protecting Agent
1's output from being silently altered downstream, a lane-placement
correctness bug affecting actor positioning, and the manual verification
methodology used to catch and confirm all of the above against real map
data rather than assumption.

## 2. Design Decision: Retaining OSM, With a Narrower Role

Before this hardening work began, the question of whether OSM enrichment
was worth keeping at all was reconsidered explicitly. An external review
(via a separate coding assistant, referred to here as the "Codex audit")
raised legitimate concerns: several fields mixed observed OSM values with
modeling assumptions under a single label, defaults were sometimes
indistinguishable from measurements, and the stage's precision had never
been checked against ground truth.

Rather than accept or reject that critique on priors, each concrete
finding was verified against real OpenStreetMap data before deciding
what to do about it. This distinction mattered: **every geocoding or
topology failure investigated turned out to be a specific, fixable
defect in how the pipeline queried and used available information — not
evidence that OpenStreetMap's underlying data was unreliable.** That
pattern (Section 4) is the empirical basis for keeping OSM enrichment in
the pipeline, with its role narrowed to what map data can actually
support: location validation, topology classification, and posted speed
limits — not invented precision (e.g., a literal accident speed) that
the source data was never going to contain.

This positions the contribution accurately relative to prior work: unlike
approaches that reconstruct the full accident-site road network from OSM
(e.g., the TRACE paper's topology-aware CARLA reconstruction — full
citation to be added), this pipeline uses OSM to *classify and enrich*
against a small, fixed set of hand-built OpenDRIVE templates. The correct
framing is **OSM-assisted topology detection and scenario enrichment**,
not full-fidelity road reconstruction from OSM — a scoping decision made
explicit precisely because the templates' fixed geometry (Section 5)
would otherwise be silently violated.

## 3. Architecture: Two Data Sources, One Enrichment Pass

`enrich_with_osm(data, cache_dir)` runs once per report, after
extraction:

1. **Geocoding (Nominatim)** — turns the report's road name(s) into an
   approximate coordinate. Nominatim is free-text search over place
   names; it has no structural concept of "intersection," which is the
   root cause of several bugs below.
2. **Nearby-road query (Overpass)** — given a coordinate and radius,
   returns every real OSM road ("way") in the area with its actual tags
   (`lanes`, `maxspeed`, `cycleway`, `oneway`, ...) and geometry (the
   literal sequence of coordinates making up that road segment).
3. **Tag extraction** — the roads whose `name` tag matches the report's
   street name(s) are kept as `matched_roads`, and bike-facility type,
   lane counts, road heading, and speed limit are derived from their
   tags.

Everything derived is recorded with its own `source` label in
`missing_parameters` (e.g. `osm_tag`, `explicit_from_report`,
`engineering_assumption`) — a provenance mechanism that already existed
before this work and is extended, not replaced, by it (Section 6).

## 4. Geocoding Precision: Four Independent Bugs, One Underlying Pattern

Four distinct defects were found and fixed, each verified against
ground truth (either OpenStreetMap's own tagged address/way data, or
manual map inspection), not merely asserted.

### 4.1 House-number priority

`_build_location_queries()` previously tried the bare street name (e.g.
`"Mühlenstr., Berlin, Germany"`) before the house-number-qualified query,
even when a house number was available. Since Berlin repeats street
names across boroughs, the bare query would silently succeed on a
same-named street elsewhere in the city and the more precise,
house-number-qualified query was never tried. Fixed by trying the
house-number query first. Verified for two reports: `crossing_01`
(house no. 89) now resolves to OSM node `831808684`, whose own
`addr:housenumber`/`addr:street` tags confirm it as the exact address;
`turning_07` (house no. 139) — this fix also surfaced a second,
previously-undetected wrong geocode (the bare-name query had landed in
Lankwitz; the real, Geoportal-Berlin-sourced address is in Marienfelde,
a different part of the city).

### 4.2 Destination-reference filtering in the deterministic street extractor

A separate, LLM-free street-name extractor (`_extract_street_candidates`)
supplies the deterministic topology-classification logic (Section 5).
Its filter for dropping destination references (`"...in Richtung
STREET2"`, meaning "heading toward," not a second collision-site
cross-street) only matched when the destination phrase directly followed
an already-extracted street name. Report phrasings with an intervening
verb (`"...von der Poststraße kommend in Richtung Spreeufer"`) broke this
adjacency check, so a spurious third street candidate was kept and the
topology logic — which only handles exactly one or two candidates —
fell back to `needs_manual_review` unnecessarily. Fixed by dropping the
adjacency requirement: any street immediately following "(in) Richtung"
is now always treated as a destination reference. Fixed two reports
(`crossing_02`, `longitudinal_02`); the second case was cross-checked
against Agent 1's own (unaffected, gold-verified) extraction, which had
already correctly identified the true cross-street.

### 4.3 Shared-node geocoding as the primary method for intersections

Nominatim's free-text geocoder was found to reliably return **no result**
for combined two-street queries (`"STREET1 / STREET2, Berlin, Germany"`
and the space-joined equivalent both tested empty against live
Nominatim), silently discarding the second street name's disambiguating
power and falling back to a single-name query. A separate mechanism
already existed elsewhere in the codebase for topology detection —
finding the real OSM node where two named ways' geometries physically
meet, via the Overpass API — and was generalized into the primary
geocoding path (`_shared_node_geocode`, `_find_shared_node_any_anchor`).
This moved the geocoded point for **7 of 19 reports**, several by more
than a kilometer, indicating the imprecision was systemic across the
corpus rather than confined to isolated cases.

A further refinement was needed when the *primary* road's own name was
itself ambiguous: `crossing_06`'s "Oranienburger Straße" geocodes on its
own to a well-known street in central Mitte, ~7 km from the report's
actual location in Reinickendorf — outside even the shared-node search's
5 km radius. The fix retries anchored at each *other* named street's own
geocode when the primary anchor fails; "Taldorfer Weg" (the report's
other named street) geocodes almost exactly to the correct site on its
own. Verified directly against a specific OSM way manually located and
supplied for cross-checking (result within ~15 m).

### 4.4 Unused structured fields

Agent 1's extraction schema includes an `intersection` field, populated
when the report explicitly names a junction (`"Kreuzung Waldnesselweg /
Erwin-Bock Str."`), separately from `primary_road`/`secondary_road`.
This field was never read by the geocoding logic. For reports where the
primary road is long (`"Müggelheimer Damm"`, several kilometers) and no
secondary road is stated, this meant the geocoding had no way to reach
the report's actual, explicitly-named collision site. Wiring this field
into the same road-name pool used for shared-node geocoding fixed
`crossing_03`'s and `crossing_08`'s geocoding; `crossing_03`'s corrected
derived speed limit (70 → 50 km/h) was independently confirmed against a
screenshot of the real OSM way tags at the correct location.

A fifth, related widening — feeding the deterministic street extractor's
output into the geocoding candidate pool generally, not just for
topology — was added for cases where no structured field captures a
report-mentioned landmark street at all (`longitudinal_01`'s "In Höhe der
Braunsdorfstraße"). This did not fully resolve that specific case (the
report names two plausible landmark streets with no structural signal
distinguishing which is the actual collision site), which was instead
recorded as an explicit, scoped manual correction (Section 6) rather than
forced through automated disambiguation.

## 5. Structural Correctness Fixes

### 5.1 Lane-type safety

Both OpenDRIVE templates (`straight_road.xodr`, `intersection_4way.xodr`)
model exactly **one real driving lane and one real biking lane per
direction**, at fixed lane IDs, regardless of what lane count a report or
OSM tag implies. Prior to this work, actor lane-assignment logic in
`complete_parameters.py` computed lane IDs as a function of the reported
lane count (`-max(1, n)`, `-(n+1)`), which — whenever that count exceeded
1 — silently placed a car or cyclist on a border, shoulder, or sidewalk
lane rather than a real driving or biking lane. This passed structural
validation (`validate_outputs.py` checks only that a referenced lane ID
exists, not its type) and was invisible without checking lane types
directly against the template geometry.

Fixed by removing lane-count dependence from lane-ID selection entirely
(both actors now always resolve to the template's one real lane of the
intended type), with a `lane_count_exceeds_template_capacity` provenance
record whenever a report/OSM implies more lanes than either template can
represent. A dedicated regression test (`test_lane_type_safety.py`) now
verifies, for all 19 reports, that every actor's assigned lane resolves
to the correct real lane type in the actually-selected template. Verified
concretely: `crossing_04` (OSM-reported 3 lanes) previously placed its
cyclist on lane `-4` (the template's sidewalk lane); it now correctly
resolves to `-2` (the real biking lane).

A related second calculation — the cyclist's *drawn trajectory* in
`generate_scenario.py`, computed separately in world coordinates from
the teleport position above — had the same defect: its lateral-offset
formula also scaled with the reported lane count, which could offset the
visible path by several meters from the position the cyclist is actually
teleported to (measured: 7 m divergence for a synthetic 3-lane case).
Fixed identically, and confirmed via a full end-to-end generation and
structural validation of a real multi-lane report (`crossing_04`).

### 5.2 Agent-1 preservation invariant

To guarantee downstream stages (OSM enrichment, parameter completion)
can only *fill* fields Agent 1 left unset and never *alter* a field it
already populated, a dedicated module (`provenance.py`) mechanically
diffs a snapshot of Agent 1's raw output against the pipeline state after
each downstream stage, raising immediately if any previously-non-null
field changed. This is enforced at runtime in the agentic pipeline
(`pipeline.py`) and verified for all 19 reports by a fast, offline
regression test (`test_agent1_preservation.py`). Each report's raw
extraction is now also persisted to its own file
(`<scenario_id>.agent1.json`) before any enrichment runs, distinct from
the final merged `<scenario_id>.enriched.json` — so the two can always be
diffed directly rather than trusted on assertion.

## 6. Manual Verification Methodology

A read-only audit tool (`osm_audit_report.py`) was built to make manual
verification tractable and non-repetitive: for all 19 reports, it dumps
every OSM-derived claim (geocoded coordinate with a direct
OpenStreetMap map link, topology classification, bike facility, lane
counts, heading, speed limit) alongside its provenance label, into a
single reviewable page. This converts "check whether OSM enrichment is
accurate" from an open-ended task into a bounded, one-pass checklist.

Findings from that manual pass are encoded as one of two things,
deliberately distinguished so the difference is never lost:

- **A code fix**, when the failure was systemic (affects the general
  logic, likely recurs) — the four geocoding bugs in Section 4.
- **A scoped, documented manual override**, when a report's location is
  genuinely ambiguous even to deterministic logic or a human
  cross-checking a map. Nine reports carry a topology override in
  `_MANUAL_TOPOLOGY_OVERRIDES`; each is explicitly labeled as one of:
  an independently re-verified fact (e.g. an exact node's arm count
  manually counted), a deterministic result accepted as the closer of
  only two available templates (e.g. a genuine 3-way junction, modeled
  as the 4-way template), a report that names a real intersection the
  logic's exactly-1-or-2-candidate constraint can't reach, or — for one
  report (`crossing_05`) — an explicit best guess, flagged as such
  rather than presented as verified, since even manual map review could
  not resolve the exact location.

This distinction matters for how the thesis should describe these
values: they are not uniformly "OSM-derived" or uniformly "verified" —
each has a specific, recorded provenance.

## 7. Results

- **Topology/template resolution**: 11 of 19 reports resolved
  conclusively by the deterministic logic at the start of this work; all
  19 now have a defensible, documented template selection — resolved
  automatically, forced deterministically by scenario type (the two
  `longitudinal` reports, which always use `straight_road.xodr`
  regardless of topology, by definition of a single-road maneuver), or a
  clearly-labeled manual override.
- **Geocoding**: at minimum 9 of 19 reports had a materially incorrect
  or imprecise geocoded location corrected this session (2 via
  house-number priority, 2 via the extractor filter fix, up to 7 more via
  the shared-node-as-primary-method change, with some overlap between
  these groups).
- **Regression coverage**: two new fast, offline (cache-only, live
  network mocked out) test suites were added
  (`test_agent1_preservation.py`, `test_lane_type_safety.py`), running in
  seconds and covering all 19 reports, alongside the pre-existing
  extraction-schema and semantic-correctness gates. All four pass at the
  time of writing.

## 8. Known Limitations and Deliberately Out-of-Scope Items

- **Bike-facility position** is not comprehensively re-verified against
  the map for all 19 reports. Analysis showed this field mostly
  collapses to the same simulated outcome (the template's one real bike
  lane) regardless of its exact source, making it lower-priority than
  geocoding/topology/speed — a deliberate scoping decision, not an
  oversight.
- **`detect_topology()`'s own geocoding fallback** has not yet been
  unified with the multi-anchor retry added to `enrich_with_osm`
  (Section 4.3); it still uses a simpler, two-candidate fallback list
  internally, which is why `crossing_06` needed a manual topology
  override even after its *geocoding* was fixed. Unifying these is a
  natural, low-risk follow-up.
- **Historical accuracy** is not addressed: all OSM data reflects the
  current map, not necessarily conditions at the time of each historical
  report. This is a known, general limitation of using live OSM data for
  historical accident reconstruction, not something resolved by this
  work.
- **`crossing_05`** remains a genuinely unresolved case: the report names
  only one street with no distinguishing detail, and manual map review
  could not identify the exact collision point either. Its topology is
  modeled as the closer-available-template best guess, explicitly
  flagged as such.

---

*Supporting artifacts referenced above*: `docs/modeling_assumptions.md`
(template geometry constraints), `docs/topology_detection_report.md`
(original topology baseline), `docs/osm_audit_report.md` /
`docs/osm_audit_report.json` (full manual-verification audit data),
`provenance.py`, `test_agent1_preservation.py`, `test_lane_type_safety.py`.
