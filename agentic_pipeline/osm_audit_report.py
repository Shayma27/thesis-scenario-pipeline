"""
osm_audit_report.py — read-only audit dump of every OSM contribution across
the 19 reports in input/*.json, for manual verification against a real map.

This does NOT change the pipeline or judge correctness itself — it only
collects, per report, what osm_enrichment.py claims (geocoded location,
topology, bike facility, lane counts, heading, maxspeed) and where each
claim came from, into one file you can check row by row against
openstreetmap.org / Google Maps.

Live network calls are allowed (unlike test_agent1_preservation.py, which is
a fast structural regression test) — this script's whole point is checking
real accuracy, so it needs real data, not just cache hits.

Usage:
    python3 osm_audit_report.py
Output:
    docs/osm_audit_report.json  (raw data, one record per scenario)
    docs/osm_audit_report.md    (human-readable table + per-report detail)
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from osm_enrichment import detect_topology, enrich_with_osm
from pipeline import _fill_location_query_fields
from template_selector import select_template

INPUT_DIR = Path(__file__).resolve().parent / "input"
OSM_CACHE_DIR = Path(__file__).resolve().parent / "output" / "osm_cache"
OUT_JSON = Path(__file__).resolve().parent / "docs" / "osm_audit_report.json"
OUT_MD = Path(__file__).resolve().parent / "docs" / "osm_audit_report.md"


def _osm_link(lat, lon, zoom=18):
    if lat is None or lon is None:
        return None
    return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map={zoom}/{lat}/{lon}"


def _audit_one(scenario_id: str, agent1_data: dict) -> dict:
    data = copy.deepcopy(agent1_data)
    _fill_location_query_fields(data.setdefault("location", {}), data.get("participants", []))

    report_text = data.get("source", {}).get("raw_text", "")
    loc = data.get("location", {})
    road_ctx = data.get("road_context", {})

    enriched = enrich_with_osm(data, OSM_CACHE_DIR)
    ctx = enriched.get("osm_context", {})
    geocoded = ctx.get("geocoded", {})
    topology = detect_topology(report_text, scenario_id, cache_dir=OSM_CACHE_DIR)

    # scenario_type == "longitudinal" always selects straight_road.xodr
    # (see template_selector.py) regardless of what detect_topology says —
    # it's a single-road maneuver by definition, so topology detection
    # doesn't apply. Surface the ACTUAL template used, not just the raw
    # (and, for longitudinal reports, irrelevant) topology diagnostic —
    # otherwise "needs_manual_review" reads as an unresolved problem for a
    # report where the template choice was never actually in question.
    scenario_type = data.get("classification", {}).get("scenario_type")
    template_used = Path(select_template(scenario_type, topology.get("topology"))).name
    topology_moot = scenario_type == "longitudinal"

    odr = enriched.get("generated_simulation_parameters", {}).get("opendrive", {})
    bike_facility = ctx.get("bike_facility", {})
    lane_evidence = ctx.get("lane_count_evidence", {})
    derived = ctx.get("derived", {})

    lat, lon = geocoded.get("lat"), geocoded.get("lon")

    return {
        "scenario_id": scenario_id,
        "report_primary_road": loc.get("primary_road"),
        "report_secondary_road": loc.get("secondary_road"),
        "report_house_number": loc.get("house_number_reference"),
        "osm_query_used": ctx.get("query"),
        "enrichment_status": ctx.get("enrichment_status"),
        "geocoded_display_name": geocoded.get("display_name"),
        "geocoded_lat": lat,
        "geocoded_lon": lon,
        "map_link": _osm_link(lat, lon),
        "topology": topology.get("topology"),
        "topology_way_count": topology.get("way_count"),
        "topology_reason": topology.get("reason"),
        "topology_moot": topology_moot,
        "template_used": template_used,
        "bike_facility_report_value": road_ctx.get("bike_facility_type"),
        "bike_facility_used_value": odr.get("primary_bike_facility_type"),
        "bike_facility_source": bike_facility.get("source"),
        "bike_facility_position": odr.get("primary_bike_facility_position"),
        "primary_lane_count": odr.get("motor_lane_count") or odr.get("primary_road_lanes"),
        "secondary_lane_count": odr.get("secondary_road_lanes"),
        "lane_count_status": (
            lane_evidence.get("primary", {}).get("status")
            if isinstance(lane_evidence, dict) and "primary" in lane_evidence
            else None
        ),
        "primary_heading_rad": odr.get("primary_heading_rad"),
        "secondary_heading_rad": odr.get("secondary_heading_rad"),
        "maxspeed_kmh": derived.get("maxspeed_kmh"),
        "notes": ctx.get("notes", []),
    }


def _fmt(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _write_markdown(records: list[dict]) -> None:
    lines = [
        "# OSM enrichment audit report",
        "",
        "Generated by `osm_audit_report.py`. This is raw evidence for manual",
        "verification against a real map — nothing here has been checked for",
        "correctness yet. Go through each row, open the map link, and confirm",
        "or reject each OSM-derived claim against what's actually there.",
        "",
        "## Summary table",
        "",
        "| scenario | geocoded to | template used | bike facility (report / osm) | lanes (p/s) | maxspeed | map |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in records:
        map_cell = f"[open]({r['map_link']})" if r["map_link"] else "—"
        bike_cell = f"{_fmt(r['bike_facility_report_value'])} / {_fmt(r['bike_facility_used_value'])} ({_fmt(r['bike_facility_source'])})"
        lanes_cell = f"{_fmt(r['primary_lane_count'])}/{_fmt(r['secondary_lane_count'])}"
        template_cell = r["template_used"]
        if r["topology_moot"]:
            template_cell += " (longitudinal — topology n/a)"
        lines.append(
            f"| {r['scenario_id']} | {_fmt(r['geocoded_display_name'])[:50]} "
            f"| {template_cell} | {bike_cell} | {lanes_cell} "
            f"| {_fmt(r['maxspeed_kmh'])} | {map_cell} |"
        )

    lines += ["", "## Per-report detail", ""]
    for r in records:
        lines.append(f"### {r['scenario_id']}")
        lines.append("")
        lines.append(f"- Report roads: **{_fmt(r['report_primary_road'])}** / **{_fmt(r['report_secondary_road'])}**"
                      f"{f', house number {r['report_house_number']}' if r['report_house_number'] else ''}")
        lines.append(f"- OSM query used: `{_fmt(r['osm_query_used'])}`  (status: {_fmt(r['enrichment_status'])})")
        lines.append(f"- Geocoded to: {_fmt(r['geocoded_display_name'])}"
                      f"  ({_fmt(r['geocoded_lat'])}, {_fmt(r['geocoded_lon'])})"
                      + (f"  — [map]({r['map_link']})" if r["map_link"] else ""))
        if r["topology_moot"]:
            lines.append(
                f"- Template: **{r['template_used']}** — scenario_type is "
                "'longitudinal', which always selects straight_road.xodr "
                "regardless of topology (single-road maneuver by "
                f"definition). Topology diagnostic was `{_fmt(r['topology'])}` "
                f"(way_count={_fmt(r['topology_way_count'])}) but doesn't "
                "affect template selection here."
            )
        else:
            lines.append(f"- Topology: **{_fmt(r['topology'])}** → template **{r['template_used']}** (way_count={_fmt(r['topology_way_count'])}) — {_fmt(r['topology_reason'])}")
        lines.append(f"- Bike facility: report says `{_fmt(r['bike_facility_report_value'])}`, "
                      f"used `{_fmt(r['bike_facility_used_value'])}` at `{_fmt(r['bike_facility_position'])}`"
                      f" (source: {_fmt(r['bike_facility_source'])})")
        lines.append(f"- Lane counts: primary={_fmt(r['primary_lane_count'])}, secondary={_fmt(r['secondary_lane_count'])}"
                      f" (status: {_fmt(r['lane_count_status'])})")
        lines.append(f"- Headings (rad): primary={_fmt(r['primary_heading_rad'])}, secondary={_fmt(r['secondary_heading_rad'])}")
        lines.append(f"- Maxspeed: {_fmt(r['maxspeed_kmh'])} km/h")
        if r["notes"]:
            lines.append(f"- Notes: {'; '.join(r['notes'])}")
        lines.append("")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    report_paths = sorted(INPUT_DIR.glob("*.json"))
    records = []
    for path in report_paths:
        scenario_id = path.stem
        agent1_data = json.loads(path.read_text(encoding="utf-8"))
        print(f"  auditing {scenario_id}...")
        try:
            records.append(_audit_one(scenario_id, agent1_data))
        except Exception as exc:  # noqa: BLE001 — a crash is itself worth recording
            print(f"    ✗ {exc}")
            records.append({"scenario_id": scenario_id, "error": str(exc)})

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_markdown(records)
    print(f"\nWrote {OUT_JSON} and {OUT_MD}")


if __name__ == "__main__":
    main()
