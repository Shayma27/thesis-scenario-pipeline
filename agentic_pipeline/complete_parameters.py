"""
Agent 3 — Parameter Completion Agent
=====================================
Fills generated_simulation_parameters.openscenario.actors with concrete
simulation values for all scenario types. Called after Agent 1 (extraction)
and Agent 2 (OSM enrichment). Only fills fields that are still absent —
values set by earlier agents are never overwritten.
"""
from __future__ import annotations

import copy

from generate_scenario import (
    _TEMPLATE_DIR,
    DEFAULT_CYCLIST_LATERAL_POSITION,
    DEFAULT_SIMULATION_DURATION_S,
    _clamp_initial_s_to_real_road,
    _parse_xodr_road_geometry,
    _real_lane_width_m,
    _resolve_road_id,
    _road_total_length,
)
from speed_estimation import estimate_actor_speed

# "crossing" is the only category using the two-road crossing layout: the
# motor vehicle goes straight on one road while the cyclist crosses it on
# another, so both need to be placed on (and timed across) two distinct
# approaches. "turning", "longitudinal", and "other" are all single-road
# scenarios by comparison.

# Used only when the real template isn't known yet (data["template_used"]
# absent) — rare now that topology/template resolution happens before
# Agent 3 runs (pipeline.py's _tool_query_osm). Single-file use, so kept
# local rather than in a shared defaults module. The lane-width fallbacks
# are approximations by necessity (the two templates' real driving-lane
# widths differ, 3.07 m vs 3.5 m — see _real_lane_widths_m below, which is
# used whenever the template IS known, i.e. almost always in practice).
_ROAD_LENGTH_FALLBACK_M = 100.0
_MOTOR_LANE_WIDTH_FALLBACK_M = 3.5
_BIKE_LANE_WIDTH_FALLBACK_M = 1.25  # identical real width in both templates


def _real_primary_road_length_m(xodr_filename: str | None) -> float | None:
    """The actually-selected template's real primary-road length, parsed
    directly from the .xodr file — or None when the template isn't known
    yet (data["template_used"] absent), so callers fall back to
    _ROAD_LENGTH_FALLBACK_M exactly as before.

    data["template_used"] is now resolved in pipeline.py's _tool_query_osm,
    right after OSM enrichment — before complete_parameters() runs — instead
    of later in _tool_generate_scenario (see docs/modeling_assumptions.md's
    "Out of scope" history for why that used to be too late for Agent 3 to
    use). This lets road_length_m (and, below, initial_road_id/initial_s_m)
    reflect the real template from the start rather than a placeholder that
    generate_scenario.py silently had to correct afterward.
    """
    if not xodr_filename:
        return None
    try:
        road_id = _resolve_road_id(xodr_filename, is_secondary_approach=False)
        segments = _parse_xodr_road_geometry(_TEMPLATE_DIR / xodr_filename, road_id)
        return _road_total_length(segments)
    except (OSError, ValueError):
        return None


def _real_lane_widths_m(xodr_filename: str | None) -> tuple[float | None, float | None]:
    """(motor_lane_width_m, bike_lane_width_m) read directly from the
    actually-selected template's real lanes (-1 driving, -2 biking — the
    template's one real lane of each type, per Assumption 1/2 in
    docs/modeling_assumptions.md) — or (None, None) when the template
    isn't known yet. Replaces flat constants that didn't match either
    template: the old 3.5 m motor-lane default matched only
    intersection_4way.xodr (straight_road.xodr's real driving lane is
    3.07 m), and the old 2.0 m bike-lane default matched neither template
    (both are actually 1.25 m).
    """
    if not xodr_filename:
        return None, None
    try:
        road_id = _resolve_road_id(xodr_filename, is_secondary_approach=False)
        motor_width_m = _real_lane_width_m(xodr_filename, road_id, -1)
        bike_width_m = _real_lane_width_m(xodr_filename, road_id, -2)
        return motor_width_m, bike_width_m
    except (OSError, ValueError):
        return None, None


