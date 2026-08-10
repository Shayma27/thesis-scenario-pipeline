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
        "stop", "wait", "parked", "open_door",
        "change_lane_left_to_right", "change_lane_right_to_left", "change_lane",
        "enter_roadway", "exit_roadway", "overtake", "unknown"
    ],
    "bike_facility_types": [
        "separated_cycle_track", "bike_lane", "shared_foot_cycle_path",
        "cycle_crossing", "roadway_mixed", "sidewalk", "median_strip"
    ],
    "directions": ["north", "south", "east", "west", "northwest", "northeast", "southwest", "southeast"],
    # Assumption 2 (docs/modeling_assumptions.md): explicit report language
    # placing a participant on a *numbered driving lane* (Fahrstreifen),
    # e.g. "den linken der drei Fahrstreifen" / "den äußerst rechten
    # Fahrstreifen" — distinct from bike_facility_type/bike_facility_position,
    # which describe a separate cycling facility, not a driving lane.
    "road_positions": ["leftmost_motor_lane", "middle_motor_lane", "rightmost_motor_lane"],
    # Which side of the road a cycling facility is on, only if explicitly stated.
    "bike_facility_positions": ["left", "middle", "right", "rightmost"],
}


def _pipe(values: list) -> str:
    """Compact 'a|b|c' rendering of an allowed-values list — cheaper in
    tokens than Python's quoted, bracketed repr, which matters against a
    small deployment context window."""
    return "|".join(values)


