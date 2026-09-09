"""AllFlow builder — defines the default flow steps for ecc-fe."""

from __future__ import annotations

from typing import Final

from fecompiler.allflow.profile import CPU_CORE, flow_steps_for

# (step_name, tool) — production front-end flow
DEFAULT_FLOW_STEPS: Final[list[tuple[str, str]]] = flow_steps_for(CPU_CORE)


def sanitize_step_token(step_name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in step_name).strip("_") or "step"


def build_allflow(design_kind: object = CPU_CORE) -> list[tuple[str, str, str]]:
    """Return (step_name, tool, state) tuples for the full flow."""
    from fecompiler.data.step import StateEnum
    return [
        (name, tool, StateEnum.Unstart.value)
        for name, tool in flow_steps_for(design_kind)
    ]
