"""Optional OpenStreetMap enrichment for traceable scenario JSON files.

The enrichment is intentionally conservative:
- it adds an osm_context block with raw-ish OSM evidence,
- it only updates generated/default simulation parameters,
- it records each update in missing_parameters.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from defaults import DEFAULT_BIKE_LANE_WIDTH_M, DEFAULT_CYCLIST_LATERAL_POSITION


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "shayma-kfz-fahrrad-scenario-pipeline/0.1"
DEFAULT_RADIUS_M = 140
BIKE_FACILITY_TYPES = {
    "separated_cycle_track",
    "protected_bike_lane",
    "bike_lane",
    "shared_lane",
}


def enrich_with_osm(data, cache_dir):
    """Return a copy of data enriched with nearby OSM road tags."""
    enriched = copy.deepcopy(data)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    query_candidates = _build_location_queries(enriched)
    query = query_candidates[0] if query_candidates else None
    context = {
        "query": query,
        "queries_tried": [],
        "source": "OpenStreetMap via Nominatim and Overpass",
        "enrichment_status": "not_started",
        "radius_m": DEFAULT_RADIUS_M,
        "matched_roads": [],
        "traffic_signals_nearby": "unknown",
        "notes": [],
    }

    if not query:
        context["enrichment_status"] = "skipped"
        context["notes"].append("No usable geocoding query could be built from JSON.")
        enriched["osm_context"] = context
        return enriched

    try:
        geocoded = None
        for candidate in query_candidates:
            context["queries_tried"].append(candidate)
            geocoded = _nominatim_search(candidate, cache_dir)
            if geocoded:
                context["query"] = candidate
                break
        if not geocoded:
            context["enrichment_status"] = "geocoding_failed"
            enriched["osm_context"] = context
            return enriched

        lat = float(geocoded["lat"])
        lon = float(geocoded["lon"])
        context["geocoded"] = {
            "display_name": geocoded.get("display_name"),
            "lat": lat,
            "lon": lon,
            "osm_type": geocoded.get("osm_type"),
            "osm_id": geocoded.get("osm_id"),
        }

        overpass = _overpass_nearby_roads(lat, lon, DEFAULT_RADIUS_M, cache_dir)
        roads, traffic_signals = _extract_road_context(overpass)
        matches = _select_relevant_roads(enriched, roads)
        context["matched_roads"] = matches
        context["traffic_signals_nearby"] = "yes" if traffic_signals else "no"
        context["enrichment_status"] = "ok"

        _apply_osm_context(enriched, context)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        context["enrichment_status"] = "error"
        context["notes"].append(f"OSM enrichment failed: {exc}")

    enriched["osm_context"] = context
    return enriched


def _build_location_queries(data):
    location = data.get("location", {})
    queries = []
    if location.get("osm_query"):
        queries.append(location["osm_query"])
    queries.extend(location.get("osm_query_candidates", []))

    city = location.get("city") or "Berlin"
    house_number = location.get("house_number_reference")
    primary = location.get("primary_road")
    secondary = location.get("secondary_road")

    if house_number and primary:
        queries.append(f"{primary} {house_number}, {city}, Germany")

    road_names = _road_names_from_location(location)
    if len(road_names) >= 2:
        queries.append(f"{road_names[0]} {road_names[1]}, {city}, Germany")
    if road_names:
        queries.extend(f"{name}, {city}, Germany" for name in road_names)

    for direction in location.get("direction_references", []):
        match = re.search(r"from\s+(.+?)\s+toward", direction, flags=re.IGNORECASE)
        if match:
            queries.append(f"{match.group(1)}, {city}, Germany")

    unique_queries = []
    for query in queries:
        if query and query not in unique_queries:
            unique_queries.append(query)
    return unique_queries


def _nominatim_search(query, cache_dir):
    params = urlencode({"format": "jsonv2", "limit": 1, "q": query})
    url = f"{NOMINATIM_URL}?{params}"
    payload = _cached_json(url, cache_dir / "nominatim", min_delay_s=1.0)
    return payload[0] if payload else None


def _overpass_nearby_roads(lat, lon, radius_m, cache_dir):
    query = f"""
