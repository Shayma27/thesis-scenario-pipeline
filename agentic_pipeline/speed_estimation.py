"""
Agent 3 — speed estimation
===========================
Grounded, cited speed defaults for actors, with an optional LLM-reasoned
narrowing step used only when the report's own text carries qualitative
evidence about speed (conflict.collision_description / severity_text /
conflict_mechanism — extracted by Agent 1, unused anywhere downstream
until now).

Design (see docs/next_chat_briefing_parameter_completion.md and the
conversation that scoped this module): the LLM never invents a number
from nothing, and — as of the live-model verification pass that found the
original design's flaw — it never invents a *calibrated* number at all.
Live testing against the real 8B model on crossing_04 (report text:
"deutlich überhöhter Geschwindigkeit" — clearly excessive speed) showed
the model correctly reading the qualitative evidence but failing at
turning it into a number: asked for a narrowed km/h range, it returned the
full un-narrowed envelope, whose midpoint (25 km/h) ended up *slower* than
the non-LLM grounded default (40 km/h) — the opposite of what "speeding"
should produce. There is no rulebook (StVO, RiLSA, or otherwise) that
defines what number "clearly excessive" means, so asking an 8B model to
invent one was asking it to hallucinate a calibration nothing grounds.

The fix, following the same LLM/formal-layer split used by SoVAR (Guo et
al., "SoVAR: Building Generalizable Scenarios from Accident Reports for
Autonomous Driving Testing," ASE 2024, DOI 10.1145/3691620.3695037 — LLM
extracts, a formal solver concretizes) and the Extended Scenic DSL paper
(LLM's output constrained to an enumerated, validated schema, not free
numeric generation): the LLM's ONLY job is classifying qualitative
evidence into a fixed category — is this specific actor's speed reported
as stopped, slower, about normal, faster, or clearly faster than the
grounded context, with a verbatim quote, or "unknown" if the report says
nothing about THIS actor. Turning that category into a number is
deterministic Python (_concretize_qualitative_relation), never the LLM,
and the result is validated for direction-consistency before it can reach
output (a "faster" classification that doesn't produce a value above the
grounded nominal is rejected, not trusted). Any failure (unreachable
server, malformed output, no textual evidence, a classification that
fails its own consistency check) falls back to the grounded default
directly. This module never blocks the pipeline on LLM availability —
every code path returns a usable speed.

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

import json

from llm_client import get_client, MODEL

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

_LLM_TIMEOUT_S = 10.0


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


def _llm_speed_estimate(vehicle_label: str, envelope: dict, conflict: dict) -> dict | None:
    """Ask the LLM to classify — never quantify — what the report's own
    words say about THIS SPECIFIC actor's speed. Returns a dict with
    knowledge_status / qualitative_relation / evidence_quote on a
    well-formed response, or None on any failure (unreachable server,
    malformed output, a report_qualitative_signal with no evidence_quote
    or an unrecognized relation) — callers must treat None as "use the
    grounded default directly," never as an error to propagate.

    Deliberately asks for a category, not a number — see module docstring
    for why numeric narrowing was tried and failed live verification.
    """
    prompt = (
        "A traffic-engineering pipeline is reconstructing a German police "
        f"accident report as a simulation. You are assessing ONLY the "
        f"{vehicle_label}'s speed — never any other participant's, even if "
        "the report describes one.\n\n"
        f"For reference only (do not return a number): this vehicle's "
        f"grounded typical/legal speed context is centered around "
        f"{envelope['nominal_kmh']} km/h.\n\n"
        "Based ONLY on the report text below, classify what it says about "
        "THIS vehicle's own speed relative to that context:\n"
        '- "stopped": the report says this vehicle was stationary/had '
        "stopped (distinct from being parked throughout).\n"
        '- "clearly_slower_than_context": clearly, markedly slower than '
        "typical.\n"
        '- "slower_than_context": somewhat slower than typical.\n'
        '- "approximately_contextual": the report addresses this vehicle\'s '
        "speed and indicates it was roughly typical.\n"
        '- "faster_than_context": somewhat faster than typical.\n'
        '- "clearly_faster_than_context": clearly, markedly faster / '
        'exceeded the appropriate speed (e.g. "überhöhte Geschwindigkeit", '
        '"raste", "deutlich zu schnell").\n'
        '- "unknown": the report says nothing about THIS vehicle\'s own '
        "speed.\n\n"
        "If your evidence quote is actually about a different participant, "
        "or isn't specific to this vehicle's own speed, you MUST classify "
        'as "unknown" — never let another participant\'s speed evidence '
        "apply to this one.\n\n"
        f"collision_description: {conflict.get('collision_description')}\n"
        f"severity_text: {conflict.get('severity_text')}\n"
        f"conflict_mechanism: {conflict.get('conflict_mechanism')}\n\n"
        "Respond as JSON only: "
        '{"knowledge_status": "report_qualitative_signal" or '
        '"not_reported", '
        '"qualitative_relation": "<one of the categories above>", '
        '"evidence_quote": "<verbatim phrase from the report text, or '
        'empty string if not_reported>"}'
    )
    try:
        client = get_client()
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": (
                    "You classify qualitative speed evidence for accident "
                    "reconstruction from report text into a fixed set of "
                    "categories. You never output a number. You only "
                    "report evidence that is specifically about the named "
                    "vehicle — never about a different participant in the "
                    "same report."
                )},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=200,
            response_format={"type": "json_object"},
            timeout=_LLM_TIMEOUT_S,
        )
        parsed = json.loads(response.choices[0].message.content)
    except Exception:
        return None

    status = parsed.get("knowledge_status")
    if status not in ("report_qualitative_signal", "not_reported"):
        return None
    if status == "not_reported":
        return {"knowledge_status": "not_reported", "qualitative_relation": "unknown", "evidence_quote": ""}

    relation = parsed.get("qualitative_relation")
    quote = parsed.get("evidence_quote")
    if relation not in _QUALITATIVE_RELATIONS:
        return None
    if not isinstance(quote, str) or not quote.strip():
        return None
    return {"knowledge_status": status, "qualitative_relation": relation, "evidence_quote": quote}


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

    Never raises; always returns a usable speed. The LLM is consulted at
    most once per call, only when not parked, and its output is always
    clamped to a hard safety envelope before use — see module docstring.
    Callers are responsible for checking whether initial_speed_mps is
    already set by an earlier, higher-priority stage before calling this
    (so an LLM call is never wasted on an actor whose speed is already
    decided) — see complete_parameters.py. is_crossing is derived from
    data itself (classification.scenario_type), not a separate argument,
    since it only affects which grounded default a motor vehicle gets —
    see _grounded_envelope.
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

    conflict = data.get("conflict", {})
    llm_result = None
    if any(conflict.get(k) for k in ("collision_description", "severity_text", "conflict_mechanism")):
        llm_result = _llm_speed_estimate(f"{vehicle_category} ({actor_id})", envelope, conflict)

    if llm_result and llm_result.get("knowledge_status") == "report_qualitative_signal":
        relation = llm_result.get("qualitative_relation")
        concretized = _concretize_qualitative_relation(relation, envelope)
        if concretized is not None:
            value_kmh, note = concretized
            value_kmh = max(0.0, min(value_kmh, envelope["safety_cap_kmh"]))
            # Direction-consistency check: a "faster" classification must
            # actually produce a value above nominal, and vice versa — this
            # is exactly the check that would have caught the live-verified
            # crossing_04 bug (speeding evidence producing a slower value
            # than the neutral default). Never trust a classification whose
            # own concretization contradicts it.
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
                    "source": "llm_qualitative_signal",
                    "reason": (
                        f"Report evidence classified as '{relation}' "
                        f"(quote: \"{llm_result.get('evidence_quote', '')}\"). "
                        f"Concretized deterministically, not by the LLM: {note}."
                    ),
                    "qualitative_relation": relation,
                    "evidence_quote": llm_result.get("evidence_quote", ""),
                }
                return speed_mps, entry
            # Consistency check failed — fall through to grounded default.

    # No LLM signal, no LLM available, or LLM output failed validation —
    # grounded default fires directly, offline, deterministic.
    speed_mps = round(_kmh_to_mps(envelope["nominal_kmh"]), 2)
    entry = {
        "parameter": f"{actor_id}.initial_speed_mps",
        "value_used": speed_mps,
        "source": envelope["source"],
        "reason": envelope["reason"],
    }
    return speed_mps, entry