def complete_parameters(data: dict) -> dict:
    """
    Return a deep copy of *data* with all required actor simulation
    parameters filled in using scenario-type-aware defaults.
    """
    data = copy.deepcopy(data)
    stype = data.get("classification", {}).get("scenario_type", "other")
    xodr_filename = data.get("template_used")

    params = data.setdefault("generated_simulation_parameters", {})
    odr = params.setdefault("opendrive", {})
    osc = params.setdefault("openscenario", {})
    actors = osc.setdefault("actors", {})

    # ── Road geometry baseline ────────────────────────────────────────────
    real_road_length_m = _real_primary_road_length_m(xodr_filename)
    _setd(odr, "road_length_m", real_road_length_m if real_road_length_m is not None else _ROAD_LENGTH_FALLBACK_M)
    real_motor_width_m, real_bike_width_m = _real_lane_widths_m(xodr_filename)
    _setd(odr, "motor_lane_width_m", real_motor_width_m if real_motor_width_m is not None else _MOTOR_LANE_WIDTH_FALLBACK_M)
    _setd(odr, "bike_lane_width_m", real_bike_width_m if real_bike_width_m is not None else _BIKE_LANE_WIDTH_FALLBACK_M)
    is_crossing = stype == "crossing"
    _setd(odr, "road_geometry", "crossing" if is_crossing else "line")
    road_len = float(odr["road_length_m"])

    _setd(osc, "simulation_duration_s", DEFAULT_SIMULATION_DURATION_S)

    motor_p = _find(data, "motor_vehicle")
    cyclist_p = _find(data, "cyclist")
    motor_maneuver = str((motor_p or {}).get("maneuver", "")).lower()
    # A stationary/parked motor vehicle (e.g. a door-opening incident) is
    # identified from the participant's own maneuver, not scenario_type —
    # "other" is a broad catch-all that includes many non-stationary cases
    # too, so this can't be inferred from the coarse category alone.
    is_parked = motor_maneuver == "parked"

    # ── Conflict parameters ───────────────────────────────────────────────
    conf = osc.setdefault("conflict", {})
    if is_crossing:
        _setd(conf, "conflict_time_s", 4.0)
        _setd(conf, "trigger_time_s", 1.0)
        ct = float(conf["conflict_time_s"])
        cs = road_len / 2
    else:
        _setd(conf, "conflict_s_m", road_len / 2)
        _setd(conf, "trigger_time_s", 1.0)
        cs = float(conf["conflict_s_m"])
        ct = None

    # ── Cyclist ───────────────────────────────────────────────────────────
    if cyclist_p:
        cid = cyclist_p["id"]
        actors.setdefault(cid, {})
        a = actors[cid]
        ctype = cyclist_p.get("type", "bicycle")

        _setd(a, "vehicle_category", ctype)
        _setd(a, "initial_road_id",
              _resolve_road_id(xodr_filename, is_secondary_approach=False) if xodr_filename else 0)
        _setd(a, "initial_lane_id", _cyclist_lane(odr, data))
        # See the function's own docstring: this can now genuinely find the
        # actor (the Agent 2/Agent 3 ordering bug fixed here), but its write
        # is guarded so _cyclist_lane() above — already Assumption-2-aware —
        # stays authoritative. Must still run after the _setd above.
        _apply_cyclist_lane_id(
            data,
            osc.get("cyclist_lateral_position", DEFAULT_CYCLIST_LATERAL_POSITION),
            odr,
        )
        # Speed is settled before position, not after: initial_s_m's
        # kinematic formula below needs the actor's actual final speed, not
        # a value that might still change. Never wastes an LLM call on an
        # already-decided speed — nothing currently sets a cyclist's speed
        # earlier than this, but the guard keeps the contract explicit.
        if "initial_speed_mps" not in a:
            cspeed, speed_entry = estimate_actor_speed(data, cid, ctype, is_parked=False)
            a["initial_speed_mps"] = cspeed
            data.setdefault("missing_parameters", []).append(speed_entry)
        else:
            cspeed = a["initial_speed_mps"]
        if is_crossing:
            _setd(a, "initial_s_m", round(max(2.0, cs - cspeed * ct), 2))
        else:
            _setd(a, "initial_s_m", round(max(2.0, cs * 0.2), 2))
        # Clamp to the real selected road's actual length now that it's
        # known (see _real_primary_road_length_m's docstring) — the same
        # correction generate_scenario.py already applies unconditionally
        # right before writing the file, just done here too so the
        # provenance record below (_note) matches what actually ends up in
        # the generated .xosc instead of a pre-correction value.
        if xodr_filename:
            a["initial_s_m"] = _clamp_initial_s_to_real_road(
                xodr_filename, a["initial_road_id"], float(a["initial_s_m"])
            )
        _note(data, cid, a, stype)

    # ── Motor vehicle ─────────────────────────────────────────────────────
    if motor_p:
        mid = motor_p["id"]
        actors.setdefault(mid, {})
        a = actors[mid]
        mtype = motor_p.get("type", "car")

        _setd(a, "vehicle_category", mtype)
        _setd(a, "initial_road_id",
              _resolve_road_id(xodr_filename, is_secondary_approach=is_crossing)
              if xodr_filename else (1 if is_crossing else 0))
        _setd(a, "initial_lane_id", _motor_lane(odr, data, mid, for_secondary_road=is_crossing))
        # Maneuver-aware override for "turning" (no-ops for every other
        # scenario_type, and for a turning report whose text already places
        # the vehicle on an explicit lane — see the function's own
        # docstring). Must run after the _setd above, since it needs
        # actors[mid] to already exist.
        _apply_turning_vehicle_lane_id(
            data, int(odr.get("primary_road_lanes", odr.get("motor_lane_count", 1)))
        )
        # Speed is settled before position — the initial_s_m formulas below
        # need the actor's actual final speed, not a value that might still
        # change afterward (previously mspeed was a flat default computed up
        # front and used for position math, while a separately-applied
        # OSM-derived speed could still override initial_speed_mps
        # afterward, leaving position and speed mutually inconsistent for a
        # crossing report — fixed here as part of wiring in the new
        # estimate below, not a separate change).
        # A crossing scenario's motor-vehicle default (RiLSA's 40 km/h
        # approach speed, replacing a previously-unverified "65% of the
        # posted limit" rule) now lives inside estimate_actor_speed()'s own
        # envelope construction — see speed_estimation._grounded_envelope —
        # instead of a separate function that pre-empted the LLM check
        # entirely. That pre-emption was itself a bug: it meant a report's
        # own "speeding car"-type language could never reach the LLM
        # reasoning step for a crossing report with an OSM speed limit,
        # since this used to claim initial_speed_mps first.
        if "initial_speed_mps" not in a:
            mspeed, speed_entry = estimate_actor_speed(data, mid, mtype, is_parked)
            a["initial_speed_mps"] = mspeed
            data.setdefault("missing_parameters", []).append(speed_entry)
        else:
            mspeed = a["initial_speed_mps"]
        if is_crossing:
            _setd(a, "initial_s_m", round(max(2.0, cs - mspeed * ct), 2))
        elif is_parked:
            _setd(a, "initial_s_m", cs)
        elif stype == "turning":
            # A turn is imminent at the conflict point, so start closer to it.
            _setd(a, "initial_s_m", round(max(2.0, cs - 20.0), 2))
        else:
            # longitudinal / other (not parked): a normal following distance
            # on a single straight road, no turn or crossing in progress.
            _setd(a, "initial_s_m", round(max(2.0, cs - 25.0), 2))
        # See the matching cyclist-block comment above: clamp now that the
        # real road is known, so provenance matches the generated file.
        if xodr_filename:
            a["initial_s_m"] = _clamp_initial_s_to_real_road(
                xodr_filename, a["initial_road_id"], float(a["initial_s_m"])
            )
        _note(data, mid, a, stype)

    return data


