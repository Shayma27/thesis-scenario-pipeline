import math
import xml.etree.ElementTree as ET
from pathlib import Path

from scenariogeneration import xosc

from defaults import DEFAULT_CYCLIST_LATERAL_POSITION, DEFAULT_SIMULATION_DURATION_S

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
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
    """Approximate lane positions for trajectory points on the straight road.

    Assumption 1 (docs/modeling_assumptions.md): straight_road.xodr models a
    standard two-way road — lane id 1 is the real OpenDRIVE lane on the
    opposite (positive-t) side of the center lane from lane id -1, adjacent
    to it across the center-lane marking. For "longitudinal" scenarios this
    positive/negative pair is reinterpreted as two same-direction parallel
    lanes rather than opposing carriageways, so the lane id's sign (not its
    absolute value) decides which side of the center lane a point sits on.
    This keeps lane 1 and lane -1 at distinct, correctly-adjacent lateral
    offsets instead of collapsing onto the same y — required for a
    lane-change between them (e.g. manual_classification_reference.md
    reports 18/19) to show up as an actual lateral move. Every other
    scenario type only ever uses negative lane ids, so this is a strict
    generalization with no behavior change for them.
    """
    s = float(actor["initial_s_m"])
    lane_id = int(actor["initial_lane_id"])
    lane_index = abs(lane_id)
    side = 1 if lane_id > 0 else -1
    motor_lane_width_m = float(odr_params.get("motor_lane_width_m", 3.5))
    bike_lane_width_m = float(odr_params.get("bike_lane_width_m", 2.0))
    motor_lane_count = int(odr_params.get("motor_lane_count", 1))

    if lane_index <= motor_lane_count:
        y = side * motor_lane_width_m * (lane_index - 0.5)
    elif lane_index == motor_lane_count + 1 and bike_lane_width_m > 0:
        y = side * (motor_lane_width_m * motor_lane_count + bike_lane_width_m / 2)
    else:
        y = side * (
            motor_lane_width_m * motor_lane_count
            + max(0, bike_lane_width_m)
            + 0.75
        )
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


def _rightmost_lane_center_offset(lane_width_m, lane_count):
    return -float(lane_width_m) * (max(1, int(lane_count)) - 0.5)


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


def _road_point(segments, s):
    s = max(0.0, min(s, _road_total_length(segments)))
    for seg in segments:
        if seg["s0"] <= s <= seg["s0"] + seg["length"] + 1e-9:
            return _evaluate_geometry_segment(seg, s - seg["s0"])
    last = segments[-1]
    return _evaluate_geometry_segment(last, last["length"])


def _road_world_point(segments, s, t_m):
    x, y, heading = _road_point(segments, s)
    nx, ny = _road_normal(heading)
    return x + nx * t_m, y + ny * t_m, heading


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
        x, y, heading = _road_world_point(entry_segs, s, t_offset_m)
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
    # actual start point are a few OpenDRIVE lanes apart in this template
    # (the reference line vs. the specific lane's connection point) — shift
    # the whole entry-road tail so it meets the connector with no jump.
    entry_end_x, entry_end_y, _ = _road_world_point(entry_segs, 0.0, t_offset_m)
    conn_start_x, conn_start_y, _ = _road_world_point(connector_segs, 0.0, t_offset_m)
    dx, dy = conn_start_x - entry_end_x, conn_start_y - entry_end_y
    samples = [(d, x + dx, y + dy, h) for d, x, y, h in samples]

    junction_entry_distance = samples[-1][0]

    # Connector: real junction geometry, s increasing 0 -> connector_length.
    n_conn = max(4, int(connector_length / sample_step_m))
    for i in range(1, n_conn + 1):
        s = connector_length * i / n_conn
        x, y, heading = _road_world_point(connector_segs, s, t_offset_m)
        samples.append((junction_entry_distance + s, x, y, heading))

    junction_exit_distance = samples[-1][0]

    # A short stretch of the exit road so trajectories can extend past impact.
    # Direction depends on which end of the exit road the connector attaches
    # to (contactPoint "start" -> s increasing; "end" -> s decreasing).
    exit_length = _road_total_length(exit_segs)
    exit_m = min(10.0, exit_length)
    n_exit = max(2, int(exit_m / sample_step_m))
    conn_end_x, conn_end_y, _ = _road_world_point(connector_segs, connector_length, t_offset_m)
    exit_anchor_s = 0.0 if exit_contact == "start" else exit_length
    exit_anchor_x, exit_anchor_y, _ = _road_world_point(exit_segs, exit_anchor_s, t_offset_m)
    edx, edy = conn_end_x - exit_anchor_x, conn_end_y - exit_anchor_y
    direction = 1.0 if exit_contact == "start" else -1.0
    for i in range(1, n_exit + 1):
        s = exit_anchor_s + direction * exit_m * i / n_exit
        x, y, heading = _road_world_point(exit_segs, s, t_offset_m)
        if exit_contact == "end":
            heading = _normalize_angle(heading + math.pi)
        samples.append((junction_exit_distance + exit_m * i / n_exit, x + edx, y + edy, heading))

    return samples, junction_entry_distance, junction_exit_distance


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


