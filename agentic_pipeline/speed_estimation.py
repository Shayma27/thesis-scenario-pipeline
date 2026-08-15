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
from nothing. It only narrows a real, sourced range, and only when the
report gives it evidence to do so; its output is always clamped to a hard
safety envelope before use, and any failure (unreachable server,
malformed output, no textual evidence) falls back to the grounded default
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


def _llm_speed_estimate(vehicle_label: str, envelope: dict, conflict: dict) -> dict | None:
    """Ask the LLM whether the report's own words give evidence to narrow
    the grounded envelope for this actor's speed. Returns a dict with
    knowledge_status / speed_range_kmh / rationale on a well-formed
    response, or None on any failure (unreachable server, malformed
    output, out-of-range values) — callers must treat None as "use the
    grounded default directly," never as an error to propagate.
    """
    prompt = (
        "A traffic-engineering pipeline is reconstructing a German police "
        f"accident report as a simulation. For the {vehicle_label}, the "
        f"grounded default speed range is {envelope['min_kmh']}-"
        f"{envelope['max_kmh']} km/h (nominal {envelope['nominal_kmh']} "
        "km/h). Based ONLY on the report text below, does it give "
        "evidence that this specific vehicle was faster or slower than "
        "that default? If yes, narrow the range and quote the relevant "
        "phrase. If the report says nothing about this vehicle's speed, "
        "say so — do not invent a narrower range without textual "
        "evidence.\n\n"
        f"collision_description: {conflict.get('collision_description')}\n"
        f"severity_text: {conflict.get('severity_text')}\n"
        f"conflict_mechanism: {conflict.get('conflict_mechanism')}\n\n"
        "Respond as JSON only: "
        '{"knowledge_status": "report_qualitative_signal" or '
        '"insufficient_evidence", '
        '"speed_range_kmh": {"min": <float>, "max": <float>}, '
        '"rationale": "<one sentence, quote the report text if applicable>"}'
    )
    try:
        client = get_client()
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": (
                    "You estimate plausible vehicle speed ranges for "
                    "accident reconstruction from report text. You never "
                    "invent a number unsupported by the text — you only "
                    "narrow a given range when the text supports it, or "
                    "say the text gives no evidence."
                )},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=300,
            response_format={"type": "json_object"},
            timeout=_LLM_TIMEOUT_S,
        )
        parsed = json.loads(response.choices[0].message.content)
    except Exception:
        return None

    status = parsed.get("knowledge_status")
    if status not in ("report_qualitative_signal", "insufficient_evidence"):
        return None
    if status == "report_qualitative_signal":
        rng = parsed.get("speed_range_kmh")
        if not isinstance(rng, dict):
            return None
        try:
            lo, hi = float(rng["min"]), float(rng["max"])
        except (KeyError, TypeError, ValueError):
            return None
        if lo < 0 or hi < lo:
            return None
        parsed["speed_range_kmh"] = {"min": lo, "max": hi}
    return parsed


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

    rng = (llm_result or {}).get("speed_range_kmh") if llm_result else None
    valid_signal = (
        llm_result
        and llm_result.get("knowledge_status") == "report_qualitative_signal"
        and isinstance(rng, dict)
        and "min" in rng
        and "max" in rng
    )
    if valid_signal:
        cap = envelope["safety_cap_kmh"]
        lo = max(0.0, min(rng["min"], cap))
        hi = max(lo, min(rng["max"], cap))
        clamped = lo != rng["min"] or hi != rng["max"]
        nominal_kmh = (lo + hi) / 2
        speed_mps = round(_kmh_to_mps(nominal_kmh), 2)
        entry = {
            "parameter": f"{actor_id}.initial_speed_mps",
            "value_used": speed_mps,
            "source": "llm_speed_estimate",
            "reason": llm_result.get("rationale", ""),
            "logical_range_kmh": [lo, hi],
            "knowledge_status": "report_qualitative_signal",
        }
        if clamped:
            entry["clamped_to_safety_cap"] = True
        return speed_mps, entry

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