# ── Lane defaults ─────────────────────────────────────────────────────────────

# Assumption 2 (docs/modeling_assumptions.md): both templates model exactly
# one cycling facility per direction — a 1.25 m at-grade painted lane (ERA
# 2010 Schutzstreifen width), directly beside the driving lane, with no curb
# or vertical separation. A report describing a *different* facility
# geometry (a physically separated Radweg, a shared Geh-/Radweg, a Gehweg,
# or a median strip) cannot be placed more precisely than this painted
# lane, so these are flagged as unrepresentable and fall back to it, same
# as "not specified" reports.
_UNREPRESENTABLE_BIKE_FACILITIES = {
    "separated_cycle_track", "shared_foot_cycle_path", "sidewalk", "median_strip",
}

# ── Single-report manual override ───────────────────────────────────────────
# Report "crossing_08" (Unter den Eichen / Drakestraße / Habelschwerdter
# Allee) was initially classified bike_facility_type="roadway_mixed"
# ("rechten Fahrstreifen der Nebenfahrbahn") and treated as representable —
# ride the driving lane like any other roadway_mixed report (commit
# 24c50b9). Manually re-verified against a satellite map: "Unter den
# Eichen" at this location is a genuine dual carriageway (Hauptfahrbahn +
# Nebenfahrbahn) — two physically separate parallel roadways with a
# median/verge between them, not extra lanes of one road. Neither
# straight_road.xodr nor intersection_4way.xodr models a separated
# parallel carriageway (both have exactly one continuous roadway with an
# adjacent bike lane), so this is a road-topology mismatch, not a
# bike-facility-type mismatch — flagged under its own source label rather
# than reusing _UNREPRESENTABLE_BIKE_FACILITIES/
# unrepresentable_bike_facility_geometry, which is about the geometry of a
# cycling facility, not the carriageway itself. Scoped to this one
# scenario_id only — every other report's "roadway_mixed" still means what
# it always meant (ride the general lane, no dedicated bike facility).
_CROSSING_08_OVERRIDE_SCENARIO_ID = "crossing_08"
_CROSSING_08_OVERRIDE_REASON = (
    "Report describes the cyclist on the 'rechten Fahrstreifen der "
    "Nebenfahrbahn' of Unter den Eichen. Manually verified against a "
    "satellite map (2026-07-17): this Nebenfahrbahn is a physically "
    "separate parallel carriageway (dual-carriageway boulevard), not a "
    "lane of the main road — neither template models a separated "
    "parallel carriageway, so this falls back to the template's biking "
    "lane like the other flagged reports."
)


