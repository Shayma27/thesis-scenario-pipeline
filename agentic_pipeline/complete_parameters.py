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

from defaults import (
    DEFAULT_BIKE_LANE_WIDTH_M,
    DEFAULT_MOTOR_LANE_WIDTH_M,
    DEFAULT_ROAD_LENGTH_M,
    DEFAULT_SIMULATION_DURATION_S,
    DEFAULT_SPEEDS_MPS,
)

# "crossing" is the only category using the two-road crossing layout: the
# motor vehicle goes straight on one road while the cyclist crosses it on
# another, so both need to be placed on (and timed across) two distinct
# approaches. "turning", "longitudinal", and "other" are all single-road
# scenarios by comparison.


def complete_parameters(data: dict) -> dict:
    """
    Return a deep copy of *data* with all required actor simulation
    parameters filled in using scenario-type-aware defaults.
    """
    data = copy.deepcopy(data)
    stype = data.get("classification", {}).get("scenario_type", "other")

    params = data.setdefault("generated_simulation_parameters", {})
    odr = params.setdefault("opendrive", {})
    osc = params.setdefault("openscenario", {})
    actors = osc.setdefault("actors", {})

    # ── Road geometry baseline ────────────────────────────────────────────
    _setd(odr, "road_length_m", DEFAULT_ROAD_LENGTH_M)
    _setd(odr, "motor_lane_width_m", DEFAULT_MOTOR_LANE_WIDTH_M)
    _setd(odr, "bike_lane_width_m", DEFAULT_BIKE_LANE_WIDTH_M)
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
        cspeed = _cyclist_speed(ctype)

        _setd(a, "vehicle_category", ctype)
        _setd(a, "initial_road_id", 0)
        _setd(a, "initial_lane_id", _cyclist_lane(odr, data))
        if is_crossing:
            _setd(a, "initial_s_m", round(max(2.0, cs - cspeed * ct), 2))
        else:
            _setd(a, "initial_s_m", round(max(2.0, cs * 0.2), 2))
        _setd(a, "initial_speed_mps", cspeed)
        _note(data, cid, a, stype)

    # ── Motor vehicle ─────────────────────────────────────────────────────
    if motor_p:
        mid = motor_p["id"]
        actors.setdefault(mid, {})
        a = actors[mid]
        mtype = motor_p.get("type", "car")
        mmaneuver = motor_p.get("maneuver", "go_straight")
        mspeed = _motor_speed(mtype, mmaneuver)

        _setd(a, "vehicle_category", mtype)
        _setd(a, "initial_road_id", 1 if is_crossing else 0)
        _setd(a, "initial_lane_id", _motor_lane(odr, data, for_secondary_road=is_crossing))
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
        _setd(a, "initial_speed_mps", mspeed)
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


def _road_position_lane_id(road_position: str, lane_count: int, *, allow_left_lane: bool) -> int | None:
    """Map an explicit report "<side> Fahrstreifen" position to a lane id.

    `allow_left_lane` gates Assumption 1's same-direction reinterpretation:
    only "longitudinal" scenarios (straight_road.xodr) may use lane id 1,
    the template's real second driving lane, as a same-direction lane.
    Everywhere else "leftmost" still means the innermost lane on the
    participant's own (negative-id) side, i.e. lane -1.
    """
    if road_position == "leftmost_motor_lane":
        return 1 if allow_left_lane else -1
    if road_position == "middle_motor_lane":
        return -max(1, (lane_count + 1) // 2)
    if road_position in {"rightmost_motor_lane", "right_motor_lane"}:
        return -max(1, lane_count)
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

    has_fac = bool(odr.get("primary_has_bike_facility"))
    if not has_fac:
        # Assumption 2 default: a "not specified" report and a report
        # describing an unrepresentable facility type both fall back to the
        # template's existing painted bike lane. "roadway_mixed" is the one
        # facility type that explicitly means "no bike facility, cyclist
        # rides the driving lane".
        has_fac = ftype != "roadway_mixed"
    return -(n + 1) if has_fac else -n


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


def _motor_lane(odr: dict, data: dict, for_secondary_road: bool = False) -> int:
    if for_secondary_road:
        n = int(odr.get("secondary_road_lanes", 1))
        return -max(1, n)

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
        return lane_id

    return -max(1, n)


# ── Speed defaults ────────────────────────────────────────────────────────────

def _cyclist_speed(ctype: str) -> float:
    if ctype == "e_bike":
        return DEFAULT_SPEEDS_MPS["e_bike"]["normal"]
    return DEFAULT_SPEEDS_MPS["cyclist"]["normal"]


def _motor_speed(mtype: str, mmaneuver: str) -> float:
    # A stationary/parked vehicle and a turning vehicle are both identified
    # from the participant's own maneuver — decoupled from scenario_type,
    # since e.g. "other" is too broad a category to imply either on its own.
    maneuver_lower = mmaneuver.lower()
    if maneuver_lower == "parked":
        return 0.0
    is_turn = any(t in maneuver_lower for t in ("turn_right", "turn_left", "turn"))
    is_overtake = "overtake" in maneuver_lower

    if mtype == "truck":
        return (DEFAULT_SPEEDS_MPS["truck"]["turning"] if is_turn
                else DEFAULT_SPEEDS_MPS["truck"]["urban_straight"])
    if mtype == "bus":
        # Buses share a truck's large-vehicle turning dynamics; only an
        # "overtaking" speed is defined for buses (the one bus report in
        # this corpus is a same-direction overtaking scenario), so that
        # remains the straight-line/longitudinal default.
        return DEFAULT_SPEEDS_MPS["truck"]["turning"] if is_turn else DEFAULT_SPEEDS_MPS["bus"]["overtaking"]
    # car / default
    if is_turn:
        return DEFAULT_SPEEDS_MPS["car"]["turning"]
    if is_overtake:
        return DEFAULT_SPEEDS_MPS["car"]["overtaking"]
    return DEFAULT_SPEEDS_MPS["car"]["urban_straight"]


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
