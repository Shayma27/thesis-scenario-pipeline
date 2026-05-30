import argparse
import json
import math
from pathlib import Path

from scenariogeneration import xosc


DEFAULT_INPUT_JSON = "output.json"
DEFAULT_ROAD_FILE = "generic_bikecar_testroad.xodr"
DEFAULT_OUTPUT_XOSC = "generic_bikecar_test.xosc"


def load_scenario(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def find_participant(scenario, role):
    for participant in scenario.get("participants", []):
        if participant.get("role") == role:
            return participant
    return {}


def participant_speed(participant, fallback):
    speed = participant.get("speed_mps")
    return fallback if speed is None else float(speed)


def make_vehicle(name, participant):
    participant_type = participant.get("type")

    if participant_type == "truck":
        bbox = xosc.BoundingBox(2.5, 8.0, 3.0, 1.5, 0, 1.2)
        front_axle = xosc.Axle(0.5, 0.8, 2.0, 5.5, 0.4)
        rear_axle = xosc.Axle(0.5, 0.8, 2.0, 0.0, 0.4)
        vehicle = xosc.Vehicle(
            name,
            xosc.VehicleCategory.truck,
            bbox,
            front_axle,
            rear_axle,
            60,
            10,
            10,
        )
        vehicle.add_property("json_participant_type", "truck")
        return vehicle

    if participant_type in {"bicycle", "e_bike"}:
        bbox = xosc.BoundingBox(0.6, 1.8, 1.5, 0.3, 0, 0.8)
        front_axle = xosc.Axle(0.0, 0.3, 0.5, 1.2, 0.15)
        rear_axle = xosc.Axle(0.0, 0.3, 0.5, 0.0, 0.15)
        vehicle = xosc.Vehicle(
            name,
            xosc.VehicleCategory.bicycle,
            bbox,
            front_axle,
            rear_axle,
            15,
            5,
            5,
        )
        vehicle.add_property("json_participant_type", participant_type)
        return vehicle

    bbox = xosc.BoundingBox(1.8, 4.5, 1.6, 1.3, 0, 0.8)
    front_axle = xosc.Axle(0.5, 0.6, 1.6, 3.0, 0.3)
    rear_axle = xosc.Axle(0.5, 0.6, 1.6, 0.0, 0.3)
    vehicle = xosc.Vehicle(
        name,
        xosc.VehicleCategory.car,
        bbox,
        front_axle,
        rear_axle,
        50,
        8,
        8,
    )
    vehicle.add_property("json_participant_type", participant_type or "unknown")
    return vehicle


def make_trajectory(name, timed_points):
    times = [point[0] for point in timed_points]
    positions = [
        xosc.WorldPosition(point[1], point[2], 0, point[3], 0, 0)
        for point in timed_points
    ]
    trajectory = xosc.Trajectory(name, False)
    trajectory.add_shape(xosc.Polyline(times, positions))
    return trajectory


def add_follow_trajectory_maneuver(entity_name, maneuver_name, trajectory):
    event = xosc.Event(f"{maneuver_name}Event", xosc.Priority.override)
    event.add_action(
        f"{maneuver_name}FollowTrajectory",
        xosc.FollowTrajectoryAction(trajectory, xosc.FollowingMode.position),
    )
    event.add_trigger(
        xosc.ValueTrigger(
            f"{maneuver_name}Start",
            0,
            xosc.ConditionEdge.none,
            xosc.SimulationTimeCondition(0.1, xosc.Rule.greaterThan),
        )
    )

    maneuver = xosc.Maneuver(maneuver_name)
    maneuver.add_event(event)
    return maneuver


def main():
    parser = argparse.ArgumentParser(
        description="Generate a simplified OpenSCENARIO file from output.json."
    )
    parser.add_argument("--input-json", default=DEFAULT_INPUT_JSON)
    parser.add_argument("--road-file", default=DEFAULT_ROAD_FILE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT_XOSC)
    args = parser.parse_args()

    scenario_json = load_scenario(args.input_json)
    motor_vehicle = find_participant(scenario_json, "motor_vehicle")
    cyclist = find_participant(scenario_json, "cyclist")
    scenario_type = scenario_json.get("scenario_type")

    road = xosc.RoadNetwork(roadfile=args.road_file)
    params = xosc.ParameterDeclarations()
    catalog = xosc.Catalog()

    truck = make_vehicle("Truck", motor_vehicle)
    bike = make_vehicle("Bicycle", cyclist)

    entities = xosc.Entities()
    entities.add_scenario_object("Truck", truck)
    entities.add_scenario_object("Bicycle", bike)

    transition = xosc.TransitionDynamics(
        xosc.DynamicsShapes.step,
        xosc.DynamicsDimension.time,
        1,
    )

    truck_speed = participant_speed(motor_vehicle, 8.0)
    bike_speed = participant_speed(cyclist, 6.0)

    init = xosc.Init()
    init.add_init_action("Truck", xosc.TeleportAction(xosc.WorldPosition(20, -1.75, 0, 0, 0, 0)))
    init.add_init_action("Truck", xosc.AbsoluteSpeedAction(truck_speed, transition))
    init.add_init_action("Bicycle", xosc.TeleportAction(xosc.WorldPosition(30, -5.0, 0, 0, 0, 0)))
    init.add_init_action("Bicycle", xosc.AbsoluteSpeedAction(bike_speed, transition))

    if scenario_type == "right_turn_conflict":
        truck_points = [
            (0, 20, -1.75, 0),
            (4, 54, -1.75, 0),
            (8, 60, -8, -math.pi / 2),
            (12, 60, -35, -math.pi / 2),
        ]
        bike_points = [
            (0, 30, -5.0, 0),
            (4, 50, -5.0, 0),
            (8, 70, -5.0, 0),
            (12, 95, -5.0, 0),
        ]
    else:
        truck_points = [
            (0, 20, -1.75, 0),
            (5, 55, -1.75, 0),
            (10, 90, -1.75, 0),
        ]
        bike_points = [
            (0, 30, -5.0, 0),
            (5, 60, -5.0, 0),
            (10, 95, -5.0, 0),
        ]

    truck_trajectory = make_trajectory("TruckTrajectory", truck_points)
    bike_trajectory = make_trajectory("BicycleTrajectory", bike_points)

    truck_maneuver = add_follow_trajectory_maneuver(
        "Truck", "TruckManeuver", truck_trajectory
    )
    bike_maneuver = add_follow_trajectory_maneuver(
        "Bicycle", "BicycleManeuver", bike_trajectory
    )

    storyboard = xosc.StoryBoard(
        init,
        xosc.ValueTrigger(
            "StopSim",
            0,
            xosc.ConditionEdge.rising,
            xosc.SimulationTimeCondition(15, xosc.Rule.greaterThan),
            "stop",
        ),
    )
    storyboard.add_maneuver(truck_maneuver, "Truck")
    storyboard.add_maneuver(bike_maneuver, "Bicycle")

    description = (
        f"Generated from {args.input_json}: {scenario_type}; "
        f"{scenario_json.get('scenario_summary') or ''}"
    )
    scenario = xosc.Scenario(
        "JSON_Derived_BikeCar_Test",
        "Shayma",
        params,
        entities=entities,
        storyboard=storyboard,
        roadnetwork=road,
        catalog=catalog,
        description=description,
    )

    scenario.write_xml(args.output)
    print(f"Success: {args.output} generated from {args.input_json}.")
    print(f"Road file linked in OpenSCENARIO: {args.road_file}")
    print(f"Scenario type: {scenario_type}")
    print(
        "Simplification: positions and timings are logical placeholders because "
        "the JSON marks exact coordinates, speeds, and impact point as missing."
    )


if __name__ == "__main__":
    main()