def _road_position_lane_id(road_position: str, lane_count: int, *, allow_left_lane: bool) -> int | None:
    """Map an explicit report "<side> Fahrstreifen" position to a lane id.

    Both templates have exactly ONE real driving lane per direction (id -1
    on the participant's own side; id 1 on the opposite side, only usable
    via Assumption 1's same-direction reinterpretation below) — never more,
    regardless of what `lane_count` (from OSM or the report) claims. So
    "leftmost"/"middle"/"rightmost" all resolve to that same single real
    lane; the report's numbered distinction between them ("the middle of
    three lanes" vs "the rightmost") isn't representable and collapses to
    it, the same accepted limitation Assumption 1 already documents for
    "leftmost". `lane_count` is kept as a parameter only so callers can
    detect and flag when it implied a distinction that got collapsed here
    (see _cyclist_lane/_motor_lane's flagging around this call) — it must
    never again feed into the returned id itself, which is what silently
    produced a wrong-typed lane (biking/border/sidewalk instead of driving)
    whenever lane_count > 1.

    `allow_left_lane` gates Assumption 1's same-direction reinterpretation:
    only "longitudinal" scenarios (straight_road.xodr) may use lane id 1,
    the template's real second driving lane, as a same-direction lane.
    """
    if road_position == "leftmost_motor_lane":
        return 1 if allow_left_lane else -1
    if road_position == "middle_motor_lane":
        return -1
    if road_position in {"rightmost_motor_lane", "right_motor_lane"}:
        return -1
    return None


