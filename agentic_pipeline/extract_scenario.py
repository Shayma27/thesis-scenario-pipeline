"""
Agent 1 — Extraction Agent
===========================
Pure semantic extraction from a raw German Berlin police accident report:
where it happened, what class of conflict it is, who was involved, what each
actor was doing, and how their movements relate. No simulator-specific
values (speeds, lane IDs, coordinates, road geometry) are inferred here —
those belong to Agent 2 (OSM enrichment) and Agent 3 (parameter completion),
which run afterward and read this JSON.

Usage:
    python3 extract_scenario.py --report "Der Fahrer wartete..." --id "right_turn_salvador_allende_1038"
    python3 extract_scenario.py --report-file my_report.txt --id "dooring_kantstr_1425"

The output JSON is saved to the input/ folder, ready to be processed by the pipeline.
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
    "scenario_types": ["turning", "crossing", "longitudinal", "other"],
    "participant_types": ["car", "truck", "bus", "bicycle", "e_bike", "pedestrian", "other"],
    "maneuvers": [
        "go_straight", "turn_right", "turn_left", "turn_right_into_parking",
        "stop", "wait", "parked", "open_door", "change_lane",
        "enter_roadway", "exit_roadway", "overtake", "unknown"
    ],
    "bike_facility_types": [
        "separated_cycle_track", "bike_lane", "shared_foot_cycle_path",
        "cycle_crossing", "roadway_mixed", "sidewalk", "median_strip"
    ],
    "traffic_rule_status": ["priority", "must_yield", "violated_priority"],
    "directions": ["north", "south", "east", "west", "northwest", "northeast", "southwest", "southeast"],
    "heading_relations": ["same_direction", "opposite_direction", "crossing", "unknown"],
    # Assumption 2 (docs/modeling_assumptions.md): explicit report language
    # placing a participant on a *numbered driving lane* (Fahrstreifen),
    # e.g. "den linken der drei Fahrstreifen" / "den äußerst rechten
    # Fahrstreifen" — distinct from bike_facility_type/bike_facility_position,
    # which describe a separate cycling facility, not a driving lane.
    "road_positions": ["leftmost_motor_lane", "middle_motor_lane", "rightmost_motor_lane"],
    # Which side of the road a cycling facility is on, only if explicitly stated.
    "bike_facility_positions": ["left", "middle", "right", "rightmost"],
}

# ── System prompt ──────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""You are Agent 1 of a multi-agent pipeline for autonomous driving research at TU Berlin.
Your task: extract structured SEMANTIC scenario information from a German Berlin police accident
report involving a car/truck and a cyclist. You answer exactly five questions: where it happened,
what class of conflict it is, who was involved, what each actor was doing, and how their movements
relate. You do NOT infer speeds, lane widths, coordinates, or any other simulator-specific value —
that happens in a later stage, not here.

OUTPUT: Return ONLY a valid JSON object. No explanation, no markdown, no code blocks. Raw JSON only.

SCENARIO_TYPE DEFINITIONS (classify into exactly one of these four):
- turning: the motor vehicle turns across the cyclist's path.
- crossing: the motor vehicle goes straight while the cyclist crosses its path
  (regardless of the cyclist's own maneuver — straight, turning, etc.).
- longitudinal: the motor vehicle and cyclist travel the same general direction
  (includes overtaking and lane-change scenarios).
- other: everything else.

EXTRACTION RULES:
- Extract only what is explicitly stated or can be directly inferred from the text
- Use JSON null (never the string "unknown") for anything not stated
- Traffic lights / signal state (LSA, "rote/grüne Ampel", "Rotlicht") are NEVER extracted or
  represented anywhere in this pipeline — ignore any such mention when filling every other
  field, including traffic_rule_status and heading_relation
- "Schutzstreifen/Radfahrschutzstreifen" = bike_lane
- "baulich getrennter Radweg" = separated_cycle_track
- "gemeinsamer Geh- und Radweg" = shared_foot_cycle_path
- "Radverkehrsfurt" = cycle_crossing
- "Nebenfahrbahn" = roadway_mixed (cyclist riding on a side roadway, no dedicated facility)
- "(begrünter) Mittelstreifen" = median_strip (cyclist coming from a median/refuge strip)
- participant id: "truck_1"/"car_1"/"bus_1" for the motor vehicle; always "cyclist_1" for the cyclist
- conflict_mechanism: snake_case summary, e.g. "right_turn_across_cycle_track"
- heading_relation: "same_direction"/"opposite_direction" only when explicitly stated (e.g. "in
  gleicher/gleiche Richtung" = same_direction); "crossing" when the paths cross at an angle
  (typical for turning/crossing scenario_types); "unknown" if not determinable
- road_position (per participant): only when the report names a *numbered driving lane*
  (Fahrstreifen), e.g. "den linken/äußerst linken Fahrstreifen" = leftmost_motor_lane, "den
  rechten/äußerst rechten Fahrstreifen" = rightmost_motor_lane, "den mittleren Fahrstreifen" =
  middle_motor_lane. Otherwise null. This is distinct from bike_facility_type/position, which
  describe a separate cycling facility, not a driving lane.
- bike_facility_position: only when the report states which side of the road the facility is on
  (e.g. "der Radweg auf der linken Seite" = left). Otherwise null.
- evidence: for each key, quote the shortest report fragment (a few words) that grounds the
  corresponding field. null if that field itself is null. Never quote large portions of the report
  — this is for later grounding checks, not a summary.

ALLOWED VALUES:
- scenario_type: {SCHEMA['scenario_types']}
- participant type: {SCHEMA['participant_types']}
- maneuver: {SCHEMA['maneuvers']}
- bike_facility_type: {SCHEMA['bike_facility_types']}
- traffic_rule_status: {SCHEMA['traffic_rule_status']}
- directions: {SCHEMA['directions']}
- heading_relation: {SCHEMA['heading_relations']}
- road_position: {SCHEMA['road_positions']}
- bike_facility_position: {SCHEMA['bike_facility_positions']}

OUTPUT JSON STRUCTURE (fill exactly this — do not add osm_query, city, lane counts, speeds,
coordinates, or any other simulator-specific field; those are added later by other code):
{{
  "schema_version": "0.2",
  "source": {{
    "dataset": "Berlin Police Reports",
    "source_id": "<will be filled by script>",
    "raw_text": "<the original report text verbatim>"
  }},
  "classification": {{
    "scenario_type": "<from allowed list>",
    "confidence": <0.0-1.0>
  }},
  "location": {{
    "primary_road": "<main street name or null>",
    "secondary_road": "<cross street or destination street or null>",
    "house_number_reference": "<house number if mentioned, else null>"
  }},
  "road_context": {{
    "bike_facility_type": "<from allowed list, or null if not stated>",
    "bike_facility_position": "<from allowed list, or null if not stated>"
  }},
  "participants": [
    {{
      "id": "<truck_1 / car_1 / bus_1>",
      "class": "motor_vehicle",
      "type": "<from allowed list>",
      "maneuver": "<from allowed list>",
      "initial_direction": "<from allowed directions, or null if not stated>",
      "traffic_rule_status": "<from allowed list, or null if not stated>",
      "road_position": "<from allowed list, or null if not stated>"
    }},
    {{
      "id": "cyclist_1",
      "class": "cyclist",
      "type": "<bicycle or e_bike>",
      "maneuver": "<from allowed list>",
      "initial_direction": "<from allowed directions, or null if not stated>",
      "traffic_rule_status": "<from allowed list, or null if not stated>",
      "road_position": "<from allowed list, or null if not stated>"
    }}
  ],
  "conflict": {{
    "conflict_mechanism": "<snake_case description>",
    "heading_relation": "<from allowed list>",
    "collision_happened": <true / false>,
    "collision_description": "<one sentence in English describing what happened>"
  }},
  "evidence": {{
    "location": "<short quoted phrase, or null>",
    "vehicle_maneuver": "<short quoted phrase, or null>",
    "cyclist_maneuver": "<short quoted phrase, or null>",
    "heading_relation": "<short quoted phrase, or null>",
    "bike_facility": "<short quoted phrase, or null>",
    "traffic_rule_status": "<short quoted phrase, or null>"
  }}
}}"""

