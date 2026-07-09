"""
Agent 1 — Extraction Agent
===========================
Takes a raw German Berlin police accident report and produces a structured
JSON file in the format expected by the scenario_pipeline (main.py).

Usage:
    python3 src/extract_scenario.py --report "Der Fahrer wartete..." --id "right_turn_salvador_allende_1038"
    python3 src/extract_scenario.py --report-file my_report.txt --id "dooring_kantstr_1425"

The output JSON is saved to the input/ folder, ready to be processed by main.py.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from llm_client import get_client, MODEL

# ── Configuration ─────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_DIR / "input"

# ── Allowed values (Hierarchical Scenario Repository) ─────────────────────────
SCHEMA = {
    "scenario_types": [
        "right_turn_conflict", "left_turn_conflict", "straight_crossing_conflict",
        "midblock_crossing_conflict", "priority_violation_conflict",
        "parking_access_conflict", "enter_roadway_conflict",
        "lane_change_conflict", "overtaking_conflict", "dooring", "unknown"
    ],
    "participant_types": ["car", "truck", "bus", "bicycle", "e_bike", "pedestrian", "other"],
    "maneuvers": [
        "go_straight", "turn_right", "turn_left", "turn_right_into_parking",
        "stop", "wait", "parked", "open_door", "change_lane",
        "enter_roadway", "exit_roadway", "overtake", "unknown"
    ],
    "bike_facility_types": [
        "separated_cycle_track", "bike_lane", "shared_foot_cycle_path",
        "cycle_crossing", "roadway_mixed", "sidewalk", "unknown"
    ],
    "traffic_rule_status": ["priority", "must_yield", "violated_priority", "unknown"],
    "directions": ["north", "south", "east", "west", "northwest", "northeast", "southwest", "southeast", "unknown"]
}

# ── System prompt ──────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""You are Agent 1 of a multi-agent pipeline for autonomous driving research at TU Berlin.
Your task: extract structured scenario information from a German Berlin police accident report involving a car/truck and a cyclist.

OUTPUT: Return ONLY a valid JSON object. No explanation, no markdown, no code blocks. Raw JSON only.

EXTRACTION RULES:
- Extract only what is explicitly stated or can be directly inferred from the text
- Use null for any value not available in the report
- For the osm_query field: construct the best possible OpenStreetMap search string from the location info
- "LSA" means traffic light (Lichtzeichenanlage)
- "Radfahrer/Radfahrerin" = bicycle
- "LKW/Lkw/Lastwagen" = truck
- "Pkw/Auto" = car
- "Pedelec/E-Bike" = e_bike
- "baulich getrennter Radweg" = separated_cycle_track
- "Schutzstreifen/Radfahrschutzstreifen" = bike_lane
- "Nebenfahrbahn" = roadway_mixed (cyclist riding on side roadway)
- participant id: use "truck_1", "car_1", "bus_1" for motor vehicle; always "cyclist_1" for cyclist
- participant class: "motor_vehicle" or "cyclist"
- conflict_mechanism: describe in snake_case what happened (e.g. "right_turn_across_cycle_track", "door_opened_into_cyclist_path")

ALLOWED VALUES:
- scenario_type: {SCHEMA['scenario_types']}
- participant type: {SCHEMA['participant_types']}
- maneuver: {SCHEMA['maneuvers']}
- bike_facility_type: {SCHEMA['bike_facility_types']}
- traffic_rule_status: {SCHEMA['traffic_rule_status']}
- directions: {SCHEMA['directions']}

OUTPUT JSON STRUCTURE (fill every field, use null if unknown):
{{
  "schema_version": "0.1",
  "source": {{
    "dataset": "Berlin Police Reports",
    "source_id": "<will be filled by script>",
    "date": "<YYYY-MM-DD or null>",
    "time": "<HH:MM or null>",
    "raw_text": "<the original report text verbatim>"
  }},
  "classification": {{
    "scenario_type": "<from allowed list>",
    "confidence": <0.0-1.0>
  }},
  "location": {{
    "city": "Berlin",
    "primary_road": "<main street name or null>",
    "secondary_road": "<cross street or destination street or null>",
    "osm_query": "<best OSM search string, e.g. 'Salvador-Allende-Str / Müggelschlößchenweg, Berlin, Germany'>",
    "osm_roads": ["<list of road names mentioned>"],
    "house_number_reference": "<house number if mentioned, else null>",
    "location_type": "<intersection / midblock / parking_access / driveway / unknown>",
    "direction_references": ["<list of directions mentioned, e.g. 'north', 'south'>"]
  }},
  "road_context": {{
    "traffic_light_present": "<yes / no / unknown>",
    "bike_facility_type": "<from allowed list>",
    "parking_present": "<yes / no / unknown>",
    "number_of_lanes_mentioned": "<number as string or 'unknown'>"
  }},
  "participants": [
    {{
      "id": "<truck_1 / car_1 / bus_1>",
      "class": "motor_vehicle",
      "type": "<from allowed list>",
      "maneuver": "<from allowed list>",
      "initial_direction": "<from allowed directions>",
      "traffic_rule_status": "<from allowed list>"
    }},
    {{
      "id": "cyclist_1",
      "class": "cyclist",
      "type": "<bicycle or e_bike>",
      "maneuver": "<from allowed list>",
      "initial_direction": "<from allowed directions>",
      "traffic_rule_status": "<from allowed list>"
    }}
  ],
  "conflict": {{
    "conflict_mechanism": "<snake_case description>",
    "collision_happened": <true / false>,
    "collision_description": "<one sentence in English describing what happened>",
    "severity_text": "<fatal / serious / minor / unknown>"
  }}
}}"""