def _cyclist_lane(odr: dict, data: dict) -> int:
    stype = data.get("classification", {}).get("scenario_type", "other")
    n = int(odr.get("primary_road_lanes", odr.get("motor_lane_count", 1)))

    # Assumption 2: explicit report language about the cyclist's own lane
    # takes priority over any bike-facility inference — e.g.
    # manual_classification_reference.md report 18 ("den linken der drei
    # Fahrstreifen" -> "den äußerst rechten Fahrstreifen") describes the
    # cyclist riding on the road itself, not a cycling facility at all.
    cyclist_p = _find(data, "cyclist") or {}
    road_position = str(cyclist_p.get("road_position") or "").casefold()
    lane_id = _road_position_lane_id(road_position, n, allow_left_lane=stype == "longitudinal")
    if lane_id is not None:
        if n > 1 and road_position in {"middle_motor_lane", "rightmost_motor_lane", "right_motor_lane"}:
            _flag_lane_count_exceeds_template(data, "cyclist_1.initial_lane_id", n)
        return lane_id

    # Flag geometry mismatches from the report text regardless of what
    # primary_has_bike_facility already says — osm_enrichment.py's
    # BIKE_FACILITY_TYPES already treats "separated_cycle_track" as "has a
    # bike facility" (true, for lane-choice purposes: it still goes on the
    # template's bike lane), which would otherwise skip this check before
    # it runs and silently miss flagging turning_01/turning_07.
    ftype = data.get("road_context", {}).get("bike_facility_type", "unknown")
    if ftype in _UNREPRESENTABLE_BIKE_FACILITIES:
        _flag_unrepresentable_bike_facility(data, ftype)

    # crossing_08 manual override (see above): its "roadway_mixed" is a
    # separate carriageway, not the generic "ride the driving lane" case
    # every other roadway_mixed report is.
    is_crossing_08_override = (
        ftype == "roadway_mixed"
        and data.get("source", {}).get("source_id") == _CROSSING_08_OVERRIDE_SCENARIO_ID
    )
    if is_crossing_08_override:
        _flag_unrepresentable_carriageway_geometry(data)

    has_fac = bool(odr.get("primary_has_bike_facility"))
    if not has_fac:
        # Assumption 2 default: a "not specified" report and a report
        # describing an unrepresentable facility type both fall back to the
        # template's existing painted bike lane. "roadway_mixed" is the one
        # facility type that explicitly means "no bike facility, cyclist
        # rides the driving lane" — except for the crossing_08 override
        # above, where it means a separate carriageway instead.
        has_fac = ftype != "roadway_mixed" or is_crossing_08_override

    # Both templates have exactly one real biking lane (-2) and one real
    # driving lane (-1) per direction, always — never "-(n+1)"/"-n" real
    # lanes. Using n here used to place the cyclist on whatever the
    # template's lane -(n+1)/-n actually is (the sidewalk or border lane,
    # for n>1), not the intended bike/driving lane. n is only used below to
    # flag that OSM/the report implied more real lanes than either template
    # models — the cyclist still goes on the one real lane that exists.
    if n > 1:
        _flag_lane_count_exceeds_template(data, "cyclist_1.initial_lane_id", n)
    return -2 if has_fac else -1


