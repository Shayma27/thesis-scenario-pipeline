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
        _setd(a, "initial_lane_id", _motor_lane(odr, for_secondary_road=is_crossing))
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

def _cyclist_lane(odr: dict, data: dict) -> int:
    has_fac = bool(odr.get("primary_has_bike_facility"))
    if not has_fac:
        ftype = data.get("road_context", {}).get("bike_facility_type", "unknown")
        has_fac = ftype not in (None, "unknown", "none_reported", "roadway_mixed")
    n = int(odr.get("primary_road_lanes", odr.get("motor_lane_count", 1)))
    return -(n + 1) if has_fac else -n


def _motor_lane(odr: dict, for_secondary_road: bool = False) -> int:
    if for_secondary_road:
        n = int(odr.get("secondary_road_lanes", 1))
    else:
        n = int(odr.get("primary_road_lanes", odr.get("motor_lane_count", 1)))
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