# ── Agent 1 function ───────────────────────────────────────────────────────────
def extract_scenario(report_text: str, scenario_id: str) -> dict:
    """
    Calls the LLM to extract structured scenario information from a German
    police report. Returns a dict in the format expected by main.py.
    """
    client = get_client()

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract the scenario from this Berlin police report:\n\n{report_text.strip()}"}
        ],
        temperature=0.0,
        max_tokens=2000,
        response_format={"type": "json_object"}
    )

    raw = response.choices[0].message.content
    extracted = json.loads(raw)

    # Always set the source_id from our argument, not from LLM
    extracted["source"]["source_id"] = scenario_id

    # Add the default simulation parameters block (filled later by Agent 2+3)
    extracted["generated_simulation_parameters"] = {
        "opendrive": {
            "road_length_m": 100,
            "motor_lane_width_m": 3.5,
            "bike_lane_width_m": 2.0,
            "road_geometry": "line"
        },
        "openscenario": {
            "simulation_duration_s": 10,
            "actors": {}
        }
    }
    extracted["missing_parameters"] = []

    return extracted


def _generate_id_from_report(text: str) -> str:
    """Generate a scenario ID from the report text if none is provided."""
    # extract date if present
    date_match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    date_str = f"{date_match.group(3)}_{date_match.group(2)}_{date_match.group(1)}" if date_match else "unknown_date"

    # extract first road name
    road_match = re.search(r"(?:der|die|den|dem)\s+([A-ZÄÖÜ][a-zäöüß\-]+(?:straße|str\.|platz|weg|damm|allee|chaussee))", text)
    road_str = road_match.group(1).lower().replace("straße", "str").replace(".", "").replace(" ", "_") if road_match else "unknown_road"

    return f"scenario_{road_str}_{date_str}"


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Agent 1: Extract structured scenario JSON from a German police report."
    )
    parser.add_argument("--report", help="Raw German report text (as string)")
    parser.add_argument("--report-file", help="Path to a .txt file containing the report")
    parser.add_argument("--id", help="Scenario ID (used as filename, e.g. right_turn_salvador_allende_1038)")
    parser.add_argument("--output-dir", default=str(INPUT_DIR), help="Output directory (default: input/)")
    args = parser.parse_args()

    # Get report text
    if args.report:
        report_text = args.report
    elif args.report_file:
        report_text = Path(args.report_file).read_text(encoding="utf-8")
    else:
        print("Error: provide --report or --report-file")
        sys.exit(1)

    # Get or generate scenario ID
    scenario_id = args.id if args.id else _generate_id_from_report(report_text)

    print(f"Agent 1 — Extraction Agent")
    print(f"Model: {MODEL}")
    print(f"Scenario ID: {scenario_id}")
    print(f"Report length: {len(report_text)} characters")
    print()

    # Run extraction
    result = extract_scenario(report_text, scenario_id)

    # Save to input/ folder
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"{scenario_id}.json"
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Extracted scenario type: {result['classification']['scenario_type']}")
    print(f"Confidence: {result['classification']['confidence']}")
    print(f"Primary road: {result['location']['primary_road']}")
    print(f"Participants: {[p['id'] for p in result['participants']]}")
    print(f"Collision: {result['conflict']['collision_happened']}")
    print()
    print(f"Saved to: {output_path}")
    print()
    print("Next step — run the pipeline:")
    print(f"  python3 src/main.py --input input/{scenario_id}.json --enrich-osm --validate")


if __name__ == "__main__":
    main()
