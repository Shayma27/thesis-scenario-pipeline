"""
Agent 3 — speed estimation
===========================
Grounded, cited speed defaults for actors, narrowed only when Agent 1's
own extraction (participant.speed_evidence / speed_evidence_quote —
extract_scenario.py) carries real, grounded qualitative evidence about
that specific actor's speed.

Design history (see docs/next_chat_briefing_parameter_completion.md and
the conversation that scoped this module) — two live-verified iterations:

1. This module originally asked its OWN LLM to both classify AND
   numerically narrow speed evidence. Live testing against the real 8B
   model on crossing_04 ("deutlich überhöhter Geschwindigkeit" — clearly
   excessive speed) showed it correctly reading the evidence but failing
   to turn it into a number: asked for a narrowed km/h range, it returned
   the full un-narrowed envelope, whose midpoint (25 km/h) ended up
   *slower* than the non-LLM grounded default (40 km/h) — backwards. No
   rulebook (StVO, RiLSA, or otherwise) defines what number "clearly
   excessive" means, so asking an 8B model to invent one was asking it to
   hallucinate a calibration nothing grounds. Fixed by constraining the
   LLM to classify only (a fixed category, never a number) and moving
   concretization to deterministic Python (_concretize_qualitative_relation,
   below) — following the same LLM/formal-layer split used by SoVAR (Guo
   et al., "SoVAR: Building Generalizable Scenarios from Accident Reports
   for Autonomous Driving Testing," ASE 2024, DOI 10.1145/3691620.3695037)
   and the Extended Scenic DSL paper.

2. That fix still had this module reading conflict.collision_description/
   severity_text and running its own second LLM call to classify — but
   Agent 1's OWN LLM call already reads raw_text once and had already
   silently dropped this exact evidence for one report (crossing_03) while
   catching it for another (crossing_04, identical phrasing) — a second
   LLM call reading Agent 1's already-lossy summary couldn't fix a gap
   that happened upstream of it. Consolidated: Agent 1 now extracts
   speed_evidence/speed_evidence_quote directly from raw_text as part of
   its one extraction call (the only place in this pipeline allowed to
   read raw_text at all), with its own deterministic grounding
   verification (_validate_speed_evidence_grounding — rejects a claim
   whose quote isn't a real substring of the text, same principle as
   turning_03's rejected fabricated "stopped" quote) and a deterministic
   backfill for the one verified pattern the LLM has been seen to drop
   (_backfill_speed_evidence). This module no longer makes any LLM call
   at all — it only ever reads what Agent 1 already grounded, then
   concretizes deterministically. One LLM call in the whole pipeline
   reads raw text, matching the project's standing rule that Agent 3
   never gets its own raw-text access.

Turning a classified relation into a number is deterministic Python
(_concretize_qualitative_relation), and the result is validated for
direction-consistency before it can reach output (a "faster"
classification that doesn't produce a value above the grounded nominal is
rejected, not trusted — the exact check that would have caught the
original crossing_04 bug). Any failure (no evidence, an unrecognized
relation, a classification that fails its own consistency check) falls
back to the grounded default directly. This module never depends on any
network resource — every code path returns a usable speed offline.

Citations for the grounded envelope (verified against primary sources,
not secondhand summaries — see conversation history):
  - MOTOR_STVO_INNERORTS_KMH: § 3 Abs. 3 Nr. 1 StVO — "innerhalb
    geschlossener Ortschaften für alle Kraftfahrzeuge 50 km/h."
  - MOTOR_RILSA_CLEARANCE_KMH: Richtlinien für Lichtsignalanlagen (RiLSA),
    FGSV Arbeitsgruppe Verkehrsmanagement, 2015, pp. 21-26,
    ISBN 978-3-939715-91-7 — motor-vehicle clearance ("Räumgeschwindigkeit")
    speed, a conservative design figure, not a typical-driving value.
  - MOTOR_RILSA_APPROACH_KMH: same RiLSA 2015 edition, § 2.5.3, p. 26
    (official English translation, FGSV-Nr. 321 E) — the motor-vehicle
    approach speed ("Vapp = 40 km/h ... regardless of the speed limit and
    direction of travel") RiLSA uses for signalized-intersection
    intergreen-time calculation. A DIFFERENT quantity from the clearance
    speed above — that one is "already in the intersection, must clear
    it"; this one is "approaching the intersection." Used here as the
    "crossing" scenario_type's motor-vehicle default (this pipeline's
    crossing scenarios are always a vehicle moving straight through,
    never stopped/queued/yielding — see Assumption 3 in
    docs/modeling_assumptions.md for why signal state itself is never
    modeled). This replaced an earlier, unverified "65% of the posted
    limit" rule — checked and confirmed to have no primary-source basis
    anywhere (StVO, RiLSA, German intersection-approach-speed studies,
    AASHTO, ITE) before being removed.
  - CYCLIST_NOMINAL_KMH / CYCLIST_RANGE_KMH: Schleinitz, Franke-Bartholdt,
    Petzoldt, Schwanitz, Gehlert, Kühn — "Pedelec-Naturalistic Cycling
    Study," Forschungsbericht Nr. 27, Unfallforschung der Versicherer
    (UDV), Berlin, August 2014, p. 80, ISBN 978-3-939163-50-3 — empirical
    mean and 15th/85th-percentile moving speed for conventional bicycles,
    n=28. Not Berlin-specific; not restricted to intersection crossings.
  - EBIKE_NOMINAL_KMH: same source/page, Pedelec riders' empirical mean.
  - EBIKE_LEGAL_CEILING_KMH: § 1 Abs. 3 StVG / StVZO — a Pedelec's motor
    assist must cut off at 25 km/h (250 W max) to be legally classified as
    a bicycle rather than a motor vehicle.

The safety-clamp constants below (MOTOR_SAFETY_CLAMP_FACTOR,
CYCLIST_SAFETY_CAP_KMH, EBIKE_SAFETY_CAP_KMH) are NOT citations — they are
an explicit engineering judgment call, labeled as such, that exists only
to backstop a hallucinated LLM output. Do not present them as sourced
facts; the numbers above them are the only ones that are.
"""
from __future__ import annotations

