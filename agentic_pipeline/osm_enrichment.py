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
_DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / "output" / "osm_cache"
BIKE_FACILITY_TYPES = {
    "separated_cycle_track",
    "protected_bike_lane",
    "bike_lane",
    "shared_lane",
}


class RoadHeadingNotFoundError(ValueError):
    """Raised when _best_road_heading cannot match the requested road name.

    Callers must not treat this as "no heading available" and substitute an
    unrelated road's heading — that produces silently wrong geometry (e.g. a
    turning scenario's secondary road heading collapsing to the primary
    road's heading, which zeroes out the computed turn angle downstream).
    """


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
        roads = _extract_road_context(overpass)
        matches = _select_relevant_roads(enriched, roads)
        context["matched_roads"] = matches
        context["enrichment_status"] = "ok"

        _apply_osm_context(enriched, context, cache_dir)
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
    # Assumption 3 (docs/modeling_assumptions.md): traffic lights are never
    # modeled, so this query no longer fetches highway=traffic_signals nodes.
    query = f"""
[out:json][timeout:25];
(
  way(around:{radius_m},{lat},{lon})["highway"];
);
out tags center geom;
"""
    return _run_overpass_query(query, cache_dir)


def _overpass_named_ways(name, lat, lon, radius_m, cache_dir):
    """Fetch OSM ways whose name matches `name` (substring, case-insensitive)
    within radius_m of (lat, lon). Filtering by name server-side keeps the
    result set small even at a large radius, unlike _overpass_nearby_roads'
    unfiltered "every highway nearby" query.
    """
    escaped = re.sub(r'(["\\])', r"\\\1", str(name))
    query = f"""
[out:json][timeout:30];
way["highway"]["name"~"{escaped}",i](around:{radius_m},{lat},{lon});
out tags geom;
"""
    return _run_overpass_query(query, cache_dir)


