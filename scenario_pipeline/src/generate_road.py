import math
from pathlib import Path

from scenariogeneration import xodr

from defaults import (
    DEFAULT_BIKE_LANE_WIDTH_M,
    DEFAULT_MOTOR_LANE_WIDTH_M,
    DEFAULT_PARKING_ACCESS_S_M,
    DEFAULT_ROAD_LENGTH_M,
)


def _road_params(data):
    return data.get("generated_simulation_parameters", {}).get("opendrive", {})


def _make_lane_section(motor_lane_width_m, bike_lane_width_m):
    """Map JSON lane-width defaults to driving, biking, and sidewalk lanes."""
    center_mark = xodr.RoadMark(xodr.RoadMarkType.solid, 0.15)
    lane_mark = xodr.RoadMark(xodr.RoadMarkType.solid, 0.12)
    sidewalk_mark = xodr.RoadMark(xodr.RoadMarkType.solid, 0.08)

    center_lane = xodr.Lane(a=0)
    center_lane.add_roadmark(center_mark)

    lane_section = xodr.LaneSection(0, center_lane)

    driving_lane = xodr.Lane(
        lane_type=xodr.LaneType.driving,
        a=float(motor_lane_width_m),
    )
    driving_lane.add_roadmark(lane_mark)
    lane_section.add_right_lane(driving_lane)

    bike_lane = xodr.Lane(
        lane_type=xodr.LaneType.biking,
        a=float(bike_lane_width_m),
    )
    bike_lane.add_roadmark(lane_mark)
    lane_section.add_right_lane(bike_lane)

    # Visual context for a separated cycle track: the cyclist still uses lane
    # -2 from the JSON, while the outer lane gives esmini a sidewalk/edge.
    sidewalk_lane = xodr.Lane(
        lane_type=xodr.LaneType.sidewalk,
        a=1.5,
    )
    sidewalk_lane.add_roadmark(sidewalk_mark)
    lane_section.add_right_lane(sidewalk_lane)

    lanes = xodr.Lanes()
    lanes.add_lanesection(lane_section)
    return lanes


def _make_multi_lane_section(
    motor_lane_width_m,
    lane_count=1,
    bike_lane_width_m=0,
    bike_facility_position="rightmost",
):
    """Create OSM-informed driving lanes, optional bike facility, and sidewalk."""
    center_mark = xodr.RoadMark(xodr.RoadMarkType.solid, 0.15)
    lane_mark = xodr.RoadMark(xodr.RoadMarkType.solid, 0.12)
    bike_mark = xodr.RoadMark(xodr.RoadMarkType.solid, 0.08)
    sidewalk_mark = xodr.RoadMark(xodr.RoadMarkType.solid, 0.08)

    center_lane = xodr.Lane(a=0)
    center_lane.add_roadmark(center_mark)

    lane_section = xodr.LaneSection(0, center_lane)
    has_right_bike_facility = (
        bike_lane_width_m > 0
        and bike_facility_position in {"right", "rightmost", "both"}
    )
    has_left_bike_facility = bike_lane_width_m > 0 and bike_facility_position == "left"

    if has_left_bike_facility:
        bike_lane = xodr.Lane(
            lane_type=xodr.LaneType.biking,
            a=float(bike_lane_width_m),
        )
        bike_lane.add_roadmark(bike_mark)
        lane_section.add_right_lane(bike_lane)

    for _ in range(max(1, int(lane_count))):
        driving_lane = xodr.Lane(
            lane_type=xodr.LaneType.driving,
            a=float(motor_lane_width_m),
        )
        driving_lane.add_roadmark(lane_mark)
        lane_section.add_right_lane(driving_lane)

    if has_right_bike_facility:
        bike_lane = xodr.Lane(
            lane_type=xodr.LaneType.biking,
            a=float(bike_lane_width_m),
        )
        bike_lane.add_roadmark(bike_mark)
        lane_section.add_right_lane(bike_lane)

    sidewalk_lane = xodr.Lane(
        lane_type=xodr.LaneType.sidewalk,
        a=1.5,
    )
    sidewalk_lane.add_roadmark(sidewalk_mark)
    lane_section.add_right_lane(sidewalk_lane)

    lanes = xodr.Lanes()
    lanes.add_lanesection(lane_section)
    return lanes


