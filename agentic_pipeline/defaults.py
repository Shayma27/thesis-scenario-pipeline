"""Default values used when police reports do not provide simulation details."""

DEFAULT_SPEEDS_MPS = {
    "cyclist": {
        "normal": 4.5,
        "fast": 6.5,
        "slow": 3.0,
    },
    "e_bike": {
        "normal": 5.5,
        "fast": 7.0,
    },
    "car": {
        "turning": 4.0,
        "urban_straight": 8.0,
        # A car needs to be faster than the cyclist it's overtaking to
        # actually complete the pass — distinct from "excessive", which
        # represents unsafe/reckless speed, not merely "fast enough to overtake".
        "overtaking": 10.0,
        "excessive": 13.0,
    },
    "truck": {
        "turning": 3.0,
        "urban_straight": 7.0,
    },
    "bus": {
        "overtaking": 7.0,
    },
}

DEFAULT_ROAD_LENGTH_M = 100.0
DEFAULT_MOTOR_LANE_WIDTH_M = 3.5
DEFAULT_BIKE_LANE_WIDTH_M = 2.0
DEFAULT_PARKING_ACCESS_S_M = 50.0
DEFAULT_SIMULATION_DURATION_S = 10.0

# Lane-placement policy for cases where the report does not specify the exact
# cycling facility position. Exceptions can come from explicit report text or
# OSM tags such as cycleway:left / cycleway:right / cycleway:both.
DEFAULT_CYCLIST_LATERAL_POSITION = "rightmost"