def _run_overpass_query(query, cache_dir, retries=3):
    cache_key = _cache_key(query)
    cache_path = cache_dir / "overpass" / f"{cache_key}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    body = urlencode({"data": query}).encode("utf-8")
    request = Request(
        OVERPASS_URL,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    # The public Overpass instance intermittently returns 504/503 (server
    # too busy) or 429 (rate limited) under load, unrelated to query
    # correctness — retry a few times with backoff before giving up.
    for attempt in range(retries + 1):
        time.sleep(1.0)
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except HTTPError as exc:
            if exc.code not in (504, 503, 429) or attempt == retries:
                raise
            time.sleep(5.0 * (attempt + 1))
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
    for element in overpass_payload.get("elements", []):
        tags = element.get("tags", {})
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
    return roads


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


def _apply_osm_context(data, context, cache_dir):
    # Assumption 3 (docs/modeling_assumptions.md): traffic lights / signal
    # state are never modeled, so road_context.traffic_light_present is not
    # populated or merged from OSM here.
    geocoded = context.get("geocoded", {})
    if geocoded.get("lat") is not None and geocoded.get("lon") is not None:
        opendrive_params = data.setdefault("generated_simulation_parameters", {}).setdefault(
            "opendrive", {}
        )
        opendrive_params["intersection_lat"] = geocoded["lat"]
        opendrive_params["intersection_lon"] = geocoded["lon"]

    _apply_bike_facility_context(data, context)
    _apply_lane_context(data, context, cache_dir)
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

    if scenario_type == "crossing" and "car_1" in actors:
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


def _apply_lane_context(data, context, cache_dir):
    params = data.setdefault("generated_simulation_parameters", {}).setdefault(
        "opendrive", {}
    )
    scenario_type = data.get("classification", {}).get("scenario_type")
    roads = context.get("matched_roads", [])
    geocoded = context.get("geocoded")

    if scenario_type == "crossing":
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
        primary_road_name = data.get("location", {}).get("primary_road")
        secondary_road_name = data.get("location", {}).get("secondary_road")
        lane_count = _best_lane_count(roads, primary_road_name)
        if lane_count:
            params["motor_lane_count"] = lane_count
            # Turning-vehicle lane assignment (left turn -> innermost lane,
            # right turn -> outermost lane) has moved to complete_parameters.py
            # (Agent 3) — it needs generated_simulation_parameters.openscenario
            # .actors[motor_id] to already exist, which this function (Agent 2,
            # query_osm) always runs before that entry is created in Agent 3's
            # complete_parameters(). See complete_parameters._apply_turning_vehicle_lane_id.
            _upsert_missing_parameter(
                data,
                parameter="motor_lane_count",
                value_used=lane_count,
                source="osm_tag",
                reason="OpenDRIVE motor-lane count was derived from nearby OSM lanes tags.",
            )
        heading = _manual_heading_override(data, primary_road_name, cache_dir)
        if heading is not None:
            heading_source, heading_reason = "manual_override", _TURNING_06_OVERRIDE_REASON
        else:
            heading_source = "osm_way_geometry"
            heading_reason = "Primary road heading derived from OSM way geometry."
            try:
                heading = _resolve_road_heading(
                    roads, primary_road_name, secondary_road_name, geocoded, cache_dir
                )
            except RoadHeadingNotFoundError as exc:
                context["notes"].append(f"primary_heading_rad: {exc}")
                _upsert_missing_parameter(
                    data,
                    parameter="primary_heading_rad",
                    value_used=None,
                    source="not_found",
                    reason=str(exc),
                )
        if heading is not None:
            params["primary_heading_rad"] = heading
            _upsert_missing_parameter(
                data,
                parameter="primary_heading_rad",
                value_used=round(heading, 6),
                source=heading_source,
                reason=heading_reason,
            )

        if secondary_road_name:
            sec_heading = _manual_heading_override(data, secondary_road_name, cache_dir)
            if sec_heading is not None:
                sec_source, sec_reason = "manual_override", _TURNING_06_OVERRIDE_SECONDARY_REASON
            else:
                sec_source = "osm_way_geometry"
                sec_reason = "Secondary road heading derived from OSM way geometry."
                try:
                    sec_heading = _resolve_road_heading(
                        roads, secondary_road_name, primary_road_name, geocoded, cache_dir
                    )
                except RoadHeadingNotFoundError as exc:
                    context["notes"].append(f"secondary_heading_rad: {exc}")
                    _upsert_missing_parameter(
                        data,
                        parameter="secondary_heading_rad",
                        value_used=None,
                        source="not_found",
                        reason=str(exc),
                    )
            if sec_heading is not None:
                params["secondary_heading_rad"] = sec_heading
                _upsert_missing_parameter(
                    data,
                    parameter="secondary_heading_rad",
                    value_used=round(sec_heading, 6),
                    source=sec_source,
                    reason=sec_reason,
                )
            sec_lanes = _best_lane_count(roads, secondary_road_name)
            if sec_lanes:
                params["secondary_road_lanes"] = sec_lanes


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
    # Converting this "position" into an actual initial_lane_id has moved to
    # complete_parameters.py (Agent 3) — it needs
    # generated_simulation_parameters.openscenario.actors["cyclist_1"] to
    # already exist, which this function (Agent 2, query_osm) always runs
    # before that entry is created in Agent 3's complete_parameters(). See
    # complete_parameters._apply_cyclist_lane_id, which also explains why
    # its write no longer unconditionally overwrites the way it used to
    # here (it would regress Assumption 2's cyclist-position work).
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


def _best_road_heading(roads, preferred_name=None):
    names = _names_from_location_field(preferred_name)
    if not names:
        candidates = roads
    else:
        matched = [road for road in roads if _name_matches(road.get("name"), names)]
        if not matched:
            if not roads:
                reason = "Overpass returned no nearby roads to search."
            else:
                seen = sorted({road.get("name") for road in roads if road.get("name")})
                reason = (
                    f"none of the {len(roads)} nearby OSM roads matched; "
                    f"names seen nearby: {seen}"
                )
            raise RoadHeadingNotFoundError(
                f"No road matching {names!r} (requested as {preferred_name!r}) "
                f"found in Overpass results: {reason}"
            )
        candidates = matched
    for road in candidates:
        heading = _road_heading_rad(road)
        if heading is not None:
            return heading
    return None


# Radius for the intersection-point fallback below. Deliberately much larger
# than DEFAULT_RADIUS_M: unlike _overpass_nearby_roads (unfiltered "every
# highway nearby"), this query filters by name server-side, so the result
# set stays small even over a wide area — needed because a long street can
# have its OSM way split into segments far from where the *other* road's
# geocoded point happens to land (e.g. Sophie-Charlotten-Straße is 6
# separate segments spread across neighborhoods).
INTERSECTION_SEARCH_RADIUS_M = 5000


def _local_heading_rad(geometry, index, window=2):
    """Heading of `geometry` (a list of {lat, lon} points) near `index`,
    using a small window instead of the whole way's endpoints — the whole
    way's start/end heading can be a poor estimate of the direction *at* a
    specific intersection point if the road curves elsewhere.
    """
    lo = max(0, index - window)
    hi = min(len(geometry) - 1, index + window)
    if lo == hi:
        return None
    lat_a, lon_a = float(geometry[lo]["lat"]), float(geometry[lo]["lon"])
    lat_b, lon_b = float(geometry[hi]["lat"]), float(geometry[hi]["lon"])
    avg_lat_rad = math.radians((lat_a + lat_b) / 2)
    dx = (lon_b - lon_a) * 111_320 * math.cos(avg_lat_rad)
    dy = (lat_b - lat_a) * 110_540
    if dx == 0 and dy == 0:
        return None
    return math.atan2(dy, dx)


def _shared_geometry_point(roads_a, roads_b, precision=6):
    """Find a geometry point shared by a road in roads_a and a road in
    roads_b (i.e. the OSM node where the two named streets actually meet),
    by comparing rounded coordinates. Returns (road_b, index_in_road_b) or
    (None, None) if the two sets of ways never touch.
    """
    points_a = {
        (round(float(pt["lat"]), precision), round(float(pt["lon"]), precision))
        for road in roads_a
        for pt in road.get("geometry", [])
    }
    for road in roads_b:
        geometry = road.get("geometry", [])
        for index, pt in enumerate(geometry):
            key = (round(float(pt["lat"]), precision), round(float(pt["lon"]), precision))
            if key in points_a:
                return road, index
    return None, None


def _intersection_heading(name_a, name_b, lat, lon, cache_dir, radius_m=INTERSECTION_SEARCH_RADIUS_M):
    """Find the OSM node where a way named name_a and a way named name_b
    actually intersect near (lat, lon), and return name_b's heading right at
    that node — regardless of which of possibly many same-named segments of
    name_b it turns out to be. Returns None if no shared point is found
    (either a genuine data gap, or the two roads' OSM ways don't share a
    node near this location) rather than guessing.
    """
    try:
        payload_a = _overpass_named_ways(name_a, lat, lon, radius_m, cache_dir)
        payload_b = _overpass_named_ways(name_b, lat, lon, radius_m, cache_dir)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None

    roads_a = _extract_road_context(payload_a)
    roads_b = _extract_road_context(payload_b)
    if not roads_a or not roads_b:
        return None

    road_b, index = _shared_geometry_point(roads_a, roads_b)
    if road_b is None:
        return None
    return _local_heading_rad(road_b.get("geometry", []), index)


def _resolve_road_heading(roads, road_name, other_road_name, geocoded, cache_dir):
    """_best_road_heading(), falling back to an intersection-point lookup
    against other_road_name when the plain nearby-roads search can't match
    road_name (e.g. it's outside the initial search radius or a different
    same-named segment). Still raises RoadHeadingNotFoundError — with a
    message covering both attempts — if neither resolves it.
    """
    try:
        return _best_road_heading(roads, road_name)
    except RoadHeadingNotFoundError as exc:
        lat, lon = (geocoded or {}).get("lat"), (geocoded or {}).get("lon")
        if not other_road_name or lat is None or lon is None:
            raise
        heading = _intersection_heading(road_name, other_road_name, float(lat), float(lon), cache_dir)
        if heading is None:
            raise RoadHeadingNotFoundError(
                f"{exc} Additionally, no shared OSM node between "
                f"{road_name!r} and {other_road_name!r} was found within "
                f"{INTERSECTION_SEARCH_RADIUS_M}m of the geocoded location."
            ) from exc
        return heading


# ── Single-report manual override ────────────────────────────────────────────
# Report "turning_06" (the report_loader.py scenario_id for
# manual_classification_reference.md's TURNING entry #6, "Eine Radfahrerin
# befuhr die Schönhauser Straße in Richtung Torstraße...") has its primary
# road "Schönhauser Straße" wrongly geocode ~7km away to an unrelated street
# in Pankow — the general name-resolution/intersection-lookup fix (commit
# 639a62c) can't recover from a wrong geocode anchor, and was deliberately
# not widened to force it (see that commit's notes). Report's "Schönhauser
# Straße" wrongly geocodes to an unrelated Pankow street; verified manually
# as Alte Schönhauser Straße at Torstraße, Mitte (52.528644, 13.409324) —
# see this session. This override is scoped to this one scenario_id and
# road name only — it must not affect any other report.
#
# The secondary road ("Torstraße") is not itself ambiguous in OSM — its own
# name resolves fine — but _resolve_road_heading()'s fallback search for it
# was still anchored at context["geocoded"], i.e. the same wrong Pankow
# point, so it never found the real segment ~15-20m from the verified
# coordinate (osm_id 1460822181 near 52.5290287, 13.4086293). The override
# below re-anchors *only* this report's secondary-road search at the same
# verified coordinate used for the primary-road fix.
_TURNING_06_OVERRIDE_SCENARIO_ID = "turning_06"
_TURNING_06_OVERRIDE_ROAD_NAME = "Schönhauser Straße"
_TURNING_06_OVERRIDE_SECONDARY_ROAD_NAME = "Torstraße"
_TURNING_06_OVERRIDE_LAT = 52.528644
_TURNING_06_OVERRIDE_LON = 13.409324
_TURNING_06_OVERRIDE_REASON = (
    "Report's 'Schönhauser Straße' wrongly geocodes to an unrelated Pankow "
    "street; verified manually as Alte Schönhauser Straße at Torstraße, "
    "Mitte (52.528644, 13.409324) — see this session."
)
_TURNING_06_OVERRIDE_SECONDARY_REASON = (
    "Report's 'Torstraße' search was anchored at the same wrong Pankow "
    "geocode as the primary road; re-anchored at the same manually "
    "verified coordinate (52.528644, 13.409324) — see this session."
)


def _local_distance_m(lat1, lon1, lat2, lon2):
    avg_lat_rad = math.radians((lat1 + lat2) / 2)
    dx = (lon2 - lon1) * 111_320 * math.cos(avg_lat_rad)
    dy = (lat2 - lat1) * 110_540
    return math.hypot(dx, dy)


def _nearest_road_heading(lat, lon, cache_dir, radius_m=150):
    """Heading of whichever real OSM road segment is physically nearest
    (lat, lon), regardless of name. Used only for the turning_06 override
    above, where a verified exact coordinate is known but the road's own
    name fails to geocode to the right place.
    """
    overpass = _overpass_nearby_roads(lat, lon, radius_m, cache_dir)
    roads = _extract_road_context(overpass)
    best = None  # (distance_m, road, index)
    for road in roads:
        for index, pt in enumerate(road.get("geometry", [])):
            distance_m = _local_distance_m(lat, lon, float(pt["lat"]), float(pt["lon"]))
            if best is None or distance_m < best[0]:
                best = (distance_m, road, index)
    if best is None:
        return None
    _, nearest_road, index = best
    return _local_heading_rad(nearest_road.get("geometry", []), index)


def _manual_heading_override(data, road_name, cache_dir):
    """Return the turning_06 coordinate-override heading if `data`/`road_name`
    match that single report, else None. See the override block above."""
    if data.get("source", {}).get("source_id") != _TURNING_06_OVERRIDE_SCENARIO_ID:
        return None
    if not road_name:
        return None
    normalized = _normalize_name(road_name)

    if normalized == _normalize_name(_TURNING_06_OVERRIDE_ROAD_NAME):
        # "Schönhauser Straße" itself geocodes to the wrong street entirely —
        # use whichever real road is physically nearest the verified point.
        return _nearest_road_heading(_TURNING_06_OVERRIDE_LAT, _TURNING_06_OVERRIDE_LON, cache_dir)

    if normalized == _normalize_name(_TURNING_06_OVERRIDE_SECONDARY_ROAD_NAME):
        # "Torstraße" is not itself ambiguous — it just needs searching near
        # the verified point instead of the wrong Pankow anchor.
        try:
            overpass = _overpass_nearby_roads(
                _TURNING_06_OVERRIDE_LAT, _TURNING_06_OVERRIDE_LON, 150, cache_dir
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return None
        roads = _extract_road_context(overpass)
        try:
            return _best_road_heading(roads, road_name)
        except RoadHeadingNotFoundError:
            return None

    return None


# ── Topology detection (midblock vs. junction) for OpenDRIVE template selection ─
#
# Automates what was previously a manual Google Maps/OSM lookup: given a
# report's text, decide whether its location is a straight midblock segment
# or a junction, and if a junction, how many roads actually meet there. This
# reuses the same intersection-node infrastructure already built and
# verified for heading resolution above (_overpass_named_ways,
# _shared_geometry_point) rather than a parallel Overpass query system —
# the only new query is _count_ways_at_node(), which reuses
# _overpass_nearby_roads() (already fixed/verified) at the node itself.

_STREET_STOPWORDS = {
    "richtung", "kreuzung", "hoehe", "höhe", "ecke", "naehe", "nähe", "bereich",
    "einmuendung", "einmündung", "hausnummer", "kreuzungsbereich", "querungshilfe",
    "kollision", "unfall", "beteiligte",
}
_STREET_EXCLUDE_FULL = {"radweg", "gehweg", "parkplatz", "mittelstreifen", "fahrbahn"}
_STREET_SUFFIX_WORDS = ("strasse", "weg", "allee", "damm", "platz", "gasse", "ufer", "ring", "chaussee")

# A handful of German street-naming patterns with an embedded lowercase
# preposition (e.g. "Straße zum Müggelhort", "Unter den Eichen") that a plain
# capitalized-word chain can't capture on its own.
_PREPOSITION_STREET_RE = re.compile(
    r"\b(?:Straße (?:zum|zur) [A-ZÄÖÜ][\wäöüÄÖÜß]*"
    r"|Unter den [A-ZÄÖÜ][\wäöüÄÖÜß]*"
    r"|Am [A-ZÄÖÜ][\wäöüÄÖÜß]*"
    r"|An der [A-ZÄÖÜ][\wäöüÄÖÜß]*)\b"
)
_STREET_CHAIN_RE = re.compile(r"\b[A-ZÄÖÜ][\wäöüÄÖÜß]*(?:[- ][A-ZÄÖÜ][\wäöüÄÖÜß]*)*\.?")
# "STREET Richtung STREET2" / "STREET in Richtung STREET2" — STREET2 is a
# heading/destination reference, not a distinct collision-site cross-street.
_RICHTUNG_BEFORE_RE = re.compile(r"(?:in\s+)?[Rr]ichtung\s+$")


def _street_word_normalize(word):
    return word.casefold().replace("ß", "ss").rstrip(".")


def _street_suffix_and_period(word):
    """Return (has_street_suffix, needs_trailing_period)."""
    n = _street_word_normalize(word)
    if n.endswith("str"):
        return True, True  # abbreviation: keep the period ("...Str.")
    return any(n.endswith(s) for s in _STREET_SUFFIX_WORDS), False


def _street_is_bare_suffix_word(word):
    n = _street_word_normalize(word)
    return n == "str" or n in _STREET_SUFFIX_WORDS


def _street_dedup_key(name):
    """Fold hyphen/space and the str/straße abbreviation so 'Erwin-Bock Str.'
    and 'Erwin-Bock-Straße' are recognized as the same street when the same
    report names it twice, inconsistently."""
    n = _street_word_normalize(name).replace("-", " ")
    n = re.sub(r"\bstr\b", "strasse", n)
    return re.sub(r"\s+", " ", n)


def _extract_street_candidates(report_text):
    """Deterministic, regex-based extraction of German street names from a
    report's raw text (no LLM call — the topology *decision* must be
    deterministic, and this keeps the whole pipeline testable/reproducible).
    Validated against all 19 reports in manual_classification_reference.md;
    known limitation: reports phrased as "from X toward Y" without an
    explicit "turned into"/"crossing of" cue can be ambiguous about which
    named streets define the actual collision site (see
    topology_detection_report.md for flagged cases).
    """
    found = []  # (name, start, end)
    for m in _PREPOSITION_STREET_RE.finditer(report_text):
        found.append((m.group(0), m.start(), m.end()))
    covered_spans = [(s, e) for _, s, e in found]

    def overlaps_covered(span):
        return any(s <= span[0] < e or s < span[1] <= e for s, e in covered_spans)

    for m in _STREET_CHAIN_RE.finditer(report_text):
        if overlaps_covered(m.span()):
            continue
        raw = m.group(0)
        base = m.start()
        tokens = re.split(r"([- ])", raw)
        words = tokens[0::2]
        seps = tokens[1::2] + [""]

        segments = [[]]  # list of (word, sep, offset_within_raw)
        offset = 0
        for word, sep in zip(words, seps):
            if _street_word_normalize(word) in _STREET_STOPWORDS:
                segments.append([])
            else:
                segments[-1].append((word, sep, offset))
            offset += len(word) + len(sep)

        for seg in segments:
            if not seg:
                continue
            last_word, _, last_offset = seg[-1]
            has_suffix, needs_period = _street_suffix_and_period(last_word)
            if not has_suffix:
                continue
            if len(seg) == 1 and _street_is_bare_suffix_word(last_word):
                continue  # lone "Straße"/"Weg"/... with no qualifier — too generic
            last_clean = last_word.rstrip(".")
            if needs_period:
                last_clean += "."
            parts = [w for w, _, _ in seg[:-1]]
            seps_between = [s for _, s, _ in seg[:-1]]
            name = "".join(p + s for p, s in zip(parts, seps_between)) + last_clean
            if _street_word_normalize(name) in _STREET_EXCLUDE_FULL:
                continue
            seg_start = base + seg[0][2]
            seg_end = base + last_offset + len(last_word)
            found.append((name, seg_start, seg_end))

    # Drop "STREET Richtung STREET2" destination references.
    found.sort(key=lambda t: t[1])
    kept = []
    for name, start, end in found:
        prefix = report_text[:start]
        m = _RICHTUNG_BEFORE_RE.search(prefix)
        is_direction_ref = False
        if m:
            before = prefix[: m.start()].rstrip()
            if any(before.endswith(k[0]) for k in kept):
                is_direction_ref = True
        if not is_direction_ref:
            kept.append((name, start, end))

    candidates = []
    seen_keys = []
    for name, _, _ in kept:
        key = _street_dedup_key(name)
        if key in seen_keys:
            continue
        seen_keys.append(key)
        candidates.append(name)
    return candidates


def _extract_house_number(report_text):
    m = re.search(r"Hausnummer\s+(\d+)", report_text)
    return m.group(1) if m else None


def _count_ways_at_node(lat, lon, cache_dir, radius_m=30):
    """Count distinct OSM ways whose geometry passes through (lat, lon) — the
    shared node found by _shared_geometry_point() — regardless of name.
    Reuses _overpass_nearby_roads(), the already-fixed nearby-roads query,
    rather than a new Overpass query path.
    """
    overpass = _overpass_nearby_roads(lat, lon, radius_m, cache_dir)
    roads = _extract_road_context(overpass)
    count = 0
    for road in roads:
        for pt in road.get("geometry", []):
            if _local_distance_m(lat, lon, float(pt["lat"]), float(pt["lon"])) < 0.5:
                count += 1
                break
    return count


# turning_06's location was manually verified for the heading override above
# (Alte Schönhauser Straße / Torstraße, Mitte, 52.528644/13.409324). Checking
# the actual shared node there (see this session) found 4 distinct ways
# touching it — Schönhauser Allee, Alte Schönhauser Straße, and Torstraße
# split into two segments — a genuine 4-way crossing, not a forced answer.
_TURNING_06_TOPOLOGY_OVERRIDE = {
    "topology": "4way_junction",
    "streets": ["Alte Schönhauser Straße", "Torstraße"],
    "house_number": None,
    "way_count": 4,
    "reason": (
        "Manually verified location (52.528644, 13.409324) — same override "
        "as the heading fix. The exact shared node there has 4 ways "
        "(Schönhauser Allee, Alte Schönhauser Straße, Torstraße x2 segments) "
        "— a genuine 4-way crossing, not a forced/guessed answer."
    ),
}


def _manual_topology_override(scenario_id):
    if scenario_id == _TURNING_06_OVERRIDE_SCENARIO_ID:
        return dict(_TURNING_06_TOPOLOGY_OVERRIDE, scenario_id=scenario_id)
    return None


def detect_topology(report_text, scenario_id, cache_dir=None):
    """Deterministically classify a report's location topology from its
    street name(s): "midblock" (-> straight_road.xodr), "4way_junction"
    (-> intersection_4way.xodr), or "needs_manual_review" (do not force a
    template choice). Reuses the intersection-node lookup infrastructure
    built for heading resolution (_overpass_named_ways/_shared_geometry_point)
    rather than a parallel query system.

    Returns a dict: {scenario_id, topology, streets, house_number,
    way_count, reason}.
    """
    if cache_dir is None:
        cache_dir = _DEFAULT_CACHE_DIR

    override = _manual_topology_override(scenario_id)
    if override is not None:
        return override

    streets = _extract_street_candidates(report_text)
    house_number = _extract_house_number(report_text)
    base = {"scenario_id": scenario_id, "streets": streets, "house_number": house_number}

    if len(streets) == 1:
        return dict(
            base,
            topology="midblock",
            way_count=None,
            reason="Only one street name found in the report text; no distinct cross-street mentioned.",
        )

    if len(streets) != 2:
        return dict(
            base,
            topology="needs_manual_review",
            way_count=None,
            reason=(
                f"{len(streets)} candidate street names found ({streets}); "
                "the decision logic is only defined for exactly 1 or 2 — "
                "cannot deterministically pick which pair (if any) defines "
                "the collision location."
            ),
        )

    name_a, name_b = streets
    # The combined "A / B" query is not always resolvable even when both
    # streets individually are; it's only used as an approximate anchor for
    # the subsequent named-way search (at a generous radius), so a fallback
    # to either street alone is fine here — precision comes from the actual
    # shared-node lookup below, not from this geocode.
    geocode_queries = [
        f"{name_a} / {name_b}, Berlin, Germany",
        f"{name_a}, Berlin, Germany",
        f"{name_b}, Berlin, Germany",
    ]
    geocoded = None
    try:
        for query in geocode_queries:
            geocoded = _nominatim_search(query, cache_dir)
            if geocoded:
                break
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        return dict(base, topology="needs_manual_review", way_count=None,
                    reason=f"Geocoding failed: {exc}")
    if not geocoded:
        return dict(base, topology="needs_manual_review", way_count=None,
                    reason="Geocoding returned no result for either street name.")
    lat, lon = float(geocoded["lat"]), float(geocoded["lon"])

    try:
        payload_a = _overpass_named_ways(name_a, lat, lon, INTERSECTION_SEARCH_RADIUS_M, cache_dir)
        payload_b = _overpass_named_ways(name_b, lat, lon, INTERSECTION_SEARCH_RADIUS_M, cache_dir)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        return dict(base, topology="needs_manual_review", way_count=None,
                    reason=f"Overpass query failed: {exc}")

    roads_a = _extract_road_context(payload_a)
    roads_b = _extract_road_context(payload_b)
    road_b, index = _shared_geometry_point(roads_a, roads_b)
    if road_b is None:
        return dict(
            base, topology="needs_manual_review", way_count=None,
            reason=(
                f"No shared OSM node found between {name_a!r} and {name_b!r} "
                f"within {INTERSECTION_SEARCH_RADIUS_M}m of the geocoded location "
                "(fragmentation, a genuine data gap, or a wrong geocode)."
            ),
        )

    node = road_b["geometry"][index]
    node_lat, node_lon = float(node["lat"]), float(node["lon"])
    try:
        way_count = _count_ways_at_node(node_lat, node_lon, cache_dir)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        return dict(base, topology="needs_manual_review", way_count=None,
                    reason=f"Way-count query at the shared node failed: {exc}")

    if way_count == 2:
        topology, reason = "midblock", f"{way_count} ways at the shared node — not actually a junction."
    elif way_count == 4:
        topology, reason = "4way_junction", f"{way_count} ways at the shared node — a genuine 4-way crossing."
    else:
        topology = "needs_manual_review"
        reason = f"{way_count} ways at the shared node — not the clean 2 or 4 case; do not force a template."

    return dict(base, topology=topology, way_count=way_count, reason=reason)


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
    if data.get("classification", {}).get("scenario_type") != "crossing":
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
    """Casefold a street name for comparison, normalizing spelling variants
    that are not meaningful differences: ß/ss, and the "Str"/"Str." ->
    "Straße" abbreviation (the only street-type abbreviation observed across
    manual_classification_reference.md's report texts — "Allee"/"Damm"/"Weg"
    already appear unabbreviated there and must not be altered).
    """
    text = re.sub(r"\s+", " ", str(name).casefold().strip())
    text = text.replace("ß", "ss")
    text = text.replace(".", "")
    # "str" abbreviates "strasse" only when it ends a word (not, e.g., the
    # "str" inside "streifen" or the "str" that already starts "strasse").
    text = re.sub(r"str(?![a-zäöü])", "strasse", text)
    return text


def _first_present(tags, *keys):
    for key in keys:
        if key in tags:
            return tags[key]
    return None


def _cache_key(value):
    return hashlib.sha1(value.encode("utf-8")).hexdigest()