def _flag_lane_count_exceeds_template(data: dict, parameter: str, lane_count: int) -> None:
    """Record that OSM or the report implied more real driving lanes than
    either template models (both have exactly one real driving lane and
    one real biking lane per direction) — the actor still goes on the
    template's one real lane; this is purely a traceability record, same
    pattern as _flag_unrepresentable_bike_facility."""
    missing = data.setdefault("missing_parameters", [])
    source = "lane_count_exceeds_template_capacity"
    if any(m.get("parameter") == parameter and m.get("source") == source for m in missing):
        return
    missing.append({
        "parameter": parameter,
        "value_used": "template's one real lane of the intended type",
        "source": source,
        "reason": (
            f"OSM or the report implied {lane_count} lanes, but neither "
            "straight_road.xodr nor intersection_4way.xodr models more than "
            "one real driving lane and one real biking lane per direction "
            "(see Assumption 1 in docs/modeling_assumptions.md). Any "
            "numbered-lane distinction beyond that isn't representable; the "
            "actor is placed on the template's one real lane of the "
            "intended type instead."
        ),
    })


def _flag_unrepresentable_bike_facility(data: dict, ftype: str) -> None:
    missing = data.setdefault("missing_parameters", [])
    param = "road_context.bike_facility_type"
    source = "unrepresentable_bike_facility_geometry"
    if any(m.get("parameter") == param and m.get("source") == source for m in missing):
        return
    missing.append({
        "parameter": param,
        "value_used": "bike_lane (template fallback)",
        "source": source,
        "reason": (
            f"Report describes bike_facility_type='{ftype}', which neither "
            "straight_road.xodr nor intersection_4way.xodr models precisely "
            "(see Assumption 2 in docs/modeling_assumptions.md). Falling "
            "back to the template's existing painted bike lane."
        ),
    })


def _flag_unrepresentable_carriageway_geometry(data: dict) -> None:
    missing = data.setdefault("missing_parameters", [])
    param = "road_context.bike_facility_type"
    source = "unrepresentable_carriageway_geometry"
    if any(m.get("parameter") == param and m.get("source") == source for m in missing):
        return
    missing.append({
        "parameter": param,
        "value_used": "bike_lane (template fallback)",
        "source": source,
        "reason": _CROSSING_08_OVERRIDE_REASON,
    })


# Agent 2/Agent 3 ordering fix: this used to live in osm_enrichment.py and
# run during Agent 2 (query_osm), inside _apply_cyclist_position_policy() —
# before Agent 3 (complete_parameters(), this module) ever creates
# generated_simulation_parameters.openscenario.actors["cyclist_1"]. Its
# "cyclist = actors.get('cyclist_1'); if not cyclist: return" guard
# therefore always fired, identical to _apply_turning_vehicle_lane_id's bug
# fixed in commit 79d8000.
#
# UNLIKE that fix, simply moving this one and letting it run the same way
# (an unconditional overwrite right after the actor entry exists) would be
# a regression, not just a bugfix: this function's own logic predates
# Assumption 2 (docs/modeling_assumptions.md, commits 24c50b9/4a81265) and
# knows nothing about it — no bike_facility_type representability
# flagging, no crossing_08 carriageway override, and its own "nothing
# matched" fallback is the driving lane (-1), not the template's bike lane
# — i.e. exactly the "hardcode a blanket default" behavior Assumption 2
# was written to replace. Verified directly: reproducing this function's
# formula against all 19 manual_classification_reference.md reports and
# comparing to _cyclist_lane()'s (the actively-maintained, Assumption
# -2-aware decision) shows 15 of 19 would silently flip to the driving
# lane if this function's write were allowed to win.
#
# So this now runs (the ordering bug is fixed — the actor exists, the
# guard clause is evaluated for real instead of trivially firing), but its
# write uses the same "only fill a field that's still absent" contract
# _setd() gives every other field in complete_parameters() — _cyclist_lane()
# already ran first via _setd() above and remains authoritative. The
# lane-selection formula itself is unchanged from the original.
def _apply_cyclist_lane_id(data: dict, position: str, opendrive_params: dict) -> None:
    actors = data.setdefault("generated_simulation_parameters", {}).setdefault(
        "openscenario", {}
    ).setdefault("actors", {})
    cyclist = actors.get("cyclist_1")
    if not cyclist:
        return

    # Both templates have exactly one real driving lane (-1) and one real
    # biking lane (-2) per direction, always — primary_road_lanes/
    # motor_lane_count (from OSM/the report) never corresponds to a real
    # additional lane in either template, so it must not feed into which id
    # is picked (see _cyclist_lane's matching comment for the full
    # explanation). Every position other than "on the bike facility" lands
    # on the one real driving lane.
    has_bike_facility = bool(opendrive_params.get("primary_has_bike_facility"))
    if has_bike_facility and position in {"right", "rightmost", "both"}:
        lane_id = -2
    else:
        lane_id = -1

    _setd(cyclist, "initial_lane_id", lane_id)
    if cyclist.get("initial_lane_id") != lane_id:
        return  # _cyclist_lane() already decided a different value; keep it.
    _upsert_missing_parameter(
        data,
        parameter="cyclist_1.initial_lane_id",
        value_used=lane_id,
        source="derived_from_cyclist_lateral_position",
        reason=(
            "Cyclist OpenSCENARIO lane id follows the chosen lateral-position "
            "policy so the initial teleport matches the generated trajectory."
        ),
    )


