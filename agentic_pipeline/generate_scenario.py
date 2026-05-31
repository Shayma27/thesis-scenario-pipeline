import math
from pathlib import Path

from scenariogeneration import xosc

from defaults import DEFAULT_CYCLIST_LATERAL_POSITION, DEFAULT_SIMULATION_DURATION_S


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
    """Approximate lane positions for trajectory points on the straight road."""
    s = float(actor["initial_s_m"])
    lane_id = int(actor["initial_lane_id"])
    lane_index = abs(lane_id)
    motor_lane_width_m = float(odr_params.get("motor_lane_width_m", 3.5))
    bike_lane_width_m = float(odr_params.get("bike_lane_width_m", 2.0))
    motor_lane_count = int(odr_params.get("motor_lane_count", 1))

    if lane_index <= motor_lane_count:
        y = -motor_lane_width_m * (lane_index - 0.5)
    elif lane_index == motor_lane_count + 1 and bike_lane_width_m > 0:
        y = -(motor_lane_width_m * motor_lane_count + bike_lane_width_m / 2)
    else:
        y = -(
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
    car_path = osc_params.get("car_path")

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
    """Generate the first OpenSCENARIO parking-access conflict prototype."""
    output_path = Path(output_path)
    scenario_type = data.get("classification", {}).get("scenario_type")
    if scenario_type == "straight_crossing_conflict":
        _generate_straight_crossing_openscenario(data, output_path, xodr_filename)
        return

    osc_params = _osc_params(data)
    odr_params = data.get("generated_simulation_parameters", {}).get("opendrive", {})

    duration_s = float(
        osc_params.get("simulation_duration_s", DEFAULT_SIMULATION_DURATION_S)
    )
    conflict_s_m = float(osc_params.get("conflict", {}).get("conflict_s_m", 50))
    motor_lane_width_m = float(odr_params.get("motor_lane_width_m", 3.5))
    bike_lane_width_m = float(odr_params.get("bike_lane_width_m", 2.0))

    motor_id = _find_motor_participant_id(data)
    motor_actor = _actor_params(data, motor_id)
    cyclist_actor = _actor_params(data, "cyclist_1")
    motor_info = _participant(data, motor_id)
    cyclist_info = _participant(data, "cyclist_1")

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
    turn_duration_s = 2.2
    impact_x = conflict_s_m
    impact_y = cyclist_y

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
            (conflict_time_s - 1.2, impact_x - 4.2, motor_y - 0.3, -0.25),
            (conflict_time_s - 0.5, impact_x - 1.2, impact_y + 0.4, -1.1),
            (conflict_time_s, impact_x, impact_y, -math.pi / 2),
            (duration_s, impact_x, impact_y, -math.pi / 2),
        ]

    cyclist_points = [
        (0, cyclist_start_s, cyclist_y, 0),
        (conflict_time_s - 0.2, impact_x - 1.0, cyclist_y, 0),
        (conflict_time_s, impact_x, impact_y, 0),
        (duration_s, impact_x, impact_y, 0),
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