def _make_bidirectional_lane_section(
    motor_lane_width_m,
    forward_lane_count=1,
    opposite_lane_count=1,
):
    """Create a two-way street: negative lanes follow the road heading, positive lanes oppose it."""
    center_mark = xodr.RoadMark(xodr.RoadMarkType.solid, 0.15)
    lane_mark = xodr.RoadMark(xodr.RoadMarkType.solid, 0.12)
    sidewalk_mark = xodr.RoadMark(xodr.RoadMarkType.solid, 0.08)

    center_lane = xodr.Lane(a=0)
    center_lane.add_roadmark(center_mark)

    lane_section = xodr.LaneSection(0, center_lane)

    for _ in range(max(0, int(opposite_lane_count))):
        lane = xodr.Lane(lane_type=xodr.LaneType.driving, a=float(motor_lane_width_m))
        lane.add_roadmark(lane_mark)
        lane_section.add_left_lane(lane)

    for _ in range(max(1, int(forward_lane_count))):
        lane = xodr.Lane(lane_type=xodr.LaneType.driving, a=float(motor_lane_width_m))
        lane.add_roadmark(lane_mark)
        lane_section.add_right_lane(lane)

    right_sidewalk = xodr.Lane(lane_type=xodr.LaneType.sidewalk, a=1.5)
    right_sidewalk.add_roadmark(sidewalk_mark)
    lane_section.add_right_lane(right_sidewalk)

    left_sidewalk = xodr.Lane(lane_type=xodr.LaneType.sidewalk, a=1.5)
    left_sidewalk.add_roadmark(sidewalk_mark)
    lane_section.add_left_lane(left_sidewalk)

    lanes = xodr.Lanes()
    lanes.add_lanesection(lane_section)
    return lanes


def _make_line_road(road_id, name, x_start, y_start, heading, length, lanes):
    planview = xodr.PlanView(x_start, y_start, heading)
    planview.add_geometry(xodr.Line(length))
    return xodr.Road(road_id, planview, lanes, name=name)


def _centered_start(length, heading):
    return (
        -math.cos(heading) * length / 2,
        -math.sin(heading) * length / 2,
    )


def _parallel_start(length, heading, lateral_offset_m):
    start_x, start_y = _centered_start(length, heading)
    return (
        start_x - math.sin(heading) * lateral_offset_m,
        start_y + math.cos(heading) * lateral_offset_m,
    )


