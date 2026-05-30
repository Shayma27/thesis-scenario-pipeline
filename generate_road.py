import argparse
import json
import math
from pathlib import Path

from scenariogeneration import xodr


DEFAULT_INPUT_JSON = "output.json"
DEFAULT_OUTPUT_XODR = "generic_bikecar_testroad.xodr"
ROAD_LENGTH = 120.0
DRIVING_WIDTH = 3.5
BIKE_WIDTH = 2.0


def load_scenario(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def get_nested(data, *keys, default=None):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def make_lane_section(include_bike_lane):
    solid = xodr.RoadMark(xodr.RoadMarkType.solid, 0.15)

    center = xodr.Lane(a=0)
    center.add_roadmark(solid)

    lanesec = xodr.LaneSection(0, center)

    lane_driving = xodr.Lane(a=DRIVING_WIDTH, lane_type=xodr.LaneType.driving)
    lane_driving.add_roadmark(solid)
    lanesec.add_right_lane(lane_driving)

    if include_bike_lane:
        lane_bike = xodr.Lane(a=BIKE_WIDTH, lane_type=xodr.LaneType.biking)
        lane_bike.add_roadmark(solid)
        lanesec.add_right_lane(lane_bike)

    lanes = xodr.Lanes()
    lanes.add_lanesection(lanesec)
    return lanes


def make_straight_road(road_id, name, x_start, y_start, heading, include_bike_lane):
    planview = xodr.PlanView(x_start, y_start, heading)
    planview.add_geometry(xodr.Line(ROAD_LENGTH))
    return xodr.Road(
        road_id,
        planview,
        make_lane_section(include_bike_lane),
        name=name,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Generate a simplified OpenDRIVE road network from output.json."
    )
    parser.add_argument("--input-json", default=DEFAULT_INPUT_JSON)
    parser.add_argument("--output", default=DEFAULT_OUTPUT_XODR)
    args = parser.parse_args()

    scenario = load_scenario(args.input_json)
    topology = get_nested(
        scenario,
        "hierarchical_scenario_elements",
        "road_topology",
        "topology_type",
        default="unknown",
    )
    cycling_infra = get_nested(
        scenario,
        "hierarchical_scenario_elements",
        "transportation_facilities",
        "cycling_infrastructure",
        default="unknown",
    )
    include_bike_lane = cycling_infra in {
        "separated_cycle_track",
        "bike_lane",
        "protected_bike_lane",
        "roadway_mixed",
    }

    odr = xodr.OpenDrive("JSON_Derived_BikeCar_TestRoad")

    main_road = make_straight_road(
        1,
        scenario.get("location", {}).get("primary_road_name") or "PrimaryRoad",
        0,
        0,
        0,
        include_bike_lane,
    )
    odr.add_road(main_road)

    if topology in {"intersection", "t_junction"}:
        secondary_name = (
            scenario.get("location", {}).get("secondary_road_name_or_access")
            or "SecondaryRoad"
        )
        side_road = make_straight_road(
            2,
            secondary_name,
            60,
            -60,
            math.pi / 2,
            False,
        )
        odr.add_road(side_road)

    odr.adjust_roads_and_lanes()
    odr.write_xml(args.output)

    print(f"Success: {args.output} generated from {args.input_json}.")
    print(f"Scenario type: {scenario.get('scenario_type')}")
    print(f"Road topology: {topology}")
    print(f"Cycling infrastructure: {cycling_infra}")
    print("Lane -1 on primary road: driving")
    if include_bike_lane:
        print("Lane -2 on primary road: biking")
    if topology in {"intersection", "t_junction"}:
        print("Road 2: simplified side road for the junction/access leg")


if __name__ == "__main__":
    main()