[out:json][timeout:25];
(
  way(around:{radius_m},{lat},{lon})["highway"];
  node(around:{radius_m},{lat},{lon})["highway"="traffic_signals"];
);
out tags center geom;
"""
    cache_key = _cache_key(query)
    cache_path = cache_dir / "overpass" / f"{cache_key}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    time.sleep(1.0)
    body = urlencode({"data": query}).encode("utf-8")
    request = Request(
        OVERPASS_URL,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    cache_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _cached_json(url, cache_dir, min_delay_s=0):
    cache_key = _cache_key(url)
    cache_path = cache_dir / f"{cache_key}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    cache_dir.mkdir(parents=True, exist_ok=True)
    if min_delay_s:
        time.sleep(min_delay_s)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    cache_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _extract_road_context(overpass_payload):
    roads = []
    traffic_signals = []
    for element in overpass_payload.get("elements", []):
        tags = element.get("tags", {})
        if element.get("type") == "node" and tags.get("highway") == "traffic_signals":
            traffic_signals.append(
                {
                    "osm_id": element.get("id"),
                    "lat": element.get("lat"),
                    "lon": element.get("lon"),
                }
            )
            continue

        if element.get("type") != "way" or "highway" not in tags:
            continue

        roads.append(
            {
                "osm_id": element.get("id"),
                "name": tags.get("name"),
                "highway": tags.get("highway"),
                "maxspeed": tags.get("maxspeed"),
                "lanes": tags.get("lanes"),
                "lanes_forward": tags.get("lanes:forward"),
                "lanes_backward": tags.get("lanes:backward"),
                "turn_lanes": _first_present(
                    tags,
                    "turn:lanes",
                    "turn:lanes:forward",
                    "turn:lanes:backward",
                ),
                "oneway": tags.get("oneway"),
                "width": tags.get("width"),
                "lane_markings": tags.get("lane_markings"),
                "bicycle": tags.get("bicycle"),
                "segregated": tags.get("segregated"),
                "cycleway": _first_present(
                    tags,
                    "cycleway",
                    "cycleway:left",
                    "cycleway:right",
                    "cycleway:both",
                ),
                "cycleway_tags": _cycleway_tags(tags),
                "sidewalk": _first_present(tags, "sidewalk", "sidewalk:left", "sidewalk:right"),
                "center": element.get("center"),
                "geometry": element.get("geometry", []),
            }
        )
    return roads, traffic_signals


def _select_relevant_roads(data, roads):
    wanted_names = _road_names_from_location(data.get("location", {}))
    selected = []
    for road in roads:
        if not wanted_names or _name_matches(road.get("name"), wanted_names):
            selected.append(road)

    if not selected:
        selected = roads[:8]

    selected.sort(key=lambda road: 0 if _name_matches(road.get("name"), wanted_names) else 1)
    return selected[:30]


def _apply_osm_context(data, context):
    road_context = data.setdefault("road_context", {})
    if road_context.get("traffic_light_present") in {None, "unknown"}:
        road_context["traffic_light_present"] = context["traffic_signals_nearby"]

    _apply_bike_facility_context(data, context)
    _apply_lane_context(data, context)
    _apply_lane_guided_maneuver_context(data, context)
    _apply_cyclist_position_policy(data, context)

    maxspeed_kmh = _first_maxspeed_kmh(context["matched_roads"])
    if maxspeed_kmh is None:
        context["notes"].append("No parseable maxspeed tag found near the geocoded location.")
        return

    context["derived"] = {
        "maxspeed_kmh": maxspeed_kmh,
        "urban_intersection_approach_factor": 0.65,
    }

    scenario_type = data.get("classification", {}).get("scenario_type")
    actors = data.setdefault("generated_simulation_parameters", {}).setdefault(
        "openscenario", {}
    ).setdefault("actors", {})

    if scenario_type == "straight_crossing_conflict" and "car_1" in actors:
        speed_mps = round(maxspeed_kmh * 0.65 / 3.6, 2)
        actors["car_1"]["initial_speed_mps"] = speed_mps
        _upsert_missing_parameter(
            data,
            parameter="car_1.initial_speed_mps",
            value_used=speed_mps,
            source="osm_derived_assumption",
            reason=(
                f"OSM maxspeed={maxspeed_kmh} km/h was found near the location; "
                "the simulation uses 65% of the limit as an intersection approach speed."
            ),
        )


def _apply_lane_context(data, context):
    params = data.setdefault("generated_simulation_parameters", {}).setdefault(
        "opendrive", {}
    )
    scenario_type = data.get("classification", {}).get("scenario_type")
    roads = context.get("matched_roads", [])

    if scenario_type == "straight_crossing_conflict":
        location = data.get("location", {})
        approaches = _approach_descriptors_from_location(location)
        primary_evidence = _approach_lane_count_evidence(
            roads,
            approaches["primary"],
        )
        secondary_evidence = _approach_lane_count_evidence(
            roads,
            approaches["secondary"],
        )
        context["lane_count_evidence"] = {
            "policy": (
                "For police-report approaches, use OSM lanes only from way "
                "segments whose name matches the 'from' road and whose geometry "
                "moves toward the 'toward' road. Nearby road segments are kept "
                "as evidence but are not averaged."
            ),
            "primary": primary_evidence,
            "secondary": secondary_evidence,
        }

        primary_lanes = primary_evidence.get("used_count")
        secondary_lanes = secondary_evidence.get("used_count")
        primary_heading = primary_evidence.get("used_heading_rad")
        secondary_heading = secondary_evidence.get("used_heading_rad")
        if primary_lanes:
            params["primary_road_lanes"] = primary_lanes
        else:
            params["primary_road_lanes"] = 1
        if secondary_lanes:
            params["secondary_road_lanes"] = secondary_lanes
        else:
            params["secondary_road_lanes"] = 1
        if primary_heading is not None:
            params["primary_heading_rad"] = primary_heading
        if secondary_heading is not None:
            params["secondary_heading_rad"] = secondary_heading

        _apply_lane_count_overrides(data, params, context)
        _apply_heading_overrides(data, params, context)

        _upsert_missing_parameter(
            data,
            parameter="intersection_lane_counts",
            value_used=(
                f"primary={params['primary_road_lanes']}, "
                f"secondary={params['secondary_road_lanes']}"
            ),
            source=_lane_count_source(primary_evidence, secondary_evidence, context),
            reason=_lane_count_reason(primary_evidence, secondary_evidence, context),
        )
    else:
        lane_count = _best_lane_count(roads, data.get("location", {}).get("primary_road"))
        if lane_count:
            params["motor_lane_count"] = lane_count
            _apply_turning_vehicle_lane_id(data, lane_count)
            _upsert_missing_parameter(
                data,
                parameter="motor_lane_count",
                value_used=lane_count,
                source="osm_tag",
                reason="OpenDRIVE motor-lane count was derived from nearby OSM lanes tags.",
            )


def _apply_bike_facility_context(data, context):
    road_context = data.setdefault("road_context", {})
    opendrive_params = data.setdefault("generated_simulation_parameters", {}).setdefault(
        "opendrive", {}
    )
    osm_facility = _infer_bike_facility(context.get("matched_roads", []))
    report_facility = road_context.get("bike_facility_type")

    if report_facility and report_facility != "unknown":
        facility_type = report_facility
        source = "explicit_from_report"
        reason = "The report explicitly describes the cycling infrastructure."
    elif osm_facility:
        facility_type = osm_facility["bike_facility_type"]
        source = "osm_tag"
        reason = f"OSM cycleway tags indicate {facility_type}."
        road_context["bike_facility_type"] = facility_type
    else:
        facility_type = road_context.get("bike_facility_type", "unknown")
        source = "explicit_missing"
        reason = "Neither the report nor OSM identify a cycling facility."

    if facility_type in BIKE_FACILITY_TYPES:
        position = (
            osm_facility.get("position")
            if osm_facility
            else road_context.get("bike_facility_position")
            or DEFAULT_CYCLIST_LATERAL_POSITION
        )
        opendrive_params["primary_has_bike_facility"] = True
        opendrive_params["primary_bike_facility_type"] = facility_type
        opendrive_params["primary_bike_facility_position"] = position
        if float(opendrive_params.get("bike_lane_width_m", 0) or 0) <= 0:
            opendrive_params["bike_lane_width_m"] = DEFAULT_BIKE_LANE_WIDTH_M
        context["bike_facility"] = {
            "type": facility_type,
            "position": position,
            "source": source,
            "osm_evidence": osm_facility,
        }
        _upsert_missing_parameter(
            data,
            parameter="bike_facility_type",
            value_used=facility_type,
            source=source,
            reason=reason,
        )
    else:
        opendrive_params["primary_has_bike_facility"] = False


def _apply_cyclist_position_policy(data, context):
    params = data.setdefault("generated_simulation_parameters", {}).setdefault(
        "openscenario", {}
    )
    opendrive_params = data.setdefault("generated_simulation_parameters", {}).setdefault(
        "opendrive", {}
    )
    road_context = data.setdefault("road_context", {})
    report_position = _participant_road_position(data, "cyclist_1")
    report_facility = road_context.get("bike_facility_position")
    bike_facility = context.get("bike_facility") or {}
    osm_position = bike_facility.get("position") or _infer_cycleway_position(
        context.get("matched_roads", [])
    )

    if report_position in {
        "leftmost_motor_lane",
        "middle_motor_lane",
        "rightmost_motor_lane",
        "right_motor_lane",
    }:
        position = (
            "rightmost_motor_lane"
            if report_position == "right_motor_lane"
            else report_position
        )
        source = "explicit_from_report"
        reason = "The report explicitly places the cyclist on a motor-vehicle lane."
    elif report_facility in {"left", "middle", "right", "rightmost"}:
        position = report_facility
        source = "explicit_from_report"
        reason = "The report specifies the cycling facility position."
    elif osm_position:
        position = osm_position
        source = "osm_tag"
        reason = "OSM cycleway side tags indicate the cycling facility position."
    else:
        position = DEFAULT_CYCLIST_LATERAL_POSITION
        source = "default_assumption"
        reason = (
            "The report and OSM do not specify an exceptional cycling facility "
            "position, so the cyclist is placed on the rightmost usable side."
        )

    params["cyclist_lateral_position"] = position
    _apply_cyclist_lane_id(data, position, opendrive_params)
    context["cyclist_lateral_position"] = {
        "value": position,
        "source": source,
        "policy": "default rightmost unless report or OSM indicate left/middle.",
    }
    _upsert_missing_parameter(
        data,
        parameter="cyclist_lateral_position",
        value_used=position,
        source=source,
        reason=reason,
    )


def _apply_cyclist_lane_id(data, position, opendrive_params):
    actors = data.setdefault("generated_simulation_parameters", {}).setdefault(
        "openscenario", {}
    ).setdefault("actors", {})
    cyclist = actors.get("cyclist_1")
    if not cyclist:
        return

    lane_count = int(
        opendrive_params.get(
            "primary_road_lanes",
            opendrive_params.get("motor_lane_count", 1),
        )
    )
    has_bike_facility = bool(opendrive_params.get("primary_has_bike_facility"))
    if position == "rightmost_motor_lane":
        lane_id = -max(1, lane_count)
    elif position == "leftmost_motor_lane":
        lane_id = -1
    elif position == "middle_motor_lane":
        lane_id = -max(1, (lane_count + 1) // 2)
    elif has_bike_facility and position in {"right", "rightmost", "both"}:
        lane_id = -(lane_count + 1)
    elif has_bike_facility and position == "left":
        lane_id = -1
    elif position in {"right", "rightmost", "both"}:
        lane_id = -max(1, lane_count)
    elif position == "middle":
        lane_id = -max(1, (lane_count + 1) // 2)
    else:
        lane_id = -1

    cyclist["initial_lane_id"] = lane_id
    _upsert_missing_parameter(
        data,
        parameter="cyclist_1.initial_lane_id",
        value_used=lane_id,
        source="derived_from_cyclist_lateral_position",
        reason=(
            "Cyclist OpenSCENARIO lane id follows the chosen lateral-position "
            "policy so the initial teleport matches the generated trajectory."
        ),
    )


def _apply_turning_vehicle_lane_id(data, lane_count):
    if data.get("classification", {}).get("scenario_type") != "parking_access_conflict":
        return

    actors = data.setdefault("generated_simulation_parameters", {}).setdefault(
        "openscenario", {}
    ).setdefault("actors", {})
    truck = actors.get("truck_1")
    if not truck:
        return

    lane_id = -max(1, int(lane_count))
    truck["initial_lane_id"] = lane_id
    _upsert_missing_parameter(
        data,
        parameter="truck_1.initial_lane_id",
        value_used=lane_id,
        source="derived_from_osm_motor_lane_count",
        reason=(
            "For a right-turn/parking-access conflict, the truck starts in the "
            "rightmost motor-vehicle lane of the generated OpenDRIVE road."
        ),
    )


def _participant_road_position(data, participant_id):
    for participant in data.get("participants", []):
        if participant.get("id") == participant_id:
            return participant.get("road_position")
    return None


def _participant_maneuver(data, participant_id):
    for participant in data.get("participants", []):
        if participant.get("id") == participant_id:
            return participant.get("maneuver")
    return None


def _infer_cycleway_position(roads):
    for road in roads:
        cycleway_tags = road.get("cycleway_tags", {})
        for key, value in cycleway_tags.items():
            text = str(value).casefold()
            if "left" in key or "left" in text or "opposite" in text:
                return "left"
            if "right" in key or "right" in text:
                return "right"
            if "both" in key or "both" in text:
                return "both"
            if text in {"lane", "track", "shared_lane", "share_busway", "separate"}:
                return "rightmost"
    return None


def _infer_bike_facility(roads):
    for road in roads:
        cycleway_tags = road.get("cycleway_tags", {})
        if road.get("highway") == "cycleway":
            return {
                "bike_facility_type": "separated_cycle_track",
                "position": "rightmost",
                "tags": {"highway": "cycleway"},
            }

        for key, value in cycleway_tags.items():
            text = str(value).casefold()
            if text in {"no", "none"}:
                continue

            position = "rightmost"
            if "left" in key:
                position = "left"
            elif "right" in key:
                position = "right"
            elif "both" in key:
                position = "both"

            if text in {"track", "opposite_track", "separate"}:
                return {
                    "bike_facility_type": "separated_cycle_track",
                    "position": position,
                    "tags": {key: value},
                }
            if text in {"lane", "opposite_lane"}:
                return {
                    "bike_facility_type": "bike_lane",
                    "position": position,
                    "tags": {key: value},
                }
            if text in {"shared_lane", "share_busway"}:
                return {
                    "bike_facility_type": "shared_lane",
                    "position": position,
                    "tags": {key: value},
                }
    return None


def _cycleway_tags(tags):
    return {
        key: value
        for key, value in tags.items()
        if key == "cycleway" or key.startswith("cycleway:")
    }


def _best_lane_count(roads, preferred_name=None):
    evidence = _lane_count_evidence(
        roads,
        _names_from_location_field(preferred_name),
    )
    return evidence.get("used_count")


def _approach_lane_count_evidence(roads, approach):
    from_names = _names_from_location_field(approach.get("from"))
    toward_names = _names_from_location_field(approach.get("toward"))
    candidates = [
        road for road in roads if from_names and _name_matches(road.get("name"), from_names)
    ]
    target_roads = [
        road for road in roads if toward_names and _name_matches(road.get("name"), toward_names)
    ]
    target_points = [
        point
        for road in target_roads
        for point in _representative_points(road)
    ]

    considered_segments = []
    directional_matches = []
    for road in candidates:
        progress_m, end_distance_m = _progress_toward_target(road, target_points)
        moves_toward_target = progress_m is None or progress_m > 1.0
        lane_count = _approach_lane_count_from_road(road, progress_m)
        heading_rad = _road_heading_rad(road, progress_m)
        segment = {
            "osm_id": road.get("osm_id"),
            "name": road.get("name"),
            "lanes_tag": road.get("lanes"),
            "turn_lanes": road.get("turn_lanes"),
            "oneway": road.get("oneway"),
            "width": road.get("width"),
            "parsed_count": lane_count,
            "heading_rad": round(heading_rad, 6) if heading_rad is not None else None,
            "heading_deg": round(math.degrees(heading_rad), 1) if heading_rad is not None else None,
            "progress_toward_target_m": (
                round(progress_m, 1) if progress_m is not None else None
            ),
            "end_distance_to_target_m": (
                round(end_distance_m, 1) if end_distance_m is not None else None
            ),
            "moves_toward_target": moves_toward_target,
        }
        considered_segments.append(segment)
        if lane_count and moves_toward_target:
            directional_matches.append(segment)

    if directional_matches:
        directional_matches.sort(
            key=lambda segment: (
                segment["end_distance_to_target_m"]
                if segment["end_distance_to_target_m"] is not None
                else 999999,
                -(
                    segment["progress_toward_target_m"]
                    if segment["progress_toward_target_m"] is not None
                    else 0
                ),
            )
        )
        best_distance = directional_matches[0]["end_distance_to_target_m"]
        selected_segments = [
            segment
            for segment in directional_matches
            if best_distance is None
            or segment["end_distance_to_target_m"] is None
            or abs(segment["end_distance_to_target_m"] - best_distance) <= 20
        ]
        selected_counts = sorted({segment["parsed_count"] for segment in selected_segments})
        if len(selected_counts) == 1:
            status = "used_directional_osm_way_match"
            used_count = selected_counts[0]
            used_heading_rad = selected_segments[0].get("heading_rad")
        else:
            status = "not_used_ambiguous_directional_osm_way_matches"
            used_count = None
            used_heading_rad = None
    else:
        selected_segments = []
        used_count = None
        used_heading_rad = None
        if candidates:
            status = "not_used_no_directional_osm_way_match"
        else:
            status = "not_used_no_osm_way_name_match"

    return {
        "approach": {
            "from": approach.get("from"),
            "toward": approach.get("toward"),
        },
        "from_names": from_names,
        "toward_names": toward_names,
        "candidate_count": len(candidates),
        "target_count": len(target_roads),
        "considered_segments": considered_segments,
        "selected_segments": selected_segments,
        "used_count": used_count,
        "used_heading_rad": used_heading_rad,
        "used_heading_deg": round(math.degrees(used_heading_rad), 1)
        if used_heading_rad is not None
        else None,
        "status": status,
    }


def _lane_count_evidence(roads, preferred_names=None):
    preferred_names = [name for name in (preferred_names or []) if name]
    matched_roads = [
        road
        for road in roads
        if not preferred_names or _name_matches(road.get("name"), preferred_names)
    ]
    candidates = matched_roads or roads
    tagged_segments = []

    for road in candidates:
        lane_count = _parse_lane_count(road.get("lanes"))
        if lane_count is None:
            continue
        tagged_segments.append(
            {
                "osm_id": road.get("osm_id"),
                "name": road.get("name"),
                "lanes_tag": road.get("lanes"),
                "parsed_count": lane_count,
            }
        )

    unique_counts = sorted({item["parsed_count"] for item in tagged_segments})
    if len(unique_counts) == 1:
        status = "used_consistent_osm_tag"
        used_count = unique_counts[0]
    elif unique_counts:
        status = "not_used_inconsistent_osm_tags"
        used_count = None
    else:
        status = "not_used_no_osm_lanes_tag"
        used_count = None

    return {
        "preferred_names": preferred_names,
        "matched_road_count": len(candidates),
        "tagged_segments": tagged_segments,
        "unique_counts": unique_counts,
        "used_count": used_count,
        "status": status,
    }


def _lane_count_source(primary_evidence, secondary_evidence, context=None):
    if context and context.get("lane_count_overrides_applied"):
        return "manual_map_review_override"

    primary_used = primary_evidence.get("used_count") is not None
    secondary_used = secondary_evidence.get("used_count") is not None
    if primary_used and secondary_used:
        return "osm_directional_way_match"
    if primary_used or secondary_used:
        return "mixed_osm_directional_and_default_due_to_osm_uncertainty"
    return "default_assumption_due_to_osm_uncertainty"


def _lane_count_reason(primary_evidence, secondary_evidence, context=None):
    if context and context.get("lane_count_overrides_applied"):
        return (
            "OSM lane tags were retained as evidence, but at least one approach "
            "lane count was manually overridden because the OSM intersection "
            "segment did not cleanly represent the report-relevant directional "
            "lane group."
        )

    if (
        primary_evidence.get("used_count") is not None
        and secondary_evidence.get("used_count") is not None
    ):
        return (
            "OpenDRIVE lane counts were derived from OSM way segments that "
            "match each report approach direction."
        )
    return (
        "At least one report approach could not be matched to an unambiguous "
        "directed OSM way segment, so uncertain approaches keep the conservative "
        "simulation abstraction."
    )


def _apply_lane_count_overrides(data, opendrive_params, context):
    overrides = data.get("road_context", {}).get("lane_count_overrides", {})
    if not isinstance(overrides, dict):
        return

    applied = {}
    for key in (
        "primary_road_lanes",
        "primary_forward_lanes",
        "primary_opposite_lanes",
        "secondary_road_lanes",
        "secondary_forward_lanes",
        "secondary_opposite_lanes",
    ):
        override = overrides.get(key)
        if not override:
            continue
        if isinstance(override, dict):
            value = override.get("value")
            source = override.get("source", "manual_override")
            reason = override.get("reason", "Manual lane-count override.")
        else:
            value = override
            source = "manual_override"
            reason = "Manual lane-count override."

        parsed = _parse_lane_count(value)
        if parsed is None:
            continue

        opendrive_params[key] = parsed
        applied[key] = {
            "value": parsed,
            "source": source,
            "reason": reason,
            "overrode": "osm_directional_way_match",
        }

    if applied:
        context["lane_count_overrides_applied"] = applied


def _apply_heading_overrides(data, opendrive_params, context):
    overrides = data.get("road_context", {}).get("heading_overrides", {})
    if not isinstance(overrides, dict):
        return

    applied = {}
    for key in ("primary_heading_rad", "secondary_heading_rad"):
        override = overrides.get(key)
        if not override:
            continue
        if isinstance(override, dict):
            value = override.get("value")
            source = override.get("source", "manual_override")
            reason = override.get("reason", "Manual heading override.")
        else:
            value = override
            source = "manual_override"
            reason = "Manual heading override."

        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue

        opendrive_params[key] = parsed
        applied[key] = {
            "value": parsed,
            "source": source,
            "reason": reason,
            "overrode": "osm_directional_way_heading",
        }

    if applied:
        context["heading_overrides_applied"] = applied


def _apply_lane_guided_maneuver_context(data, context):
    if data.get("classification", {}).get("scenario_type") != "straight_crossing_conflict":
        return

    car_position = _participant_road_position(data, "car_1")
    car_maneuver = str(_participant_maneuver(data, "car_1") or "").casefold()
    allow_inferred_turn = (
        data.get("road_context", {}).get("infer_turn_from_turn_lanes") is True
        or "turn_left" in car_maneuver
    )
    if not allow_inferred_turn:
        context.setdefault("notes", []).append(
            "OSM turn:lanes tags were kept as evidence only; the report does "
            "not explicitly describe a left-turn maneuver for car_1."
        )
        return

    lane_evidence = context.get("lane_count_evidence", {}).get("secondary", {})
    selected_segments = lane_evidence.get("selected_segments", [])
    turn_lanes = selected_segments[0].get("turn_lanes") if selected_segments else None
    if car_position != "leftmost_motor_lane" or not turn_lanes:
        return

    first_lane = str(turn_lanes).split("|", 1)[0].casefold()
    if "left" not in first_lane:
        return

    osc_params = data.setdefault("generated_simulation_parameters", {}).setdefault(
        "openscenario", {}
    )
    osc_params["car_path"] = "turn_left_from_secondary_to_primary"
    context["lane_guided_maneuver"] = {
        "actor": "car_1",
        "value": "turn_left_from_secondary_to_primary",
        "source": "inferred_from_report_lane_and_osm_turn_lanes",
        "evidence": {
            "report_road_position": car_position,
            "osm_turn_lanes": turn_lanes,
        },
    }
    _upsert_missing_parameter(
        data,
        parameter="car_1.path",
        value_used="turn_left_from_secondary_to_primary",
        source="inferred_from_report_lane_and_osm_turn_lanes",
        reason=(
            "The report places the Pkw on the left lane of Drakestraße, and "
            "the matched OSM approach marks the leftmost lane as a left-turn "
            "lane. The resulting path is an inferred lane-guided maneuver."
        ),
    )


def _parse_lane_count(value):
    if not value:
        return None
    if isinstance(value, list):
        for item in value:
            parsed = _parse_lane_count(item)
            if parsed:
                return parsed
        return None

    match = re.search(r"\d+", str(value))
    if not match:
        return None
    lane_count = int(match.group(0))
    return max(1, min(lane_count, 5))


def _approach_lane_count_from_road(road, progress_toward_target_m):
    oneway = str(road.get("oneway") or "").casefold()
    if oneway in {"yes", "1", "true"}:
        return _parse_lane_count(road.get("lanes"))

    if progress_toward_target_m is not None:
        if progress_toward_target_m > 0 and road.get("lanes_forward"):
            return _parse_lane_count(road.get("lanes_forward"))
        if progress_toward_target_m < 0 and road.get("lanes_backward"):
            return _parse_lane_count(road.get("lanes_backward"))

    # For two-way roads, a total lanes=* tag is not direction-specific. Avoid
    # silently turning it into an approach lane count unless OSM gives a
    # direction-specific lanes:forward/backward tag.
    if road.get("lanes_forward") or road.get("lanes_backward"):
        return None
    return _parse_lane_count(road.get("lanes")) if oneway in {"yes", "1", "true"} else None


def _upsert_missing_parameter(data, parameter, value_used, source, reason):
    missing = data.setdefault("missing_parameters", [])
    for item in missing:
        if item.get("parameter") == parameter:
            item["value_used"] = value_used
            item["source"] = source
            item["reason"] = reason
            return
    missing.append(
        {
            "parameter": parameter,
            "value_used": value_used,
            "source": source,
            "reason": reason,
        }
    )


def _first_maxspeed_kmh(roads):
    for road in roads:
        parsed = _parse_maxspeed_kmh(road.get("maxspeed"))
        if parsed is not None:
            return parsed
    return None


def _parse_maxspeed_kmh(value):
    if not value:
        return None
    if isinstance(value, list):
        for item in value:
            parsed = _parse_maxspeed_kmh(item)
            if parsed is not None:
                return parsed
        return None

    text = str(value).strip().lower()
    country_defaults = {
        "de:urban": 50,
        "de:rural": 100,
        "de:motorway": 130,
        "walk": 7,
    }
    if text in country_defaults:
        return country_defaults[text]

    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None

    speed = float(match.group(1))
    if "mph" in text:
        speed *= 1.60934
    return round(speed, 1)


def _approach_descriptors_from_location(location):
    refs = []
    for reference in location.get("direction_references", []):
        match = re.search(
            r"from\s+(.+?)\s+toward\s+(.+)$",
            reference,
            flags=re.IGNORECASE,
        )
        if match:
            refs.append(
                {
                    "from": match.group(1).strip(),
                    "toward": match.group(2).strip(),
                }
            )

    return {
        "primary": refs[0] if len(refs) >= 1 else _approach_from_field(location.get("primary_road")),
        "secondary": refs[1] if len(refs) >= 2 else _approach_from_field(location.get("secondary_road")),
    }


def _approach_from_field(value):
    names = _names_from_location_field(value)
    return {
        "from": names[0] if names else None,
        "toward": names[1] if len(names) > 1 else None,
    }


def _representative_points(road):
    points = []
    if road.get("center"):
        points.append(road["center"])
    geometry = road.get("geometry") or []
    if geometry:
        points.append(geometry[0])
        points.append(geometry[-1])
    return points


def _progress_toward_target(road, target_points):
    geometry = road.get("geometry") or []
    if len(geometry) < 2 or not target_points:
        return None, None

    start = geometry[0]
    end = geometry[-1]
    start_distance = min(_point_distance_m(start, target) for target in target_points)
    end_distance = min(_point_distance_m(end, target) for target in target_points)
    return start_distance - end_distance, end_distance


def _road_heading_rad(road, progress_toward_target_m=None):
    geometry = road.get("geometry") or []
    if len(geometry) < 2:
        return None

    start = geometry[0]
    end = geometry[-1]
    if progress_toward_target_m is not None and progress_toward_target_m < 0:
        start, end = end, start

    lat_a = float(start["lat"])
    lon_a = float(start["lon"])
    lat_b = float(end["lat"])
    lon_b = float(end["lon"])
    avg_lat_rad = math.radians((lat_a + lat_b) / 2)
    dx = (lon_b - lon_a) * 111_320 * math.cos(avg_lat_rad)
    dy = (lat_b - lat_a) * 110_540
    if dx == 0 and dy == 0:
        return None
    return math.atan2(dy, dx)


def _point_distance_m(a, b):
    lat_a = float(a["lat"])
    lon_a = float(a["lon"])
    lat_b = float(b["lat"])
    lon_b = float(b["lon"])
    avg_lat_rad = math.radians((lat_a + lat_b) / 2)
    dx = (lon_b - lon_a) * 111_320 * math.cos(avg_lat_rad)
    dy = (lat_b - lat_a) * 110_540
    return math.hypot(dx, dy)


def _road_names_from_location(location):
    names = []
    for key in ("primary_road", "secondary_road", "osm_roads"):
        value = location.get(key)
        if not value:
            continue
        if isinstance(value, list):
            names.extend(value)
        else:
            names.append(value)

    cleaned = []
    for name in names:
        for part in re.split(r"\s+to\s+|/|,", str(name), flags=re.IGNORECASE):
            part = part.replace("approach", "").strip()
            if part and part not in cleaned:
                cleaned.append(part)
    return cleaned


def _names_from_location_field(value):
    if not value:
        return []
    if isinstance(value, list):
        values = value
    else:
        values = [value]

    names = []
    for item in values:
        for part in re.split(r"\s+to\s+|/|,", str(item), flags=re.IGNORECASE):
            part = part.replace("approach", "").strip()
            if part and part not in names:
                names.append(part)
    return names


def _name_matches(name, wanted_names):
    if not name:
        return False
    if isinstance(name, list):
        return any(_name_matches(item, wanted_names) for item in name)
    name_norm = _normalize_name(name)
    return any(_normalize_name(wanted) in name_norm or name_norm in _normalize_name(wanted) for wanted in wanted_names)


def _normalize_name(name):
    return re.sub(r"\s+", " ", str(name).casefold().strip())


def _first_present(tags, *keys):
    for key in keys:
        if key in tags:
            return tags[key]
    return None


def _cache_key(value):
    return hashlib.sha1(value.encode("utf-8")).hexdigest()
