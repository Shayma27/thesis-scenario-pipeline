"""
gold_reference.py — manually verified expected semantic values for the 19
reports in docs/manual_classification_reference.md, checked field-by-field
against source.raw_text.

This is the baseline test_semantic_correctness.py compares against. It is
NOT auto-generated and NOT copied from any single extraction run — every
value here was individually checked against the report text it comes from.
Where a report is genuinely ambiguous, the field is set to SKIP rather than
guessed at, and the reason is in the scenario's "notes".

Two review passes went into this:
  1. A full manual field-by-field audit (this session, cross-referencing
     input/*.json against report_loader's raw report text).
  2. A second-opinion review (via Codex, external), which independently
     caught several things the first pass missed: conflict_mechanism
     silently claiming a cycle facility exists when bike_facility_type is
     null, an unsupported "same direction" claim in crossing_07's
     collision_description, and crossing_03's secondary_road merging two
     street names into one field.

Sentinel used in this file only (not a real schema value):
    SKIP — this field is genuinely ambiguous from the text; do not test it,
    see the scenario's "notes" for why.
"""

SKIP = "__SKIP__"

# Terms that must NEVER appear in conflict_mechanism or collision_description,
# in ANY scenario — the project's own rule (extract_scenario.py's RULES
# section) is that signal state is never extracted or represented in any
# field. turning_08 currently violates this ("...from_red_light").
GLOBAL_FORBIDDEN_TERMS = [
    "red_light", "redlight", "rotlicht", "ampel", "traffic_light",
    "traffic signal", "grüne ampel", "rote ampel", "green light", "grünes licht",
]

# Terms that imply a dedicated bike facility exists — forbidden in
# conflict_mechanism/collision_description whenever gold bike_facility_type
# is None, since that would silently contradict the structured field.
# (This is what caught turning_02/turning_04/turning_05.)
BIKE_FACILITY_IMPLYING_TERMS = [
    "cycle_path", "cycle track", "cycle_track", "cycle path",
    "bike_lane", "bike lane", "bikelane", "cycle_crossing", "cycle crossing",
]