# ── Grounded envelope constants ─────────────────────────────────────────

MOTOR_STVO_INNERORTS_KMH = 50.0
MOTOR_RILSA_CLEARANCE_KMH = 36.0
MOTOR_RILSA_APPROACH_KMH = 40.0

CYCLIST_NOMINAL_KMH = 15.3
CYCLIST_RANGE_KMH = (12.3, 18.1)

EBIKE_NOMINAL_KMH = 17.4
EBIKE_LEGAL_CEILING_KMH = 25.0

# Engineering judgment, not literature — see module docstring.
MOTOR_SAFETY_CLAMP_FACTOR = 1.6
CYCLIST_SAFETY_CAP_KMH = 40.0
EBIKE_SAFETY_CAP_KMH = 35.0

def _kmh_to_mps(kmh: float) -> float:
    return kmh / 3.6


def _grounded_envelope(
    vehicle_category: str, osm_maxspeed_kmh: float | None, is_crossing: bool = False
) -> dict:
    """The deterministic, cited speed envelope for a vehicle category —
    always available, no LLM required. Returns a dict with nominal_kmh,
    min_kmh, max_kmh (the grounded/legal ceiling), safety_cap_kmh (the
    separate, non-cited hallucination backstop — see module docstring),
    source, and reason.

    is_crossing only affects a motor vehicle's nominal_kmh — see
    MOTOR_RILSA_APPROACH_KMH's citation in the module docstring.
    """
    if vehicle_category == "e_bike":
        return {
            "nominal_kmh": EBIKE_NOMINAL_KMH,
            "min_kmh": 0.0,
            "max_kmh": EBIKE_LEGAL_CEILING_KMH,
            "safety_cap_kmh": EBIKE_SAFETY_CAP_KMH,
            "source": "empirical_population_mean",
            "reason": (
                f"Empirical mean urban Pedelec riding speed, "
                f"{EBIKE_NOMINAL_KMH} km/h (Schleinitz et al. 2014, UDV "
                "Forschungsbericht 27, TU Chemnitz naturalistic cycling "
                "study, p.80), capped at the legal Pedelec motor-assist "
                f"cutoff of {EBIKE_LEGAL_CEILING_KMH} km/h "
                "(§ 1 Abs. 3 StVG / StVZO)."
            ),
        }
    if vehicle_category == "bicycle":
        lo, hi = CYCLIST_RANGE_KMH
        return {
            "nominal_kmh": CYCLIST_NOMINAL_KMH,
            "min_kmh": lo,
            "max_kmh": hi,
            "safety_cap_kmh": CYCLIST_SAFETY_CAP_KMH,
            "source": "empirical_population_mean",
            "reason": (
                f"Empirical mean urban cycling speed, {CYCLIST_NOMINAL_KMH} "
                f"km/h (15th-85th percentile {lo}-{hi} km/h) — Schleinitz "
                "et al. 2014, UDV Forschungsbericht 27, TU Chemnitz "
                "naturalistic cycling study, p.80, n=28 conventional "
                "bicycles. Not Berlin-specific; not restricted to "
                "intersection crossings."
            ),
        }
    # car / truck / bus — no verified source differentiates a typical
    # value by vehicle type or maneuver (a curvature-derived turning-speed
    # refinement was investigated and dropped as unsourceable), so one
    # shared envelope for any motor vehicle.
    ceiling = osm_maxspeed_kmh if osm_maxspeed_kmh is not None else MOTOR_STVO_INNERORTS_KMH
    ceiling_source = (
        "an OSM-observed maxspeed tag" if osm_maxspeed_kmh is not None
        else "the § 3 Abs. 3 Nr. 1 StVO innerorts default (50 km/h)"
    )
    if is_crossing:
        # See MOTOR_RILSA_APPROACH_KMH's citation in the module docstring:
        # RiLSA states this value applies "regardless of the speed limit,"
        # so — unlike the non-crossing branch below — it is NOT scaled by
        # or capped at the ceiling; the ceiling still bounds the envelope
        # given to the LLM and the safety clamp, just not the nominal.
        return {
            "nominal_kmh": MOTOR_RILSA_APPROACH_KMH,
            "min_kmh": 0.0,
            "max_kmh": ceiling,
            "safety_cap_kmh": max(ceiling, MOTOR_RILSA_APPROACH_KMH) * MOTOR_SAFETY_CLAMP_FACTOR,
            "source": "engineering_assumption",
            "reason": (
                f"RiLSA's {MOTOR_RILSA_APPROACH_KMH} km/h motor-vehicle "
                "approach-speed assumption (RiLSA 2015, FGSV, § 2.5.3, "
                "p.26 — official English translation, FGSV-Nr. 321 E), "
                "used there for signalized-intersection intergreen-time "
                "calculation and applied here as this pipeline's "
                "'crossing' scenario default (the vehicle is always "
                "moving straight through, never stopped/queued/yielding — "
                "signal state itself is never modeled, see Assumption 3, "
                "docs/modeling_assumptions.md). RiLSA states this value "
                f"applies 'regardless of the speed limit,' so it is not "
                f"scaled by {ceiling_source} ({ceiling} km/h). Replaces an "
                "earlier, unverified 'approach speed = 65% of the posted "
                "limit' rule that had no primary-source basis."
            ),
        }
    return {
        "nominal_kmh": min(MOTOR_RILSA_CLEARANCE_KMH, ceiling),
        "min_kmh": 0.0,
        "max_kmh": ceiling,
        "safety_cap_kmh": ceiling * MOTOR_SAFETY_CLAMP_FACTOR,
        "source": "osm_tag" if osm_maxspeed_kmh is not None else "legal_default",
        "reason": (
            f"Posted-speed-limit-based default: {ceiling_source} "
            f"({ceiling} km/h) as the legal ceiling; nominal value is the "
            f"lower of that and RiLSA's {MOTOR_RILSA_CLEARANCE_KMH} km/h "
            "conservative motor-vehicle clearance reference (RiLSA 2015, "
            "FGSV, pp. 21-26, ISBN 978-3-939715-91-7), since no verified "
            "source differentiates a typical value by vehicle type or "
            "maneuver."
        ),
    }


