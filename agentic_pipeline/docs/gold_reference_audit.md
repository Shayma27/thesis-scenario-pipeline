# gold_reference.py — Human Verification Audit

**Purpose:** `gold_reference.py` was written by me (an LLM), with one extra
cross-check pass from another AI tool (Codex). That is not independent
ground truth — proof of that: the Codex pass already caught real mistakes
in my own first read (turning_02/04/05's facility-implying terms,
crossing_07's "same direction" claim). This document exists so a human
(you) can check every field against the actual raw German text yourself,
instead of trusting my notes as an answer key.

**How to use this:** for each scenario, read the raw text block first,
independently of the table below it. Then check the table. Where I've
written "⚠ WORTH CHECKING", that's a field I'm genuinely not fully certain
about, not just formality — read those extra carefully, they're the ones
most likely to be wrong.

---

## turning_01
**Raw:** Der Fahrer wartete in dem von ihm geführten LKW auf der Fahrbahn der Salvador-Allende-Str. vor der Rotlicht abstrahlenden LSA. Als die LSA für seine Richtung grünes Licht abstrahlte, bog er nach rechts in den Müggelschlößchenweg ab und erfasste die neben ihm in gleicher Richtung geradeaus fahrende Radfahrerin. Sie befuhr den von der Fahrbahn baulich getrennten Radweg der Salvador-Allende-Str. in nördliche Richtung. Die LSA für Radfahrende strahlte grünes Wechsellicht ab. Die Radfahrerin und das Fahrrad wurden durch den rechts abbiegenden LKW vollständig überrollt.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | turning | truck's own maneuver is turn_right → forces "turning" by definition, regardless of the cyclist |
| location.primary_road | Salvador-Allende-Str. | "auf der Fahrbahn der Salvador-Allende-Str." |
| location.secondary_road | Müggelschlößchenweg | "bog er nach rechts in den Müggelschlößchenweg ab" |
| road_context.bike_facility_type | separated_cycle_track | "den von der Fahrbahn baulich getrennten Radweg" |
| road_context.bike_facility_position | null | no left/right/middle word describing which side the Radweg is on, anywhere |
| truck_1.type | truck | "LKW" |
| truck_1.maneuver | turn_right | "bog er nach rechts ... ab" |
| truck_1.initial_direction | north | inferred: "in gleicher Richtung" as the cyclist, whose own direction is explicit north |
| truck_1.heading_reference | null | no "Richtung X" phrase attached to the truck specifically |
| cyclist_1.type | bicycle | "Radfahrerin"/"Fahrrad" |
| cyclist_1.maneuver | go_straight | "geradeaus fahrende Radfahrerin" |
| cyclist_1.initial_direction | north | "in nördliche Richtung" explicit |
| cyclist_1.heading_reference | null | no destination phrase, only compass |
| collision_happened | true | "vollständig überrollt" |

⚠ WORTH CHECKING: the traffic light (LSA/Rotlicht/grünes Licht) content is deliberately excluded from every field per the project's own signal-exclusion rule — confirm no run leaks it into conflict_mechanism/collision_description.

---

## turning_02
**Raw:** Eine LKW fahrende Person bog nach rechts in die Mollstraße ab, ohne auf eine in gleicher Richtung Rad fahrende Person zu achten. Es kam zum Zusammenstoß.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | turning | truck turn_right |
| location.primary_road | Mollstraße | only road named at all — "in die Mollstraße ab" |
| location.secondary_road | null | no second road named |
| road_context.bike_facility_type | null | no facility phrase anywhere (Schutzstreifen/Radweg/etc. never appear) |
| road_context.bike_facility_position | null | — |
| truck_1.type | truck | "LKW fahrende Person" |
| truck_1.maneuver | turn_right | "bog nach rechts ... ab" |
| truck_1.initial_direction | null | no compass word anywhere |
| truck_1.heading_reference | null | "in gleicher Richtung" is relational (describes the cyclist relative to the truck), not a destination phrase |
| cyclist_1.type | bicycle | "Rad fahrende Person" |
| cyclist_1.maneuver | go_straight | implied — no turn stated for the cyclist |
| cyclist_1.initial_direction | null | no compass word |
| cyclist_1.heading_reference | null | no destination phrase |
| collision_happened | true | "Es kam zum Zusammenstoß" |

⚠ WORTH CHECKING: conflict_mechanism/collision_description must NOT claim a cycle path/track exists — no facility is named at all here (this is the specific Codex catch: a real run once produced "right_turn_across_cycle_path", contradicting bike_facility_type=null).

---

## turning_03
**Raw:** Eine Lkw fahrende Person befuhr die Gutschmidtstraße in Richtung Westen und bog nach rechts auf den Buckower Damm Richtung Britzer Damm ab. Dabei kam es in Höhe der Radverkehrsfuhrt zum Zusammenstoß mit einer E-Bike fahrenden Person, die aus der Gutschmidtstraße kommend geradeaus auf die Kreuzung fuhr.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | turning | truck turn_right |
| location.primary_road | Buckower Damm | road turned onto, where the collision/cycle crossing is: "bog nach rechts auf den Buckower Damm ... ab", "in Höhe der Radverkehrsfuhrt zum Zusammenstoß" |
| location.secondary_road | Gutschmidtstraße | road both vehicles originally traveled: "befuhr die Gutschmidtstraße", "aus der Gutschmidtstraße kommend" |
| road_context.bike_facility_type | cycle_crossing | "Radverkehrsfuhrt" (sic, matches "Radverkehrsfurt" mapping) |
| road_context.bike_facility_position | null | no side word |
| truck_1.type | truck | "Lkw fahrende Person" |
| truck_1.maneuver | turn_right | "bog nach rechts ... ab" |
| truck_1.initial_direction | west | "in Richtung Westen" — literal compass word |
| truck_1.heading_reference | toward Britzer Damm | "Richtung Britzer Damm" — the truck's own post-turn path ("bog nach rechts auf den Buckower Damm Richtung Britzer Damm ab") |
| cyclist_1.type | e_bike | "E-Bike fahrenden Person" |
| cyclist_1.maneuver | go_straight | "geradeaus ... fuhr" |
| cyclist_1.initial_direction | null | no compass word attached to the e-bike |
| cyclist_1.heading_reference | null | "in Höhe der Radverkehrsfuhrt" describes the COLLISION location, not the e-bike's own path |
| collision_happened | true | "zum Zusammenstoß" |

⚠ WORTH CHECKING: `truck_1.heading_reference` was flipped from null to "toward Britzer Damm" mid-session (see gold_reference.py's own note) — this is exactly the kind of self-correction that should make you double check it again independently rather than trust the "already reconsidered once" framing as extra credibility.

---

## turning_04
**Raw:** Eine Radfahrerin befuhr den Spandauer Damm in Richtung Otto-Suhr-Allee. Ein in derselben Richtung fahrender Pkw bog nach rechts in die Sophie-Charlotten-Straße ab, wobei es zur Kollision kam.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | turning | car turn_right |
| location.primary_road | Spandauer Damm | "befuhr den Spandauer Damm" |
| location.secondary_road | Sophie-Charlotten-Straße | "bog nach rechts in die Sophie-Charlotten-Straße ab" |
| road_context | null/null | no facility phrase |
| car_1.type | car | "Pkw" |
| car_1.maneuver | turn_right | "bog nach rechts ... ab" |
| car_1.initial_direction | null | "in derselben Richtung" is relational; the cyclist it refers to also has no compass value (see below), so nothing propagates |
| car_1.heading_reference | null | "Richtung Otto-Suhr-Allee" belongs grammatically to the cyclist's own sentence, not the car's |
| cyclist_1.type | bicycle | "Radfahrerin" |
| cyclist_1.maneuver | go_straight | implied, no turn stated |
| cyclist_1.initial_direction | null | "Richtung Otto-Suhr-Allee" is a place name, not one of the 8 compass words |
| cyclist_1.heading_reference | toward Otto-Suhr-Allee | "in Richtung Otto-Suhr-Allee" |
| collision_happened | true | "zur Kollision kam" |

---

## turning_05
**Raw:** Ein Lastwagen samt Anhänger befuhr die Kiefholzstraße in Richtung Südostallee. Beim Rechtsabbiegen in den Dammweg übersah der Fahrer eine Radfahrerin, wodurch es zur Kollision kam.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | turning | truck turn_right |
| location.primary_road | Kiefholzstraße | "befuhr die Kiefholzstraße" |
| location.secondary_road | Dammweg | "Rechtsabbiegen in den Dammweg" |
| road_context | null/null | no facility phrase |
| truck_1.type | truck | "Lastwagen samt Anhänger" |
| truck_1.maneuver | turn_right | "Rechtsabbiegen ... in den Dammweg" |
| truck_1.initial_direction | null | "Südostallee" is a proper street name containing "Süd/Ost" as a substring — NOT the literal compass word "südosten"/"südöstlich" |
| truck_1.heading_reference | toward Südostallee | "in Richtung Südostallee" — destination phrase, kept as the real German street name |
| cyclist_1.type | bicycle | "Radfahrerin" |
| cyclist_1.maneuver | go_straight | implied, no turn stated |
| cyclist_1.initial_direction | null | no compass word for cyclist |
| cyclist_1.heading_reference | null | no destination phrase attached to cyclist |
| collision_happened | true | "zur Kollision" |

⚠ WORTH CHECKING: this is the "Südostallee is a street name, not a compass word" case — compare directly against crossing_03 below, where "Südosten" (no "-allee" suffix) IS treated as a real compass word. Read both side by side.

---

## turning_06
**Raw:** Eine Radfahrerin befuhr die Schönhauser Straße in Richtung Torstraße. Als sie auf die Kreuzung der Torstraße in Richtung Alte Schönhauser fuhr, erfasste sie ein Lkw-Fahrer, der zunächst in gleicher Richtung fahrend dort nach rechts in die Torstraße abbog.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | turning | truck turn_right |
| location.primary_road | Schönhauser Straße | "befuhr die Schönhauser Straße" |
| location.secondary_road | Torstraße | "nach rechts in die Torstraße abbog" |
| road_context | null/null | no facility mentioned |
| cyclist_1.type | bicycle | "Radfahrerin" |
| cyclist_1.maneuver | go_straight | "auf die Kreuzung ... fuhr" — entering the intersection, no turn stated for her |
| cyclist_1.initial_direction | null | no compass word anywhere in the text |
| cyclist_1.heading_reference | toward Torstraße | "in Richtung Torstraße" — first destination phrase, grammatical subject is the cyclist ("Eine Radfahrerin befuhr ... in Richtung Torstraße") |
| truck_1.type | truck | "Lkw-Fahrer" |
| truck_1.maneuver | turn_right | "bog ... nach rechts in die Torstraße" |
| truck_1.initial_direction | null | "in gleicher Richtung" is relational to the cyclist, who also has no compass value |
| truck_1.heading_reference | null | both "Richtung Torstraße" and "Richtung Alte Schönhauser" belong to the cyclist's clause; the truck is introduced only afterward ("erfasste sie ein Lkw-Fahrer") |
| collision_happened | true | "erfasste sie ein Lkw-Fahrer" |

⚠ WORTH CHECKING: there's a SECOND destination phrase here, "Richtung Alte Schönhauser", also grammatically the cyclist's (same sentence, continued via "sie") — the schema only holds one heading_reference value, and "toward Torstraße" was picked as more specific/immediate. Is that the right call, or should it be "toward Alte Schönhauser" instead? Genuinely a judgment call, not a fact I can verify mechanically.

**This is the scenario the previous debugging session was about** — a real extraction run had attributed "toward Torstraße" to `truck_1` instead of `cyclist_1`. That specific bug is now fixed in `extract_scenario.py`, but the *value itself* (which destination phrase, attributed to which participant) is still worth your independent read.

---

## turning_07
**Raw:** Die Radfahrerin befuhr die Malteserstraße auf einem baulich von der Fahrbahn getrennten Radweg in südliche Richtung. In selber Richtung fuhr ein Lkw-Fahrer, welcher auf Höhe der Hausnummer 139 über den Radweg hinweg nach rechts auf den dortigen Parkplatz abbog. Während des Abbiegevorganges kam es zur Kollision zwischen Lkw und Fahrrad.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | turning | truck turn_right_into_parking |
| location.primary_road | Malteserstraße | "befuhr die Malteserstraße" |
| location.secondary_road | null | no second road named |
| location.house_number_reference | "139" | "auf Höhe der Hausnummer 139" |
| road_context.bike_facility_type | separated_cycle_track | "baulich von der Fahrbahn getrennten Radweg" |
| road_context.bike_facility_position | null | no side word |
| cyclist_1.type | bicycle | "Radfahrerin" |
| cyclist_1.maneuver | go_straight | no turn stated for cyclist |
| cyclist_1.initial_direction | south | "in südliche Richtung" explicit |
| cyclist_1.heading_reference | null | no destination phrase, only compass |
| truck_1.type | truck | "Lkw-Fahrer" |
| truck_1.maneuver | turn_right_into_parking | "über den Radweg hinweg nach rechts auf den dortigen Parkplatz abbog" — explicit "Parkplatz" grounds the "_into_parking" suffix |
| truck_1.initial_direction | south | inferred: "In selber Richtung fuhr ein Lkw-Fahrer" — same direction as the explicit-south cyclist |
| truck_1.heading_reference | null | no destination phrase for truck |
| collision_happened | true | "kam es zur Kollision" |

---

## turning_08
**Raw:** Ein Pkw befuhr die Reinickendorfer Straße und wollte links in die Pankstraße abbiegen. Ein Radfahrer überfuhr eine rote Ampel, wodurch es zum Zusammenstoß kam.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | turning | car turn_left |
| location.primary_road | Reinickendorfer Straße | "befuhr die Reinickendorfer Straße" |
| location.secondary_road | Pankstraße | "links in die Pankstraße abbiegen" |
| road_context | null/null | no facility mentioned |
| car_1.type | car | "Pkw" |
| car_1.maneuver | turn_left | "wollte links ... abbiegen" |
| car_1.initial_direction | null | no compass word |
| car_1.heading_reference | null | no destination phrase |
| cyclist_1.type | bicycle | "Radfahrer" |
| cyclist_1.maneuver | go_straight | no turn stated for the cyclist |
| cyclist_1.initial_direction | null | — |
| cyclist_1.heading_reference | null | — |
| collision_happened | true | "zum Zusammenstoß kam" |

⚠ WORTH CHECKING, important and counter-intuitive: the report explicitly states the cyclist ran a red light ("überfuhr eine rote Ampel") — a TRUE, stated fact. But the project's own rule says signal state must NEVER appear in ANY field, including conflict_mechanism/collision_description, even descriptively. So a correct output must OMIT this true fact entirely, not just avoid inventing a false one. A real run once produced "cyclist_crosses_vehicle_path_from_red_light" — that violates the rule and must not recur. Confirm you actually agree with the underlying project rule (never represent signal state, even truthfully) — that's a design decision, not something I can validate from the text itself.

---

## turning_09
**Raw:** Eine Pkw fahrende Person fuhr auf der Straße zum Müggelhort nach Süden zum Müggelheimer Damm. Dort bog sie nach rechts nach Köpenick ab, ohne die Vorfahrtregelung durch Z.205 zu beachten. Sie übersah beim Abbiegen eine Rad fahrenden Person, die auf dem gemeinsamen Geh- und Radweg des Müggelheimer Damm vorfahrtberechtigt war. Beide Beteiligte waren nicht in der Lage, die Kollision abzuwenden.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | turning | car turn_right |
| location.primary_road | Müggelheimer Damm | road turned onto, where the shared path is |
| location.secondary_road | Straße zum Müggelhort | "auf der Straße zum Müggelhort" — road the car started on |
| road_context.bike_facility_type | shared_foot_cycle_path | "gemeinsamen Geh- und Radweg" |
| road_context.bike_facility_position | null | — |
| car_1.type | car | "Pkw fahrende Person" |
| car_1.maneuver | turn_right | "bog sie nach rechts ... ab" |
| car_1.initial_direction | south | "nach Süden" explicit |
| car_1.heading_reference | null | see ⚠ below |
| cyclist_1.type | bicycle | "Rad fahrenden Person" |
| cyclist_1.maneuver | go_straight | no turn stated, she has right of way going through |
| cyclist_1.initial_direction | null | — |
| cyclist_1.heading_reference | null | — |
| collision_happened | true | "waren nicht in der Lage, die Kollision abzuwenden" |

⚠ WORTH CHECKING (genuine open question, not settled): the text says "nach Köpenick" (to Köpenick) — a destination, same idea as "Richtung Köpenick" would be. The extraction rule as written only covers "Richtung X" and "in Höhe von X" phrasing; "nach X" is a different construction the rule doesn't literally mention. Is `car_1.heading_reference = null` the right reading of a narrow rule, or is this actually a missed destination phrase that should be "toward Köpenick"? I don't have a confident answer — this is exactly the kind of thing that needs your judgment, not mine.

Also worth checking: "Z.205" is a German traffic-sign reference (right-of-way/yield sign), not a traffic light (LSA/Ampel) — confirm the signal-exclusion rule correctly does NOT apply to it (it isn't excluded from conflict_mechanism/collision_description the way red lights are).

---

## crossing_01
**Raw:** Eine Pkw fahrende Person befuhr den äußerst linken Fahrstreifen der Mühlenstr. als eine Rad fahrende Person unvermittelt vom Gehweg auf Höhe der Hausnummer 89 auf die Fahrbahn fuhr. Es kam zum Zusammenstoß.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | crossing | car goes straight, cyclist enters from sidewalk, crossing its path |
| location.primary_road | Mühlenstr. | "der Mühlenstr." |
| location.secondary_road | null | — |
| location.house_number_reference | "89" | "auf Höhe der Hausnummer 89" |
| road_context.bike_facility_type | sidewalk | "vom Gehweg" — cyclist came FROM the sidewalk |
| road_context.bike_facility_position | null | — |
| car_1.type | car | "Pkw fahrende Person" |
| car_1.maneuver | go_straight | no turn stated |
| car_1.initial_direction | null | no compass word |
| car_1.heading_reference | null | — |
| car_1.road_position | leftmost_motor_lane | "äußerst linken Fahrstreifen" explicit |
| cyclist_1.type | bicycle | "Rad fahrende Person" |
| cyclist_1.maneuver | enter_roadway | "unvermittelt vom Gehweg ... auf die Fahrbahn fuhr" |
| collision_happened | true | "Es kam zum Zusammenstoß" |

⚠ WORTH CHECKING: "sidewalk" for road_context.bike_facility_type is a defensible reading ("Gehweg" = sidewalk), but the SYSTEM_PROMPT's own German-term mapping table only explicitly lists Schutzstreifen/Radweg/Geh-und-Radweg/Radverkehrsfurt/Nebenfahrbahn/Mittelstreifen → their types — "Gehweg" alone isn't literally in that list. Is treating a plain sidewalk (that a cyclist illegally rode from) as "road_context" — i.e. as if it were the cycling facility for this scenario — actually the right modeling choice, or should this be null since a sidewalk-that-a-cyclist-shouldn't-be-on isn't really "the bike facility" in the sense the other 18 scenarios use that field?

---

## crossing_02
**Raw:** Ein Pedelec-Fahrer befuhr die Rathausstraße von der Poststraße kommend in Richtung Spreeufer. Ein Pkw Fahrer fuhr vom rechten Fahrbahnrand an und übersah den Pedelec-Fahrer. Es kam zur Kollision.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | crossing | car entering roadway from the curb crosses the pedelec's path |
| location.primary_road | Rathausstraße | "befuhr die Rathausstraße" |
| location.secondary_road | Poststraße | "von der Poststraße kommend" |
| road_context | null/null | no bike facility mentioned (Pedelec is a vehicle type, not a facility) |
| cyclist_1.type | e_bike | "Pedelec-Fahrer" |
| cyclist_1.maneuver | go_straight | implied |
| cyclist_1.initial_direction | null | "Spreeufer" is a place name, not a compass word |
| cyclist_1.heading_reference | toward Spreeufer | "in Richtung Spreeufer" |
| car_1.type | car | "Pkw Fahrer" |
| car_1.maneuver | enter_roadway | "fuhr vom rechten Fahrbahnrand an" — pulled away from the curb |
| car_1.road_position | null | "rechten Fahrbahnrand" = right edge of the roadway, NOT "rechten Fahrstreifen" (right lane) — different concept, correctly not road_position |
| collision_happened | true | "Es kam zur Kollision" |

⚠ WORTH CHECKING: `car_1.maneuver = enter_roadway` for "fuhr vom rechten Fahrbahnrand an" (pulled away from the curb) is flagged in the original notes as "a defensible approximation given the available enum" — meaning even the person who wrote it wasn't fully confident this is the best available value versus, say, go_straight. Worth your own call.

---

## crossing_03
**Raw:** Eine Pkw fahrende Person befuhr den Müggelheimer Damm mit deutlich überhöhter Geschwindigkeit Richtung Südosten nach Müggelheim und kollidierte an der Kreuzung Waldnesselweg/Erwin-Bock Str. mit einer unachtsam die Fahrbahn querenden Gruppe von Radfahrenden. Eine Rad fahrende Person wurde beim Überqueren des Müggelheimer Damm Richtung Erwin-Bock-Straße vom Pkw erfasst.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | crossing | cyclist(s) cross the road, car goes straight |
| location.primary_road | Müggelheimer Damm | "befuhr den Müggelheimer Damm" |
| location.secondary_road | **SKIP** (not tested) | real intersection is 3-way ("Kreuzung Waldnesselweg/Erwin-Bock Str."), schema only holds one secondary_road — deliberately left untested, flagged as needing a human decision on how to represent a 3-way intersection, not a mechanical bug |
| road_context | null/null | a group of cyclists crossing is not itself a facility |
| car_1.type | car | "Pkw fahrende Person" |
| car_1.maneuver | go_straight | no turn stated |
| car_1.initial_direction | southeast | "Richtung Südosten" — literal compass word (contrast with turning_05's "Südostallee", a street name) |
| car_1.heading_reference | null | see ⚠ below |
| cyclist_1.type | bicycle | "Rad fahrende Person" / "Gruppe von Radfahrenden" |
| cyclist_1.maneuver | go_straight | "beim Überqueren" (while crossing) — closest available enum, no literal "cross" value exists |
| cyclist_1.heading_reference | toward Erwin-Bock-Straße | "Richtung Erwin-Bock-Straße" |
| collision_happened | true | "vom Pkw erfasst" |

⚠ WORTH CHECKING (two real open questions):
1. The report describes a **group** of cyclists ("Gruppe von Radfahrenden"), but only one specific person is later named as struck ("Eine Rad fahrende Person"). Representing this as a single `cyclist_1` — is that the right simplification, or does something get lost?
2. Same "nach X" vs "Richtung X" question as turning_09: "nach Müggelheim" is a destination for the car, but uses "nach" not "Richtung" — is `car_1.heading_reference = null` correct, or should "toward Müggelheim" be captured too?

---

## crossing_04
**Raw:** Eine Rad fahrende Person querte an einer Querungshilfe unachtsam die stadteinwärts führende Richtungsfahrbahn der Landsberger Allee vom begrünten Mittelstreifen kommend nach Norden. Dabei wurde sie von einer Pkw fahrenden Person ungebremst erfasst, die auf der Landsberger Allee Richtung Westen mit deutlich überhöhter Geschwindigkeit fuhr.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | crossing | — |
| location.primary_road | Landsberger Allee | — |
| location.secondary_road | null | — |
| road_context.bike_facility_type | median_strip | "vom begrünten Mittelstreifen kommend" |
| car_1.maneuver | go_straight | no turn stated |
| car_1.initial_direction | west | "Richtung Westen" literal compass |
| cyclist_1.maneuver | go_straight | "querte ... nach Norden" — closest available enum, no "cross" value exists |
| cyclist_1.initial_direction | north | "nach Norden" explicit |
| collision_happened | true | "ungebremst erfasst" |

⚠ WORTH CHECKING: "begrünter Mittelstreifen" = vegetated/planted median strip — this was double-checked earlier in the session specifically to rule out confusion with a traffic-signal color ("green" as in Ampel), since "green" appears in the English rendering ("green median strip") for an unrelated reason. Worth your own confirmation this reads unambiguously as a physical median, not signal-adjacent.

---

## crossing_05
**Raw:** Eine Rad fahrende Person befuhr die Storkower Straße in Richtung Osten und übersah beim Linksabbiegen einen in gleicher Richtung fahrenden Pkw. Es kam zum Zusammenstoß.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | **crossing** (not turning!) | see ⚠ below |
| location.primary_road | Storkower Straße | — |
| cyclist_1.maneuver | turn_left | subject-attribution: "[Rad fahrende Person] ... übersah beim Linksabbiegen einen ... Pkw" — the cyclist is the subject of BOTH "übersah" and "beim Linksabbiegen" |
| cyclist_1.initial_direction | east | "Richtung Osten" literal compass |
| car_1.maneuver | go_straight | "in gleicher Richtung fahrender Pkw" — no turn stated for the car |
| car_1.initial_direction | east | inferred, same direction as the explicit-east cyclist |
| collision_happened | true | "zum Zusammenstoß" |

⚠ WORTH CHECKING, this is the single most important row in this whole document to re-verify: `scenario_type = crossing`, NOT turning — even though the CYCLIST is the one turning left. The project's own schema definition ties "turning" exclusively to the motor vehicle's maneuver, never the cyclist's, specifically to avoid this exact intuition trap. Also re-check the subject-attribution itself: "Linksabbiegen" sits grammatically close to "Pkw" in the sentence, but German subject-verb structure puts the cyclist ("Rad fahrende Person") as the one turning — read it slowly in German, not by which noun a turn phrase happens to sit next to.

---

## crossing_06
**Raw:** Eine Rad fahrende Person befuhr den Radfahrschutzstreifen der Oranienburger Straße nach Norden und wollte nach links in den Taldorfer Weg abbiegen, ohne auf den nachfolgenden in gleicher Richtung fahrenden Pkw zu achten. Es kam zum Zusammenstoß.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | **crossing** (not turning!) | same vehicle-only rule as crossing_05 — cyclist turns, car goes straight |
| location.primary_road | Oranienburger Straße | — |
| location.secondary_road | Taldorfer Weg | "abbiegen ... in den Taldorfer Weg" |
| road_context.bike_facility_type | bike_lane | "Radfahrschutzstreifen" |
| cyclist_1.maneuver | turn_left | "wollte nach links ... abbiegen" — subject is the cyclist |
| cyclist_1.initial_direction | north | "nach Norden" explicit |
| car_1.maneuver | go_straight | "nachfolgenden ... fahrenden Pkw" — following car, no turn |
| car_1.initial_direction | north | inferred, same direction as cyclist |
| collision_happened | true | "zum Zusammenstoß" |

⚠ WORTH CHECKING: real extraction runs have previously hallucinated `maneuver = "turn_left_into_parking"` here (not a real enum value — pattern-completed from turn_right_into_parking, no parking lot anywhere in this text). Confirm the current output shows plain `turn_left`.

---

## crossing_07
**Raw:** Eine Radfahrerin befuhr eine Straße in Charlottenburg vom Luisenplatz in Richtung Kaiser-Friedrich-Straße und fuhr in eine Kreuzung ein. Dabei kam es zur Kollision mit einem Pkw, der von der Otto-Suhr-Allee in Richtung Spandauer Damm unterwegs war.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | crossing | both go_straight; classified crossing because the cyclist's path crosses the car's at the intersection, not via either vehicle turning |
| location.primary_road | Kaiser-Friedrich-Straße | see ⚠ below |
| location.secondary_road | Otto-Suhr-Allee | the car's own named road |
| cyclist_1.maneuver | go_straight | "fuhr in eine Kreuzung ein" — no turn stated |
| cyclist_1.heading_reference | toward Kaiser-Friedrich-Straße | "in Richtung Kaiser-Friedrich-Straße" |
| car_1.maneuver | go_straight | "unterwegs war" — no turn stated |
| car_1.heading_reference | toward Spandauer Damm | "in Richtung Spandauer Damm" |
| collision_happened | true | "kam es zur Kollision" |
| extra_forbidden | "same direction" / "gleiche richtung" / "gleicher richtung" | see below |

⚠ WORTH CHECKING (real ambiguity, not settled): the cyclist's OWN street is never named — only her start point (Luisenplatz, "eine Straße in Charlottenburg" is explicitly unnamed) and her destination (Kaiser-Friedrich-Straße). `location.primary_road = Kaiser-Friedrich-Straße` currently repurposes her *destination* as the location field. Is that the right modeling choice, given the car's road (Otto-Suhr-Allee) is the one actually named as a real street the car is on?

**Also the critical field this whole audit exists to catch:** nothing in this text states the cyclist and car travel in the same direction — she comes from Luisenplatz toward Kaiser-Friedrich-Straße, the car comes from Otto-Suhr-Allee toward Spandauer Damm, two separate, unrelated streets. Compare this directly against longitudinal_01/02 below, where "gleiche/dieselbe Richtung" IS explicitly stated. A real run fabricated "traveling in the same direction" here — confirm you agree these two paths are genuinely unrelated before trusting that "must not appear" is the right call.

---

## crossing_08
**Raw:** Eine Radfahrerin befuhr den rechten Fahrstreifen der Nebenfahrbahn Unter den Eichen und fuhr bei Rot in den Kreuzungsbereich Unter den Eichen / Drakestraße / Habelschwerdter Allee ein. Dabei kam es zur Kollision mit einem Pkw, der den linken Fahrstreifen der Drakestraße in Richtung Unter den Eichen befuhr.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | crossing | both go straight; cyclist crosses against red (signal state itself excluded from output) |
| location.primary_road | Unter den Eichen | — |
| location.secondary_road | Drakestraße | see ⚠ below |
| road_context.bike_facility_type | roadway_mixed | "Nebenfahrbahn" |
| cyclist_1.maneuver | go_straight | "fuhr ... in den Kreuzungsbereich ... ein" — no turn stated |
| cyclist_1.road_position | rightmost_motor_lane | "den rechten Fahrstreifen der Nebenfahrbahn" |
| car_1.maneuver | go_straight | — |
| car_1.initial_direction | null | "Richtung Unter den Eichen" is a street-name destination, not a compass word |
| car_1.heading_reference | toward Unter den Eichen | same phrase, correctly placed here |
| car_1.road_position | leftmost_motor_lane | "den linken Fahrstreifen der Drakestraße" |
| collision_happened | true | "kam es zur Kollision" |

⚠ WORTH CHECKING, two things:
1. This is ALSO a named 3-way intersection ("Unter den Eichen / Drakestraße / Habelschwerdter Allee"), same structural situation as crossing_03 — but here `secondary_road = Drakestraße` is tested normally, NOT skipped like crossing_03's. Is there a real distinguishing reason (e.g. Habelschwerdter Allee is less load-bearing to the collision than Waldnesselweg/Erwin-Bock in crossing_03), or is this an inconsistency between the two scenarios?
2. "bei Rot" (ran the red) must never leak into conflict_mechanism/collision_description — same exclusion rule as turning_08.

---

## longitudinal_01
**Raw:** Ein Radfahrer befuhr den linken der drei Fahrstreifen auf der Straße Alt-Biesdorf von der Lötschbergstraße kommend in Richtung Grabensprung. In Höhe der Braunsdorfstraße wechselte der Radfahrer auf den äußerst rechten Fahrstreifen, wobei es zum Zusammenstoß mit einem Toyota-Fahrer kam, der mit seinem Wagen in die gleiche Richtung unterwegs war.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | longitudinal | car goes straight, cyclist changes lanes alongside it, same general direction |
| location.primary_road | Alt-Biesdorf | — |
| location.secondary_road | null | see ⚠ below |
| cyclist_1.maneuver | change_lane_left_to_right | "den linken der drei Fahrstreifen" (start) → "wechselte ... auf den äußerst rechten Fahrstreifen" (end) — both lanes explicit |
| cyclist_1.heading_reference | toward Grabensprung | "in Richtung Grabensprung" |
| cyclist_1.road_position | leftmost_motor_lane | the STARTING lane, before the change |
| car_1.type | car | "Toyota-Fahrer" |
| car_1.maneuver | go_straight | "in die gleiche Richtung unterwegs war" — no turn |
| collision_happened | true | "zum Zusammenstoß ... kam" |

⚠ WORTH CHECKING: neither "Lötschbergstraße" (origin) nor "Braunsdorfstraße" (a "Höhe von" landmark near the lane change) is captured as `secondary_road` — compare against longitudinal_02 below, where a structurally similar landmark road (Persiusstraße) IS captured. Is that a real distinction ("Einmündung" = a true intersecting road vs. "in Höhe von" = a pure landmark) or an inconsistency?

Also note: "gleiche Richtung" IS explicitly stated here — so "same direction" language in collision_description is legitimate/grounded in this scenario, unlike crossing_07.

---

## longitudinal_02
**Raw:** Eine Radfahrerin befuhr den Markgrafendamm auf dem Schutzstreifen für Fahrradfahrende in Richtung Hauptstraße. Kurz nach der Einmündung zur Persiusstraße wechselte sie nach links in den danebenliegenden rechten Fahrstreifen, wo sie von einem Pkw erfasst wurde, der zu diesem Zeitpunkt den Markgrafendamm in dieselbe Richtung befuhr.

| Field | Value | Evidence |
|---|---|---|
| scenario_type | longitudinal | — |
| location.primary_road | Markgrafendamm | — |
| location.secondary_road | Persiusstraße | "Kurz nach der Einmündung zur Persiusstraße" — see ⚠ above |
| road_context.bike_facility_type | bike_lane | "Schutzstreifen für Fahrradfahrende" |
| cyclist_1.maneuver | change_lane (plain, not directional) | see ⚠ below |
| cyclist_1.heading_reference | toward Hauptstraße | "in Richtung Hauptstraße" |
| cyclist_1.road_position | null | she starts on a Schutzstreifen (bike lane), not a numbered Fahrstreifen — no motor-lane position to report |
| car_1.maneuver | go_straight | "befuhr" — no turn |
| collision_happened | true | "erfasst wurde" |

⚠ WORTH CHECKING: the text literally says "wechselte sie nach links in den ... rechten Fahrstreifen" (changed LEFT into the RIGHT lane) — a genuinely confusing combination, since she starts on a bike lane (not a numbered motor lane), so "left" here isn't in the same coordinate system as the motor-lane road_position enum. `maneuver = change_lane` (the plain, non-directional value) was chosen specifically to avoid asserting a directional claim ("left_to_right"/"right_to_left") that the mismatched coordinate systems don't actually support. Do you agree this is the right call, or does the "rechten Fahrstreifen" she ends up in deserve to be captured as `road_position = rightmost_motor_lane` regardless (it IS a numbered lane, just her *starting* position isn't)?

Also note: "dieselbe Richtung" IS explicitly stated here too — "same direction" language is legitimately grounded, same as longitudinal_01.

---

## Summary of open items worth your explicit sign-off

These are the ones I'm least confident about, ranked by how much they could actually matter to your results:

1. **crossing_05 / crossing_06**: scenario_type=crossing (not turning) when the *cyclist* turns — re-read the schema's vehicle-only rule and confirm you agree with the definition itself, not just that the code follows it consistently.
2. **turning_08 / crossing_08**: true facts (red light run) that must be *omitted entirely*, not just accurately stated — confirm you agree with the project's own signal-exclusion rule as a modeling choice.
3. **turning_09 / crossing_03**: "nach X" destination phrases (not "Richtung X") currently produce `heading_reference = null` — open question whether the extraction rule should cover this construction too.
4. **crossing_03 vs crossing_08**: inconsistent handling of 3-way-named intersections (one SKIPped, one not) — is there a real reason, or should both be treated the same way?
5. **crossing_01**: whether a plain sidewalk a cyclist illegally used counts as "the bike facility" for `road_context.bike_facility_type`, given it's not in the SYSTEM_PROMPT's own explicit mapping table.
6. **longitudinal_01 vs longitudinal_02**: inconsistent secondary_road capture for structurally similar "landmark near a lane change" roads.