# ── Few-shot examples (one per scenario_type) ──────────────────────────────────
# Real Berlin police reports, picked for having the most explicitly grounded
# fields (not the sparsest ones) so the model sees each field actually filled
# at least once, not just its null case.
FEWSHOT_EXAMPLES = """
EXAMPLES (one per scenario_type, chosen for having the richest grounded detail —
study exactly which fields are filled vs. left null, and how "evidence" backs them):

--- EXAMPLE 1 (turning) ---
REPORT:
"Eine Pkw fahrende Person fuhr auf der Straße zum Müggelhort nach Süden zum Müggelheimer Damm. Dort bog sie nach rechts nach Köpenick ab, ohne die Vorfahrtregelung durch Z.205 zu beachten. Sie übersah beim Abbiegen eine Rad fahrenden Person, die auf dem gemeinsamen Geh- und Radweg des Müggelheimer Damm vorfahrtberechtigt war. Beide Beteiligte waren nicht in der Lage, die Kollision abzuwenden."

CORRECT OUTPUT:
{
  "schema_version": "0.2",
  "source": {
    "dataset": "Berlin Police Reports",
    "source_id": "<will be filled by script>",
    "raw_text": "<verbatim report text>"
  },
  "classification": { "scenario_type": "turning", "confidence": 1.0 },
  "location": {
    "primary_road": "Müggelheimer Damm",
    "secondary_road": "Straße zum Müggelhort",
    "house_number_reference": null
  },
  "road_context": {
    "bike_facility_type": "shared_foot_cycle_path",
    "bike_facility_position": null
  },
  "participants": [
    { "id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "turn_right",
      "initial_direction": "south", "traffic_rule_status": "violated_priority", "road_position": null },
    { "id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "go_straight",
      "initial_direction": null, "traffic_rule_status": "priority", "road_position": null }
  ],
  "conflict": {
    "conflict_mechanism": "right_turn_violating_yield_sign_across_shared_path",
    "heading_relation": "crossing",
    "collision_happened": true,
    "collision_description": "A car turning right failed to observe a yield sign and struck a cyclist who had right of way on the shared foot and cycle path."
  },
  "evidence": {
    "location": "Straße zum Müggelhort ... zum Müggelheimer Damm",
    "vehicle_maneuver": "bog sie nach rechts ... ab",
    "cyclist_maneuver": null,
    "heading_relation": "übersah beim Abbiegen",
    "bike_facility": "gemeinsamen Geh- und Radweg",
    "traffic_rule_status": "ohne die Vorfahrtregelung durch Z.205 zu beachten; vorfahrtberechtigt"
  }
}
Note: "ohne die Vorfahrtregelung durch Z.205 zu beachten" (ignored the Z.205 yield sign) grounds
traffic_rule_status="violated_priority" for the car, and "vorfahrtberechtigt" (had right of way)
grounds "priority" for the cyclist. The cyclist's own direction is never stated (only the car's
"nach Süden" is), so it stays null — not guessed from the car's direction or general geography.
The cyclist's maneuver is implied, not action-verbed, so evidence.cyclist_maneuver is null even
though maneuver itself is still "go_straight" (directly inferable).

--- EXAMPLE 2 (crossing) ---
REPORT:
"Eine Rad fahrende Person querte an einer Querungshilfe unachtsam die stadteinwärts führende Richtungsfahrbahn der Landsberger Allee vom begrünten Mittelstreifen kommend nach Norden. Dabei wurde sie von einer Pkw fahrenden Person ungebremst erfasst, die auf der Landsberger Allee Richtung Westen mit deutlich überhöhter Geschwindigkeit fuhr."

CORRECT OUTPUT:
{
  "schema_version": "0.2",
  "source": {
    "dataset": "Berlin Police Reports",
    "source_id": "<will be filled by script>",
    "raw_text": "<verbatim report text>"
  },
  "classification": { "scenario_type": "crossing", "confidence": 1.0 },
  "location": {
    "primary_road": "Landsberger Allee",
    "secondary_road": null,
    "house_number_reference": null
  },
  "road_context": {
    "bike_facility_type": "median_strip",
    "bike_facility_position": null
  },
  "participants": [
    { "id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "go_straight",
      "initial_direction": "west", "traffic_rule_status": null, "road_position": null },
    { "id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "go_straight",
      "initial_direction": "north", "traffic_rule_status": null, "road_position": null }
  ],
  "conflict": {
    "conflict_mechanism": "cyclist_crosses_vehicle_path_from_median",
    "heading_relation": "crossing",
    "collision_happened": true,
    "collision_description": "A cyclist crossing from a green median strip was struck by a speeding car."
  },
  "evidence": {
    "location": "Landsberger Allee",
    "vehicle_maneuver": "Richtung Westen ... fuhr",
    "cyclist_maneuver": "querte ... vom begrünten Mittelstreifen kommend",
    "heading_relation": "querte ... die ... Richtungsfahrbahn",
    "bike_facility": "begrünten Mittelstreifen",
    "traffic_rule_status": null
  }
}
Note: "vom begrünten Mittelstreifen kommend" is the exact rule-mapped phrase for
bike_facility_type="median_strip". Both directions are explicit ("nach Norden" for the cyclist,
"Richtung Westen" for the car). Speed ("deutlich überhöhter Geschwindigkeit") is not a schema
field, so it is not invented into any other field — no traffic_rule_status is stated, so it (and
its evidence) stays null.

--- EXAMPLE 3 (longitudinal) ---
REPORT:
"Ein Radfahrer befuhr den linken der drei Fahrstreifen auf der Straße Alt-Biesdorf von der Lötschbergstraße kommend in Richtung Grabensprung. In Höhe der Braunsdorfstraße wechselte der Radfahrer auf den äußerst rechten Fahrstreifen, wobei es zum Zusammenstoß mit einem Toyota-Fahrer kam, der mit seinem Wagen in die gleiche Richtung unterwegs war."

CORRECT OUTPUT:
{
  "schema_version": "0.2",
  "source": {
    "dataset": "Berlin Police Reports",
    "source_id": "<will be filled by script>",
    "raw_text": "<verbatim report text>"
  },
  "classification": { "scenario_type": "longitudinal", "confidence": 1.0 },
  "location": {
    "primary_road": "Alt-Biesdorf",
    "secondary_road": null,
    "house_number_reference": null
  },
  "road_context": {
    "bike_facility_type": "roadway_mixed",
    "bike_facility_position": null
  },
  "participants": [
    { "id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "change_lane",
      "initial_direction": null, "traffic_rule_status": null, "road_position": "leftmost_motor_lane" },
    { "id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "go_straight",
      "initial_direction": null, "traffic_rule_status": null, "road_position": null }
  ],
  "conflict": {
    "conflict_mechanism": "cyclist_lane_change_into_car_path",
    "heading_relation": "same_direction",
    "collision_happened": true,
    "collision_description": "A cyclist changed from the leftmost to the rightmost of three lanes and collided with a car traveling in the same direction."
  },
  "evidence": {
    "location": "Straße Alt-Biesdorf",
    "vehicle_maneuver": "in die gleiche Richtung unterwegs",
    "cyclist_maneuver": "wechselte ... auf den äußerst rechten Fahrstreifen",
    "heading_relation": "in die gleiche Richtung unterwegs",
    "bike_facility": null,
    "traffic_rule_status": null
  }
}
Note: "den linken der drei Fahrstreifen" is an explicit numbered-lane reference, so
road_position="leftmost_motor_lane" is grounded. No dedicated cycling facility is named — the
cyclist rides directly in numbered driving lanes — so bike_facility_type is "roadway_mixed" (a
grounded inference from the lane language) even though evidence.bike_facility is null, since no
specific facility phrase exists to quote.

GENERAL RULE ACROSS ALL EXAMPLES: every field above is either a direct quote-level match to the
report or explicitly null. Never write the string "unknown" for a field's value — if something is
not stated in the report, its value is JSON null. Do not add fields beyond this structure: no
lane counts, coordinates, speeds, OSM queries, or simulator parameters — those come later.
"""

SYSTEM_PROMPT = SYSTEM_PROMPT + "\n" + FEWSHOT_EXAMPLES


# ── Agent 1 function ───────────────────────────────────────────────────────────
def extract_scenario(report_text: str, scenario_id: str) -> dict:
    """
    Calls the LLM to extract structured SEMANTIC scenario information from a
    German police report — no simulator-specific parameters, no OSM/query
    fields, nothing computed in Python. Returns exactly what the LLM produced,
    with only source_id overridden. Anything the next pipeline stage needs
    beyond this (OSM query strings, etc.) is that stage's own responsibility.
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