_QUALITATIVE_RELATIONS = (
    "stopped",
    "clearly_slower_than_context",
    "slower_than_context",
    "approximately_contextual",
    "faster_than_context",
    "clearly_faster_than_context",
)


def _read_speed_evidence(data: dict, actor_id: str) -> tuple[str, str] | None:
    """Read this actor's already-grounded speed evidence directly from
    Agent 1's output — no LLM call, no re-parsing of prose. Returns
    (relation, quote) or None if Agent 1 found no evidence for this actor.

    Trusts Agent 1's own grounding verification
    (extract_scenario._validate_speed_evidence_grounding) completely,
    the same way this module already trusts every other Agent-1 field —
    it's the only place in the pipeline that reads raw_text at all. A
    defensive re-check against SCHEMA validity still guards against a
    malformed/unexpected value reaching the concretizer.
    """
    for participant in data.get("participants", []):
        if participant.get("id") != actor_id:
            continue
        relation = participant.get("speed_evidence")
        if relation not in _QUALITATIVE_RELATIONS:
            return None
        quote = participant.get("speed_evidence_quote") or ""
        return relation, quote
    return None


def _concretize_qualitative_relation(relation: str, envelope: dict) -> tuple[float, str] | None:
    """Turn an LLM-classified qualitative relation into an actual km/h
    value — deterministic Python, never the LLM (see module docstring for
    why). Returns (value_kmh, note) or None for "unknown".

    Uses only envelope['min_kmh']/['nominal_kmh']/['safety_cap_kmh'] as
    reference points for "faster" concretization — deliberately NOT
    envelope['max_kmh']: for a "crossing" motor vehicle, max_kmh is the
    independent OSM speed limit, which can legitimately be BELOW the
    RiLSA-fixed nominal (RiLSA's approach speed applies "regardless of the
    speed limit" — see _grounded_envelope), so max_kmh is not guaranteed
    to exceed nominal_kmh the way safety_cap_kmh always is by construction
    (safety_cap_kmh >= nominal_kmh * MOTOR_SAFETY_CLAMP_FACTOR in every
    branch). min_kmh IS always <= nominal_kmh in every branch, so it's
    safe to use directly for "slower" concretization.
    """
    floor = envelope["min_kmh"]
    nominal = envelope["nominal_kmh"]
    cap = envelope["safety_cap_kmh"]

    if relation == "stopped":
        return 0.0, (
            "the report gives explicit evidence this vehicle was "
            "stationary, despite an otherwise-moving maneuver"
        )
    if relation == "clearly_slower_than_context":
        return floor, (
            f"the grounded envelope's own lower bound ({floor} km/h) — "
            "'clearly slower' evidence, no source quantifies the "
            "magnitude further, so the envelope's floor is used as a "
            "conservative point estimate (engineering_assumption)"
        )
    if relation == "slower_than_context":
        value = (floor + nominal) / 2
        return value, (
            f"midpoint between the grounded envelope's floor ({floor} "
            f"km/h) and nominal ({nominal} km/h) — 'slower than context' "
            "evidence, magnitude not quantifiable from any cited source "
            "(engineering_assumption)"
        )
    if relation == "approximately_contextual":
        return nominal, "report evidence indicates no meaningful deviation from the grounded nominal"
    if relation == "faster_than_context":
        value = (nominal + cap) / 2
        return value, (
            f"midpoint between the grounded nominal ({nominal} km/h) and "
            f"this pipeline's existing safety-clamp backstop ({cap} "
            "km/h) — 'faster than context' evidence, magnitude not "
            "quantifiable from any cited source (engineering_assumption)"
        )
    if relation == "clearly_faster_than_context":
        return cap, (
            f"this pipeline's existing safety-clamp backstop ({cap} "
            "km/h) — 'clearly faster' evidence explicitly implies "
            "exceeding the typical/legal context; magnitude not "
            "quantifiable from any cited source, so the pre-existing "
            "engineering safety cap is used directly as the upper bound "
            "of what's representable (engineering_assumption)"
        )
    return None  # "unknown"


