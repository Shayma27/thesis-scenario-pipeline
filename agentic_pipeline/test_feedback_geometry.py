"""
Test: FEEDBACK_SYSTEM_PROMPT geometry correction.

Loads an existing enriched.json, strips the bike facility, then applies
feedback "die Fahrradspur fehlt, bitte hinzufügen" and checks that:
  1. The LLM outputs JSON with primary_has_bike_facility: true
  2. The .xodr is regenerated with a bike lane present
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import AgentState, run_feedback_iteration

ENRICHED = (
    Path(__file__).resolve().parent
    / "output/agentic/20260602_mueggelschloesschenweg"
    / "20260602_mueggelschloesschenweg.enriched.json"
)

REPORT_SNIPPET = (
    "Am 01.06.2026 gegen 14:30 Uhr kam es an der Salvador-Allende-Str / "
    "Müggelschlößchenweg in Berlin zu einem Unfall zwischen einem LKW und "
    "einem Fahrradfahrer."
)

FEEDBACK = "die Fahrradspur fehlt, bitte hinzufügen"


def main() -> None:
    data = json.loads(ENRICHED.read_text(encoding="utf-8"))

    # Strip bike facility so there is something to fix
    sim = data["generated_simulation_parameters"]
    sim["opendrive"]["primary_has_bike_facility"] = False
    sim["opendrive"]["primary_bike_facility_type"] = "none"
    # Cyclist must be on driving lane when no bike lane exists
    sim["openscenario"]["actors"]["cyclist_1"]["initial_lane_id"] = -1

    sid = "feedback_geometry_test"
    state = AgentState(sid)
    state.data = copy.deepcopy(data)
    state.data["source"] = {"source_id": sid}
    state.output_dir.mkdir(parents=True, exist_ok=True)

    # Pre-generate so state.xodr_path / xosc_path are set
    from pipeline import _tool_generate_scenario, _tool_validate_and_fix
    gen = _tool_generate_scenario(state)
    if not gen.get("success"):
        print(f"Pre-generation failed: {gen.get('error')}")
        sys.exit(1)

    print(f"\n{'═' * 70}")
    print(f"  BEFORE feedback: primary_has_bike_facility = False")
    print(f"  Feedback message: {FEEDBACK!r}")
    print(f"{'═' * 70}")

    result = run_feedback_iteration(state, REPORT_SNIPPET, FEEDBACK)

    print(f"\n{'═' * 70}")
    print("  RESULT")
    print(f"{'─' * 70}")
    print(f"  success          : {result.get('success')}")
    print(f"  valid            : {result.get('valid')}")
    print(f"  overrides_applied: {json.dumps(result.get('overrides_applied'), indent=4, ensure_ascii=False)}")
    print(f"  validation_errors: {result.get('validation_errors')}")

    # Check the final state
    final_odr = state.data["generated_simulation_parameters"]["opendrive"]
    bike_on = final_odr.get("primary_has_bike_facility")
    bike_type = final_odr.get("primary_bike_facility_type")
    print(f"\n  After feedback:")
    print(f"    primary_has_bike_facility : {bike_on}")
    print(f"    primary_bike_facility_type: {bike_type}")

    # Confirm in .xodr file (OpenDRIVE uses lane type="biking")
    xodr_text = state.xodr_path.read_text(encoding="utf-8") if state.xodr_path else ""
    bike_in_xodr = 'type="biking"' in xodr_text
    print(f"    bike lane in .xodr        : {bike_in_xodr}  (type=\"biking\" present)")

    if bike_on and bike_in_xodr:
        print(f"\n  ✓ PASS — bike lane was added to both parameters and .xodr")
    else:
        print(f"\n  ✗ FAIL — bike lane was NOT properly added")

    print(f"{'═' * 70}\n")


if __name__ == "__main__":
    main()