# ── System prompt ──────────────────────────────────────────────────────────────
# Kept deliberately compact — this prompt runs against a small-context-window
# deployment (prompt + report + response must fit one shared token budget), so
# every line here costs real headroom. Prefer the 3 few-shot examples below to
# teach a pattern over adding another prose paragraph.
SYSTEM_PROMPT = f"""You are Agent 1 of a multi-agent pipeline for autonomous driving research at TU Berlin.
Extract structured SEMANTIC scenario information from a German Berlin police accident report
(car/truck vs. cyclist): where it happened, what class of conflict, who was involved, what each
actor did, how their movements relate. Do NOT infer speeds, lane widths, coordinates, or any other
simulator-specific value — a later stage does that, not you.

OUTPUT: ONLY a valid JSON object. No explanation, no markdown, no code blocks.

SCENARIO_TYPE (exactly one, decided ONLY from the motor vehicle's own maneuver, never the cyclist's):
turning = vehicle's maneuver is turn_right/turn_left/turn_right_into_parking. This ALWAYS wins,
full stop, no matter what the cyclist does — if the vehicle turns, the type is turning, never
crossing or longitudinal. crossing = vehicle goes straight AND the cyclist's path crosses it at an
angle (entering, crossing, or the cyclist itself turning across the vehicle's straight path).
longitudinal = vehicle goes straight AND the cyclist travels the same general direction alongside
it (following, overtaking, lane change). other = anything else.

RULES:
- Extract only what is stated or directly inferable. Use JSON null (never "unknown") if not stated.
- Traffic lights/signals (LSA, "rote/grüne Ampel") are NEVER extracted or represented in any field —
  ignore any such mention entirely.
- "Schutzstreifen/Radfahrschutzstreifen"=bike_lane, "baulich getrennter Radweg"=separated_cycle_track,
  "gemeinsamer Geh- und Radweg"=shared_foot_cycle_path, "Radverkehrsfurt"=cycle_crossing,
  "Nebenfahrbahn"=roadway_mixed, "(begrünter) Mittelstreifen"=median_strip.
- bike_facility_type: null UNLESS one of the phrases above is literally present. A cyclist riding
  on an ordinary street/lane with no facility named is null, NOT roadway_mixed — roadway_mixed
  requires the word "Nebenfahrbahn" specifically, it is not a default for "no facility mentioned".
- participant id: truck_1/car_1/bus_1 for the motor vehicle, always cyclist_1 for the cyclist.
- maneuver attribution: match each action to its actual grammatical subject, never the nearest
  noun. "[Radfahrer] übersah beim Abbiegen [einen Pkw]" — the SUBJECT (Radfahrer) is turning, the
  Pkw is not, even though "Abbiegen" sits right next to "Pkw" in the sentence.
- change_lane maneuver: if the report states which lane it started/ended in (e.g. "vom linken auf
  den rechten Fahrstreifen"), use change_lane_left_to_right or change_lane_right_to_left. Use plain
  change_lane only if a change happened but the direction isn't stated.
- conflict_mechanism: snake_case summary, e.g. "right_turn_across_cycle_track".
- road_position (per participant): only for an explicit *numbered driving lane* (Fahrstreifen) —
  "den linken/äußerst linken Fahrstreifen"=leftmost_motor_lane, "rechten/äußerst rechten"=
  rightmost_motor_lane, "mittleren"=middle_motor_lane. Else null. Distinct from bike_facility_*,
  which is a separate cycling facility, not a driving lane.
- bike_facility_position: only if the report states which side of the road the facility is on. Else null.
- initial_direction: must be exactly one of the allowed compass words below, or null — never a
  description like "same as X", and never a place/street name. Only literal compass words like
  "nördliche Richtung" (north) count; if only implied relative to another actor, use that actor's
  own literal compass value, never a phrase.
- heading_reference: for EACH participant separately, check the text near their own action for
  "Richtung X" (toward X) or "in Höhe von X" (near X) — extremely common in these reports, and NOT
  a compass direction. Do this check even when initial_direction is already filled for that
  participant, and even when the OTHER participant already has a heading_reference — check both
  participants independently every time. Write it in ENGLISH, short — "Richtung Torstraße"
  becomes "toward Torstraße", "in Höhe der Braunsdorfstraße" becomes "near Braunsdorfstraße" —
  never copy the German phrase verbatim. Null only if truly nothing like this is stated.

ALLOWED VALUES — scenario_type:{_pipe(SCHEMA['scenario_types'])} participant_type:{_pipe(SCHEMA['participant_types'])}
maneuver:{_pipe(SCHEMA['maneuvers'])} bike_facility_type:{_pipe(SCHEMA['bike_facility_types'])}
directions:{_pipe(SCHEMA['directions'])} road_position:{_pipe(SCHEMA['road_positions'])}
bike_facility_position:{_pipe(SCHEMA['bike_facility_positions'])}

STRUCTURE (fill exactly this, nothing more — no osm_query/city/lane counts/speeds/coordinates):
{{ "schema_version": "0.2",
  "source": {{ "dataset": "Berlin Police Reports", "source_id": "<script fills>" }},
  "classification": {{ "scenario_type": "<allowed>", "confidence": <0.0-1.0> }},
  "location": {{ "primary_road": "<or null>", "secondary_road": "<or null>", "house_number_reference": "<or null>" }},
  "road_context": {{ "bike_facility_type": "<allowed or null>", "bike_facility_position": "<allowed or null>" }},
  "participants": [
    {{ "id": "<truck_1/car_1/bus_1>", "class": "motor_vehicle", "type": "<allowed>", "maneuver": "<allowed>",
       "initial_direction": "<allowed or null>", "heading_reference": "<short English phrase or null>", "road_position": "<allowed or null>" }},
    {{ "id": "cyclist_1", "class": "cyclist", "type": "<bicycle/e_bike>", "maneuver": "<allowed>",
       "initial_direction": "<allowed or null>", "heading_reference": "<short English phrase or null>", "road_position": "<allowed or null>" }}
  ],
  "conflict": {{ "conflict_mechanism": "<snake_case>",
    "collision_happened": <bool>, "collision_description": "<one English sentence>" }}
}}"""