def _motor_lane(odr: dict, data: dict, motor_id: str, for_secondary_road: bool = False) -> int:
    # Both templates have exactly one real driving lane per direction on
    # every real approach road — never more, regardless of what
    # primary_road_lanes/secondary_road_lanes/motor_lane_count (OSM or
    # report-derived) claims. See _cyclist_lane's matching comment: those
    # counts must not feed into which lane id is picked, only into whether
    # to flag that the template can't represent as many lanes as reported.
    if for_secondary_road:
        n = int(odr.get("secondary_road_lanes", 1))
        if n > 1:
            _flag_lane_count_exceeds_template(data, f"{motor_id}.initial_lane_id (secondary road)", n)
        return -1

    n = int(odr.get("primary_road_lanes", odr.get("motor_lane_count", 1)))
    stype = data.get("classification", {}).get("scenario_type", "other")

    # Assumption 1/2: symmetric with _cyclist_lane — a report can place the
    # motor vehicle on an explicit numbered lane too (none of the 19
    # reference reports need this today, but the two actors are equally
    # eligible per Assumption 1's "both may use either of the two lanes").
    motor_p = _find(data, "motor_vehicle") or {}
    road_position = str(motor_p.get("road_position") or "").casefold()
    lane_id = _road_position_lane_id(road_position, n, allow_left_lane=stype == "longitudinal")
    if lane_id is not None:
        if n > 1 and road_position in {"middle_motor_lane", "rightmost_motor_lane", "right_motor_lane"}:
            _flag_lane_count_exceeds_template(data, f"{motor_id}.initial_lane_id", n)
        return lane_id

    if n > 1:
        _flag_lane_count_exceeds_template(data, f"{motor_id}.initial_lane_id", n)
    return -1


