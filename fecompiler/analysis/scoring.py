"""How step scores are put together.

The weights and tolerances are data (``scoring_policy.json``); the functions
here apply three rules that hold for every step:

- A component that could not be evaluated, because the check does not apply
  to the design or the tool for it is not installed, is left out. It neither
  earns nor loses points, and the score is taken over what was evaluated.
- Warning counts are judged per thousand lines of the user's RTL, so a small
  module and a large core are held to the same density.
- A score depends on the result alone, never on earlier runs.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

_POLICY_PATH = Path(__file__).with_name("scoring_policy.json")


@cache
def scoring_policy() -> dict[str, Any]:
    return json.loads(_POLICY_PATH.read_text(encoding="utf-8"))


def scoring_version() -> int:
    return int(scoring_policy()["scoring_version"])


def round_score(value: float) -> int | float:
    rounded = round(float(value), 1)
    return int(rounded) if rounded.is_integer() else rounded


def component(
    component_id: str,
    label: str,
    earned: float,
    possible: float,
    summary: str,
    *,
    evaluated: bool = True,
) -> dict[str, Any]:
    """One line of a score; *evaluated* False keeps it out of the total."""
    return {
        "id": component_id,
        "label": label,
        "earned": round_score(earned) if evaluated else 0,
        "possible": round_score(possible),
        "evaluated": evaluated,
        "summary": summary,
    }


def score(label: str, components: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Total the evaluated components on a scale of 100; None when there are none."""
    evaluated = [item for item in components if item["evaluated"]]
    weight = sum(float(item["possible"]) for item in evaluated)
    if weight <= 0:
        return None
    earned = sum(float(item["earned"]) for item in evaluated)
    return {
        "label": label,
        "value": round_score(100 * earned / weight),
        "maximum": 100,
        "scoring_version": scoring_version(),
        "evaluated_weight": round_score(weight),
        "components": components,
    }


def scored_kloc(lines: int) -> float:
    """Thousands of lines a design is scored as; tiny designs share a floor."""
    minimum = int(scoring_policy()["size"]["minimum_lines"])
    return max(int(lines), minimum) / 1000


def density_credit(count: int, lines: int, possible: float, per_kloc_for_zero: float) -> float:
    """Credit that falls linearly from *possible* to 0 as the density rises."""
    density = count / scored_kloc(lines)
    return possible * max(0.0, 1.0 - density / per_kloc_for_zero)


def density_summary(count: int, lines: int, noun: str) -> str:
    density = count / scored_kloc(lines)
    plural = "" if count == 1 else "s"
    minimum = int(scoring_policy()["size"]["minimum_lines"])
    if lines >= minimum:
        size = f"{lines} lines"
    elif lines > 0:
        size = f"{lines} lines, scored as {minimum}"
    else:
        size = f"no counted lines, scored as {minimum} lines"
    return f"{count} {noun}{plural} in {size} ({density:.1f} per 1000 lines)."


__all__ = [
    "component",
    "density_credit",
    "density_summary",
    "round_score",
    "score",
    "scored_kloc",
    "scoring_policy",
    "scoring_version",
]
