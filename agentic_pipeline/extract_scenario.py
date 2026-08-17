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
    # Qualitative speed evidence, per participant — same vocabulary Agent 3
    # (speed_estimation.py) already concretizes into an actual km/h value.
    # Extracted here, not left to Agent 3's own text re-parsing, because
    # only Agent 1 reads raw_text at all (see extract_scenario.py's own
    # module docstring and the project's standing rule on this).
    "speed_evidence_relations": [
        "stopped", "clearly_slower_than_context", "slower_than_context",
        "approximately_contextual", "faster_than_context", "clearly_faster_than_context",
    ],
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
- Traffic control state is NEVER extracted or represented in any field, even truthfully — this covers
  BOTH traffic lights/signals (LSA, "rote/grüne Ampel") AND right-of-way/yield rules (Vorfahrt,
  "Vorfahrtregelung", yield/priority signs like "Z.205", running a red light, violating right of way).
  Ignore any such mention entirely, including when a participant legitimately had the right of way.
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
  "Richtung X" (toward X), "nach X" (toward X, when X is a place — not "nach rechts/links"), or
  "in Höhe von X" (near X) — extremely common in these reports, and NOT a compass direction. If a
  participant has more than one such phrase, use the one closer to the collision, not the first
  one mentioned. Do this check even when initial_direction is already filled for that
  participant, and even when the OTHER participant already has a heading_reference — check both
  participants independently every time. Write it in ENGLISH, short — "Richtung Torstraße"
  becomes "toward Torstraße", "in Höhe der Braunsdorfstraße" becomes "near Braunsdorfstraße" —
  never copy the German phrase verbatim. Null only if truly nothing like this is stated.