def _generate_crossing_opendrive(data, output_path):
    """Generate two crossing roads for a simple intersection abstraction."""
    params = _road_params(data)
    road_length_m = float(params.get("road_length_m", DEFAULT_ROAD_LENGTH_M))
    motor_lane_width_m = float(
        params.get("motor_lane_width_m", DEFAULT_MOTOR_LANE_WIDTH_M)
    )
    bike_lane_width_m = float(params.get("bike_lane_width_m", DEFAULT_BIKE_LANE_WIDTH_M))
    primary_bike_width_m = (
        bike_lane_width_m if params.get("primary_has_bike_facility") else 0
    )
    primary_bike_position = params.get(
        "primary_bike_facility_position",
        "rightmost",
    )
    primary_lanes = int(params.get("primary_road_lanes", 1))
    secondary_lanes = int(params.get("secondary_road_lanes", 1))
    secondary_forward_lanes = int(params.get("secondary_forward_lanes", secondary_lanes))
    secondary_opposite_lanes = int(params.get("secondary_opposite_lanes", 0))
    primary_heading = float(params.get("primary_heading_rad", -math.pi / 2))
    secondary_heading = float(params.get("secondary_heading_rad", math.pi))
    primary_start_x, primary_start_y = _centered_start(road_length_m, primary_heading)
    secondary_start_x, secondary_start_y = _centered_start(road_length_m, secondary_heading)

    primary_name = data.get("location", {}).get("primary_road") or "PrimaryRoad"
    secondary_name = data.get("location", {}).get("secondary_road") or "SecondaryRoad"
    road_layout = data.get("road_context", {}).get("road_layout")

    odr = xodr.OpenDrive(data["source"]["source_id"])

    # Road 0: primary/report cyclist approach.
    # The heading can come from OSM or from a manual map-review override.
    odr.add_road(
        _make_line_road(
            0,
            primary_name,
            primary_start_x,
            primary_start_y,
            primary_heading,
            road_length_m,
            _make_multi_lane_section(
                motor_lane_width_m,
                primary_lanes,
                primary_bike_width_m,
                primary_bike_position,
            ),
        )
    )

    # Road 1: secondary/report car approach.
    # For complex streets this can be rendered as a bidirectional 2+2 section.
    odr.add_road(
        _make_line_road(
            1,
            secondary_name,
            secondary_start_x,
            secondary_start_y,
            secondary_heading,
            road_length_m,
            (
                _make_bidirectional_lane_section(
                    motor_lane_width_m,
                    secondary_forward_lanes,
                    secondary_opposite_lanes,
                )
                if secondary_opposite_lanes > 0
                else _make_multi_lane_section(motor_lane_width_m, secondary_lanes)
            ),
        )
    )

    if road_layout == "divided_main_road_with_side_road":
        main_offset_m = float(params.get("main_carriageway_offset_m", 11.0))
        opposite_offset_m = float(params.get("opposite_carriageway_offset_m", 18.0))
        main_lanes = int(params.get("context_main_road_lanes", 2))

        main_start_x, main_start_y = _parallel_start(
            road_length_m,
            primary_heading,
            main_offset_m,
        )
        opposite_start_x, opposite_start_y = _parallel_start(
            road_length_m,
            primary_heading + math.pi,
            -opposite_offset_m,
        )
        odr.add_road(
            _make_line_road(
                2,
                "Unter den Eichen main carriageway context",
                main_start_x,
                main_start_y,
                primary_heading,
                road_length_m,
                _make_multi_lane_section(motor_lane_width_m, main_lanes),
            )
        )
        odr.add_road(
            _make_line_road(
                3,
                "Unter den Eichen opposite carriageway context",
                opposite_start_x,
                opposite_start_y,
                primary_heading + math.pi,
                road_length_m,
                _make_multi_lane_section(motor_lane_width_m, main_lanes),
            )
        )

    odr.adjust_roads_and_lanes()
    odr.write_xml(str(output_path))


def generate_opendrive(data, output_path):
    """Generate a straight OpenDRIVE road for the Malteserstraße prototype."""
    output_path = Path(output_path)
    scenario_type = data.get("classification", {}).get("scenario_type")
    if scenario_type == "straight_crossing_conflict":
        _generate_crossing_opendrive(data, output_path)
        return

    params = _road_params(data)

    road_length_m = float(params.get("road_length_m", DEFAULT_ROAD_LENGTH_M))
    motor_lane_width_m = float(
        params.get("motor_lane_width_m", DEFAULT_MOTOR_LANE_WIDTH_M)
    )
    bike_lane_width_m = float(
        params.get("bike_lane_width_m", DEFAULT_BIKE_LANE_WIDTH_M)
    )
    parking_access_s_m = float(
        params.get("parking_access_s_m", DEFAULT_PARKING_ACCESS_S_M)
    )
    motor_lane_count = int(params.get("motor_lane_count", 1))
    bike_facility_position = params.get("primary_bike_facility_position", "rightmost")

    road_name = data.get("location", {}).get("primary_road") or "PrimaryRoad"
    odr = xodr.OpenDrive(data["source"]["source_id"])

    # road_length_m and road_geometry=line map to a single OpenDRIVE Line geometry.
    planview = xodr.PlanView(0, 0, 0)
    planview.add_geometry(xodr.Line(road_length_m))

    road = xodr.Road(
        0,
        planview,
        _make_multi_lane_section(
            motor_lane_width_m,
            motor_lane_count,
            bike_lane_width_m,
            bike_facility_position,
        ),
        name=road_name,
    )

    # parking_access_s_m marks where the truck crosses the bike lane. For this
    # first esmini test we keep the parking access out of OpenDRIVE objects and
    # express it through the OpenSCENARIO truck trajectory instead.
    _ = parking_access_s_m

    odr.add_road(road)
    odr.adjust_roads_and_lanes()
    odr.write_xml(str(output_path))
