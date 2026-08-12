"""Enforces the Agent-1 preservation invariant.

Agent 1 (extract_scenario.py) is the frozen, gold-verified semantic
extraction stage (see docs/gold_reference.py and test_semantic_correctness.py).
Everything downstream — OSM enrichment (osm_enrichment.py) and parameter
completion (complete_parameters.py) — is allowed to FILL fields Agent 1 left
null, but must never CHANGE a field Agent 1 already set. This module checks
that rule mechanically, so the guarantee doesn't rest on every enrichment
function remembering to check "is this field already set" before writing.

Only the semantic sections Agent 1 actually produces are checked — the
generated_simulation_parameters/osm_context/missing_parameters/topology
sections are enrichment's own workspace and are exempt by construction (they
don't exist in Agent 1's output at all).
"""

from __future__ import annotations

SEMANTIC_SECTIONS = (
    "source",
    "classification",
    "location",
    "road_context",
    "participants",
    "conflict",
)


class Agent1FieldOverwritten(ValueError):
    """A field Agent 1 already set was changed by later pipeline stages.

    This must never happen — later stages may only fill in a field Agent 1
    left null, never alter one it already populated.
    """


def check_agent1_preserved(agent1_data: dict, current_data: dict) -> None:
    """Raise Agent1FieldOverwritten if any non-null field Agent 1 set in
    `agent1_data` has a different value in `current_data`. Fields Agent 1
    left null (or absent) may hold anything in `current_data` — that's a
    legitimate fill, not an overwrite.
    """
    violations: list[str] = []
    for section in SEMANTIC_SECTIONS:
        if section not in agent1_data:
            continue
        _diff(agent1_data[section], current_data.get(section), section, violations)
    if violations:
        raise Agent1FieldOverwritten(
            "Agent 1 field(s) were changed after extraction — enrichment may "
            "only fill fields Agent 1 left null, never alter ones it set: "
            + "; ".join(violations)
        )


def _diff(agent1_value, current_value, path: str, violations: list[str]) -> None:
    if isinstance(agent1_value, dict):
        if not isinstance(current_value, dict):
            violations.append(f"{path}: was {agent1_value!r}, now {current_value!r}")
            return
        for key, value in agent1_value.items():
            _diff(value, current_value.get(key), f"{path}.{key}", violations)
        return

    if isinstance(agent1_value, list):
        if not isinstance(current_value, list) or len(current_value) != len(agent1_value):
            violations.append(
                f"{path}: list changed shape (was {agent1_value!r}, now {current_value!r})"
            )
            return
        # Participants are matched positionally — extraction always emits
        # them in the same order it discovered them, and nothing downstream
        # reorders the list.
        for index, (a, b) in enumerate(zip(agent1_value, current_value)):
            _diff(a, b, f"{path}[{index}]", violations)
        return

    if agent1_value is not None and agent1_value != current_value:
        violations.append(f"{path}: {agent1_value!r} -> {current_value!r}")