- speed_evidence (per participant, ONLY about that participant's own speed — never the other
  participant's, even if the report describes it): "deutlich überhöht(e/er)"/"raste"=
  clearly_faster_than_context, "überhöht"/"zu schnell"=faster_than_context, "langsam"/"gering(e)"=
  slower_than_context, "sehr langsam"/"deutlich zu langsam"=clearly_slower_than_context, explicitly
  came to a stop mid-maneuver (NOT the same as maneuver=stop/wait/parked, which is stationary the
  whole time)=stopped. speed_evidence_quote: the exact German phrase as it appears in the report
  (verbatim substring, copy it exactly — do NOT translate it here, unlike heading_reference), or
  null if speed_evidence is null. Never set speed_evidence without a literal phrase to quote.

ALLOWED VALUES — scenario_type:{_pipe(SCHEMA['scenario_types'])} participant_type:{_pipe(SCHEMA['participant_types'])}
maneuver:{_pipe(SCHEMA['maneuvers'])} bike_facility_type:{_pipe(SCHEMA['bike_facility_types'])}
directions:{_pipe(SCHEMA['directions'])} road_position:{_pipe(SCHEMA['road_positions'])}
bike_facility_position:{_pipe(SCHEMA['bike_facility_positions'])} speed_evidence:{_pipe(SCHEMA['speed_evidence_relations'])}

STRUCTURE (fill exactly this, nothing more — no osm_query/city/lane counts/speeds/coordinates):
{{ "schema_version": "0.2",
  "source": {{ "dataset": "Berlin Police Reports", "source_id": "<script fills>" }},
  "classification": {{ "scenario_type": "<allowed>", "confidence": <0.0-1.0> }},
  "location": {{ "primary_road": "<or null>", "secondary_road": "<or null>", "house_number_reference": "<or null>" }},
  "road_context": {{ "bike_facility_type": "<allowed or null>", "bike_facility_position": "<allowed or null>" }},
  "participants": [
    {{ "id": "<truck_1/car_1/bus_1>", "class": "motor_vehicle", "type": "<allowed>", "maneuver": "<allowed>",
       "initial_direction": "<allowed or null>", "heading_reference": "<short English phrase or null>", "road_position": "<allowed or null>",
       "speed_evidence": "<allowed or null>", "speed_evidence_quote": "<verbatim German phrase or null>" }},
    {{ "id": "cyclist_1", "class": "cyclist", "type": "<bicycle/e_bike>", "maneuver": "<allowed>",
       "initial_direction": "<allowed or null>", "heading_reference": "<short English phrase or null>", "road_position": "<allowed or null>",
       "speed_evidence": "<allowed or null>", "speed_evidence_quote": "<verbatim German phrase or null>" }}
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
    { "id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "turn_right", "initial_direction": "south", "heading_reference": "toward Köpenick", "road_position": null, "speed_evidence": null, "speed_evidence_quote": null },
    { "id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "go_straight", "initial_direction": null, "heading_reference": null, "road_position": null, "speed_evidence": null, "speed_evidence_quote": null }
  ],
  "conflict": { "conflict_mechanism": "right_turn_across_shared_path", "collision_happened": true, "collision_description": "A car turning right struck a cyclist going straight on the shared path." }
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
    { "id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "go_straight", "initial_direction": "west", "heading_reference": null, "road_position": null, "speed_evidence": "clearly_faster_than_context", "speed_evidence_quote": "mit deutlich überhöhter Geschwindigkeit" },
    { "id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "go_straight", "initial_direction": "north", "heading_reference": null, "road_position": null, "speed_evidence": null, "speed_evidence_quote": null }
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
    { "id": "cyclist_1", "class": "cyclist", "type": "bicycle", "maneuver": "change_lane_left_to_right", "initial_direction": null, "heading_reference": "toward Grabensprung", "road_position": "leftmost_motor_lane", "speed_evidence": null, "speed_evidence_quote": null },
    { "id": "car_1", "class": "motor_vehicle", "type": "car", "maneuver": "go_straight", "initial_direction": null, "heading_reference": null, "road_position": null, "speed_evidence": null, "speed_evidence_quote": null }
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
    "speed_evidence": SCHEMA["speed_evidence_relations"],
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
                # speed_evidence_quote is meaningless without a valid
                # category to attach to — clear it too so a later stage
                # never sees an orphaned quote with no relation.
                if field == "speed_evidence":
                    participant["speed_evidence_quote"] = None

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


# ── Deterministic evidence checks (Phase 5) ─────────────────────────────────
# These operate on raw_text directly rather than trusting the LLM's own
# judgment — compass words, "Richtung X"/"in Höhe von X" phrases, and
# numbered-lane phrases are all small, fixed, mechanically checkable German
# patterns. Built after a live batch run showed the LLM inconsistently
# hallucinating or missing exactly these three fields; see gold_reference.py
# for the specific cases each function was verified against.

_COMPASS_STEMS = {
    # German has two unrelated spellings per direction: the noun ("Norden",
    # no umlaut) and the adjective ("nördlich", WITH umlaut — a genuinely
    # different substring, not just an inflection of "nord"). Missing this
    # cost turning_01 its entire grounding for "north" ("nördliche Richtung").
    "north": (r"nord(?!ost|west)", r"nördlich"),
    "south": (r"süd(?!ost|west)",),
    "east": (r"(?<!nord)(?<!süd)ost", r"(?<!nord)(?<!süd)östlich"),
    "west": (r"(?<!nord)(?<!süd)west",),
    "northeast": (r"nordost", r"nordöstlich"),
    "northwest": (r"nordwest",),
    "southeast": (r"südost", r"südöstlich"),
    "southwest": (r"südwest",),
}
_COMPASS_PATTERNS = {
    direction: re.compile("|".join(stems), re.IGNORECASE) for direction, stems in _COMPASS_STEMS.items()
}
# Exact compass words (not stems) — for deciding whether a captured
# "Richtung X" destination IS a compass direction (belongs in
# initial_direction) rather than a place name that merely CONTAINS a compass
# stem as a substring (e.g. "Südostallee" — a real street name, must not be
# treated as if the report just said "southeast").
_COMPASS_EXACT_WORDS = {
    "norden", "süden", "osten", "westen", "nordosten", "nordwesten", "südosten", "südwesten",
    "nördlich", "südlich", "östlich", "westlich", "nordöstlich", "nordwestlich", "südöstlich", "südwestlich",
}


def _validate_direction_grounding(extracted: dict) -> None:
    """Null out initial_direction unless its compass word actually appears
    somewhere in raw_text. A hallucinated-but-valid compass value (e.g.
    turning_04/06's ungrounded "north", crossing_08's direction bouncing
    between "south"/"north" across runs — neither ever stated) isn't caught
    by _sanitize_enum_fields since it's a real enum member, just unsupported.
    Deliberately loose about WHICH participant the word belongs to (only
    checks presence anywhere in the text) — that's what makes it safe to run
    unconditionally: it can only null a value, never invent one.
    """
    raw_text = extracted.get("source", {}).get("raw_text", "") or ""
    for participant in extracted.get("participants", []):
        direction = participant.get("initial_direction")
        if direction is None:
            continue
        pattern = _COMPASS_PATTERNS.get(direction)
        if pattern is None or not pattern.search(raw_text):
            participant["initial_direction"] = None


_COMPASS_WORD_TO_DIRECTION = {
    "norden": "north", "nördlich": "north",
    "süden": "south", "südlich": "south",
    "osten": "east", "östlich": "east",
    "westen": "west", "westlich": "west",
    "nordosten": "northeast", "nordöstlich": "northeast",
    "nordwesten": "northwest", "nordwestlich": "northwest",
    "südosten": "southeast", "südöstlich": "southeast",
    "südwesten": "southwest", "südwestlich": "southwest",
}
_COMPASS_WORD_PATTERN = re.compile(
    "|".join(sorted(_COMPASS_WORD_TO_DIRECTION, key=len, reverse=True)), re.IGNORECASE
)


def _backfill_initial_direction(extracted: dict) -> None:
    """Same position-based approach as heading_reference/road_position, for
    plain compass words. _validate_direction_grounding only NULLS values the
    model already produced — it can't fill a real gap like crossing_03's
    car, where "Richtung Südosten" is explicit but the model left
    initial_direction null entirely. Only fills gaps (matches
    _validate_direction_grounding running first in the same pass, so a
    hallucinated value is already cleared before this runs).
    """
    raw_text = extracted.get("source", {}).get("raw_text", "") or ""
    sentences = re.split(r"(?<=[.!?])\s+", raw_text)

    by_class = {"cyclist": [], "motor_vehicle": []}
    for sentence in sentences:
        lowered = sentence.lower()
        cyclist_positions = [lowered.find(m) for m in _CYCLIST_MARKERS if m in lowered]
        motor_positions = [lowered.find(m) for m in _MOTOR_MARKERS if m in lowered]
        cyclist_pos = min(cyclist_positions) if cyclist_positions else None
        motor_pos = min(motor_positions) if motor_positions else None

        for match in _COMPASS_WORD_PATTERN.finditer(sentence):
            match_pos = match.start()
            candidate_classes = []
            if cyclist_pos is not None and cyclist_pos < match_pos:
                candidate_classes.append("cyclist")
            if motor_pos is not None and motor_pos < match_pos:
                candidate_classes.append("motor_vehicle")
            if len(candidate_classes) == 1:
                direction = _COMPASS_WORD_TO_DIRECTION[match.group(0).lower()]
                by_class[candidate_classes[0]].append(direction)

    for participant in extracted.get("participants", []):
        if participant.get("initial_direction") is not None:
            continue
        cls = participant.get("class")
        if by_class.get(cls):
            participant["initial_direction"] = by_class[cls][0]


def _validate_speed_evidence_grounding(extracted: dict) -> None:
    """Null out a participant's speed_evidence (and its quote) unless
    speed_evidence_quote actually appears verbatim in raw_text. Same
    principle as _validate_direction_grounding, and the same lesson learned
    live-verifying Agent 3's own speed classifier (turning_03: the model
    claimed "stopped" quoting the word "stopped", which appears nowhere in
    the actual text) — a non-empty quote string is not proof of anything;
    only a real substring match is. Deliberately loose about attribution
    (doesn't re-check which participant the quote "should" belong to,
    only whether it's real at all) — that's what makes it safe to run
    unconditionally: it can only null a value, never invent one.
    """
    raw_text = (extracted.get("source", {}).get("raw_text", "") or "").lower()
    for participant in extracted.get("participants", []):
        relation = participant.get("speed_evidence")
        if relation is None:
            continue
        quote = participant.get("speed_evidence_quote")
        if not isinstance(quote, str) or not quote.strip() or quote.strip().lower() not in raw_text:
            participant["speed_evidence"] = None
            participant["speed_evidence_quote"] = None


_SPEED_EVIDENCE_PATTERN = re.compile(r"überhöht\w*", re.IGNORECASE)


def _backfill_speed_evidence(extracted: dict) -> None:
    """Same position-based approach as _backfill_initial_direction, for the
    one speed-evidence pattern verified live to be dropped inconsistently:
    the model captured "mit deutlich überhöhter Geschwindigkeit" into
    crossing_04's speed_evidence/collision_description but silently
    dropped the structurally identical phrase for crossing_03 — same
    wording, inconsistent extraction, not caught by
    _validate_speed_evidence_grounding since it only nulls unsupported
    claims, it can't fill a gap where the model left speed_evidence null
    entirely. Only fills gaps (matches _backfill_initial_direction running
    after its own validation pass), and only when attribution to a single
    participant class is unambiguous (a class marker must appear BEFORE
    the match in the same sentence, not just anywhere in it — a naive
    "anywhere" check misattributes crossing_03's case, whose sentence also
    mentions "Radfahrenden" later on).

    Deliberately narrow: this is a backstop for one verified gap, not a
    general speed-evidence extractor — that's the LLM's job, done directly
    in the main extraction call now that speed_evidence is part of the
    schema. Widen the pattern only against real, verified cases found the
    same way this one was, never speculatively.
    """
    raw_text = extracted.get("source", {}).get("raw_text", "") or ""
    sentences = re.split(r"(?<=[.!?])\s+", raw_text)

    matched_class = None
    matched_quote = None
    for sentence in sentences:
        match = _SPEED_EVIDENCE_PATTERN.search(sentence)
        if not match:
            continue
        lowered = sentence.lower()
        match_pos = match.start()
        cyclist_positions = [lowered.find(m) for m in _CYCLIST_MARKERS if m in lowered]
        motor_positions = [lowered.find(m) for m in _MOTOR_MARKERS if m in lowered]
        cyclist_pos = min(cyclist_positions) if cyclist_positions else None
        motor_pos = min(motor_positions) if motor_positions else None
        candidates = []
        if cyclist_pos is not None and cyclist_pos < match_pos:
            candidates.append("cyclist")
        if motor_pos is not None and motor_pos < match_pos:
            candidates.append("motor_vehicle")
        if len(candidates) == 1:
            matched_class = candidates[0]
            matched_quote = match.group(0)
            break

    if matched_class is None:
        return

    for participant in extracted.get("participants", []):
        if participant.get("class") == matched_class and participant.get("speed_evidence") is None:
            participant["speed_evidence"] = "clearly_faster_than_context"
            participant["speed_evidence_quote"] = matched_quote
            break


_HEADING_PATTERN = re.compile(
    r"(?:Richtung|nach)\s+"
    r"([A-ZÄÖÜ][\wÄÖÜäöüß-]*(?:\s+(?:den|der|die)\s+[A-ZÄÖÜ][\wÄÖÜäöüß-]*|\s+[A-ZÄÖÜ][\wÄÖÜäöüß-]*)*)"
)
_HEADING_DESTINATION_DENYLIST = {
    "rad", "radfahrer", "radfahrerin", "radfahrende", "person", "fahrer", "fahrerin",
    "wechsellicht", "licht", "kollision", "unfall", "fahrbahn", "kreuzung", "seine", "ihre",
}
_CYCLIST_MARKERS = ("rad fahr", "radfahr", "fahrrad", "pedelec", "e-bike", "ebike")
_MOTOR_MARKERS = ("pkw", "lkw", " auto", "fahrzeug", "wagen", "lastwagen", "lastkraftwagen", "toyota")


def _extract_heading_candidates(raw_text: str) -> list:
    """Find 'Richtung X'/'nach X' destination phrases and, conservatively,
    which participant class each belongs to. ('in Höhe von/der X' is
    deliberately not handled here — it's genuinely ambiguous between "near
    this location" [describing where the collision happened] and "this
    participant's own path".) 'nach X' is included alongside 'Richtung X'
    (turning_09's "bog sie nach rechts nach Köpenick ab", crossing_03's
    "Richtung Südosten nach Müggelheim" — both destinations were missed
    entirely when only "Richtung" was matched) — the exact-compass-word
    check below already keeps this safe from swallowing "nach Süden"/"nach
    Norden" style compass phrases (those belong to initial_direction, not
    here), and "nach rechts"/"nach links" (maneuver direction, lowercase)
    never match since the pattern requires a capitalized word after it.

    Attribution prefers a participant marker appearing BEFORE the phrase in
    the same sentence, not just anywhere in it — turning_06's second
    sentence names the cyclist only via the pronoun "sie" (continuing from
    the previous sentence) and introduces "Lkw-Fahrer" only afterward; a
    same-sentence-anywhere check would misattribute the cyclist's own
    "Richtung Alte Schönhauser" to the truck. When a sentence has NO marker
    for either class before the match (pure pronoun continuation — same
    "sie" case, but also turning_09's "Dort bog sie nach rechts nach
    Köpenick ab", which has no participant marker anywhere in that
    sentence), fall back to whichever class's marker most recently appeared
    in an EARLIER sentence — carried forward only when the current sentence
    doesn't itself introduce a same-sentence marker (ambiguous same-sentence
    cases are never guessed at). Candidates are returned in text order; the
    caller takes the LAST candidate per class, not the first — when a
    participant has two destination phrases (turning_06's cyclist has both
    "Richtung Torstraße" and, moments later, "Richtung Alte Schönhauser"),
    the one stated closer to the collision is more relevant than an earlier one.
    """
    candidates = []
    sentences = re.split(r"(?<=[.!?])\s+", raw_text)
    last_class = None
    for sentence in sentences:
        lowered = sentence.lower()
        cyclist_positions = [lowered.find(m) for m in _CYCLIST_MARKERS if m in lowered]
        motor_positions = [lowered.find(m) for m in _MOTOR_MARKERS if m in lowered]
        cyclist_pos = min(cyclist_positions) if cyclist_positions else None
        motor_pos = min(motor_positions) if motor_positions else None

        for match in _HEADING_PATTERN.finditer(sentence):
            destination = match.group(1).strip().rstrip(".,;")
            words = destination.split()
            first_word = words[0].lower() if words else ""
            if first_word in _HEADING_DESTINATION_DENYLIST:
                continue
            # a bare compass word ("Richtung Westen") belongs to
            # initial_direction, not heading_reference — exact-word check,
            # not substring: "Südostallee" is a street name that merely
            # CONTAINS the "southeast" stem, it does not equal the word.
            if len(words) == 1 and destination.lower() in _COMPASS_EXACT_WORDS:
                continue

            match_pos = match.start()
            candidate_classes = []
            if cyclist_pos is not None and cyclist_pos < match_pos:
                candidate_classes.append("cyclist")
            if motor_pos is not None and motor_pos < match_pos:
                candidate_classes.append("motor_vehicle")
            if len(candidate_classes) == 1:
                candidates.append((candidate_classes[0], f"toward {destination}"))
            elif len(candidate_classes) == 0 and last_class is not None:
                candidates.append((last_class, f"toward {destination}"))

        # carry the most recently mentioned participant class forward into
        # the next sentence — whichever marker appears LAST in this
        # sentence is the current subject a following pronoun-only sentence
        # would continue.
        last_mentions = []
        if cyclist_pos is not None:
            last_mentions.append((max(lowered.rfind(m) for m in _CYCLIST_MARKERS if m in lowered), "cyclist"))
        if motor_pos is not None:
            last_mentions.append((max(lowered.rfind(m) for m in _MOTOR_MARKERS if m in lowered), "motor_vehicle"))
        if last_mentions:
            last_class = max(last_mentions)[1]

    return candidates


def _backfill_heading_references(extracted: dict) -> None:
    """Deterministically fill/correct heading_reference from raw_text
    instead of relying on the model to both notice the phrase and attribute
    it to the right participant — a live batch run showed it doing neither
    reliably (missing ~7 of ~13 applicable cases, and once attaching the
    right phrase to the wrong participant in turning_06). Overrides rather
    than only fills gaps, specifically because of that misattribution case —
    which means that once ANY destination phrase is found in the text (for
    either participant), every participant's heading_reference becomes fully
    determined by raw_text, including nulling a class with no grounded
    candidate of its own: turning_06's truck_1 had no destination phrase
    attributed to it (only cyclist_1's was grounded), yet an earlier version
    of this function only assigned when by_class[cls] was non-empty,
    silently leaving the LLM's hallucinated copy of the cyclist's own
    heading on the truck untouched.

    When a class has more than one candidate (turning_06's cyclist has both
    "Richtung Torstraße" and "Richtung Alte Schönhauser"), the LAST one
    wins — the destination stated closest to the collision is the more
    useful reference point, not whichever was mentioned first.

    But when the text has NO destination phrase at all for EITHER
    participant, this function still leaves the LLM's own value alone
    (early return) — there's no grounding signal to check against one way
    or the other in that case, and test_extract_scenario_schema.py encodes
    this as a deliberate invariant: heading_reference is free text that must
    survive untouched absent a reason to distrust it, unlike initial_direction/
    road_position which are always sanitized against an enum.
    """
    raw_text = extracted.get("source", {}).get("raw_text", "") or ""
    candidates = _extract_heading_candidates(raw_text)
    if not candidates:
        return

    by_class = {"cyclist": [], "motor_vehicle": []}
    for cls, rendered in candidates:
        by_class[cls].append(rendered)

    for participant in extracted.get("participants", []):
        cls = participant.get("class")
        values = by_class.get(cls)
        participant["heading_reference"] = values[-1] if values else None


_ROAD_POSITION_PATTERN = re.compile(
    r"den\s+(äußerst\s+link\w*|link\w*|mittler\w*|äußerst\s+recht\w*|recht\w*)\s+Fahrstreifen",
    re.IGNORECASE,
)


def _road_position_from_phrase(phrase: str) -> str:
    phrase = phrase.lower()
    if "recht" in phrase:
        return "rightmost_motor_lane"
    if "mittler" in phrase:
        return "middle_motor_lane"
    return "leftmost_motor_lane"


def _backfill_road_position(extracted: dict) -> None:
    """Same approach as heading_reference, for explicit numbered-lane
    phrases ("den rechten/linken/mittleren Fahrstreifen") — crossing_08's
    lane assignments for both participants were explicitly stated in the
    text and extracted correctly in one run, then lost in a later one, with
    no prompt change that should have affected this field. road_position
    feeds real lane-placement logic downstream, so losing an explicit,
    unambiguous statement is worth a deterministic backstop, same as
    heading_reference. Only fills gaps (does not override) — unlike
    heading_reference we have no observed misattribution case for this
    field, so overriding a model-provided value isn't justified yet.
    """
    raw_text = extracted.get("source", {}).get("raw_text", "") or ""
    sentences = re.split(r"(?<=[.!?])\s+", raw_text)

    by_class = {"cyclist": [], "motor_vehicle": []}
    for sentence in sentences:
        lowered = sentence.lower()
        cyclist_positions = [lowered.find(m) for m in _CYCLIST_MARKERS if m in lowered]
        motor_positions = [lowered.find(m) for m in _MOTOR_MARKERS if m in lowered]
        cyclist_pos = min(cyclist_positions) if cyclist_positions else None
        motor_pos = min(motor_positions) if motor_positions else None

        for match in _ROAD_POSITION_PATTERN.finditer(sentence):
            # Attribute only to a participant class whose marker was already
            # introduced EARLIER in the sentence than this phrase — a marker
            # appearing later usually belongs to a different sub-clause about
            # a different subject. (Caught a real regression: longitudinal_02's
            # Fahrstreifen phrase precedes "Pkw", which belongs to a later
            # relative clause about what happened to the cyclist, not the
            # car's own lane — whole-sentence marker presence alone
            # misattributed it. crossing_08 is the opposite order — "Pkw"
            # is introduced first, then "der den linken Fahrstreifen..."
            # correctly describes that same Pkw.)
            match_pos = match.start()
            candidates = []
            if cyclist_pos is not None and cyclist_pos < match_pos:
                candidates.append("cyclist")
            if motor_pos is not None and motor_pos < match_pos:
                candidates.append("motor_vehicle")
            if len(candidates) == 1:
                by_class[candidates[0]].append(_road_position_from_phrase(match.group(1)))

    for participant in extracted.get("participants", []):
        if participant.get("road_position") is not None:
            continue
        cls = participant.get("class")
        if by_class.get(cls):
            participant["road_position"] = by_class[cls][0]


_INTERSECTION_PATTERN = re.compile(
    r"Kreuzung(?:sbereich)?\s+([A-ZÄÖÜ].+?/.+?)(?=\s+\bein\b|\s+\bmit\b|$)"
)


def _backfill_intersection(extracted: dict) -> None:
    """location.intersection: only filled when the report explicitly names
    a multi-way intersection via "Kreuzung X/Y" or "Kreuzungsbereich X/Y/Z"
    phrasing (crossing_03's "Kreuzung Waldnesselweg/Erwin-Bock Str.",
    crossing_08's "Kreuzungsbereich Unter den Eichen / Drakestraße /
    Habelschwerdter Allee") — primary_road/secondary_road can only hold two
    roads, so a genuinely 3+-way named intersection has nowhere else to go.
    User decision: this is a search hint for OSM/template selection only,
    not meant to reproduce exact intersection geometry — so unlike
    primary_road/secondary_road, it's fine for this field to hold more than
    two road names verbatim. Requires a literal "/" between road names (the
    multi-way marker), so it doesn't fire on an ordinary single-road
    "Kreuzung X" mention (turning_06's "die Kreuzung der Torstraße") or a
    bare "eine Kreuzung" with no road named at all (crossing_07).
    """
    raw_text = extracted.get("source", {}).get("raw_text", "") or ""
    match = _INTERSECTION_PATTERN.search(raw_text)
    extracted.setdefault("location", {})["intersection"] = match.group(1).strip() if match else None


def _clear_multi_road_secondary(extracted: dict) -> None:
    """secondary_road holds exactly one road; a value containing "/" means
    it was crammed with multiple road names (crossing_03's pre-intersection-
    field output: "Waldnesselweg/Erwin-Bock Str.") — that content belongs in
    location.intersection now (see _backfill_intersection), so null it here
    rather than leave a malformed multi-road string in a single-road field.
    Deliberately keyed on the LITERAL "/" character, not on overlap with
    intersection's own value — crossing_08's secondary_road ("Drakestraße")
    is legitimately a single road that also happens to be named inside its
    own location.intersection string, and must NOT be cleared just because
    it's a substring of that field.
    """
    location = extracted.get("location", {})
    secondary = location.get("secondary_road")
    if secondary and "/" in secondary:
        location["secondary_road"] = None


_FACILITY_IMPLYING_TERMS = (
    "cycle_path", "cycle_track", "cycle_crossing", "bike_lane", "bikelane",
)
_SIGNAL_TERMS = (
    "red_light", "redlight", "rotlicht", "ampel", "traffic_light", "signal",
    "yield_sign", "right_of_way", "priority_sign", "vorfahrt",
)


def _sanitize_conflict_mechanism(extracted: dict) -> None:
    """conflict_mechanism is free-text snake_case, so it can silently
    contradict a structured field (claiming a cycle facility exists when
    bike_facility_type is null — turning_02/04/05) or leak forbidden content
    (traffic control state — signal state per turning_08, and per user
    decision also right-of-way/yield-sign violations, even when truthfully
    stated) without any enum check catching it. Strips the offending
    token(s) rather than rejecting the whole field, since the rest of the
    label is typically still accurate.
    """
    conflict = extracted.get("conflict", {})
    mechanism = conflict.get("conflict_mechanism")
    if not mechanism:
        return

    forbidden = list(_SIGNAL_TERMS)
    if extracted.get("road_context", {}).get("bike_facility_type") is None:
        forbidden += list(_FACILITY_IMPLYING_TERMS)

    cleaned = mechanism
    for term in forbidden:
        pattern = re.compile(r"_?" + re.escape(term) + r"_?", re.IGNORECASE)
        cleaned = pattern.sub("_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if cleaned and cleaned != mechanism:
        conflict["conflict_mechanism"] = cleaned


_BIKE_FACILITY_MARKERS = (
    "radweg", "schutzstreifen", "geh- und radweg", "radverkehrsfurt",
    "mittelstreifen", "nebenfahrbahn", "radfahrstreifen", "radfahrschutzstreifen",
)
_BIKE_FACILITY_SIDE_PATTERNS = {
    "left": re.compile(r"link(?:e|en|s)?", re.IGNORECASE),
    "right": re.compile(r"recht(?:e|en|s)?", re.IGNORECASE),
    "middle": re.compile(r"mittler\w*|mitte", re.IGNORECASE),
    "rightmost": re.compile(r"äußerst\s+recht\w*", re.IGNORECASE),
}


def _validate_bike_facility_position_grounding(extracted: dict) -> None:
    """Null out bike_facility_position unless a side word ("links/rechts/
    mittleren") actually co-occurs, in the same sentence, with a bike-facility
    keyword — every one of the 19 gold-verified reports has this field null
    (turning_01's "left" was a live hallucination: its raw_text never states
    which side the separated cycle track is on, it just names the facility
    and a compass direction). A loose "does the word appear anywhere in
    raw_text" check (the style used for initial_direction) doesn't work here
    specifically, because "links"/"rechts" show up constantly in unrelated
    contexts — turning maneuvers ("bog nach rechts ab"), numbered driving
    lanes — so presence alone doesn't mean it describes the facility's side.
    """
    road_context = extracted.get("road_context", {})
    position = road_context.get("bike_facility_position")
    if position is None:
        return

    pattern = _BIKE_FACILITY_SIDE_PATTERNS.get(position)
    if pattern is None:
        road_context["bike_facility_position"] = None
        return

    raw_text = extracted.get("source", {}).get("raw_text", "") or ""
    sentences = re.split(r"(?<=[.!?])\s+", raw_text)
    grounded = any(
        any(marker in sentence.lower() for marker in _BIKE_FACILITY_MARKERS) and pattern.search(sentence)
        for sentence in sentences
    )
    if not grounded:
        road_context["bike_facility_position"] = None


_SAME_DIRECTION_GROUNDING_TERMS = ("gleiche richtung", "gleicher richtung", "dieselbe richtung", "selbe richtung")
_SAME_DIRECTION_CLAIM_PATTERN = re.compile(r"\b(?:\w+ing\s+)?in\s+the\s+same\s+direction\b", re.IGNORECASE)
_SAME_DIRECTION_MECHANISM_PATTERN = re.compile(r"_?same_direction_?", re.IGNORECASE)


def _sanitize_ungrounded_same_direction_claim(extracted: dict) -> None:
    """collision_description/conflict_mechanism can assert two participants
    travel "in the same direction" as a plausible-sounding but ungrounded
    inference — crossing_07's cyclist and car come from two unrelated
    streets (Codex catch, verified: nothing in raw_text states this). Only
    strip the claim when raw_text itself contains no German "same direction"
    phrase (gleiche/gleicher/dieselbe/selbe Richtung) — longitudinal_01/02
    make this exact claim legitimately, grounded in "die gleiche Richtung"/
    "dieselbe Richtung" in their own raw_text, so this must not be a blanket
    ban the way signal-state terms are in _sanitize_conflict_mechanism.
    """
    raw_text = (extracted.get("source", {}).get("raw_text", "") or "").lower()
    if any(term in raw_text for term in _SAME_DIRECTION_GROUNDING_TERMS):
        return

    conflict = extracted.get("conflict", {})

    mechanism = conflict.get("conflict_mechanism")
    if mechanism:
        cleaned = _SAME_DIRECTION_MECHANISM_PATTERN.sub("_", mechanism)
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        if cleaned and cleaned != mechanism:
            conflict["conflict_mechanism"] = cleaned

    description = conflict.get("collision_description")
    if description:
        cleaned = _SAME_DIRECTION_CLAIM_PATTERN.sub("", description)
        cleaned = re.sub(r"\s+([.,;])", r"\1", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        if cleaned and cleaned != description:
            conflict["collision_description"] = cleaned


_FORBIDDEN_PROSE_PATTERNS = (
    re.compile(r"\bfailed to observe (?:a|the) (?:red light|traffic signal|yield sign)\b", re.IGNORECASE),
    re.compile(r"\b(?:ran|ignored|disregarded) (?:a|the) (?:red light|traffic signal)\b", re.IGNORECASE),
    re.compile(r"\bwho had (?:the )?right of way\b", re.IGNORECASE),
    re.compile(r"\bright of way\b", re.IGNORECASE),
    re.compile(r"\byield sign\b", re.IGNORECASE),
)


def _sanitize_forbidden_prose(extracted: dict) -> None:
    """collision_description is free prose, so a true fact the project
    still forbids representing (signal state, right-of-way/yield violations
    — see the SYSTEM_PROMPT's RULES section) can leak in as a fluent
    sentence rather than an obviously-wrong token, unlike conflict_mechanism
    where _sanitize_conflict_mechanism's snake_case token stripping already
    catches it. Unconditional, unlike _sanitize_ungrounded_same_direction_claim
    — this is forbidden even when true (turning_09's real report does state
    "ohne die Vorfahrtregelung ... zu beachten" and a real run rendered that
    truthfully as "failed to observe a yield sign ... who had right of way",
    which must still not appear).
    """
    conflict = extracted.get("conflict", {})
    description = conflict.get("collision_description")
    if not description:
        return

    cleaned = description
    for pattern in _FORBIDDEN_PROSE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s+([.,;])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if cleaned and cleaned != description:
        conflict["collision_description"] = cleaned


def _clear_redundant_parking_heading(extracted: dict) -> None:
    """turn_right_into_parking already captures "turns into the parking
    lot" structurally — a heading_reference restating that as "toward the
    parking lot" (turning_07: a real LLM run produced exactly this) is
    redundant, not a genuine additional destination, and isn't grounded in
    any "Richtung X"/"nach X" phrase either — the raw text says "auf den
    dortigen Parkplatz abbog" ("auf", not "Richtung"/"nach"), so
    _extract_heading_candidates correctly finds nothing here and leaves the
    LLM's own value untouched (per the "no signal to check" invariant) —
    this narrow check catches the one specific case where that invariant
    lets an ungrounded-but-plausible-sounding value through anyway.
    """
    for participant in extracted.get("participants", []):
        if participant.get("maneuver") != "turn_right_into_parking":
            continue
        heading = participant.get("heading_reference")
        if heading and "park" in heading.lower():
            participant["heading_reference"] = None


def _downgrade_ungrounded_lane_change_direction(extracted: dict) -> None:
    """change_lane_left_to_right/right_to_left claim a specific starting
    lane side — if road_position (the participant's starting numbered-lane
    position) isn't itself grounded, the directional claim has no support
    either, even though it's a valid enum value. Matches the gold_reference
    judgment call for longitudinal_02: the cyclist starts on a Schutzstreifen
    (not a numbered motor lane), so "left_to_right" isn't meaningful there,
    unlike longitudinal_01 where leftmost_motor_lane is explicit.
    """
    for participant in extracted.get("participants", []):
        maneuver = participant.get("maneuver")
        if maneuver in ("change_lane_left_to_right", "change_lane_right_to_left") and participant.get("road_position") is None:
            participant["maneuver"] = "change_lane"


# ── Agent 1 function ───────────────────────────────────────────────────────────
def extract_scenario(report_text: str, scenario_id: str) -> dict:
    """
    Calls the LLM to extract structured SEMANTIC scenario information from a
    German police report — no simulator-specific parameters, no OSM/query
    fields. Python owns: source_id/raw_text (already known authoritatively),
    enum sanitization, the turning-definition self-consistency check, and a
    set of deterministic evidence checks (Phase 5) for fields with small,
    fixed, mechanically-checkable German patterns — compass directions,
    "Richtung X"/"in Höhe von X" phrases, and numbered-lane statements —
    since a live batch run showed the LLM inconsistently hallucinating or
    missing exactly these. Anything the next pipeline stage needs beyond
    this (OSM query strings, etc.) is that stage's own responsibility.
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
    _validate_direction_grounding(extracted)
    _backfill_initial_direction(extracted)
    _validate_speed_evidence_grounding(extracted)
    _backfill_speed_evidence(extracted)
    _backfill_heading_references(extracted)
    _backfill_road_position(extracted)
    _backfill_intersection(extracted)
    _clear_multi_road_secondary(extracted)
    _validate_bike_facility_position_grounding(extracted)
    _clear_redundant_parking_heading(extracted)
    _downgrade_ungrounded_lane_change_direction(extracted)
    _sanitize_conflict_mechanism(extracted)
    _sanitize_ungrounded_same_direction_claim(extracted)
    _sanitize_forbidden_prose(extracted)

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
