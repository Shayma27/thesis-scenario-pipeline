import argparse
import json
from pathlib import Path

from complete_parameters import complete_parameters
from generate_road import generate_opendrive
from generate_scenario import generate_openscenario
from osm_enrichment import enrich_with_osm
from validate_outputs import validate_generated_files


PROJECT_DIR = Path(__file__).resolve().parents[1]
INPUT_PATH = PROJECT_DIR / "input" / "malteser_139.json"
OUTPUT_DIR = PROJECT_DIR / "output"


def main():
    parser = argparse.ArgumentParser(
        description="Generate OpenDRIVE/OpenSCENARIO files from a traceable scenario JSON."
    )
    parser.add_argument(
        "--input",
        default=str(INPUT_PATH),
        help="Path to the refined police-report scenario JSON.",
    )
    parser.add_argument(
        "--enrich-osm",
        action="store_true",
        help="Query/cache OpenStreetMap context and apply OSM-derived defaults.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate generated XML links, actors, lanes, and trajectories.",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = PROJECT_DIR / input_path

    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if args.enrich_osm:
        data = enrich_with_osm(data, OUTPUT_DIR / "osm_cache")

    data = complete_parameters(data)

    scenario_id = data["source"]["source_id"]

    xodr_path = OUTPUT_DIR / f"{scenario_id}.xodr"
    xosc_path = OUTPUT_DIR / f"{scenario_id}.xosc"
    enriched_json_path = OUTPUT_DIR / f"{scenario_id}.enriched.json"

    if args.enrich_osm:
        enriched_json_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    generate_opendrive(data, xodr_path)
    generate_openscenario(data, xosc_path, xodr_filename=xodr_path.name)

    if args.validate:
        validation = validate_generated_files(data, xodr_path, xosc_path)
        print(validation.format())
        if not validation.ok:
            raise SystemExit(1)

    if args.enrich_osm:
        print(f"Generated: {enriched_json_path}")
    print(f"Generated: {xodr_path}")
    print(f"Generated: {xosc_path}")


if __name__ == "__main__":
    main()