GOLD = {
    # ── TURNING ──────────────────────────────────────────────────────────
    "turning_01": {
        "scenario_type": "turning",
        "location": {"primary_road": "Salvador-Allende-Str.", "secondary_road": "Müggelschlößchenweg"},
        "road_context": {"bike_facility_type": "separated_cycle_track", "bike_facility_position": None},
        "participants": {
            "truck_1": {"type": "truck", "maneuver": "turn_right", "initial_direction": "north", "heading_reference": None, "road_position": None},
            "cyclist_1": {"type": "bicycle", "maneuver": "go_straight", "initial_direction": "north", "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "bike_facility_position must be null — no side is ever stated. "
                 "Directions are grounded: cyclist explicit 'nördliche Richtung', "
                 "truck inferred via 'in gleicher Richtung' relative to the cyclist.",
    },
    "turning_02": {
        "scenario_type": "turning",
        "location": {"primary_road": "Mollstraße", "secondary_road": None},
        "road_context": {"bike_facility_type": None, "bike_facility_position": None},
        "participants": {
            "truck_1": {"type": "truck", "maneuver": "turn_right", "initial_direction": None, "heading_reference": None, "road_position": None},
            "cyclist_1": {"type": "bicycle", "maneuver": "go_straight", "initial_direction": None, "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "No bike facility is mentioned at all — conflict_mechanism must not "
                 "claim a cycle path/track exists (Codex catch: a real run produced "
                 "'right_turn_across_cycle_path' here, contradicting bike_facility_type=null).",
    },
    "turning_03": {
        "scenario_type": "turning",
        "location": {"primary_road": "Buckower Damm", "secondary_road": "Gutschmidtstraße"},
        "road_context": {"bike_facility_type": "cycle_crossing", "bike_facility_position": None},
        "participants": {
            "truck_1": {"type": "truck", "maneuver": "turn_right", "initial_direction": "west", "heading_reference": None, "road_position": None},
            "cyclist_1": {"type": "e_bike", "maneuver": "go_straight", "initial_direction": None, "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "'Radverkehrsfuhrt' (bike crossing) explicitly named -> cycle_crossing is "
                 "correctly grounded here, unlike turning_02/04/05's bare hallucination. "
                 "Truck direction explicit ('Richtung Westen').",
    },
    "turning_04": {
        "scenario_type": "turning",
        "location": {"primary_road": "Spandauer Damm", "secondary_road": "Sophie-Charlotten-Straße"},
        "road_context": {"bike_facility_type": None, "bike_facility_position": None},
        "participants": {
            "car_1": {"type": "car", "maneuver": "turn_right", "initial_direction": None, "heading_reference": None, "road_position": None},
            "cyclist_1": {"type": "bicycle", "maneuver": "go_straight", "initial_direction": None, "heading_reference": "toward Otto-Suhr-Allee", "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "'Richtung Otto-Suhr-Allee' is a destination phrase, NOT a compass word — "
                 "initial_direction must stay null, the destination belongs in "
                 "heading_reference instead. No bike facility mentioned -> conflict_mechanism "
                 "must not claim one.",
    },
    "turning_05": {
        "scenario_type": "turning",
        "location": {"primary_road": "Kiefholzstraße", "secondary_road": "Dammweg"},
        "road_context": {"bike_facility_type": None, "bike_facility_position": None},
        "participants": {
            "truck_1": {"type": "truck", "maneuver": "turn_right", "initial_direction": None, "heading_reference": "toward Südostallee", "road_position": None},
            "cyclist_1": {"type": "bicycle", "maneuver": "go_straight", "initial_direction": None, "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "'Richtung Südostallee' is a street name (destination), not a compass word "
                 "despite containing 'Süd/Ost' as a substring. No bike facility mentioned.",
    },
    "turning_06": {
        "scenario_type": "turning",
        "location": {"primary_road": "Schönhauser Straße", "secondary_road": "Torstraße"},
        "road_context": {"bike_facility_type": None, "bike_facility_position": None},
        "participants": {
            "cyclist_1": {"type": "bicycle", "maneuver": "go_straight", "initial_direction": None, "heading_reference": "toward Torstraße", "road_position": None},
            "truck_1": {"type": "truck", "maneuver": "turn_right", "initial_direction": None, "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "'Richtung Torstraße' belongs to the CYCLIST ('Eine Radfahrerin befuhr die "
                 "Schönhauser Straße in Richtung Torstraße') — a real run misattributed it to "
                 "the truck instead, and separately hallucinated truck initial_direction=north "
                 "with zero compass word anywhere in the text. There is a second destination "
                 "phrase, 'Richtung Alte Schönhauser', also the cyclist's, describing the same "
                 "onward path — heading_reference only holds one value, 'toward Torstraße' is "
                 "preferred as the more specific/immediate reference.",
    },
    "turning_07": {
        "scenario_type": "turning",
        "location": {"primary_road": "Malteserstraße", "secondary_road": None, "house_number_reference": "139"},
        "road_context": {"bike_facility_type": "separated_cycle_track", "bike_facility_position": None},
        "participants": {
            "truck_1": {"type": "truck", "maneuver": "turn_right_into_parking", "initial_direction": "south", "heading_reference": None, "road_position": None},
            "cyclist_1": {"type": "bicycle", "maneuver": "go_straight", "initial_direction": "south", "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "Fully grounded: 'südliche Richtung' explicit for cyclist, truck inferred "
                 "same direction; 'auf den dortigen Parkplatz abbog' explicitly grounds "
                 "turn_right_into_parking.",
    },
    "turning_08": {
        "scenario_type": "turning",
        "location": {"primary_road": "Reinickendorfer Straße", "secondary_road": "Pankstraße"},
        "road_context": {"bike_facility_type": None, "bike_facility_position": None},
        "participants": {
            "car_1": {"type": "car", "maneuver": "turn_left", "initial_direction": None, "heading_reference": None, "road_position": None},
            "cyclist_1": {"type": "bicycle", "maneuver": "go_straight", "initial_direction": None, "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "Report explicitly mentions the cyclist ran a red light — this MUST NOT "
                 "appear in conflict_mechanism/collision_description per the project's own "
                 "signal-exclusion rule, even descriptively. A real run produced "
                 "'cyclist_crosses_vehicle_path_from_red_light', which violates this.",
    },
    "turning_09": {
        "scenario_type": "turning",
        "location": {"primary_road": "Müggelheimer Damm", "secondary_road": "Straße zum Müggelhort"},
        "road_context": {"bike_facility_type": "shared_foot_cycle_path", "bike_facility_position": None},
        "participants": {
            "car_1": {"type": "car", "maneuver": "turn_right", "initial_direction": "south", "heading_reference": None, "road_position": None},
            "cyclist_1": {"type": "bicycle", "maneuver": "go_straight", "initial_direction": None, "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "Fully grounded: 'nach Süden' explicit for the car; 'gemeinsamen Geh- und "
                 "Radweg' explicitly grounds shared_foot_cycle_path.",
    },

    # ── CROSSING ─────────────────────────────────────────────────────────
    "crossing_01": {
        "scenario_type": "crossing",
        "location": {"primary_road": "Mühlenstr.", "secondary_road": None, "house_number_reference": "89"},
        "road_context": {"bike_facility_type": "sidewalk", "bike_facility_position": None},
        "participants": {
            "car_1": {"type": "car", "maneuver": "go_straight", "initial_direction": None, "heading_reference": None, "road_position": "leftmost_motor_lane"},
            "cyclist_1": {"type": "bicycle", "maneuver": "enter_roadway", "initial_direction": None, "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "Fully grounded: 'äußerst linken Fahrstreifen' -> leftmost_motor_lane; "
                 "cyclist came 'vom Gehweg' (sidewalk) -> enter_roadway maneuver is apt.",
    },
    "crossing_02": {
        "scenario_type": "crossing",
        "location": {"primary_road": "Rathausstraße", "secondary_road": "Poststraße"},
        "road_context": {"bike_facility_type": None, "bike_facility_position": None},
        "participants": {
            "cyclist_1": {"type": "e_bike", "maneuver": "go_straight", "initial_direction": None, "heading_reference": "toward Spreeufer", "road_position": None},
            "car_1": {"type": "car", "maneuver": "enter_roadway", "initial_direction": None, "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "'in Richtung Spreeufer' is explicit and belongs in heading_reference. "
                 "car_1.maneuver='enter_roadway' for 'fuhr vom rechten Fahrbahnrand an' "
                 "(pulled away from the curb) is a defensible approximation given the "
                 "available enum — not flagged as wrong.",
    },
    "crossing_03": {
        "scenario_type": "crossing",
        "location": {"primary_road": "Müggelheimer Damm", "secondary_road": SKIP},
        "road_context": {"bike_facility_type": None, "bike_facility_position": None},
        "participants": {
            "car_1": {"type": "car", "maneuver": "go_straight", "initial_direction": "southeast", "heading_reference": None, "road_position": None},
            "cyclist_1": {"type": "bicycle", "maneuver": "go_straight", "initial_direction": None, "heading_reference": "toward Erwin-Bock-Straße", "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "secondary_road is SKIPPED deliberately: the report names a real 3-street "
                 "intersection ('Kreuzung Waldnesselweg/Erwin-Bock Str.'), and our schema only "
                 "has room for one secondary_road. The current value jams both into one "
                 "string ('Waldnesselweg/Erwin-Bock Str.') — plausible but not clearly right "
                 "or wrong; needs a human decision on how (or whether) to represent a 3-way "
                 "intersection, not a mechanical fix. car_1 direction is explicit "
                 "('Richtung Südosten' = southeast) and was missed in every run so far.",
    },
    "crossing_04": {
        "scenario_type": "crossing",
        "location": {"primary_road": "Landsberger Allee", "secondary_road": None},
        "road_context": {"bike_facility_type": "median_strip", "bike_facility_position": None},
        "participants": {
            "car_1": {"type": "car", "maneuver": "go_straight", "initial_direction": "west", "heading_reference": None, "road_position": None},
            "cyclist_1": {"type": "bicycle", "maneuver": "go_straight", "initial_direction": "north", "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "Fully grounded, matches our own few-shot example. Checked Codex's concern "
                 "about 'begrünter Mittelstreifen' being confused with a traffic-light color: "
                 "current collision_description says 'green median strip', a normal English "
                 "rendering of 'begrünt' (vegetated/planted) — no actual signal-color "
                 "confusion found in real output, so no correction needed here.",
    },
    "crossing_05": {
        "scenario_type": "crossing",
        "location": {"primary_road": "Storkower Straße", "secondary_road": None},
        "road_context": {"bike_facility_type": None, "bike_facility_position": None},
        "participants": {
            "cyclist_1": {"type": "bicycle", "maneuver": "turn_left", "initial_direction": "east", "heading_reference": None, "road_position": None},
            "car_1": {"type": "car", "maneuver": "go_straight", "initial_direction": "east", "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "Fully grounded, including correct subject-attribution of the left turn to "
                 "the cyclist (not the car) — 'übersah beim Linksabbiegen' modifies the "
                 "cyclist, the sentence's subject.",
    },
    "crossing_06": {
        "scenario_type": "crossing",
        "location": {"primary_road": "Oranienburger Straße", "secondary_road": "Taldorfer Weg"},
        "road_context": {"bike_facility_type": "bike_lane", "bike_facility_position": None},
        "participants": {
            "cyclist_1": {"type": "bicycle", "maneuver": "turn_left", "initial_direction": "north", "heading_reference": None, "road_position": None},
            "car_1": {"type": "car", "maneuver": "go_straight", "initial_direction": "north", "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "Fully grounded: 'Radfahrschutzstreifen' -> bike_lane; 'nach Norden' explicit; "
                 "maneuver=turn_left correctly on the cyclist (a real run once hallucinated "
                 "'turn_left_into_parking', not a real value — the recovery fallback degrades "
                 "that to plain turn_left).",
    },
    "crossing_07": {
        "scenario_type": "crossing",
        "location": {"primary_road": "Kaiser-Friedrich-Straße", "secondary_road": "Otto-Suhr-Allee"},
        "road_context": {"bike_facility_type": None, "bike_facility_position": None},
        "participants": {
            "cyclist_1": {"type": "bicycle", "maneuver": "go_straight", "initial_direction": None, "heading_reference": "toward Kaiser-Friedrich-Straße", "road_position": None},
            "car_1": {"type": "car", "maneuver": "go_straight", "initial_direction": None, "heading_reference": "toward Spandauer Damm", "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": ["same direction", "gleiche richtung", "gleicher richtung"],
        "notes": "Codex catch, verified: the cyclist comes from Luisenplatz toward "
                 "Kaiser-Friedrich-Straße; the car comes from Otto-Suhr-Allee toward Spandauer "
                 "Damm — two unrelated streets, nothing states they travel the same direction. "
                 "A real run's collision_description claimed 'traveling in the same direction' "
                 "— unsupported, must not appear. Both participants have explicit destination "
                 "phrases that were missed in every run so far.",
    },
    "crossing_08": {
        "scenario_type": "crossing",
        "location": {"primary_road": "Unter den Eichen", "secondary_road": "Drakestraße"},
        "road_context": {"bike_facility_type": "roadway_mixed", "bike_facility_position": None},
        "participants": {
            "cyclist_1": {"type": "bicycle", "maneuver": "go_straight", "initial_direction": None, "heading_reference": None, "road_position": "rightmost_motor_lane"},
            "car_1": {"type": "car", "maneuver": "go_straight", "initial_direction": None, "heading_reference": "toward Unter den Eichen", "road_position": "leftmost_motor_lane"},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "bike_facility_type=roadway_mixed is correctly grounded here via explicit "
                 "'Nebenfahrbahn'. car_1 direction has bounced between 'south'/'north' across "
                 "runs — neither is stated, only 'in Richtung Unter den Eichen' (destination, "
                 "not compass) -> must be null with heading_reference capturing the "
                 "destination instead. Lane positions are explicit: cyclist 'rechten "
                 "Fahrstreifen' (rightmost), car 'linken Fahrstreifen' (leftmost) — both were "
                 "correctly extracted in one run and then lost in a later one.",
    },

    # ── LONGITUDINAL ─────────────────────────────────────────────────────
    "longitudinal_01": {
        "scenario_type": "longitudinal",
        "location": {"primary_road": "Alt-Biesdorf", "secondary_road": None},
        "road_context": {"bike_facility_type": None, "bike_facility_position": None},
        "participants": {
            "cyclist_1": {"type": "bicycle", "maneuver": "change_lane_left_to_right", "initial_direction": None, "heading_reference": "toward Grabensprung", "road_position": "leftmost_motor_lane"},
            "car_1": {"type": "car", "maneuver": "go_straight", "initial_direction": None, "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "'in Richtung Grabensprung' is explicit and destination-only (no compass "
                 "word) — belongs in heading_reference, currently missing in every run since "
                 "the field was introduced. Lane change is a clean leftmost->rightmost motor "
                 "lane transition, unambiguous.",
    },
    "longitudinal_02": {
        "scenario_type": "longitudinal",
        "location": {"primary_road": "Markgrafendamm", "secondary_road": "Persiusstraße"},
        "road_context": {"bike_facility_type": "bike_lane", "bike_facility_position": None},
        "participants": {
            "cyclist_1": {"type": "bicycle", "maneuver": "change_lane", "initial_direction": None, "heading_reference": "toward Hauptstraße", "road_position": None},
            "car_1": {"type": "car", "maneuver": "go_straight", "initial_direction": None, "heading_reference": None, "road_position": None},
        },
        "collision_happened": True,
        "extra_forbidden": [],
        "notes": "maneuver deliberately set to plain change_lane, not a directional variant: "
                 "the cyclist starts on the Schutzstreifen (bike lane, not a numbered motor "
                 "lane), so 'left_to_right'/'right_to_left' don't cleanly apply — the report's "
                 "own phrasing is directionally confusing ('wechselte nach links' [moved left] "
                 "'in den... rechten Fahrstreifen' [into the right lane]), since bike-lane-"
                 "relative left/right isn't the same coordinate system as motor-lane position. "
                 "Separately, 'in Richtung Hauptstraße' is an explicit destination phrase for "
                 "the cyclist, missed in every run so far -> heading_reference.",
    },
}
