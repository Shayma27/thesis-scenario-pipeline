Context: I'm building an agentic pipeline (bachelor thesis) that converts
German Berlin police accident reports (car/truck vs. cyclist collisions)
into ASAM OpenDRIVE + OpenSCENARIO simulation files. The pipeline has three
stages: (1) semantic extraction via a local LLM — done, frozen, gold-verified
19/19; (2) OSM/OpenStreetMap enrichment (geocoding, topology classification,
lane counts, speed limits) — done, just hardened and verified against real
map data; (3) parameter completion — turning everything from stages 1+2 into
concrete simulation parameters (actor speeds, positions, timing offsets,
lane assignments). Stage 3 currently exists as a first pass, but it's pure
deterministic Python: flat constant defaults (e.g. "a turning car always
gets this exact speed"), no reasoning about what's actually knowable from
the report versus genuinely unknown.

I want to rebuild stage 3 to use an LLM instead, and this is the most
important part of my thesis. The core problem: a police report often states
some things exactly (a named street, an explicit maneuver, sometimes a
speed) and leaves everything else unstated (the cyclist's exact speed, the
exact gap between vehicles, exact timing). The current code just invents a
single fixed number for anything unstated. I want the new version to
instead reason about what's genuinely knowable versus uncertain, and
represent that honestly — likely as a constraint, a range, or a documented
default with an explicit "this is an assumption, here's why" label, not a
silently-invented point value.

I already have partial grounding from an earlier session: SoVAR's approach
of representing missing accident information "through structured constraints
and generalizable parameterization rather than treating one invented
concrete value as fact," and Safa/Jiang/Zheng's Extended Scenic DSL pipeline
(which separates high-level semantic understanding from low-level scenario
rendering, using a purpose-built intermediate representation). A prior
review of my own pipeline also suggested this general shape: report facts →
validated map constraints → deterministic geometry → constraint solving →
documented ranges/defaults.

What I need from you, using your access to the paper corpus:

1. Go deeper than the earlier surface-level citations. For SoVAR
   specifically — and the Extended Scenic DSL paper — what EXACTLY is the
   mechanism they use for representing and resolving missing/uncertain
   parameter values? Not just "they use constraints" — the actual
   technique: is it a constraint-satisfaction solver, a range with sampling,
   an LLM prompted for confidence/uncertainty directly, a rule-based
   fallback hierarchy, something else? I need enough mechanistic detail to
   actually design against it, not just cite it.

2. Search the rest of the corpus (not just the three papers already
   surfaced) for anything else directly relevant to: (a) LLM-based inference
   of missing/uncertain structured parameters from natural-language incident
   reports, specifically in an autonomous-vehicle-safety or accident-
   reconstruction context; (b) representing uncertainty in generated
   simulation/scenario parameters (ranges, distributions, confidence,
   multiple candidate values) rather than single invented numbers; (c) any
   paper that specifically discusses using an LLM to complete/parameterize
   a scenario while respecting hard geometric/physical constraints (e.g. a
   fixed road template) so the LLM can't propose something the simulator
   can't actually represent.

3. For each paper you surface, tell me: what specific mechanism it uses,
   whether it's directly adoptable for an LLM-based pipeline stage
   (vs. requiring a solver/simulator I don't have), and where it falls short
   for my case (my templates are fixed and very limited — exactly one real
   driving lane and one real biking lane per direction, only two road
   layouts total — so any technique assuming rich, flexible road geometry
   needs to be adapted down, not used as-is).

4. End with a synthesis: 2-3 concrete candidate designs for how an LLM
   should structure its output for this stage — e.g. "propose a value +
   confidence + fallback," "propose a range with a justification," "two-pass:
   propose then validate against constraints" — each with which paper(s) it
   draws from and its tradeoffs for my specific constraints (small, fixed
   template set; local 8B-class LLM already used elsewhere in the pipeline,
   so cost/latency matters).

I'll take this synthesis to a separate Claude Code session that already has
the actual pipeline code and will do the implementation — so optimize for
giving it real design ammunition (specific mechanisms, tradeoffs, paper
citations it can point to in my thesis), not a general literature summary.