def _cyclist_lateral_offset(odr_params, osc_params, road_key="primary_road_lanes"):
    lane_width_m = float(odr_params.get("motor_lane_width_m", 3.5))
    bike_lane_width_m = float(odr_params.get("bike_lane_width_m", 2.0))
    lane_count = int(odr_params.get(road_key, 1))
    policy = osc_params.get("cyclist_lateral_position", DEFAULT_CYCLIST_LATERAL_POSITION)
    has_bike_facility = bool(odr_params.get("primary_has_bike_facility"))

    if policy == "rightmost_motor_lane":
        return _rightmost_lane_center_offset(lane_width_m, lane_count)
    if policy == "leftmost_motor_lane":
        return -lane_width_m / 2
    if policy == "middle_motor_lane":
        return _rightmost_lane_center_offset(lane_width_m, max(1, (lane_count + 1) // 2))
    if has_bike_facility and policy in {"right", "rightmost", "both"}:
        return -(lane_width_m * lane_count + bike_lane_width_m / 2)
    if has_bike_facility and policy == "left":
        return -bike_lane_width_m / 2
    if policy in {"right", "rightmost", "both"}:
        return _rightmost_lane_center_offset(lane_width_m, lane_count)
    if policy == "left":
        return -lane_width_m / 2
    if policy == "middle":
        return _rightmost_lane_center_offset(lane_width_m, max(1, (lane_count + 1) // 2))
    return _rightmost_lane_center_offset(lane_width_m, lane_count)


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
    # JSON road/lane/s values place both actors on the two OpenDRIVE approaches.
    init.add_init_action("cyclist_1", xosc.TeleportAction(_lane_position(cyclist_actor)))
    init.add_init_action(
        "cyclist_1",
        xosc.AbsoluteSpeedAction(float(cyclist_actor["initial_speed_mps"]), transition),
    )
    init.add_init_action("car_1", xosc.TeleportAction(_lane_position(car_actor)))
    init.add_init_action(
        "car_1",
        xosc.AbsoluteSpeedAction(float(car_actor["initial_speed_mps"]), transition),
    )

    # Match the report-specific movement directions. When OSM enrichment has
    # found directed way segments, their headings are used here; otherwise this
    # falls back to the simple perpendicular crossing abstraction.
    cyclist_offset = _cyclist_lateral_offset(odr_params, osc_params, "primary_road_lanes")
    car_offset = -float(odr_params.get("motor_lane_width_m", 3.5)) * (
        abs(int(car_actor["initial_lane_id"])) - 0.5
    )
    car_path = osc_params.get("car_path")

    if _is_junction_template(xodr_filename):
        # Build trajectories from intersection_4way.xodr's real junction
        # connector-road geometry instead of a synthetic s/t formula and
        # line-intersection. The choreography (times, distance-before-impact)
        # is preserved from the original design; only the spatial mapping
        # changes. "Impact" is placed at the midpoint of each vehicle's own
        # connector road (see generate_openscenario for the same convention).
        cyclist_d0 = road_length_m / 2 - float(cyclist_actor["initial_s_m"])
        car_d0 = road_length_m / 2 - float(car_actor["initial_s_m"])
        car_maneuver = (
            "turn_left" if car_path == "turn_left_from_secondary_to_primary" else "go_straight"
        )

        cyclist_samples, cyc_j_start, cyc_j_end = _junction_maneuver_samples(
            0, "go_straight", cyclist_offset, approach_margin_m=max(30.0, cyclist_d0 + 5)
        )
        cyclist_impact_dist = cyc_j_start + 0.5 * (cyc_j_end - cyc_j_start)

        def _cyclist_at(dist_before_impact):
            return _path_point_at_distance(
                cyclist_samples, cyclist_impact_dist - dist_before_impact
            )

        cyclist_points = [
            (0, *_cyclist_at(cyclist_d0)),
            (impact_time_s - 0.3, *_cyclist_at(1.8)),
            (impact_time_s, *_cyclist_at(0.0)),
            (duration_s, *_cyclist_at(0.0)),
        ]

        car_samples, car_j_start, car_j_end = _junction_maneuver_samples(
            1, car_maneuver, car_offset, approach_margin_m=max(30.0, car_d0 + 5)
        )
        car_impact_dist = car_j_start + 0.5 * (car_j_end - car_j_start)

        def _car_at(dist_before_impact):
            return _path_point_at_distance(car_samples, car_impact_dist - dist_before_impact)

        if car_path == "turn_left_from_secondary_to_primary":
            car_points = [
                (0, *_car_at(car_d0)),
                (impact_time_s - 1.0, *_car_at(6.0)),
                (impact_time_s - 0.3, *_car_at(2.5)),
                (impact_time_s, *_car_at(0.0)),
                (duration_s, *_car_at(0.0)),
            ]
        else:
            car_points = [
                (0, *_car_at(car_d0)),
                (impact_time_s - 0.3, *_car_at(2.5)),
                (impact_time_s, *_car_at(0.0)),
                (duration_s, *_car_at(0.0)),
            ]
    else:
        cyclist_start = _world_from_road_s_t(
            road_length_m,
            primary_heading,
            float(cyclist_actor["initial_s_m"]),
            cyclist_offset,
        )
        car_start = _world_from_road_s_t(
            road_length_m,
            secondary_heading,
            float(car_actor["initial_s_m"]),
            car_offset,
        )
        cyclist_lane_origin = _world_from_road_s_t(
            road_length_m,
            primary_heading,
            road_length_m / 2,
            cyclist_offset,
        )
        car_lane_origin = _world_from_road_s_t(
            road_length_m,
            secondary_heading,
            road_length_m / 2,
            car_offset,
        )
        impact_x, impact_y = _line_intersection(
            cyclist_lane_origin,
            primary_heading,
            car_lane_origin,
            secondary_heading,
        )

        cyclist_pre_x = impact_x - math.cos(primary_heading) * 1.8
        cyclist_pre_y = impact_y - math.sin(primary_heading) * 1.8
        car_pre_x = impact_x - math.cos(secondary_heading) * 2.5
        car_pre_y = impact_y - math.sin(secondary_heading) * 2.5

        # Both paths are timed to meet at the same conflict point. After impact,
        # the positions are held so the collision is visible instead of a pass-through.
        cyclist_points = [
            (0, cyclist_start[0], cyclist_start[1], primary_heading),
            (impact_time_s - 0.3, cyclist_pre_x, cyclist_pre_y, primary_heading),
            (impact_time_s, impact_x, impact_y, primary_heading),
            (duration_s, impact_x, impact_y, primary_heading),
        ]
        if car_path == "turn_left_from_secondary_to_primary":
            nominal_left_exit = _normalize_angle(secondary_heading + math.pi / 2)
            exit_heading = _closest_heading(
                nominal_left_exit,
                [primary_heading, _normalize_angle(primary_heading + math.pi)],
            )
            car_turn_entry_x = impact_x - math.cos(secondary_heading) * 6.0
            car_turn_entry_y = impact_y - math.sin(secondary_heading) * 6.0
            car_turn_pre_x = impact_x - math.cos(exit_heading) * 2.5
            car_turn_pre_y = impact_y - math.sin(exit_heading) * 2.5
            car_points = [
                (0, car_start[0], car_start[1], secondary_heading),
                (impact_time_s - 1.0, car_turn_entry_x, car_turn_entry_y, secondary_heading),
                (
                    impact_time_s - 0.3,
                    car_turn_pre_x,
                    car_turn_pre_y,
                    _interpolate_heading(secondary_heading, exit_heading, 0.65),
                ),
                (impact_time_s, impact_x, impact_y, exit_heading),
                (duration_s, impact_x, impact_y, exit_heading),
            ]
        else:
            car_points = [
                (0, car_start[0], car_start[1], secondary_heading),
                (impact_time_s - 0.3, car_pre_x, car_pre_y, secondary_heading),
                (impact_time_s, impact_x, impact_y, secondary_heading),
                (duration_s, impact_x, impact_y, secondary_heading),
            ]

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


def generate_openscenario(data, output_path, xodr_filename):
    """Generate the OpenSCENARIO file for "turning"/"longitudinal"/"other"
    scenarios (dispatches to _generate_straight_crossing_openscenario for
    "crossing")."""
    output_path = Path(output_path)
    scenario_type = data.get("classification", {}).get("scenario_type")
    if scenario_type == "crossing":
        _generate_straight_crossing_openscenario(data, output_path, xodr_filename)
        return

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
    # _resolve_road_id's / _clamp_initial_s_to_real_road's docstrings. Both
    # actors share the same (primary) approach here; only "crossing"
    # (handled above, in _generate_straight_crossing_openscenario) puts the
    # motor vehicle on a secondary approach.
    motor_actor["initial_road_id"] = _resolve_road_id(xodr_filename, is_secondary_approach=False)
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

    road_length_m = float(odr_params.get("road_length_m", 100))
    primary_heading = float(odr_params.get("primary_heading_rad", 0))
    secondary_heading = float(
        odr_params.get("secondary_heading_rad", primary_heading - math.pi / 2)
    )
    # Turn angle relative to primary road (negative = rightward)
    turn_rel = _normalize_angle(secondary_heading - primary_heading)

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
    turn_duration_s = 2.2
    impact_x = conflict_s_m
    impact_y = cyclist_y

    if _is_junction_template(xodr_filename):
        # Build trajectories from intersection_4way.xodr's real junction
        # connector-road geometry instead of a synthetic s/t formula. The
        # choreography (times, and distance-before-impact at each waypoint)
        # is preserved from the original design; only the spatial mapping
        # changes. "Impact" is placed at the midpoint of each vehicle's own
        # connector road — finding the exact geometric crossing of the two
        # real connector polylines is a further refinement, out of scope here.
        motor_maneuver = _maneuver_kind(motor_info.get("maneuver"))

        cyclist_samples, cyc_j_start, cyc_j_end = _junction_maneuver_samples(
            0, "go_straight", cyclist_y,
            approach_margin_m=max(30.0, impact_x - cyclist_start_s + 5),
        )
        cyclist_impact_dist = cyc_j_start + 0.5 * (cyc_j_end - cyc_j_start)

        def _cyclist_at(dist_before_impact):
            return _path_point_at_distance(
                cyclist_samples, cyclist_impact_dist - dist_before_impact
            )

        cyclist_points = [
            (0, *_cyclist_at(impact_x - cyclist_start_s)),
            (conflict_time_s - 0.2, *_cyclist_at(1.0)),
            (conflict_time_s, *_cyclist_at(0.0)),
            (duration_s, *_cyclist_at(0.0)),
        ]

        # Parked motor vehicle (dooring) or motor already past conflict: static trajectory.
        if motor_speed_mps <= 0 or motor_start_s >= conflict_s_m - 5:
            motor_samples, motor_j_start, _ = _junction_maneuver_samples(
                0, "go_straight", motor_y,
                approach_margin_m=max(30.0, impact_x - motor_start_s + 5),
            )
            point = _path_point_at_distance(
                motor_samples, motor_j_start - (impact_x - motor_start_s)
            )
            motor_points = [(0, *point), (duration_s, *point)]
        else:
            motor_samples, motor_j_start, motor_j_end = _junction_maneuver_samples(
                0, motor_maneuver, motor_y,
                approach_margin_m=max(30.0, impact_x - motor_start_s + 5),
            )
            motor_impact_dist = motor_j_start + 0.5 * (motor_j_end - motor_j_start)

            def _motor_at(dist_before_impact):
                return _path_point_at_distance(
                    motor_samples, motor_impact_dist - dist_before_impact
                )

            motor_turn_start_time_s = max(1.0, conflict_time_s - turn_duration_s)
            motor_approach_dist = impact_x - min(
                conflict_s_m - 8.0,
                motor_start_s + motor_speed_mps * motor_turn_start_time_s,
            )
            motor_points = [
                (0, *_motor_at(impact_x - motor_start_s)),
                (motor_turn_start_time_s, *_motor_at(motor_approach_dist)),
                (conflict_time_s - 1.2, *_motor_at(4.2)),
                (conflict_time_s - 0.5, *_motor_at(1.2)),
                (conflict_time_s, *_motor_at(0.0)),
                (duration_s, *_motor_at(0.0)),
            ]
    else:
        # Parked motor vehicle (dooring) or motor already past conflict: static trajectory.
        # All other types: right-turn approach to the conflict point so the collision is
        # visible in esmini regardless of the exact scenario sub-type.
        if motor_speed_mps <= 0 or motor_start_s >= conflict_s_m - 5:
            motor_points = [
                (0, motor_start_s, motor_y, 0),
                (duration_s, motor_start_s, motor_y, 0),
            ]
        else:
            motor_turn_start_time_s = max(1.0, conflict_time_s - turn_duration_s)
            motor_approach_s = min(
                conflict_s_m - 8.0,
                motor_start_s + motor_speed_mps * motor_turn_start_time_s,
            )
            motor_points = [
                (0, motor_start_s, motor_y, 0),
                (motor_turn_start_time_s, motor_approach_s, motor_y, 0),
                (conflict_time_s - 1.2, impact_x - 4.2, motor_y - 0.3, turn_rel * 0.159),
                (conflict_time_s - 0.5, impact_x - 1.2, impact_y + 0.4, turn_rel * 0.700),
                (conflict_time_s, impact_x, impact_y, turn_rel),
                (duration_s, impact_x, impact_y, turn_rel),
            ]

        cyclist_points = [
            (0, cyclist_start_s, cyclist_y, 0),
            (conflict_time_s - 0.2, impact_x - 1.0, cyclist_y, 0),
            (conflict_time_s, impact_x, impact_y, 0),
            (duration_s, impact_x, impact_y, 0),
        ]

        def _to_world(points):
            result = []
            for t, s, lat, hdg in points:
                wx, wy = _world_from_road_s_t(road_length_m, primary_heading, s, lat)
                result.append((t, wx, wy, primary_heading + hdg))
            return result

        motor_points = _to_world(motor_points)
        cyclist_points = _to_world(cyclist_points)

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
