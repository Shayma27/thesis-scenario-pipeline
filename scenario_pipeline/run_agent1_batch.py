#!/usr/bin/env python3
"""Run Agent 1 on all 18 police reports and save JSONs to input/."""
import sys, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from extract_scenario import extract_scenario

INPUT_DIR = Path(__file__).parent / "input"
INPUT_DIR.mkdir(exist_ok=True)

REPORTS = [
    ("right_turn_mollstr_0405",
     "Eine LKW fahrende Person bog nach rechts in die Mollstraße ab, ohne auf eine in gleicher Richtung Rad fahrende Person zu achten. Es kam zum Zusammenstoß. Datum: Sa, 27.07.2024 Uhrzeit: 04:05 Uhr"),

    ("right_turn_gutschmidt_1215",
     "Eine Lkw fahrende Person befuhr die Gutschmidtstraße in Richtung Westen und bog nach rechts auf den Buckower Damm Richtung Britzer Damm ab. Dabei kam es in Höhe der Radverkehrsfuhrt zum Zusammenstoß mit einer E-Bike fahrenden Person, die aus der Gutschmidtstraße kommend geradeaus auf die Kreuzung fuhr. Datum: Di, 18.03.2025 Uhrzeit: 12:15 Uhr"),

    ("right_turn_spandauer_damm_1500",
     "Eine Radfahrerin befuhr den Spandauer Damm in Richtung Otto-Suhr-Allee. Ein in derselben Richtung fahrender Pkw bog nach rechts in die Sophie-Charlotten-Straße ab, wobei es zur Kollision kam. Datum: 17.09.2022 Uhrzeit: 15:00 Uhr"),

    ("right_turn_kiefholzstr_1150",
     "Ein Lastwagen samt Anhänger befuhr die Kiefholzstraße in Richtung Südostallee. Beim Rechtsabbiegen in den Dammweg übersah der Fahrer eine Radfahrerin, wodurch es zur Kollision kam. Datum: 13.09.2022 Uhrzeit: 11:50 Uhr"),

    ("right_turn_schoenhauser_1000",
     "Eine Radfahrerin befuhr die Schönhauser Straße in Richtung Torstraße. Als sie auf die Kreuzung der Torstraße in Richtung Alte Schönhauser fuhr, erfasste sie ein Lkw-Fahrer, der zunächst in gleicher Richtung fahrend dort nach rechts in die Torstraße abbog. Datum: 13.10.2021 Uhrzeit: 10:00 Uhr"),

    ("left_turn_storkower_0212",
     "Eine Rad fahrende Person befuhr die Storkower Straße in Richtung Osten und übersah beim Linksabbiegen einen in gleicher Richtung fahrenden Pkw. Es kam zum Zusammenstoß. Datum: Fr, 31.05.2024 Uhrzeit: 02:12 Uhr"),

    ("left_turn_oranienburger_1820",
     "Eine Rad fahrende Person befuhr den Radfahrschutzstreifen der Oranienburger Straße nach Norden und wollte nach links in den Taldorfer Weg abbiegen, ohne auf den nachfolgenden in gleicher Richtung fahrenden Pkw zu achten. Es kam zum Zusammenstoß. Datum: Fr, 01.11.2024 Uhrzeit: 18:20 Uhr"),

    ("left_turn_reinickendorfer_1420",
     "Ein Pkw befuhr die Reinickendorfer Straße und wollte links in die Pankstraße abbiegen. Ein Radfahrer überfuhr eine rote Ampel, wodurch es zum Zusammenstoß kam. Datum: 14.09.2022 Uhrzeit: 14:20 Uhr"),

    ("midblock_mueggelheimer_2100",
     "Eine Pkw fahrende Person befuhr den Müggelheimer Damm mit deutlich überhöhter Geschwindigkeit Richtung Südosten nach Müggelheim und kollidierte an der Kreuzung Waldnesselweg/Erwin-Bock Str. mit einer unachtsam die Fahrbahn querenden Gruppe von Radfahrenden. Datum: Do, 27.06.2024 Uhrzeit: 21:00 Uhr"),

    ("midblock_landsberger_0007",
     "Eine Rad fahrende Person querte an einer Querungshilfe unachtsam die stadteinwärts führende Richtungsfahrbahn der Landsberger Allee vom begrünten Mittelstreifen kommend nach Norden. Dabei wurde sie von einer Pkw fahrenden Person ungebremst erfasst, die auf der Landsberger Allee Richtung Westen mit deutlich überhöhter Geschwindigkeit fuhr. Datum: Di, 26.11.2024 Uhrzeit: 00:07 Uhr"),

    ("priority_falkenseer_2100",
     "Der Radfahrer befuhr mit seinem Fahrrad den Falkenseer Damm in Richtung Falkenseer Chaussee und wendete an der Einmündung Askanierring in Richtung des südlichen Fahrradweges. Dabei missachtete er den entgegenkommenden Pkw-Fahrer, der die Falkenseer Chaussee in Richtung Falkenseer Damm befuhr. Datum: Mo 02.01.2023 Uhrzeit: 21:00 Uhr"),

    ("priority_sundgauer_1100",
     "Eine Pkw fahrende Person befuhr die Sundgauer Straße vom Lützelsteiner Weg zur Clayallee nordwestwärts. An der Kreuzung Sundgauer Straße/Clayallee kam es zum Zusammenstoß mit einer vorfahrtberechtigten Rad fahrenden Person, die auf dem Radweg der Clayallee nach Norden fuhr. Datum: Sa, 14.06.2025 Uhrzeit: 11:00 Uhr"),

    ("priority_mueggelhort_0800",
     "Eine Pkw fahrende Person fuhr auf der Straße zum Müggelhort nach Süden zum Müggelheimer Damm. Dort bog sie nach rechts nach Köpenick ab, ohne die Vorfahrtregelung durch Z.205 zu beachten. Sie übersah beim Abbiegen eine Rad fahrenden Person, die auf dem gemeinsamen Geh- und Radweg des Müggelheimer Damm vorfahrtberechtigt war. Datum: Mo, 03.11.2025 Uhrzeit: 08:00 Uhr"),

    ("enter_roadway_muehlenstr_2328",
     "Eine Pkw fahrende Person befuhr den äußerst linken Fahrstreifen der Mühlenstr. als eine Rad fahrende Person unvermittelt vom Gehweg auf Höhe der Hausnummer 89 auf die Fahrbahn fuhr. Es kam zum Zusammenstoß. Datum: Sa, 09.11.2024 Uhrzeit: 23:28 Uhr"),

    ("enter_roadway_rathausstr_1545",
     "Ein Pedelec-Fahrer befuhr die Rathausstraße von der Poststraße kommend in Richtung Spreeufer. Ein Pkw Fahrer fuhr vom rechten Fahrbahnrand an und übersah den Pedelec-Fahrer. Es kam zur Kollision. Datum: 05.09.2021 Uhrzeit: 15:45 Uhr"),

    ("lane_change_altbiesdorf_0310",
     "Ein Radfahrer befuhr den linken der drei Fahrstreifen auf der Straße Alt-Biesdorf von der Lötschbergstraße kommend in Richtung Grabensprung. In Höhe der Braunsdorfstraße wechselte der Radfahrer auf den äußerst rechten Fahrstreifen, wobei es zum Zusammenstoß mit einem Toyota-Fahrer kam. Datum: 17.10.2021 Uhrzeit: 03:10 Uhr"),

    ("lane_change_markgrafendamm_1745",
     "Eine Radfahrerin befuhr den Markgrafendamm auf dem Schutzstreifen für Fahrradfahrende in Richtung Hauptstraße. Kurz nach der Einmündung zur Persiusstraße wechselte sie nach links in den danebenliegenden rechten Fahrstreifen, wo sie von einem Pkw erfasst wurde. Datum: 18.09.2021 Uhrzeit: 17:45 Uhr"),

    ("overtaking_odernheimer_1958",
     "Ein Busfahrer fuhr die Odernheimer Str. aus Richtung Neuhelgoländer Weg kommend in Richtung Alt Müggelheim. In Höhe der Odernheimer Str. 22 überholte der Busfahrer eine Radfahrerin, welche in die gleiche Richtung fuhr. Es kam zum Zusammenstoß der beiden Fahrzeuge, wodurch die Radfahrerin stürzte und von dem Bus überrollt wurde. Datum: Fr., 29.09.2023 Uhrzeit: 19:58 Uhr"),
]

ok, failed = [], []
for i, (scenario_id, report_text) in enumerate(REPORTS):
    out_path = INPUT_DIR / f"{scenario_id}.json"
    try:
        result = extract_scenario(report_text, scenario_id)
        out_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        stype = result.get("classification", {}).get("scenario_type", "?")
        conf  = result.get("classification", {}).get("confidence", 0)
        print(f"[{i+1:02d}/18] OK  {scenario_id}  ({stype}, conf={conf})")
        ok.append(scenario_id)
    except Exception as exc:
        print(f"[{i+1:02d}/18] ERR {scenario_id}: {exc}")
        failed.append((scenario_id, str(exc)))
    time.sleep(0.5)   # respect Groq rate limit

print(f"\nDone: {len(ok)} OK, {len(failed)} failed")
if failed:
    for sid, err in failed:
        print(f"  FAILED {sid}: {err}")
