import math
import xml.etree.ElementTree as ET
from pathlib import Path

from scenariogeneration import xosc

# Shared staging/policy defaults — decisions the pipeline makes when
# nothing else determines a value, not measurements. Kept here (not a
# separate defaults.py) since this module already needs both and is the
# one every other stage already imports shared template/geometry pieces
# from (_TEMPLATE_DIR, _resolve_road_id, ...) — one place to look, not a
# thin module that exists only to be imported.
DEFAULT_SIMULATION_DURATION_S = 10.0  # scenario length in seconds; a staging choice, not a derived quantity
DEFAULT_CYCLIST_LATERAL_POSITION = "rightmost"  # policy fallback when neither the report nor OSM specify a side

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_JUNCTION_XODR_NAME = "intersection_4way.xodr"

# Connector-road map for templates/intersection_4way.xodr's <junction id="4">.
# Verified directly against that file's <road>/<junction> elements (arc
# curvature sign + lane-link count distinguish turn_right/turn_left/go_straight
# per entry road). The entry-road-1 side does NOT mirror entry-road-0's
# connector numbering — road 5 is turn_left (positive curvature, 2 lane
# links, exits to road 0), road 6 is turn_right (negative curvature, 4 lane
# links, exits to road 2), road 7 is go_straight (near-zero curvature, 2 lane
# links, exits to road 3).
_JUNCTION_CONNECTORS = {
    0: {"turn_right": 8, "go_straight": 9, "turn_left": 10},
    1: {"turn_right": 6, "go_straight": 7, "turn_left": 5},
}
_JUNCTION_EXIT_ROAD = {8: 1, 9: 2, 10: 3, 6: 2, 7: 3, 5: 0}
# Whether each connector's successor link attaches to the exit road's own
# s=0 ("start") or s=length ("end") point — read directly from each
# connector <road>'s <link><successor contactPoint=...>. "end" means travel
# continues with s *decreasing* on the exit road.
_JUNCTION_EXIT_CONTACT = {8: "start", 9: "end", 10: "end", 6: "end", 7: "end", 5: "start"}

_xodr_road_geometry_cache: dict[tuple[str, str], list[dict]] = {}
_xodr_lane_offset_cache: dict[tuple[str, str], list[dict]] = {}


def _osc_params(data):
    return data.get("generated_simulation_parameters", {}).get("openscenario", {})


def _actor_params(data, actor_id):
    return _osc_params(data).get("actors", {}).get(actor_id, {})


def _participant(data, participant_id):
    for participant in data.get("participants", []):
        if participant.get("id") == participant_id:
            return participant
    return {}


def _make_vehicle(name, vehicle_category):
    """Map JSON actor vehicle_category to an OpenSCENARIO vehicle entity."""
    if vehicle_category == "truck":
        bbox = xosc.BoundingBox(2.5, 8.0, 3.0, 1.5, 0, 1.2)
        front_axle = xosc.Axle(0.5, 0.8, 2.0, 5.5, 0.4)
        rear_axle = xosc.Axle(0.5, 0.8, 2.0, 0.0, 0.4)
        return xosc.Vehicle(
            name,
            xosc.VehicleCategory.truck,
            bbox,
            front_axle,
            rear_axle,
            30,
            4,
            6,
            model3d="truck_yellow.osgb",
        )

    if vehicle_category in ("bicycle", "e_bike"):
        bbox = xosc.BoundingBox(0.6, 1.8, 1.5, 0.3, 0, 0.8)
        front_axle = xosc.Axle(0.0, 0.3, 0.5, 1.2, 0.15)
        rear_axle = xosc.Axle(0.0, 0.3, 0.5, 0.0, 0.15)
        return xosc.Vehicle(
            name,
            xosc.VehicleCategory.bicycle,
            bbox,
            front_axle,
            rear_axle,
            15,
            3,
            4,
            model3d="cyclist.osgb",
        )

    if vehicle_category == "bus":
        bbox = xosc.BoundingBox(2.5, 12.0, 3.5, 1.5, 0, 1.5)
        front_axle = xosc.Axle(0.5, 1.0, 2.0, 9.0, 0.5)
        rear_axle = xosc.Axle(0.5, 1.0, 2.0, 0.0, 0.5)
        return xosc.Vehicle(
            name,
            xosc.VehicleCategory.bus,
            bbox,
            front_axle,
            rear_axle,
            80,
            5,
            8,
        )

    bbox = xosc.BoundingBox(1.8, 4.5, 1.6, 1.3, 0, 0.8)
    front_axle = xosc.Axle(0.5, 0.6, 1.6, 3.0, 0.3)
    rear_axle = xosc.Axle(0.5, 0.6, 1.6, 0.0, 0.3)
    return xosc.Vehicle(
        name,
        xosc.VehicleCategory.car,
        bbox,
        front_axle,
        rear_axle,
        50,
        8,
        8,
        model3d="car_red.osgb",
    )


def _lane_position(actor):
    """Map initial_road_id, initial_lane_id, and initial_s_m to LanePosition."""
    return xosc.LanePosition(
        s=float(actor["initial_s_m"]),
        offset=0,
        lane_id=str(actor["initial_lane_id"]),
        road_id=str(actor["initial_road_id"]),
    )


def _world_position_from_lane_s(actor, odr_params):
    """Lane positions for trajectory points, straight from the fixed lane
    layout — both templates have exactly ONE real driving lane (id ±1) and
    ONE real biking lane (id ±2) per direction, always (Assumption 1/2,
    docs/modeling_assumptions.md), enforced by test_lane_type_safety.py.
    Geometric placement must never depend on a reported/OSM lane COUNT —
    that count doesn't change which real lane exists, only complete_
    parameters.py's flagging of when it exceeds what the template can
    represent (_flag_lane_count_exceeds_template).

    This used to branch on motor_lane_count instead of lane_index directly,
    which was a live bug, not just an unnecessary computation: verified
    directly against 4 real reports (turning_01/05/07/09, each with OSM
    motor_lane_count=2), a cyclist's lane_index (always 2) satisfied
    `2 <= motor_lane_count`, so its trajectory was computed with the
    DRIVING-lane branch instead of the bike-lane branch — a real, wrong
    lateral offset. Fixed the same way _cyclist_lateral_offset already was
    for the crossing-scenario case.

    Assumption 1 (docs/modeling_assumptions.md): straight_road.xodr models a
    standard two-way road — lane id 1 is the real OpenDRIVE lane on the
    opposite (positive-t) side of the center lane from lane id -1, adjacent
    to it across the center-lane marking. For "longitudinal" scenarios this
    positive/negative pair is reinterpreted as two same-direction parallel
    lanes rather than opposing carriageways, so the lane id's sign (not its
    absolute value) decides which side of the center lane a point sits on.
    Every other scenario type only ever uses negative lane ids.
    """
    s = float(actor["initial_s_m"])
    lane_id = int(actor["initial_lane_id"])
    lane_index = abs(lane_id)
    side = 1 if lane_id > 0 else -1
    motor_lane_width_m = float(odr_params.get("motor_lane_width_m", 3.5))
    bike_lane_width_m = float(odr_params.get("bike_lane_width_m", 1.25))

    if lane_index == 1:
        y = side * motor_lane_width_m * 0.5
    elif lane_index == 2:
        y = side * (motor_lane_width_m + bike_lane_width_m / 2)
    else:
        # Defensive fallback only — test_lane_type_safety.py guarantees no
        # other lane_id is ever actually assigned in either template.
        y = side * (motor_lane_width_m + max(0, bike_lane_width_m) + 0.75)
    return s, y


def _make_trajectory(name, timed_points):
    trajectory = xosc.Trajectory(name, False)
    times = [point[0] for point in timed_points]
    positions = [
        xosc.WorldPosition(point[1], point[2], 0, point[3], 0, 0)
        for point in timed_points
    ]
    trajectory.add_shape(xosc.Polyline(times, positions))
    return trajectory


def _make_follow_trajectory_maneuver(name, trajectory):
    event = xosc.Event(f"{name}Event", xosc.Priority.override)
    event.add_action(
        f"{name}FollowTrajectory",
        xosc.FollowTrajectoryAction(
            trajectory,
            xosc.FollowingMode.position,
            reference_domain=xosc.ReferenceContext.relative,
            scale=1,
            offset=0,
        ),
    )
    event.add_trigger(
        xosc.ValueTrigger(
            f"{name}Start",
            0,
            xosc.ConditionEdge.none,
            xosc.SimulationTimeCondition(0.1, xosc.Rule.greaterThan),
        )
    )

    maneuver = xosc.Maneuver(f"{name}Maneuver")
    maneuver.add_event(event)
    return maneuver


def _road_start(length_m, heading_rad):
    return (
        -math.cos(heading_rad) * length_m / 2,
        -math.sin(heading_rad) * length_m / 2,
    )


def _road_normal(heading_rad):
    return (-math.sin(heading_rad), math.cos(heading_rad))


def _world_from_road_s_t(length_m, heading_rad, s_m, t_m):
    start_x, start_y = _road_start(length_m, heading_rad)
    normal_x, normal_y = _road_normal(heading_rad)
    return (
        start_x + math.cos(heading_rad) * s_m + normal_x * t_m,
        start_y + math.sin(heading_rad) * s_m + normal_y * t_m,
    )


def _junction_template_path():
    return _TEMPLATE_DIR / _JUNCTION_XODR_NAME


def _is_junction_template(xodr_filename):
    return Path(xodr_filename).name == _JUNCTION_XODR_NAME