# Agent 2/Agent 3 ordering fix: this used to live in osm_enrichment.py and run
# during Agent 2 (query_osm), inside _apply_lane_context() — before Agent 3
# (complete_parameters(), this module) ever creates
# generated_simulation_parameters.openscenario.actors[motor_id]. Its
# "actor = actors.get(motor_id); if not actor: return" guard therefore always
# fired (actors was always {} at that point in every version of the
# pipeline), making it dead code that never actually assigned a turning
# vehicle's lane. The lane-selection logic itself (left turn -> innermost
# lane -1, right turn -> outermost lane) is unchanged from the original;
# only where/when it runs has moved — now called from complete_parameters()
# right after this actor's entry (and its road-position-aware default lane,
# from _motor_lane() above) already exist.
def _apply_turning_vehicle_lane_id(data: dict, lane_count) -> None:
    if data.get("classification", {}).get("scenario_type") != "turning":
        return

    motor_participant = next(
        (p for p in data.get("participants", []) if p.get("class") == "motor_vehicle"), None
    )
    if not motor_participant:
        return
    motor_id = motor_participant.get("id")

    # Assumption 2: explicit report text about which lane the vehicle is on
    # takes priority over this maneuver-derived heuristic — same priority
    # _motor_lane() above already gives road_position for every other
    # scenario type. A turning report that also says e.g. "aus der
    # mittleren Spur" should keep that explicit lane, not the
    # turn-direction default computed below.
    road_position = str(motor_participant.get("road_position") or "").casefold()
    if road_position in {
        "leftmost_motor_lane", "middle_motor_lane", "rightmost_motor_lane", "right_motor_lane",
    }:
        return

    actors = data.setdefault("generated_simulation_parameters", {}).setdefault(
        "openscenario", {}
    ).setdefault("actors", {})
    actor = actors.get(motor_id)
    if not actor:
        return

    # Both templates have exactly one real driving lane per direction
    # (id -1) — never the "rightmost of lane_count" lane that used to be
    # computed here. With only one real lane, "innermost" (left turn) and
    # "outermost" (right turn) are the same physical lane; the distinction
    # this function used to encode only ever mattered for lane_count > 1,
    # which is exactly the case where -max(1, lane_count) pointed at the
    # template's biking/border/sidewalk lane instead of a driving lane.
    maneuver = str(motor_participant.get("maneuver", "")).lower()
    lane_id = -1
    if "turn_left" in maneuver:
        reason = (
            "For a left-turning vehicle, it starts in the innermost "
            "motor-vehicle lane (adjacent to the centerline)."
        )
    else:
        reason = (
            "For a right-turning vehicle, it starts in the rightmost "
            "motor-vehicle lane of the generated OpenDRIVE road — which, "
            "since the template models only one real driving lane per "
            "direction, is the same lane a left turn starts from."
        )
    if int(lane_count) > 1:
        _flag_lane_count_exceeds_template(data, f"{motor_id}.initial_lane_id", int(lane_count))

    actor["initial_lane_id"] = lane_id
    _upsert_missing_parameter(
        data,
        parameter=f"{motor_id}.initial_lane_id",
        value_used=lane_id,
        source="derived_from_osm_motor_lane_count",
        reason=reason,
    )


def _upsert_missing_parameter(data: dict, parameter: str, value_used, source: str, reason: str) -> None:
    """Local copy of osm_enrichment.py's helper of the same name/behavior —
    this module deliberately doesn't import from osm_enrichment.py (see
    _flag_unrepresentable_bike_facility/_flag_unrepresentable_carriageway_geometry
    above for the same "keep modules independent" pattern, and _note() below
    for this module's own append-only variant). Unlike _note(), this one
    overwrites an existing entry for the same `parameter` rather than
    skipping — matching the original osm_enrichment.py behavior exactly.
    """
    missing = data.setdefault("missing_parameters", [])
    for item in missing:
        if item.get("parameter") == parameter:
            item["value_used"] = value_used
            item["source"] = source
            item["reason"] = reason
            return
    missing.append({
        "parameter": parameter,
        "value_used": value_used,
        "source": source,
        "reason": reason,
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find(data: dict, participant_class: str) -> dict | None:
    for p in data.get("participants", []):
        if p.get("class") == participant_class:
            return p
    return None


def _setd(d: dict, key: str, value) -> None:
    if key not in d:
        d[key] = value


def _note(data: dict, actor_id: str, actor: dict, stype: str) -> None:
    reason = (
        f"Agent 3 default for scenario type '{stype}': derived from "
        "participant type and maneuver; not specified in report or OSM."
    )
    missing = data.setdefault("missing_parameters", [])
    for field in ("vehicle_category", "initial_road_id", "initial_lane_id",
                  "initial_s_m", "initial_speed_mps"):
        if field not in actor:
            continue
        param = f"{actor_id}.{field}"
        if any(m.get("parameter") == param for m in missing):
            continue  # already recorded by an earlier agent
        missing.append({
            "parameter": param,
            "value_used": actor[field],
            "source": "agent3_default_assumption",
            "reason": reason,
        })
