"""AllFlow builder — defines the default flow steps for ecc-fe."""

from __future__ import annotations

from typing import Final

# (step_name, tool) — production front-end flow
DEFAULT_FLOW_STEPS: Final[list[tuple[str, str]]] = [
    ("prepare", "fe"),       # merge / normalize CPU+SoC inputs
    ("review", "fe"),        # static RTL review for IC/FPGA readiness
    ("elab", "slang"),       # SV elaboration / semantic check
    ("lint", "verilator"),   # RTL lint
    ("sim", "verilator"),    # compile + simulation (requires testbench)
]


def sanitize_step_token(step_name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in step_name).strip("_") or "step"


def build_allflow() -> list[tuple[str, str, str]]:
    """Return (step_name, tool, state) tuples for the full flow."""
    from fecompiler.data.step import StateEnum
    return [(name, tool, StateEnum.Unstart.value) for name, tool in DEFAULT_FLOW_STEPS]