def _resolve_road_id(xodr_filename, is_secondary_approach=False):
    """The actor's real OpenDRIVE road id for whichever template was
    actually selected.

    complete_parameters.py can't get this right on its own: template
    selection (and the topology detection it depends on) happens later, in
    pipeline.py's _tool_generate_scenario, after complete_parameters()
    already ran and set initial_road_id — always "0", matching
    intersection_4way.xodr's primary approach. That's wrong whenever the
    template actually selected turns out to be straight_road.xodr, whose
    one real <road> element has id="1", not "0" (verified directly against
    the template file) — which happens for every "longitudinal" scenario
    (always straight_road.xodr, regardless of topology) and for any other
    scenario_type whose topology resolves to "midblock" instead of
    "4way_junction" (see template_selector.select_template / topology_
    detection_report.md — several turning/crossing reports do resolve to
    "midblock" in practice). This is the one place with definitive
    knowledge of the actually-selected xodr_filename, so it corrects
    initial_road_id here rather than guessing earlier.
    """
    if not _is_junction_template(xodr_filename):
        # straight_road.xodr: a single road, real id "1" — every actor is
        # on it, regardless of scenario_type.
        return 1
    # intersection_4way.xodr: "crossing" puts the motor vehicle on the
    # secondary approach (real road id "1"); every other actor — including
    # the cyclist, and the motor vehicle for every non-crossing type, both
    # of which start on the shared approach before any turn — is on the
    # primary approach (real road id "0").
    return 1 if is_secondary_approach else 0


def _clamp_initial_s_to_real_road(xodr_filename, road_id, initial_s_m):
    """Clamp an actor's teleport s-position to the real selected road's
    actual length, parsed directly from the template file.

    complete_parameters.py computes initial_s_m against a synthetic
    road_length_m (100 m by default) — a reasonable abstraction for
    straight_road.xodr (whose one real road is 500 m, comfortably larger),
    but intersection_4way.xodr's individual roads are real, geometrically
    varied, and several are much shorter (e.g. the secondary approach is
    ~16.9 m). An unclamped initial_s_m from that synthetic 100 m
    assumption can exceed a short real road's actual length, which fails
    OpenDRIVE lane-position validation ("initial s=X is outside road Y
    length Z"). This mirrors the same defensive clamping
    _junction_maneuver_samples already does internally for the trajectory
    itself (e.g. `approach_m = min(approach_margin_m, entry_length)`) —
    the teleport's starting s should be consistent with it, not a
    separate, unbounded value.
    """
    try:
        segments = _parse_xodr_road_geometry(_TEMPLATE_DIR / xodr_filename, road_id)
    except (OSError, ValueError):
        return initial_s_m
    return max(0.0, min(initial_s_m, _road_total_length(segments)))


def _maneuver_kind(raw_maneuver):
    """Normalize a report/participant 'maneuver' string to a connector key."""
    text = (raw_maneuver or "").lower()
    if "turn_left" in text:
        return "turn_left"
    if "turn_right" in text:
        return "turn_right"
    return "go_straight"


def _parse_xodr_road_geometry(xodr_path, road_id):
    """Read <road id=road_id>'s <planView> geometry blocks (line/arc/paramPoly3)."""
    key = (str(xodr_path), str(road_id))
    cached = _xodr_road_geometry_cache.get(key)
    if cached is not None:
        return cached

    tree = ET.parse(xodr_path)
    road_el = next(
        (r for r in tree.getroot().findall("road") if r.get("id") == str(road_id)),
        None,
    )
    if road_el is None:
        raise ValueError(f"Road id={road_id!r} not found in {xodr_path}")

    segments = []
    for geom in road_el.find("planView").findall("geometry"):
        base = {
            "s0": float(geom.get("s")),
            "x0": float(geom.get("x")),
            "y0": float(geom.get("y")),
            "hdg0": float(geom.get("hdg")),
            "length": float(geom.get("length")),
        }
        arc = geom.find("arc")
        poly3 = geom.find("paramPoly3")
        if arc is not None:
            base["kind"] = "arc"
            base["curvature"] = float(arc.get("curvature"))
        elif poly3 is not None:
            base["kind"] = "paramPoly3"
            for coeff in ("aU", "bU", "cU", "dU", "aV", "bV", "cV", "dV"):
                base[coeff] = float(poly3.get(coeff))
        else:
            base["kind"] = "line"
        segments.append(base)

    segments.sort(key=lambda seg: seg["s0"])
    _xodr_road_geometry_cache[key] = segments
    return segments


def _parse_xodr_lane_offset(xodr_path, road_id):
    """Read <road id=road_id>'s <lanes><laneOffset> polynomial segments.

    OpenDRIVE's laneOffset shifts a road's lane-section t=0 reference away
    from its raw <planView> geometry line. In this template every
    junction="4" connector/exit road carries a constant +1.75 laneOffset
    while the four real approach roads (junction="-1") don't -- ignoring it
    (as _road_world_point previously did) put every connector/exit-road
    point 1.75m+ off from where the road's actual lanes sit.
    """
    key = (str(xodr_path), str(road_id))
    cached = _xodr_lane_offset_cache.get(key)
    if cached is not None:
        return cached

    tree = ET.parse(xodr_path)
    road_el = next(
        (r for r in tree.getroot().findall("road") if r.get("id") == str(road_id)),
        None,
    )
    if road_el is None:
        raise ValueError(f"Road id={road_id!r} not found in {xodr_path}")

    segments = []
    lanes_el = road_el.find("lanes")
    if lanes_el is not None:
        for lo in lanes_el.findall("laneOffset"):
            segments.append(
                {
                    "s0": float(lo.get("s")),
                    "a": float(lo.get("a")),
                    "b": float(lo.get("b")),
                    "c": float(lo.get("c")),
                    "d": float(lo.get("d")),
                }
            )
    segments.sort(key=lambda seg: seg["s0"])
    _xodr_lane_offset_cache[key] = segments
    return segments


def _lane_offset_at(lane_offset_segments, s):
    if not lane_offset_segments:
        return 0.0
    seg = lane_offset_segments[0]
    for cand in lane_offset_segments:
        if cand["s0"] <= s:
            seg = cand
        else:
            break
    ds = s - seg["s0"]
    return seg["a"] + seg["b"] * ds + seg["c"] * ds**2 + seg["d"] * ds**3


def _evaluate_geometry_segment(seg, local_s):
    x0, y0, hdg0, kind = seg["x0"], seg["y0"], seg["hdg0"], seg["kind"]

    if kind == "arc" and abs(seg["curvature"]) > 1e-12:
        k = seg["curvature"]
        heading = hdg0 + k * local_s
        x = x0 + (math.sin(heading) - math.sin(hdg0)) / k
        y = y0 - (math.cos(heading) - math.cos(hdg0)) / k
        return x, y, heading

    if kind == "paramPoly3":
        p = local_s
        u = seg["aU"] + seg["bU"] * p + seg["cU"] * p**2 + seg["dU"] * p**3
        v = seg["aV"] + seg["bV"] * p + seg["cV"] * p**2 + seg["dV"] * p**3
        du = seg["bU"] + 2 * seg["cU"] * p + 3 * seg["dU"] * p**2
        dv = seg["bV"] + 2 * seg["cV"] * p + 3 * seg["dV"] * p**2
        heading = hdg0 + math.atan2(dv, du) if (du or dv) else hdg0
        x = x0 + u * math.cos(hdg0) - v * math.sin(hdg0)
        y = y0 + u * math.sin(hdg0) + v * math.cos(hdg0)
        return x, y, heading

    # kind == "line", or an arc with ~zero curvature (degenerates to a line)
    x = x0 + math.cos(hdg0) * local_s
    y = y0 + math.sin(hdg0) * local_s
    return x, y, hdg0


def _road_total_length(segments):
    last = segments[-1]
    return last["s0"] + last["length"]


def _real_lane_width_m(xodr_filename, road_id, lane_id):
    """The real, constant width of a specific lane on the actually-
    selected template, read directly from its <width> element's "a"
    coefficient. Both templates use a single, non-tapering <width>
    segment per lane (b=c=d=0), so this is exact, not an approximation —
    see complete_parameters.py's _real_lane_widths_m, which uses this to
    replace flat width constants that didn't match either template
    (verified directly: straight_road.xodr's driving lane is 3.07 m, not
    the old 3.5 m default; both templates' biking lane is 1.25 m, not the
    old 2.0 m default).
    """
    tree = ET.parse(_TEMPLATE_DIR / xodr_filename)
    road_el = next(r for r in tree.getroot().findall("road") if r.get("id") == str(road_id))
    for side in ("left", "right"):
        side_el = road_el.find(f"lanes/laneSection/{side}")
        if side_el is None:
            continue
        for lane in side_el.findall("lane"):
            if int(lane.get("id")) == lane_id:
                width_el = lane.find("width")
                if width_el is None:
                    raise ValueError(f"lane {lane_id} on road {road_id} of {xodr_filename} has no width element")
                return float(width_el.get("a"))
    raise ValueError(f"lane {lane_id} not found on road {road_id} of {xodr_filename}")


def _road_point(segments, s):
    s = max(0.0, min(s, _road_total_length(segments)))
    for seg in segments:
        if seg["s0"] <= s <= seg["s0"] + seg["length"] + 1e-9:
            return _evaluate_geometry_segment(seg, s - seg["s0"])
    last = segments[-1]
    return _evaluate_geometry_segment(last, last["length"])


def _road_world_point(segments, s, t_m, lane_offset_segments=None):
    x, y, heading = _road_point(segments, s)
    total_t = t_m + _lane_offset_at(lane_offset_segments, s)
    nx, ny = _road_normal(heading)
    return x + nx * total_t, y + ny * total_t, heading