# ── Few-shot examples (one per scenario_type) ──────────────────────────────────
# Real Berlin police reports, picked for having the most explicitly grounded
# fields (not the sparsest ones) so the model sees each field actually filled
# at least once, not just its null case. Kept compact (single-line sub-objects,
# one-line notes) — this whole prompt has to fit a small deployment context
# window alongside the report text and the response.
FEWSHOT_EXAMPLES = """
EXAMPLES (one per scenario_type — study which fields are filled vs. left null):

--- EXAMPLE 1 (turning) ---
REPORT: "Eine Pkw fahrende Person fuhr auf der Straße zum Müggelhort nach Süden zum Müggelheimer Damm. Dort bog sie nach rechts nach Köpenick ab, ohne die Vorfahrtregelung durch Z.205 zu beachten. Sie übersah beim Abbiegen eine Rad fahrenden Person, die auf dem gemeinsamen Geh- und Radweg des Müggelheimer Damm vorfahrtberechtigt war."

CORRECT OUTPUT:
{
  "schema_version": "0.2",
  "source": { "dataset": "Berlin Police Reports", "source_id": "<will be filled by script>" },
  "classification": { "scenario_type": "turning", "confidence": 1.0 },
  "location": { "primary_road": "Müggelheimer Damm", "secondary_road": "Straße zum Müggelhort", "house_number_reference": null },
  "road_context": { "bike_facility_type": "shared_foot_cycle_path", "bike_facility_position": null },
  "participants": [
    { "id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "turn_right", "initial_direction": "south", "heading_reference": null, "road_position": null },
    { "id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "go_straight", "initial_direction": null, "heading_reference": null, "road_position": null }
  ],
  "conflict": { "conflict_mechanism": "right_turn_violating_yield_sign_across_shared_path", "collision_happened": true, "collision_description": "A car turning right failed to observe a yield sign and struck a cyclist who had right of way on the shared path." }
}

--- EXAMPLE 2 (crossing) ---
REPORT: "Eine Rad fahrende Person querte an einer Querungshilfe unachtsam die stadteinwärts führende Richtungsfahrbahn der Landsberger Allee vom begrünten Mittelstreifen kommend nach Norden. Dabei wurde sie von einer Pkw fahrenden Person ungebremst erfasst, die auf der Landsberger Allee Richtung Westen mit deutlich überhöhter Geschwindigkeit fuhr."

CORRECT OUTPUT:
{
  "schema_version": "0.2",
  "source": { "dataset": "Berlin Police Reports", "source_id": "<will be filled by script>" },
  "classification": { "scenario_type": "crossing", "confidence": 1.0 },
  "location": { "primary_road": "Landsberger Allee", "secondary_road": null, "house_number_reference": null },
  "road_context": { "bike_facility_type": "median_strip", "bike_facility_position": null },
  "participants": [
    { "id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "go_straight", "initial_direction": "west", "heading_reference": null, "road_position": null },
    { "id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "go_straight", "initial_direction": "north", "heading_reference": null, "road_position": null }
  ],
  "conflict": { "conflict_mechanism": "cyclist_crosses_vehicle_path_from_median", "collision_happened": true, "collision_description": "A cyclist crossing from a green median strip was struck by a speeding car." }
}

--- EXAMPLE 3 (longitudinal) ---
REPORT: "Ein Radfahrer befuhr den linken der drei Fahrstreifen auf der Straße Alt-Biesdorf von der Lötschbergstraße kommend in Richtung Grabensprung. In Höhe der Braunsdorfstraße wechselte der Radfahrer auf den äußerst rechten Fahrstreifen, wobei es zum Zusammenstoß mit einem Toyota-Fahrer kam, der mit seinem Wagen in die gleiche Richtung unterwegs war."

CORRECT OUTPUT:
{
  "schema_version": "0.2",
  "source": { "dataset": "Berlin Police Reports", "source_id": "<will be filled by script>" },
  "classification": { "scenario_type": "longitudinal", "confidence": 1.0 },
  "location": { "primary_road": "Alt-Biesdorf", "secondary_road": null, "house_number_reference": null },
  "road_context": { "bike_facility_type": null, "bike_facility_position": null },
  "participants": [
    { "id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "change_lane_left_to_right", "initial_direction": null, "heading_reference": "toward Grabensprung", "road_position": "leftmost_motor_lane" },
    { "id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "go_straight", "initial_direction": null, "heading_reference": null, "road_position": null }
  ],
  "conflict": { "conflict_mechanism": "cyclist_lane_change_into_car_path", "collision_happened": true, "collision_description": "A cyclist changed from the leftmost to the rightmost of three lanes and collided with a car traveling in the same direction." }
}

RULE ACROSS ALL EXAMPLES: every field is either a direct quote-level match or explicitly null.
Never write the string "unknown". Add nothing beyond this structure — no lane counts,
coordinates, speeds, OSM queries, or simulator parameters.
"""

SYSTEM_PROMPT = SYSTEM_PROMPT + "\n" + FEWSHOT_EXAMPLES


