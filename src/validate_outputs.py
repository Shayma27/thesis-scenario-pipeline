"""Lightweight validation for generated OpenDRIVE/OpenSCENARIO pairs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET


@dataclass
class ValidationResult:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self):
        return not self.errors

    def format(self):
        lines = []
        if self.errors:
            lines.append("Validation errors:")
            lines.extend(f"  - {error}" for error in self.errors)
        if self.warnings:
            lines.append("Validation warnings:")
            lines.extend(f"  - {warning}" for warning in self.warnings)
        if not lines:
            lines.append("Validation passed.")
        return "\n".join(lines)


def validate_generated_files(data, xodr_path, xosc_path):
    """Check the generated files for common pipeline/template mistakes."""
    xodr_path = Path(xodr_path)
    xosc_path = Path(xosc_path)
    result = ValidationResult(errors=[], warnings=[])

    xodr_root = _parse_xml(xodr_path, result)
    xosc_root = _parse_xml(xosc_path, result)
    if xodr_root is None or xosc_root is None:
        return result

    _validate_road_reference(xosc_path, xosc_root, result)
    roads = _collect_roads(xodr_root, result)
    _validate_entities(data, xosc_root, result)
    _validate_lane_positions(data, xosc_root, roads, result)
    _validate_trajectories(xosc_root, result)
    _validate_stop_time(xosc_root, result)
    return result


def _parse_xml(path, result):
    if not path.exists():
        result.errors.append(f"Missing file: {path}")
        return None
    try:
        return ET.parse(path).getroot()
    except ET.ParseError as exc:
        result.errors.append(f"Invalid XML in {path}: {exc}")
        return None


def _validate_road_reference(xosc_path, xosc_root, result):
    logic_file = xosc_root.find(".//LogicFile")
    if logic_file is None:
        result.errors.append("OpenSCENARIO has no RoadNetwork/LogicFile reference.")
        return

    filepath = logic_file.get("filepath")
    if not filepath:
        result.errors.append("OpenSCENARIO LogicFile has no filepath.")
        return

    referenced_path = xosc_path.parent / filepath
    if not referenced_path.exists():
        result.errors.append(
            f"OpenSCENARIO references missing OpenDRIVE file: {referenced_path}"
        )


def _collect_roads(xodr_root, result):
    roads = {}
    for road in xodr_root.findall(".//road"):
        road_id = road.get("id")
        if road_id is None:
            result.errors.append("OpenDRIVE road without id.")
            continue

        try:
            length = float(road.get("length", "0"))
        except ValueError:
            result.errors.append(f"OpenDRIVE road {road_id} has invalid length.")
            length = 0.0

        lanes = set()
        for lane in road.findall(".//lane"):
            lane_id = lane.get("id")
            if lane_id is not None:
                lanes.add(lane_id)
        roads[road_id] = {"length": length, "lanes": lanes}

    if not roads:
        result.errors.append("OpenDRIVE contains no roads.")
    return roads


def _validate_entities(data, xosc_root, result):
    scenario_objects = {
        node.get("name") for node in xosc_root.findall(".//ScenarioObject")
    }
    expected = set(
        data.get("generated_simulation_parameters", {})
        .get("openscenario", {})
        .get("actors", {})
        .keys()
    )

    missing = expected - scenario_objects
    extra = scenario_objects - expected
    for actor_id in sorted(missing):
        result.errors.append(f"Actor {actor_id} is in JSON but missing in Entities.")
    for actor_id in sorted(extra):
        result.warnings.append(f"Actor {actor_id} is in Entities but not JSON actors.")

    referenced = set()
    for private in xosc_root.findall(".//Private"):
        if private.get("entityRef"):
            referenced.add(private.get("entityRef"))
    for entity_ref in xosc_root.findall(".//EntityRef"):
        if entity_ref.get("entityRef"):
            referenced.add(entity_ref.get("entityRef"))

    for actor_id in sorted(referenced - scenario_objects):
        result.errors.append(f"Storyboard references unknown actor {actor_id}.")


def _validate_lane_positions(data, xosc_root, roads, result):
    json_actors = (
        data.get("generated_simulation_parameters", {})
        .get("openscenario", {})
        .get("actors", {})
    )

    for private in xosc_root.findall(".//Init/Actions/Private"):
        actor_id = private.get("entityRef")
        lane_position = private.find(".//LanePosition")
        if lane_position is None:
            result.errors.append(f"Actor {actor_id} has no initial LanePosition.")
            continue

        road_id = lane_position.get("roadId")
        lane_id = lane_position.get("laneId")
        if road_id not in roads:
            result.errors.append(f"Actor {actor_id} starts on unknown road {road_id}.")
            continue
        if lane_id not in roads[road_id]["lanes"]:
            result.errors.append(
                f"Actor {actor_id} starts on missing lane {lane_id} of road {road_id}."
            )

        try:
            s_value = float(lane_position.get("s", "nan"))
        except ValueError:
            result.errors.append(f"Actor {actor_id} has invalid initial s value.")
            continue
        if not 0 <= s_value <= roads[road_id]["length"]:
            result.errors.append(
                f"Actor {actor_id} initial s={s_value} is outside road {road_id} "
                f"length {roads[road_id]['length']}."
            )

        json_actor = json_actors.get(actor_id, {})
        if str(json_actor.get("initial_road_id")) != road_id:
            result.warnings.append(
                f"Actor {actor_id} roadId differs from JSON initial_road_id."
            )
        if str(json_actor.get("initial_lane_id")) != lane_id:
            result.warnings.append(
                f"Actor {actor_id} laneId differs from JSON initial_lane_id."
            )


def _validate_trajectories(xosc_root, result):
    trajectories = xosc_root.findall(".//Trajectory")
    if not trajectories:
        result.errors.append("OpenSCENARIO contains no trajectories.")
        return

    for trajectory in trajectories:
        name = trajectory.get("name", "<unnamed>")
        vertices = trajectory.findall(".//Vertex")
        if len(vertices) < 2:
            result.errors.append(f"Trajectory {name} has fewer than two vertices.")
            continue

        times = []
        for vertex in vertices:
            try:
                times.append(float(vertex.get("time", "nan")))
            except ValueError:
                result.errors.append(f"Trajectory {name} has invalid vertex time.")
                continue
            if vertex.find(".//WorldPosition") is None:
                result.errors.append(f"Trajectory {name} has a vertex without position.")

        if times and times[0] != 0:
            result.warnings.append(f"Trajectory {name} does not start at time 0.")
        for earlier, later in zip(times, times[1:]):
            if later < earlier:
                result.errors.append(f"Trajectory {name} has decreasing vertex times.")


def _validate_stop_time(xosc_root, result):
    stop_conditions = [
        node
        for node in xosc_root.findall(".//SimulationTimeCondition")
        if node.get("rule") == "greaterThan"
    ]
    if not stop_conditions:
        result.warnings.append("No SimulationTimeCondition stop/check found.")
        return

    try:
        max_stop_value = max(float(node.get("value", "0")) for node in stop_conditions)
    except ValueError:
        result.errors.append("Invalid SimulationTimeCondition value.")
        return

    trajectory_times = []
    for vertex in xosc_root.findall(".//Trajectory//Vertex"):
        try:
            trajectory_times.append(float(vertex.get("time", "0")))
        except ValueError:
            continue

    if trajectory_times and max(trajectory_times) > max_stop_value:
        result.warnings.append(
            "A trajectory extends beyond the largest SimulationTimeCondition value."
        )