def _junction_maneuver_samples(
    entry_road_id, maneuver_kind, t_offset_m, approach_margin_m=30.0, sample_step_m=0.5
):
    """Sample real WorldPosition points (path_distance_m, x, y, heading) for a
    vehicle approaching entry_road_id and executing maneuver_kind through
    templates/intersection_4way.xodr's junction id="4", using the actual
    connector road geometry (not an invented s/t formula).

    Returns (samples, junction_entry_distance_m, junction_exit_distance_m).
    """
    xodr_path = _junction_template_path()
    connector_id = _JUNCTION_CONNECTORS[entry_road_id][maneuver_kind]
    exit_road_id = _JUNCTION_EXIT_ROAD[connector_id]
    exit_contact = _JUNCTION_EXIT_CONTACT[connector_id]

    entry_segs = _parse_xodr_road_geometry(xodr_path, entry_road_id)
    connector_segs = _parse_xodr_road_geometry(xodr_path, connector_id)
    exit_segs = _parse_xodr_road_geometry(xodr_path, exit_road_id)
    entry_lo = _parse_xodr_lane_offset(xodr_path, entry_road_id)
    connector_lo = _parse_xodr_lane_offset(xodr_path, connector_id)
    exit_lo = _parse_xodr_lane_offset(xodr_path, exit_road_id)

    entry_length = _road_total_length(entry_segs)
    connector_length = _road_total_length(connector_segs)
    approach_m = min(approach_margin_m, entry_length)

    samples = []

    # Entry road: travel *toward* the junction, i.e. s decreasing to 0 (the
    # junction boundary), since these entry roads' predecessor is the
    # junction at their own s=0.
    n_entry = max(2, int(approach_m / sample_step_m))
    for i in range(n_entry + 1):
        frac = i / n_entry
        s = approach_m * (1 - frac)
        x, y, heading = _road_world_point(entry_segs, s, t_offset_m, entry_lo)
        heading = _normalize_angle(heading + math.pi)
        samples.append((approach_m * frac, x, y, heading))

    # If the requested approach margin exceeds the entry road's real modeled
    # length, extend linearly backward along its own start heading (these
    # entry roads are near-straight in this template, so this is a faithful
    # continuation, not fabricated curvature).
    if approach_margin_m > entry_length:
        extra_m = approach_margin_m - entry_length
        base_x, base_y, base_heading = samples[0][1], samples[0][2], samples[0][3]
        samples = [(d + extra_m, x, y, h) for d, x, y, h in samples]
        n_extra = max(2, int(extra_m / sample_step_m))
        extension = []
        for i in range(n_extra):
            back_dist = extra_m * (1 - i / n_extra)
            x = base_x - math.cos(base_heading) * back_dist
            y = base_y - math.sin(base_heading) * back_dist
            extension.append((extra_m - back_dist, x, y, base_heading))
        samples = extension + samples

    # The entry road's own reference-line endpoint (s=0) and the connector's
    # actual start point can still be a bit apart in this template even with
    # laneOffset accounted for (a genuine small modeling seam between the two
    # roads' authored geometry) -- shift the whole entry-road tail so it
    # meets the connector with no jump. This is a rigid, uniform shift (not
    # tapered): tapering it to vary along the approach would add an
    # artificial "drift" component to each point's position that the stored
    # heading (the real road's own tangent) doesn't reflect, which distorts
    # the vehicle's facing direction during the approach -- worse than a
    # small, constant position offset at the very start of the trajectory.
    entry_end_x, entry_end_y, _ = _road_world_point(entry_segs, 0.0, t_offset_m, entry_lo)
    conn_start_x, conn_start_y, _ = _road_world_point(connector_segs, 0.0, t_offset_m, connector_lo)
    dx, dy = conn_start_x - entry_end_x, conn_start_y - entry_end_y
    samples = [(d, x + dx, y + dy, h) for d, x, y, h in samples]
    junction_entry_distance = samples[-1][0]

    # Connector: real junction geometry, s increasing 0 -> connector_length.
    n_conn = max(4, int(connector_length / sample_step_m))
    for i in range(1, n_conn + 1):
        s = connector_length * i / n_conn
        x, y, heading = _road_world_point(connector_segs, s, t_offset_m, connector_lo)
        samples.append((junction_entry_distance + s, x, y, heading))

    junction_exit_distance = samples[-1][0]

    # A short stretch of the exit road so trajectories can extend past impact.
    # Direction depends on which end of the exit road the connector attaches
    # to (contactPoint "start" -> s increasing; "end" -> s decreasing).
    exit_length = _road_total_length(exit_segs)
    exit_m = min(10.0, exit_length)
    n_exit = max(2, int(exit_m / sample_step_m))
    conn_end_x, conn_end_y, _ = _road_world_point(
        connector_segs, connector_length, t_offset_m, connector_lo
    )
    exit_anchor_s = 0.0 if exit_contact == "start" else exit_length
    exit_anchor_x, exit_anchor_y, _ = _road_world_point(exit_segs, exit_anchor_s, t_offset_m, exit_lo)
    edx, edy = conn_end_x - exit_anchor_x, conn_end_y - exit_anchor_y
    direction = 1.0 if exit_contact == "start" else -1.0
    for i in range(1, n_exit + 1):
        s = exit_anchor_s + direction * exit_m * i / n_exit
        x, y, heading = _road_world_point(exit_segs, s, t_offset_m, exit_lo)
        if exit_contact == "end":
            heading = _normalize_angle(heading + math.pi)
        samples.append((junction_exit_distance + exit_m * i / n_exit, x + edx, y + edy, heading))

    return samples, junction_entry_distance, junction_exit_distance