_TURNING_MANEUVERS = {"turn_right", "turn_left", "turn_right_into_parking"}
_ENUM_FIELDS = {
    "initial_direction": SCHEMA["directions"],
    "road_position": SCHEMA["road_positions"],
}
_ROAD_CONTEXT_ENUM_FIELDS = {
    "bike_facility_type": SCHEMA["bike_facility_types"],
    "bike_facility_position": SCHEMA["bike_facility_positions"],
}


def _sanitize_enum_fields(extracted: dict) -> None:
    """Null out any allowed-list field whose value isn't actually in that
    list. The model occasionally invents non-enum strings (e.g. "same as
    final", "torstraße" for initial_direction, "turn_left_into_parking" —
    not a real value, a pattern-completion from turn_right_into_parking —
    for maneuver) instead of picking from the list or using null — a
    hallucinated value is worse than null downstream, since nothing
    consumes it expecting free text.
    """
    for participant in extracted.get("participants", []):
        for field, allowed in _ENUM_FIELDS.items():
            value = participant.get(field)
            if value is not None and value not in allowed:
                participant[field] = None

        # maneuver: invalid values degrade to the recognizable prefix rather
        # than being discarded wholesale — e.g. "turn_left_into_parking" (a
        # real bug: invented by pattern-completion from turn_right_into_parking,
        # with no parking lot anywhere in that report) still correctly tells
        # us the participant turned left; only the fabricated "_into_parking"
        # part is ungrounded. Falls back to "unknown" (not null) only when
        # nothing recognizable remains — "unknown" is a legitimate value in
        # this specific enum, unlike the null-for-"not stated" convention
        # used everywhere else, and downstream code does str(maneuver).lower()
        # without a None guard.
        maneuver = participant.get("maneuver")
        if maneuver is not None and maneuver not in SCHEMA["maneuvers"]:
            if maneuver.startswith("turn_left"):
                participant["maneuver"] = "turn_left"
            elif maneuver.startswith("turn_right"):
                participant["maneuver"] = "turn_right"
            elif maneuver.startswith("change_lane"):
                participant["maneuver"] = "change_lane"
            else:
                participant["maneuver"] = "unknown"

    road_context = extracted.get("road_context", {})
    for field, allowed in _ROAD_CONTEXT_ENUM_FIELDS.items():
        value = road_context.get(field)
        if value is not None and value not in allowed:
            road_context[field] = None


def _enforce_turning_definition(extracted: dict) -> None:
    """scenario_type=turning is fully determined by the motor vehicle's own
    maneuver (see SYSTEM_PROMPT's SCENARIO_TYPE rule) — but the model can
    still self-contradict, extracting maneuver=turn_right for the vehicle
    while classifying scenario_type=crossing in the same response. Enforce
    the rule in Python using data the model already produced, rather than
    trusting it to cross-check its own two fields consistently.
    """
    for participant in extracted.get("participants", []):
        if participant.get("class") == "motor_vehicle" and participant.get("maneuver") in _TURNING_MANEUVERS:
            extracted.setdefault("classification", {})["scenario_type"] = "turning"
            break


# ── Agent 1 function ───────────────────────────────────────────────────────────
def extract_scenario(report_text: str, scenario_id: str) -> dict:
    """
    Calls the LLM to extract structured SEMANTIC scenario information from a
    German police report — no simulator-specific parameters, no OSM/query
    fields, nothing computed in Python except source_id/raw_text (which
    Python already knows authoritatively — no reason to trust the LLM to
    reproduce them) and two safety nets: nulling out hallucinated non-enum
    values, and enforcing the turning definition against the model's own
    self-contradictions. Anything the next pipeline stage needs beyond this
    (OSM query strings, etc.) is that stage's own responsibility.
    """
    client = get_client()

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract the scenario from this Berlin police report:\n\n{report_text.strip()}"}
        ],
        temperature=0.0,
        max_tokens=800,
        response_format={"type": "json_object"}
    )

    raw = response.choices[0].message.content
    extracted = json.loads(raw)

    # source_id/raw_text: Python already has the authoritative values, no
    # reason to trust (or even ask) the LLM to reproduce them — this is what
    # let turning_01's raw_text get corrupted to a literal few-shot placeholder.
    extracted["source"]["source_id"] = scenario_id
    extracted["source"]["raw_text"] = report_text.strip()

    _sanitize_enum_fields(extracted)
    _enforce_turning_definition(extracted)

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
