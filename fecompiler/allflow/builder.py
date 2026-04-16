"""AllFlow builder — defines the default flow steps for ecc-fe."""

from __future__ import annotations

from typing import Final

# (step_name, tool) — sim runs first via verilator; step1-7 are EDA placeholders
DEFAULT_FLOW_STEPS: Final[list[tuple[str, str]]] = [
    ("sim",   "verilator"),
    ("step1", "ecc"),
    ("step2", "ecc"),
    ("step3", "ecc"),
    ("step4", "ecc"),
    ("step5", "ecc"),
    ("step6", "ecc"),
    ("step7", "ecc"),
]


def sanitize_step_token(step_name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in step_name).strip("_") or "step"


def build_allflow() -> list[tuple[str, str, str]]:
    """Return (step_name, tool, state) tuples for the full flow."""
    from fecompiler.data.step import StateEnum
    return [(name, tool, StateEnum.Unstart.value) for name, tool in DEFAULT_FLOW_STEPS]