def _find_junction_crossing_point(samples_a, span_a, samples_b, span_b):
    """Find each path's own path-distance at the point where two junction
    connector paths (already offset for each vehicle's real lane position)
    pass closest to each other -- the real physical crossing location, not
    an arbitrary midpoint of either connector alone. Restricted to each
    path's own junction span (entry_distance..exit_distance) so the match
    point is inside the physical intersection, not somewhere on an
    approach/exit straight.

    Live-verified need for this (2026-08-27/28 visual review): with each
    vehicle's own connector-road midpoint used as "impact", the two
    vehicles' impact points were measured 1-4.5m apart even for a plain
    go_straight/go_straight crossing -- generally too far for their
    bounding boxes to ever overlap, which is exactly what most crossing
    reports showed ("no collision happened"), while the true nearest
    distance between the same two paths was under 0.3m almost everywhere.
    """
    pts_a = [p for p in samples_a if span_a[0] <= p[0] <= span_a[1]]
    pts_b = [p for p in samples_b if span_b[0] <= p[0] <= span_b[1]]
    best_dist = math.inf
    best_a = pts_a[len(pts_a) // 2][0]
    best_b = pts_b[len(pts_b) // 2][0]
    for pa in pts_a:
        for pb in pts_b:
            d = math.hypot(pa[1] - pb[1], pa[2] - pb[2])
            if d < best_dist:
                best_dist = d
                best_a, best_b = pa[0], pb[0]
    return best_a, best_b


def _path_point_at_distance(samples, distance_m):
    distance_m = max(samples[0][0], min(distance_m, samples[-1][0]))
    for (d0, x0, y0, h0), (d1, x1, y1, h1) in zip(samples, samples[1:]):
        if d0 <= distance_m <= d1:
            if d1 == d0:
                return x0, y0, h0
            frac = (distance_m - d0) / (d1 - d0)
            x = x0 + (x1 - x0) * frac
            y = y0 + (y1 - y0) * frac
            return x, y, _interpolate_heading(h0, h1, frac)
    return samples[-1][1], samples[-1][2], samples[-1][3]


def _line_intersection(point_a, heading_a, point_b, heading_b):
    dx_a = math.cos(heading_a)
    dy_a = math.sin(heading_a)
    dx_b = math.cos(heading_b)
    dy_b = math.sin(heading_b)
    det = dx_a * dy_b - dy_a * dx_b
    if abs(det) < 1e-6:
        return point_a

    delta_x = point_b[0] - point_a[0]
    delta_y = point_b[1] - point_a[1]
    scale_a = (delta_x * dy_b - delta_y * dx_b) / det
    return point_a[0] + scale_a * dx_a, point_a[1] + scale_a * dy_a


def _normalize_angle(angle_rad):
    while angle_rad <= -math.pi:
        angle_rad += 2 * math.pi
    while angle_rad > math.pi:
        angle_rad -= 2 * math.pi
    return angle_rad


def _heading_delta(target_rad, source_rad):
    return _normalize_angle(target_rad - source_rad)


def _interpolate_heading(start_rad, end_rad, fraction):
    return _normalize_angle(start_rad + _heading_delta(end_rad, start_rad) * fraction)


def _closest_heading(reference_rad, candidates):
    return min(candidates, key=lambda candidate: abs(_heading_delta(candidate, reference_rad)))


def _cyclist_lateral_offset(odr_params, osc_params):
    """Lateral offset (meters, from the road centerline) for the cyclist's
    drawn trajectory — a separate calculation from the actual OpenDRIVE
    lane the cyclist teleports to (initial_lane_id, fixed by
    complete_parameters.py's _cyclist_lane to the template's one real
    lane). Both templates have exactly one real driving lane and one real
    biking lane per direction, always, so every "leftmost"/"middle"/
    "rightmost" policy converges to the same physical offset here too —
    same principle as complete_parameters.py's lane-id fix, applied to
    this separate trajectory-offset calculation.

    Before this fix, several branches multiplied by the OSM/report-derived
    lane count instead of a fixed single lane, which could offset the
    drawn path meters away from wherever the cyclist is actually
    teleported (verified: up to 7m for a report claiming 3 lanes) whenever
    that count was > 1 — the teleport and the trajectory would silently
    disagree on where the cyclist starts.
    """
    lane_width_m = float(odr_params.get("motor_lane_width_m", 3.5))
    bike_lane_width_m = float(odr_params.get("bike_lane_width_m", 1.25))
    policy = osc_params.get("cyclist_lateral_position", DEFAULT_CYCLIST_LATERAL_POSITION)
    has_bike_facility = bool(odr_params.get("primary_has_bike_facility"))

    if has_bike_facility and policy in {"right", "rightmost", "both"}:
        return -(lane_width_m + bike_lane_width_m / 2)
    if has_bike_facility and policy == "left":
        return -bike_lane_width_m / 2
    return -lane_width_m / 2


def _generate_straight_crossing_openscenario(data, output_path, xodr_filename):
    """Generate an intersection crossing conflict between cyclist and car."""
    osc_params = _osc_params(data)
    odr_params = data.get("generated_simulation_parameters", {}).get("opendrive", {})
    duration_s = float(
        osc_params.get("simulation_duration_s", DEFAULT_SIMULATION_DURATION_S)
    )
    road_length_m = float(odr_params.get("road_length_m", 100))
    conflict = osc_params.get("conflict", {})
    impact_time_s = float(conflict.get("conflict_time_s", 4.0))
    primary_heading = float(odr_params.get("primary_heading_rad", -math.pi / 2))
    secondary_heading = float(odr_params.get("secondary_heading_rad", math.pi))

    cyclist_actor = _actor_params(data, "cyclist_1")
    car_actor = _actor_params(data, "car_1")
    cyclist_info = _participant(data, "cyclist_1")
    car_info = _participant(data, "car_1")
    # Correct for whichever template was actually selected — see
    # _resolve_road_id's / _clamp_initial_s_to_real_road's docstrings.
    cyclist_actor["initial_road_id"] = _resolve_road_id(xodr_filename, is_secondary_approach=False)
    car_actor["initial_road_id"] = _resolve_road_id(xodr_filename, is_secondary_approach=True)
    cyclist_actor["initial_s_m"] = _clamp_initial_s_to_real_road(
        xodr_filename, cyclist_actor["initial_road_id"], float(cyclist_actor["initial_s_m"])
    )
    car_actor["initial_s_m"] = _clamp_initial_s_to_real_road(
        xodr_filename, car_actor["initial_road_id"], float(car_actor["initial_s_m"])
    )

    cyclist_offset = _cyclist_lateral_offset(odr_params, osc_params)
    car_offset = -float(odr_params.get("motor_lane_width_m", 3.5)) * (
        abs(int(car_actor["initial_lane_id"])) - 0.5
    )
    car_path = osc_params.get("car_path")
    cyclist_maneuver = _maneuver_kind(cyclist_info.get("maneuver"))
    car_maneuver = (
        "turn_left" if car_path == "turn_left_from_secondary_to_primary" else "go_straight"
    )

    if _is_junction_template(xodr_filename):
        # complete_parameters.py's initial_s_m was itself only ever a
        # scene-staging guess (see its own "engineering_assumption"
        # provenance labels), computed independently per actor with no
        # knowledge of the OTHER actor's speed -- live-verified this
        # regularly produces a fast/close actor and a slow/far actor whose
        # natural travel times differ by many seconds (crossing_03: car
        # ~0.4s vs cyclist ~9.1s). Forcing them to meet anyway (see
        # junction_impact_time_s below) previously meant the fast/close
        # actor either crawled the whole approach or parked near the
        # junction -- both live-rejected as unrealistic. Extending that
        # actor's own real starting position farther back along its real
        # road (bounded by the road's own real modeled length -- a
        # LanePosition can't be validated beyond that) lets it drive
        # continuously at its own real speed instead, which is what
        # actually gets teleported below.
        def _sample_and_dist0(cyclist_s, car_s):
            cyc_samples, cyc_j_start, cyc_j_end = _junction_maneuver_samples(
                0, cyclist_maneuver, cyclist_offset, approach_margin_m=max(30.0, cyclist_s + 5)
            )
            car_samples, car_j_start, car_j_end = _junction_maneuver_samples(
                1, car_maneuver, car_offset, approach_margin_m=max(30.0, car_s + 5)
            )
            cyc_impact, car_impact = _find_junction_crossing_point(
                cyc_samples, (cyc_j_start, cyc_j_end), car_samples, (car_j_start, car_j_end)
            )
            return cyc_impact - (cyc_j_start - cyclist_s), car_impact - (car_j_start - car_s)

        cyclist_s0 = float(cyclist_actor["initial_s_m"])
        car_s0 = float(car_actor["initial_s_m"])
        cyclist_dist0_pre, car_dist0_pre = _sample_and_dist0(cyclist_s0, car_s0)
        cyclist_speed_mps = float(cyclist_actor["initial_speed_mps"])
        car_speed_mps = float(car_actor["initial_speed_mps"])
        cyclist_natural_time_pre = (
            cyclist_dist0_pre / cyclist_speed_mps if cyclist_speed_mps > 0 else impact_time_s
        )
        car_natural_time_pre = (
            car_dist0_pre / car_speed_mps if car_speed_mps > 0 else impact_time_s
        )
        junction_impact_time_s_pre = min(
            duration_s - 1.0,
            max(impact_time_s, cyclist_natural_time_pre, car_natural_time_pre),
        )

        def _extend_if_slack(s, speed_mps, entry_road_id, dist0, natural_time, actor_id):
            if speed_mps <= 0 or natural_time >= junction_impact_time_s_pre - 1e-6:
                return s
            target_dist0 = speed_mps * junction_impact_time_s_pre
            real_length = _road_total_length(
                _parse_xodr_road_geometry(_junction_template_path(), entry_road_id)
            )
            new_s = min(max(s, s + (target_dist0 - dist0)), real_length)
            if new_s != s:
                # complete_parameters.py already recorded a missing_parameters
                # entry for this actor's initial_s_m -- update it in place
                # (same convention test_constants_provenance.py checks
                # elsewhere: the recorded value_used must match what's
                # actually used) rather than leaving it pointing at the
                # pre-extension value while a different one ends up in
                # openscenario.actors below.
                entry = next(
                    (m for m in data.get("missing_parameters", [])
                     if m.get("parameter") == f"{actor_id}.initial_s_m"),
                    None,
                )
                reason = (
                    f"Extended from {s:.2f}m to {new_s:.2f}m (still within "
                    f"the real road's {real_length:.2f}m modeled length) so "
                    "this actor can drive continuously at its own real "
                    "initial_speed_mps for the whole approach instead of "
                    "crawling or parking near the junction to synchronize "
                    "with the other actor's much longer natural travel time "
                    "-- a rendering-time correction, same category as "
                    "_clamp_initial_s_to_real_road's."
                )
                if entry is not None:
                    entry["value_used"] = new_s
                    entry["reason"] = reason
                else:
                    data.setdefault("missing_parameters", []).append({
                        "parameter": f"{actor_id}.initial_s_m",
                        "value_used": new_s,
                        "source": "engineering_assumption",
                        "reason": reason,
                    })
            return new_s

        cyclist_actor["initial_s_m"] = _extend_if_slack(
            cyclist_s0, cyclist_speed_mps, 0, cyclist_dist0_pre, cyclist_natural_time_pre, "cyclist_1"
        )
        car_actor["initial_s_m"] = _extend_if_slack(
            car_s0, car_speed_mps, 1, car_dist0_pre, car_natural_time_pre, "car_1"
        )
    else:
        # straight_road.xodr has exactly one real road -- both actors are
        # actually on it (initial_road_id=1 for both). This used to be
        # rendered as two synthetic roads crossing at an angle (primary_
        # heading for the cyclist, secondary_heading for the car) via
        # _world_from_road_s_t, which assumes a road CENTERED at the
        # origin -- that has no relationship to this template's real,
        # authored geometry (confirmed directly from the .xodr file: the
        # real road starts at (0,0), heading 0, extends to (500,0)). Live-
        # verified this could put both actors 100+ meters off the actual
        # modeled pavement ("all wrong", vehicles floating over the ground
        # plane in the screenshots -- crossing_01). Separately, the roles
        # were backwards from both this pipeline's own documented
        # convention (complete_parameters.py: "'crossing' scenarios are
        # defined as the cyclist's path crossing the vehicle's straight
        # path") and the report's own extracted semantics (crossing_04's
        # conflict_mechanism is literally "cyclist_crosses_vehicle_path_
        # from_median") -- the CAR should drive normally on the real road,
        # and the CYCLIST is the one crossing into it at an angle, not the
        # other way around.
        #
        # Fixed: the car is placed using the template's real road geometry
        # (so it's actually on the pavement, driving normally); the
        # cyclist approaches the same real point from the side (its own
        # documented "entering the roadway"/"crossing from the median"
        # maneuver), using secondary_heading purely as that approach
        # direction. complete_parameters.py's cyclist initial_s_m was
        # computed for the old, incompatible two-synthetic-roads model and
        # no longer means anything real here, so a fresh, short, real-
        # speed-derived crossing distance (this codebase's existing "4.0s
        # kinematic backward projection" convention, e.g. ~17m for a
        # typical cycling speed -- plausible for "crossing from a median")
        # replaces it, rather than reusing a value computed for a
        # different geometry entirely.
        car_segs = _parse_xodr_road_geometry(_TEMPLATE_DIR / xodr_filename, car_actor["initial_road_id"])
        car_lo = _parse_xodr_lane_offset(_TEMPLATE_DIR / xodr_filename, car_actor["initial_road_id"])
        real_road_length = _road_total_length(car_segs)
        car_speed_mps = float(car_actor["initial_speed_mps"])
        cyclist_speed_mps = float(cyclist_actor["initial_speed_mps"])

        car_real_s0 = float(car_actor["initial_s_m"])
        impact_s = min(
            real_road_length - 5.0,
            max(5.0, float(osc_params.get("conflict", {}).get("conflict_s_m", real_road_length / 2))),
        )
        car_forward = impact_s >= car_real_s0
        car_dist0_pre = abs(impact_s - car_real_s0)
        car_natural_time_pre = car_dist0_pre / car_speed_mps if car_speed_mps > 0 else impact_time_s

        cyclist_dist0 = max(3.0, cyclist_speed_mps * 4.0) if cyclist_speed_mps > 0 else 10.0
        cyclist_natural_time = cyclist_dist0 / cyclist_speed_mps if cyclist_speed_mps > 0 else impact_time_s

        straight_impact_time_s = min(
            duration_s - 1.0, max(impact_time_s, car_natural_time_pre, cyclist_natural_time)
        )

        # If the car has slack (would naturally arrive early), move its
        # real starting s farther back along the real road -- same "drive
        # continuously at real speed" principle as everywhere else this
        # session, still clamped to the real road's own bounds since this
        # is a real, validated road position.
        if car_speed_mps > 0 and car_natural_time_pre < straight_impact_time_s - 1e-6:
            target_dist0 = car_speed_mps * straight_impact_time_s
            delta = target_dist0 - car_dist0_pre
            car_real_s0 = car_real_s0 - delta if car_forward else car_real_s0 + delta
            car_real_s0 = max(0.0, min(real_road_length, car_real_s0))
            car_actor["initial_s_m"] = car_real_s0
            reason = (
                f"Recomputed to {car_real_s0:.2f}m so the car can drive "
                "continuously at its own real initial_speed_mps for the "
                "whole approach to the conflict point on the real road, "
                "instead of needing to crawl or exceed its real speed to "
                "arrive on the shared schedule -- a rendering-time "
                "correction, same category as _clamp_initial_s_to_real_road's."
            )
            entry = next(
                (m for m in data.get("missing_parameters", [])
                 if m.get("parameter") == "car_1.initial_s_m"),
                None,
            )
            if entry is not None:
                entry["value_used"] = car_real_s0
                entry["reason"] = reason
            else:
                data.setdefault("missing_parameters", []).append({
                    "parameter": "car_1.initial_s_m",
                    "value_used": car_real_s0,
                    "source": "engineering_assumption",
                    "reason": reason,
                })

        car_dist0 = abs(impact_s - car_real_s0)
        car_start_x, car_start_y, _ = _road_world_point(car_segs, car_real_s0, car_offset, car_lo)
        impact_x, impact_y, impact_tangent = _road_world_point(car_segs, impact_s, car_offset, car_lo)
        car_heading = impact_tangent if car_forward else _normalize_angle(impact_tangent + math.pi)
        car_start = (car_start_x, car_start_y)

        # secondary_heading is an approximate, OSM/report-derived compass
        # bearing for "the direction the cyclist crosses from" -- not
        # necessarily exactly perpendicular to the car's real heading.
        # Snapping it to the nearest true perpendicular (keeping whichever
        # side it already pointed to) makes the crossing read as a clean
        # 90-degree T-bone at the collision, matching the actual real-world
        # geometry of "crossing the road" rather than an arbitrary angle.
        cyclist_heading = _closest_heading(
            secondary_heading,
            [_normalize_angle(car_heading + math.pi / 2), _normalize_angle(car_heading - math.pi / 2)],
        )
        cyclist_start = (
            impact_x - math.cos(cyclist_heading) * cyclist_dist0,
            impact_y - math.sin(cyclist_heading) * cyclist_dist0,
        )

    entities = xosc.Entities()
    entities.add_scenario_object(
        "cyclist_1",
        _make_vehicle("cyclist_1", cyclist_actor.get("vehicle_category", "bicycle")),
    )
    entities.add_scenario_object(
        "car_1",
        _make_vehicle("car_1", car_actor.get("vehicle_category", "car")),
    )

    transition = xosc.TransitionDynamics(
        xosc.DynamicsShapes.step,
        xosc.DynamicsDimension.time,
        1,
    )

    init = xosc.Init()
    if _is_junction_template(xodr_filename):
        # JSON road/lane/s values place both actors on the two real OpenDRIVE
        # approaches, and the junction trajectory built below is anchored to
        # those same real positions -- LanePosition is correct here.
        init.add_init_action("cyclist_1", xosc.TeleportAction(_lane_position(cyclist_actor)))
        init.add_init_action("car_1", xosc.TeleportAction(_lane_position(car_actor)))
    else:
        # cyclist_start/car_start/cyclist_heading/car_heading were already
        # computed above (real road geometry for the car, a short
        # crossing-from-the-side approach for the cyclist) so the
        # trajectory below can reuse them directly and stay consistent.
        init.add_init_action(
            "cyclist_1",
            xosc.TeleportAction(
                xosc.WorldPosition(cyclist_start[0], cyclist_start[1], 0, cyclist_heading, 0, 0)
            ),
        )
        init.add_init_action(
            "car_1",
            xosc.TeleportAction(
                xosc.WorldPosition(car_start[0], car_start[1], 0, car_heading, 0, 0)
            ),
        )
    init.add_init_action(
        "cyclist_1",
        xosc.AbsoluteSpeedAction(float(cyclist_actor["initial_speed_mps"]), transition),
    )
    init.add_init_action(
        "car_1",
        xosc.AbsoluteSpeedAction(float(car_actor["initial_speed_mps"]), transition),
    )

    if _is_junction_template(xodr_filename):
        # Build trajectories from intersection_4way.xodr's real junction
        # connector-road geometry instead of a synthetic s/t formula and
        # line-intersection. The choreography (times, distance-before-impact)
        # is preserved from the original design; only the spatial mapping
        # changes. "Impact" is placed at the midpoint of each vehicle's own
        # connector road (see generate_openscenario for the same convention).
        # cyclist_actor/car_actor's initial_s_m are already the real, clamped
        # s-values on their real entry roads (see the clamping above) -- use
        # those directly as the approach-margin basis and to compute each
        # actor's real distance-before-impact, instead of the old
        # road_length_m/2-based synthetic formula (a leftover from the
        # straight-road/line-intersection abstraction this junction path no
        # longer uses). That synthetic value could be tens of meters off the
        # real distance along the actual connector-road geometry (verified:
        # 20m+ off for crossing_05), which put the FollowTrajectoryAction's
        # t=0 waypoint far from the TeleportAction's real starting position --
        # the same class of bug already fixed for the sibling turning-conflict
        # generator below (see its "cyclist_dist0"/"motor_dist0" comments).
        cyclist_s = float(cyclist_actor["initial_s_m"])
        car_s = float(car_actor["initial_s_m"])

        cyclist_samples, cyc_j_start, cyc_j_end = _junction_maneuver_samples(
            0, cyclist_maneuver, cyclist_offset, approach_margin_m=max(30.0, cyclist_s + 5)
        )
        car_samples, car_j_start, car_j_end = _junction_maneuver_samples(
            1, car_maneuver, car_offset, approach_margin_m=max(30.0, car_s + 5)
        )
        # Each vehicle's own connector-road midpoint (the previous formula)
        # is generally NOT the same physical point once each vehicle's real
        # lane offset is applied -- live-verified visual review found "no
        # collision happened" across most go_straight/go_straight crossing
        # reports, and measuring it directly confirmed why: the two
        # connectors' own midpoints can be 1-4.5m apart even though the
        # paths themselves pass within centimeters of each other somewhere
        # else along their length. Use that real nearest-approach point
        # (restricted to each path's own junction span) as the single
        # shared impact location instead.
        cyclist_impact_dist, car_impact_dist = _find_junction_crossing_point(
            cyclist_samples, (cyc_j_start, cyc_j_end), car_samples, (car_j_start, car_j_end)
        )
        cyclist_dist0 = cyclist_impact_dist - (cyc_j_start - cyclist_s)
        car_dist0 = car_impact_dist - (car_j_start - car_s)

        # The choreography used to assign each waypoint a fixed time offset
        # from a flat conflict_time_s constant, regardless of each actor's
        # real speed or real distance. Three fixes were tried and
        # live-verified as still wrong before this one: (1) a single fixed
        # window forced whichever actor was fast-and-close to crawl for most
        # of the approach then jump speed in the last 0.3s ("during the
        # collision the car increased speed, but should normally brake" --
        # crossing_03); (2) letting that actor drive at its real speed and
        # then hold position near the junction removed the jump but replaced
        # it with the actor visibly freezing in place close to the crash
        # site for several seconds -- rejected on sight as equally
        # unrealistic; (3) one constant speed for the whole approach removed
        # both artifacts but, whenever the two actors' real distance/speed
        # ratios differed sharply (a car spawned close & fast next to a
        # cyclist spawned far & slow -- the report data's own numbers, not
        # an error), forced the close/fast actor to visibly crawl the entire
        # time ("very langsam", "it seems like the car is waiting for the
        # bike" -- crossing_02/03/05/06/07/08 second review round).
        #
        # This version never asks an actor to move at anything other than
        # its own real initial_speed_mps. Whichever actor has slack (would
        # naturally reach the impact point before the other) simply stays
        # parked at its real starting position until the moment it needs to
        # start driving continuously, at full real speed, to arrive exactly
        # on time -- a normal "waiting to pull into the junction" behavior,
        # not a simulation artifact.
        cyclist_speed_mps = float(cyclist_actor["initial_speed_mps"])
        car_speed_mps = float(car_actor["initial_speed_mps"])
        cyclist_natural_time = (
            cyclist_dist0 / cyclist_speed_mps if cyclist_speed_mps > 0 else impact_time_s
        )
        car_natural_time = car_dist0 / car_speed_mps if car_speed_mps > 0 else impact_time_s
        junction_impact_time_s = min(
            duration_s - 1.0,
            max(impact_time_s, cyclist_natural_time, car_natural_time),
        )

        def _cyclist_at(dist_before_impact):
            return _path_point_at_distance(
                cyclist_samples, cyclist_impact_dist - dist_before_impact
            )

        def _car_at(dist_before_impact):
            return _path_point_at_distance(car_samples, car_impact_dist - dist_before_impact)

        def _curve_markers(samples, j_start, impact_dist, dist0):
            """Every real sample point between the junction entry and the
            impact point, expressed as "distance before impact" -- so the
            rendered polyline actually follows the connector's real
            curvature instead of a sparse straight-line approximation
            cutting through it. Live-verified bug this replaces: with only
            1-2 fixed near-impact markers (4.0m/1.8m), a turning cyclist's
            real dist0 growing past ~35m (the extended-start fix above)
            meant the single long "cruise" segment linearly cut straight
            through the ENTIRE curved turn, only picking the real curve
            back up in the final ~2m before impact -- "weird bike
            trajectory" (crossing_05/06's turn_left cyclist).
            """
            lo = max(j_start, impact_dist - dist0)
            return sorted(
                {impact_dist - d for (d, x, y, h) in samples if lo < d < impact_dist},
                reverse=True,
            )

        def _real_speed_points(at_fn, dist0, marker_dists, speed_mps):
            def _time_for(dist_before_impact):
                if speed_mps <= 0:
                    return junction_impact_time_s
                return max(0.0, junction_impact_time_s - dist_before_impact / speed_mps)

            start_time = _time_for(dist0)
            points = [(0.0, *at_fn(dist0))]
            if start_time > 1e-6:
                # Parked at its real starting position until it's time to
                # begin driving continuously at full real speed.
                points.append((start_time, *at_fn(dist0)))
            for marker in marker_dists:
                if 0 < marker < dist0:
                    points.append((_time_for(marker), *at_fn(marker)))
            points.append((junction_impact_time_s, *at_fn(0.0)))
            return points

        cyclist_markers = _curve_markers(cyclist_samples, cyc_j_start, cyclist_impact_dist, cyclist_dist0)
        cyclist_points = _real_speed_points(
            _cyclist_at, cyclist_dist0, cyclist_markers, cyclist_speed_mps
        )
        cyclist_points.append((duration_s, *_cyclist_at(0.0)))

        car_markers = _curve_markers(car_samples, car_j_start, car_impact_dist, car_dist0)
        car_points = _real_speed_points(_car_at, car_dist0, car_markers, car_speed_mps)
        car_points.append((duration_s, *_car_at(0.0)))
    else:
        # cyclist_start/car_start/cyclist_heading/car_heading/impact_x/
        # impact_y/cyclist_dist0/car_dist0/straight_impact_time_s were all
        # already computed above (real road geometry for the car, a short
        # crossing-from-the-side approach for the cyclist -- see that
        # block's comments for the full "car coming from nowhere"/"all
        # wrong" root-cause history), so the trajectory here just needs to
        # move each actor at its own real speed from its real start to the
        # shared impact point -- same real-speed-timing principle as the
        # junction branch (see its own comments for the history of
        # rejected attempts: crawl-then-jump, drive-then-freeze).
        def _straight_points(start, heading, dist0, speed_mps):
            def _time_for(dist_before_impact):
                if speed_mps <= 0:
                    return straight_impact_time_s
                return max(0.0, straight_impact_time_s - dist_before_impact / speed_mps)

            start_time = _time_for(dist0)
            points = [(0.0, start[0], start[1], heading)]
            if start_time > 1e-6:
                points.append((start_time, start[0], start[1], heading))
            points.append((straight_impact_time_s, impact_x, impact_y, heading))
            return points

        cyclist_points = _straight_points(cyclist_start, cyclist_heading, cyclist_dist0, cyclist_speed_mps)
        cyclist_points.append((duration_s, impact_x, impact_y, cyclist_heading))

        # A turning-car special case ("turn_left_from_secondary_to_primary")
        # used to live here, gated on car_path -- removed as dead code, zero
        # coverage across all 8 active straight_road.xodr/junction-template
        # crossing reports (car_path is None for every one; only a report
        # whose car turns left within a "crossing" scenario_type would ever
        # set it, and none currently do). If a future report needs it, this
        # is the same class of fix as the junction crossing generator's own
        # turn handling (_junction_maneuver_samples/_find_junction_crossing_
        # point), not something to restore from git history blindly.
        car_points = _straight_points(car_start, car_heading, car_dist0, car_speed_mps)
        car_points.append((duration_s, impact_x, impact_y, car_heading))

    storyboard = xosc.StoryBoard(
        init,
        xosc.ValueTrigger(
            "StopSimulation",
            0,
            xosc.ConditionEdge.rising,
            xosc.SimulationTimeCondition(duration_s, xosc.Rule.greaterThan),
            "stop",
        ),
    )
    storyboard.add_maneuver(
        _make_follow_trajectory_maneuver(
            "CyclistEnterIntersection",
            _make_trajectory("CyclistEnterIntersectionTrajectory", cyclist_points),
        ),
        "cyclist_1",
    )
    storyboard.add_maneuver(
        _make_follow_trajectory_maneuver(
            "CarStraightThroughIntersection",
            _make_trajectory("CarStraightThroughIntersectionTrajectory", car_points),
        ),
        "car_1",
    )

    scenario = xosc.Scenario(
        data["source"]["source_id"],
        "Shayma",
        xosc.ParameterDeclarations(),
        entities=entities,
        storyboard=storyboard,
        roadnetwork=xosc.RoadNetwork(roadfile=xodr_filename),
        catalog=xosc.Catalog(),
    )
    scenario.header.description = (
        f"{data['classification']['scenario_type']}: "
        f"{cyclist_info.get('maneuver', 'cyclist maneuver')} vs "
        f"{car_info.get('maneuver', 'car maneuver')}. "
        f"{data['conflict']['collision_description']}"
    )
    scenario.write_xml(str(output_path))


def _find_motor_participant_id(data):
    for p in data.get("participants", []):
        if p.get("class") == "motor_vehicle":
            return p["id"]
    return "truck_1"


def _generate_longitudinal_openscenario(data, output_path, xodr_filename):
    """Generate a same-direction cyclist-lane-change-into-car conflict.

    Live-verified bugs this replaces:
    1. (longitudinal_01/02, "very weird simulation and positions of the
       car and cyclist!! only the template was right"): this scenario
       type used to silently fall through to the "turning" conflict's
       trajectory model (a motor vehicle executing a ~90-degree turn into
       the cyclist's path) -- conceptually wrong here: both actors travel
       the same direction the whole time, and the collision is the
       CYCLIST changing lanes into the car's lane, not the car turning.
    2. ("why is it starting from there" -- second review round, after (1)
       was fixed): positions were still computed via _world_from_road_s_t,
       a fully synthetic s/t system that assumes a road CENTERED at the
       origin -- the same bug already root-caused and fixed for the
       crossing generator's non-junction branch (see its own comments):
       straight_road.xodr's real road actually starts at (0,0), heading 0,
       and extends to (500,0), so this could put both actors visibly off
       the real modeled pavement. Fixed the same way: real road geometry
       via _road_world_point, not a synthetic centered-at-origin line.
    """
    osc_params = _osc_params(data)
    odr_params = data.get("generated_simulation_parameters", {}).get("opendrive", {})
    duration_s = float(osc_params.get("simulation_duration_s", DEFAULT_SIMULATION_DURATION_S))
    road_length_m = float(odr_params.get("road_length_m", 100))
    impact_s = float(osc_params.get("conflict", {}).get("conflict_s_m", road_length_m / 2))

    motor_id = _find_motor_participant_id(data)
    motor_actor = _actor_params(data, motor_id)
    cyclist_actor = _actor_params(data, "cyclist_1")
    motor_info = _participant(data, motor_id)
    cyclist_info = _participant(data, "cyclist_1")

    motor_actor["initial_road_id"] = _resolve_road_id(xodr_filename, is_secondary_approach=False)
    cyclist_actor["initial_road_id"] = _resolve_road_id(xodr_filename, is_secondary_approach=False)
    motor_actor["initial_s_m"] = _clamp_initial_s_to_real_road(
        xodr_filename, motor_actor["initial_road_id"], float(motor_actor["initial_s_m"])
    )
    cyclist_actor["initial_s_m"] = _clamp_initial_s_to_real_road(
        xodr_filename, cyclist_actor["initial_road_id"], float(cyclist_actor["initial_s_m"])
    )

    motor_speed_mps = float(motor_actor["initial_speed_mps"])
    cyclist_speed_mps = float(cyclist_actor["initial_speed_mps"])
    _, motor_y = _world_position_from_lane_s(motor_actor, odr_params)
    _, cyclist_lane_y = _world_position_from_lane_s(cyclist_actor, odr_params)

    real_segs = _parse_xodr_road_geometry(_TEMPLATE_DIR / xodr_filename, motor_actor["initial_road_id"])
    real_lo = _parse_xodr_lane_offset(_TEMPLATE_DIR / xodr_filename, motor_actor["initial_road_id"])

    def _real_world_point(s, t):
        return _road_world_point(real_segs, s, t, real_lo)

    def _natural_time(actor_s, speed_mps):
        return (impact_s - actor_s) / speed_mps if speed_mps > 0 else duration_s * 0.7

    motor_s0 = float(motor_actor["initial_s_m"])
    cyclist_s0 = float(cyclist_actor["initial_s_m"])
    motor_natural_time = _natural_time(motor_s0, motor_speed_mps)
    cyclist_natural_time = _natural_time(cyclist_s0, cyclist_speed_mps)
    impact_time_s = min(duration_s - 1.0, max(4.0, motor_natural_time, cyclist_natural_time))

    def _extend_start(s0, speed_mps, natural_time, actor_id):
        if speed_mps <= 0:
            return s0
        # Always derive placement from the chosen shared impact_time_s and
        # this actor's own real speed -- not just when its own natural_time
        # is short. A real bug caught here before shipping: skipping this
        # whenever natural_time >= impact_time_s (originally meant to leave
        # "the determining actor" alone) also silently skipped an actor
        # whose natural_time only looked large because duration_s -- 1.0
        # capped impact_time_s below it (longitudinal_01's cyclist: 200m at
        # 4.25 m/s needs 47s, but duration_s=10s only allows ~9s) -- leaving
        # it at its original, real-world-implausible 200m starting distance
        # entirely untouched, 161m away from where the car actually ends up.
        new_s = max(0.0, impact_s - speed_mps * impact_time_s)
        if new_s == s0:
            return s0
        reason = (
            f"Recomputed from {s0:.2f}m to {new_s:.2f}m so this actor can "
            "drive continuously at its own real initial_speed_mps for the "
            "whole approach to the conflict point, instead of needing to "
            "exceed its real speed (or, if its original distance genuinely "
            "wasn't reachable at real speed within the scenario's time "
            "budget, remaining permanently short of the conflict point) -- "
            "a rendering-time correction, same category as "
            "_clamp_initial_s_to_real_road's."
        )
        entry = next(
            (m for m in data.get("missing_parameters", [])
             if m.get("parameter") == f"{actor_id}.initial_s_m"),
            None,
        )
        if entry is not None:
            entry["value_used"] = new_s
            entry["reason"] = reason
        else:
            data.setdefault("missing_parameters", []).append({
                "parameter": f"{actor_id}.initial_s_m",
                "value_used": new_s,
                "source": "engineering_assumption",
                "reason": reason,
            })
        return new_s

    motor_s0 = _extend_start(motor_s0, motor_speed_mps, motor_natural_time, motor_id)
    cyclist_s0 = _extend_start(cyclist_s0, cyclist_speed_mps, cyclist_natural_time, "cyclist_1")
    motor_actor["initial_s_m"] = motor_s0
    cyclist_actor["initial_s_m"] = cyclist_s0

    entities = xosc.Entities()
    entities.add_scenario_object(
        motor_id, _make_vehicle(motor_id, motor_actor.get("vehicle_category", "truck"))
    )
    entities.add_scenario_object(
        "cyclist_1", _make_vehicle("cyclist_1", cyclist_actor.get("vehicle_category", "bicycle"))
    )

    transition = xosc.TransitionDynamics(xosc.DynamicsShapes.step, xosc.DynamicsDimension.time, 1)
    motor_start_x, motor_start_y, real_heading = _real_world_point(motor_s0, motor_y)
    motor_start = (motor_start_x, motor_start_y)
    cyclist_start_x, cyclist_start_y, _ = _real_world_point(cyclist_s0, cyclist_lane_y)
    cyclist_start = (cyclist_start_x, cyclist_start_y)
    primary_heading = real_heading

    init = xosc.Init()
    init.add_init_action(
        motor_id,
        xosc.TeleportAction(xosc.WorldPosition(motor_start[0], motor_start[1], 0, primary_heading, 0, 0)),
    )
    init.add_init_action(motor_id, xosc.AbsoluteSpeedAction(motor_speed_mps, transition))
    init.add_init_action(
        "cyclist_1",
        xosc.TeleportAction(xosc.WorldPosition(cyclist_start[0], cyclist_start[1], 0, primary_heading, 0, 0)),
    )
    init.add_init_action("cyclist_1", xosc.AbsoluteSpeedAction(cyclist_speed_mps, transition))

    # Motor vehicle: straight line, its own lane, constant real speed the
    # whole time -- no turn, matching a same-direction road.
    motor_impact_xy = _real_world_point(impact_s, motor_y)[:2]
    motor_points = [
        (0.0, motor_start[0], motor_start[1], primary_heading),
        (impact_time_s, motor_impact_xy[0], motor_impact_xy[1], primary_heading),
        (duration_s, motor_impact_xy[0], motor_impact_xy[1], primary_heading),
    ]

    # Cyclist: same constant real speed along s the whole time, but shifts
    # laterally from its own real lane into the motor's lane during a short
    # lane-change window ending exactly at impact -- a real lane change,
    # not a turn.
    lane_change_duration_s = min(2.0, impact_time_s * 0.5)
    lane_change_start_s = max(0.0, impact_time_s - lane_change_duration_s)

    def _cyclist_world_at(t):
        s = cyclist_s0 + cyclist_speed_mps * t
        if t <= lane_change_start_s:
            lat = cyclist_lane_y
        else:
            frac = min(1.0, (t - lane_change_start_s) / max(1e-6, lane_change_duration_s))
            lat = cyclist_lane_y + (motor_y - cyclist_lane_y) * frac
        return _real_world_point(s, lat)[:2]

    cyclist_points = [(0.0, cyclist_start[0], cyclist_start[1], primary_heading)]
    if lane_change_start_s > 1e-6:
        mid = _cyclist_world_at(lane_change_start_s)
        cyclist_points.append((lane_change_start_s, mid[0], mid[1], primary_heading))
    cyclist_impact_xy = _cyclist_world_at(impact_time_s)
    cyclist_points.append((impact_time_s, cyclist_impact_xy[0], cyclist_impact_xy[1], primary_heading))
    cyclist_points.append((duration_s, cyclist_impact_xy[0], cyclist_impact_xy[1], primary_heading))

    storyboard = xosc.StoryBoard(
        init,
        xosc.ValueTrigger(
            "StopSimulation", 0, xosc.ConditionEdge.rising,
            xosc.SimulationTimeCondition(duration_s, xosc.Rule.greaterThan), "stop",
        ),
    )
    storyboard.add_maneuver(
        _make_follow_trajectory_maneuver(
            "MotorVehicleManeuver", _make_trajectory("MotorVehicleTrajectory", motor_points)
        ),
        motor_id,
    )
    storyboard.add_maneuver(
        _make_follow_trajectory_maneuver(
            "CyclistLaneChange", _make_trajectory("CyclistLaneChangeTrajectory", cyclist_points)
        ),
        "cyclist_1",
    )

    scenario = xosc.Scenario(
        data["source"]["source_id"],
        "Shayma",
        xosc.ParameterDeclarations(),
        entities=entities,
        storyboard=storyboard,
        roadnetwork=xosc.RoadNetwork(roadfile=xodr_filename),
        catalog=xosc.Catalog(),
    )
    scenario.header.description = (
        f"{data['classification']['scenario_type']}: "
        f"{cyclist_info.get('maneuver', 'cyclist maneuver')} vs "
        f"{motor_info.get('maneuver', 'car maneuver')}. "
        f"{data['conflict']['collision_description']}"
    )
    scenario.write_xml(str(output_path))


def generate_openscenario(data, output_path, xodr_filename):
    """Generate the OpenSCENARIO file for "turning" scenarios (dispatches
    to _generate_straight_crossing_openscenario for "crossing",
    _generate_longitudinal_openscenario for "longitudinal").

    Junction-template only: every "turning" report in the active corpus
    resolves to intersection_4way.xodr (the one straight_road.xodr
    "turning" report, turning_07, needs parking-lot access geometry
    neither template can represent and is excluded from the corpus --
    see report_loader.py's EXCLUDED_SCENARIO_IDS). The straight_road.xodr
    trajectory model this function used to also support was removed as
    dead code once that was confirmed (zero coverage across all 18 active
    reports); _is_junction_template's assertion below fails loudly instead
    of a confusing NameError if a future report ever needs it back.
    """
    output_path = Path(output_path)
    scenario_type = data.get("classification", {}).get("scenario_type")
    if scenario_type == "crossing":
        _generate_straight_crossing_openscenario(data, output_path, xodr_filename)
        return
    if scenario_type == "longitudinal":
        _generate_longitudinal_openscenario(data, output_path, xodr_filename)
        return
    assert _is_junction_template(xodr_filename), (
        f"generate_openscenario's 'turning' trajectory model only supports "
        f"the junction template; got {xodr_filename!r}. The straight_road.xodr "
        f"variant was removed as dead code (see this function's docstring) -- "
        f"if a real report now needs it, that model must be restored, not assumed."
    )

    osc_params = _osc_params(data)
    odr_params = data.get("generated_simulation_parameters", {}).get("opendrive", {})

    duration_s = float(
        osc_params.get("simulation_duration_s", DEFAULT_SIMULATION_DURATION_S)
    )
    conflict_s_m = float(osc_params.get("conflict", {}).get("conflict_s_m", 50))

    motor_id = _find_motor_participant_id(data)
    motor_actor = _actor_params(data, motor_id)
    cyclist_actor = _actor_params(data, "cyclist_1")
    motor_info = _participant(data, motor_id)
    cyclist_info = _participant(data, "cyclist_1")
    # Correct for whichever template was actually selected — see
    # _resolve_road_id's / _clamp_initial_s_to_real_road's docstrings.
    # Live-verified real-world correction, but deliberately scoped to
    # turn_left only: turning_08's report (a car on Reinickendorfer
    # Strasse turning left into Pankstrasse, hit by a cyclist) needs the
    # motor and cyclist on two PERPENDICULAR real streets, not the same
    # street's two lanes side by side -- putting the motor on entry road 1
    # instead of 0 fixed both its missing turn and its missing collision.
    # Applying that same change to turn_right reports too changed their
    # already-confirmed geometry (user feedback, 2026-08-30: "please do
    # not mix the turning right and left... get the code back to when i
    # said the turning right scenarios are okay") -- turn_right and
    # go_straight motors stay on entry road 0 exactly as before, matching
    # what was already reviewed and accepted.
    motor_maneuver_for_road = _maneuver_kind(motor_info.get("maneuver"))
    motor_actor["initial_road_id"] = _resolve_road_id(
        xodr_filename, is_secondary_approach=(motor_maneuver_for_road == "turn_left")
    )
    cyclist_actor["initial_road_id"] = _resolve_road_id(xodr_filename, is_secondary_approach=False)
    motor_actor["initial_s_m"] = _clamp_initial_s_to_real_road(
        xodr_filename, motor_actor["initial_road_id"], float(motor_actor["initial_s_m"])
    )
    cyclist_actor["initial_s_m"] = _clamp_initial_s_to_real_road(
        xodr_filename, cyclist_actor["initial_road_id"], float(cyclist_actor["initial_s_m"])
    )

    entities = xosc.Entities()
    entities.add_scenario_object(
        motor_id,
        _make_vehicle(motor_id, motor_actor.get("vehicle_category", "truck")),
    )
    entities.add_scenario_object(
        "cyclist_1",
        _make_vehicle("cyclist_1", cyclist_actor.get("vehicle_category", "bicycle")),
    )

    transition = xosc.TransitionDynamics(
        xosc.DynamicsShapes.step,
        xosc.DynamicsDimension.time,
        1,
    )

    init = xosc.Init()
    init.add_init_action(motor_id, xosc.TeleportAction(_lane_position(motor_actor)))
    init.add_init_action(
        motor_id,
        xosc.AbsoluteSpeedAction(float(motor_actor["initial_speed_mps"]), transition),
    )
    init.add_init_action("cyclist_1", xosc.TeleportAction(_lane_position(cyclist_actor)))
    init.add_init_action(
        "cyclist_1",
        xosc.AbsoluteSpeedAction(float(cyclist_actor["initial_speed_mps"]), transition),
    )

    motor_start_s, motor_y = _world_position_from_lane_s(motor_actor, odr_params)
    cyclist_start_s, cyclist_y = _world_position_from_lane_s(cyclist_actor, odr_params)
    motor_speed_mps = float(motor_actor["initial_speed_mps"])
    cyclist_speed_mps = float(cyclist_actor["initial_speed_mps"])
    cyclist_conflict_time_s = (
        (conflict_s_m - cyclist_start_s) / cyclist_speed_mps
        if cyclist_speed_mps > 0
        else duration_s * 0.7
    )
    conflict_time_s = min(duration_s - 1.0, max(4.0, cyclist_conflict_time_s))
    impact_x = conflict_s_m
    impact_y = cyclist_y

    if _is_junction_template(xodr_filename):
        # Build trajectories from intersection_4way.xodr's real junction
        # connector-road geometry instead of a synthetic s/t formula.
        motor_maneuver = _maneuver_kind(motor_info.get("maneuver"))
        # See motor_actor["initial_road_id"]'s comment above: only turn_left
        # uses the perpendicular entry road 1; turn_right/go_straight stay
        # on entry road 0, matching the already-confirmed geometry.
        motor_entry_road = 1 if motor_maneuver == "turn_left" else 0

        cyclist_samples, cyc_j_start, cyc_j_end = _junction_maneuver_samples(
            0, "go_straight", cyclist_y,
            approach_margin_m=max(30.0, impact_x - cyclist_start_s + 5),
        )

        motor_samples, motor_j_start, motor_j_end = _junction_maneuver_samples(
            motor_entry_road, motor_maneuver, motor_y,
            approach_margin_m=max(30.0, impact_x - motor_start_s + 5),
        )
        # Each vehicle's own connector-road midpoint is generally NOT the
        # same physical point once each vehicle's real lane offset is
        # applied -- same live-verified bug as the crossing generator's
        # (see _find_junction_crossing_point's docstring): the cyclist
        # and the turning motor vehicle would each reach a DIFFERENT
        # point at the scripted "impact" time, so their paths never
        # actually meet -- no visible collision, and afterward both
        # actors just look like they drove on past each other normally
        # ("the bike continues going straight even after the collision"
        # -- turning_01/03/04/05/06, first visual review).
        cyclist_impact_dist, motor_impact_dist = _find_junction_crossing_point(
            cyclist_samples, (cyc_j_start, cyc_j_end), motor_samples, (motor_j_start, motor_j_end)
        )
        # Live-verified degenerate case (turning_08, turn_left): the
        # real nearest-approach point between a straight cyclist and a
        # turning motor vehicle can legitimately fall right at the very
        # start of the motor's own connector -- before the connector has
        # curved away from the entry road at all, since a turn lane
        # initially hugs the same line as going straight. Geometrically
        # correct as a minimum-distance answer, but it means the turn
        # has zero visible progress before the scripted impact freezes
        # the vehicle -- "the car was supposed to turn left but didn't."
        #
        # A first attempt forced the search to require some minimum
        # turn-progress before accepting an impact point -- rejected on
        # sight: any meaningful progress requirement pushes the
        # "impact" location tens of meters past where the paths
        # actually come close (verified: even 15% progress widens the
        # gap from 2.37m to 2.70m, and it only gets worse from there --
        # 40% progress needs a 4.18m gap), so the car visibly turning
        # made "no collision happened" worse, not better. The real
        # tension is structural (this turn lane and the straight lane
        # diverge almost immediately in this template), not a number to
        # tune away.
        #
        # Instead: keep the impact at the genuine tightest-gap point
        # (best chance of a believable collision), and let the turn
        # become visible AFTER impact instead of before it -- the motor
        # vehicle keeps curving along its real connector for a couple
        # more meters/seconds post-collision before finally stopping
        # (a real car does not stop dead on contact), rather than
        # freezing at the exact moment of impact. See the
        # "post-impact continuation" markers added to motor_points
        # below.

        def _cyclist_at(dist_before_impact):
            return _path_point_at_distance(
                cyclist_samples, cyclist_impact_dist - dist_before_impact
            )

        def _motor_at(dist_before_impact):
            return _path_point_at_distance(
                motor_samples, motor_impact_dist - dist_before_impact
            )

        cyclist_dist0 = cyclist_impact_dist - (cyc_j_start - cyclist_start_s)
        motor_dist0 = motor_impact_dist - (motor_j_start - motor_start_s)

        # Same real-speed timing as the crossing generator: neither
        # actor is ever asked to move faster than its own configured
        # speed. Whichever actor has slack stays parked at its own real
        # starting position until it needs to start driving continuously
        # at full real speed.
        motor_natural_time = (
            motor_dist0 / motor_speed_mps if motor_speed_mps > 0 else conflict_time_s
        )
        cyclist_natural_time = (
            cyclist_dist0 / cyclist_speed_mps if cyclist_speed_mps > 0 else conflict_time_s
        )
        junction_impact_time_s = min(
            duration_s - 1.0,
            max(conflict_time_s, motor_natural_time, cyclist_natural_time),
        )

        def _real_speed_points_local(at_fn, dist0, marker_dists, speed_mps):
            def _time_for(dist_before_impact):
                if speed_mps <= 0:
                    return junction_impact_time_s
                return max(0.0, junction_impact_time_s - dist_before_impact / speed_mps)

            start_time = _time_for(dist0)
            points = [(0.0, *at_fn(dist0))]
            if start_time > 1e-6:
                points.append((start_time, *at_fn(dist0)))
            for marker in marker_dists:
                if 0 < marker < dist0:
                    points.append((_time_for(marker), *at_fn(marker)))
            points.append((junction_impact_time_s, *at_fn(0.0)))
            return points

        def _curve_markers_local(samples, j_start, impact_dist, dist0):
            lo = max(j_start, impact_dist - dist0)
            return sorted(
                {impact_dist - d for (d, x, y, h) in samples if lo < d < impact_dist},
                reverse=True,
            )

        cyclist_markers = _curve_markers_local(cyclist_samples, cyc_j_start, cyclist_impact_dist, cyclist_dist0)
        cyclist_points = _real_speed_points_local(
            _cyclist_at, cyclist_dist0, cyclist_markers, cyclist_speed_mps
        )
        cyclist_points.append((duration_s, *_cyclist_at(0.0)))

        motor_markers = _curve_markers_local(motor_samples, motor_j_start, motor_impact_dist, motor_dist0)
        motor_points = _real_speed_points_local(
            _motor_at, motor_dist0, motor_markers, motor_speed_mps
        )
        # Post-impact continuation (see the comment above
        # _find_junction_crossing_point's call): a real car doesn't
        # stop dead on contact, so when the pre-impact turn had
        # near-zero visible progress (the degenerate case -- turn_right
        # already shows ~61% progress before impact on its own and
        # doesn't need this; only fires when progress is small, e.g.
        # turning_08's turn_left), keep it curving along its own real
        # connector a bit further before finally holding, instead of
        # freezing exactly at the impact point -- this is what actually
        # makes the turn visible, without moving the collision point
        # itself away from the genuine tightest-gap location. Scoped
        # tightly so already-confirmed turn_right reports (which never
        # hit this condition) render byte-identical to before.
        motor_span_check = motor_j_end - motor_j_start
        motor_progress_check = (
            (motor_impact_dist - motor_j_start) / motor_span_check if motor_span_check > 0 else 1.0
        )
        motor_final_dist_before_impact = 0.0
        if (
            motor_maneuver in ("turn_left", "turn_right")
            and motor_speed_mps > 0
            and motor_progress_check < 0.2
        ):
            continue_dist = min(6.0, motor_j_end - motor_impact_dist)
            if continue_dist > 0.5:
                continue_time = continue_dist / motor_speed_mps
                motor_points.append(
                    (junction_impact_time_s + continue_time, *_motor_at(-continue_dist))
                )
                motor_final_dist_before_impact = -continue_dist
        motor_points.append((duration_s, *_motor_at(motor_final_dist_before_impact)))

    storyboard = xosc.StoryBoard(
        init,
        xosc.ValueTrigger(
            "StopSimulation",
            0,
            xosc.ConditionEdge.rising,
            xosc.SimulationTimeCondition(duration_s, xosc.Rule.greaterThan),
            "stop",
        ),
    )
    storyboard.add_maneuver(
        _make_follow_trajectory_maneuver(
            "MotorVehicleManeuver",
            _make_trajectory("MotorVehicleTrajectory", motor_points),
        ),
        motor_id,
    )
    storyboard.add_maneuver(
        _make_follow_trajectory_maneuver(
            "CyclistGoStraight",
            _make_trajectory("CyclistGoStraightTrajectory", cyclist_points),
        ),
        "cyclist_1",
    )

    scenario = xosc.Scenario(
        data["source"]["source_id"],
        "Shayma",
        xosc.ParameterDeclarations(),
        entities=entities,
        storyboard=storyboard,
        roadnetwork=xosc.RoadNetwork(roadfile=xodr_filename),
        catalog=xosc.Catalog(),
    )
    scenario.header.description = (
        f"{data['classification']['scenario_type']}: "
        f"{motor_info.get('maneuver', 'vehicle maneuver')} vs "
        f"{cyclist_info.get('maneuver', 'cyclist maneuver')}. "
        f"{data['conflict']['collision_description']}"
    )
    scenario.write_xml(str(output_path))