def estimate_actor_speed(
    data: dict,
    actor_id: str,
    vehicle_category: str,
    is_parked: bool,
) -> tuple[float, dict]:
    """Return (initial_speed_mps, missing_parameter_entry) for this actor.

    Never raises; always returns a usable speed, entirely offline — no
    network resource of any kind. Reads speed_evidence straight from
    Agent 1's already-grounded extraction (see module docstring); makes no
    LLM call itself. is_crossing is derived from data itself
    (classification.scenario_type), not a separate argument, since it only
    affects which grounded default a motor vehicle gets — see
    _grounded_envelope.
    """
    if is_parked:
        return 0.0, {
            "parameter": f"{actor_id}.initial_speed_mps",
            "value_used": 0.0,
            "source": "explicit_from_report",
            "reason": (
                "Participant's own maneuver is 'parked' — a stationary "
                "vehicle has zero speed by definition, not an estimate."
            ),
        }

    osm_maxspeed_kmh = data.get("osm_context", {}).get("derived", {}).get("maxspeed_kmh")
    is_crossing = data.get("classification", {}).get("scenario_type") == "crossing"
    envelope = _grounded_envelope(vehicle_category, osm_maxspeed_kmh, is_crossing)

    evidence = _read_speed_evidence(data, actor_id)
    if evidence is not None:
        relation, quote = evidence
        concretized = _concretize_qualitative_relation(relation, envelope)
        if concretized is not None:
            value_kmh, note = concretized
            value_kmh = max(0.0, min(value_kmh, envelope["safety_cap_kmh"]))
            # Direction-consistency check: a "faster" classification must
            # actually produce a value above nominal, and vice versa — this
            # is exactly the check that would have caught the original
            # crossing_04 bug (speeding evidence producing a slower value
            # than the neutral default). Never trust a classification whose
            # own concretization contradicts it, however it was derived.
            consistent = True
            if relation in ("faster_than_context", "clearly_faster_than_context"):
                consistent = value_kmh > envelope["nominal_kmh"]
            elif relation in ("slower_than_context", "clearly_slower_than_context"):
                consistent = value_kmh < envelope["nominal_kmh"]
            if consistent:
                speed_mps = round(_kmh_to_mps(value_kmh), 2)
                entry = {
                    "parameter": f"{actor_id}.initial_speed_mps",
                    "value_used": speed_mps,
                    "source": "agent1_speed_evidence",
                    "reason": (
                        f"Report evidence classified as '{relation}' "
                        f"(quote: \"{quote}\") during Agent 1 extraction. "
                        f"Concretized deterministically here: {note}."
                    ),
                    "qualitative_relation": relation,
                    "evidence_quote": quote,
                }
                return speed_mps, entry
            # Consistency check failed — fall through to grounded default.

    # No evidence, or the classification failed its own consistency check —
    # grounded default fires directly, offline, deterministic.
    speed_mps = round(_kmh_to_mps(envelope["nominal_kmh"]), 2)
    entry = {
        "parameter": f"{actor_id}.initial_speed_mps",
        "value_used": speed_mps,
        "source": envelope["source"],
        "reason": envelope["reason"],
    }
    return speed_mps, entry
