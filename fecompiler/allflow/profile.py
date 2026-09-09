"""Frontend design profiles and their supported flow capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


CPU_CORE: Final = "cpu_core"
GENERIC_RTL: Final = "generic_rtl"


@dataclass(frozen=True, slots=True)
class FrontendFlowProfile:
    design_kind: str
    steps: tuple[tuple[str, str], ...]
    capabilities: frozenset[str]


_CPU_CORE_PROFILE = FrontendFlowProfile(
    design_kind=CPU_CORE,
    steps=(
        ("prepare", "fe"),
        ("review", "fe"),
        ("elab", "slang"),
        ("lint", "verilator"),
        ("sim", "verilator"),
    ),
    capabilities=frozenset(
        {
            "source",
            "qor",
            "simulation",
            "waveform",
            "cpu_tests",
            "difftest",
            "disassembly",
        }
    ),
)

_GENERIC_RTL_PROFILE = FrontendFlowProfile(
    design_kind=GENERIC_RTL,
    steps=(
        ("prepare", "fe"),
        ("review", "fe"),
        ("elab", "slang"),
        ("lint", "verilator"),
    ),
    capabilities=frozenset({"source", "qor"}),
)

_PROFILES: Final = {
    CPU_CORE: _CPU_CORE_PROFILE,
    GENERIC_RTL: _GENERIC_RTL_PROFILE,
}


def normalize_frontend_design_kind(value: object) -> str:
    """Normalize a persisted design kind; missing values are legacy CPU workspaces."""
    normalized = str(value or "").strip().lower().replace("-", "_")
    if not normalized:
        return CPU_CORE
    if normalized not in _PROFILES:
        raise ValueError(f"unsupported frontend design kind: {value}")
    return normalized


def frontend_flow_profile(value: object) -> FrontendFlowProfile:
    return _PROFILES[normalize_frontend_design_kind(value)]


def flow_steps_for(value: object) -> list[tuple[str, str]]:
    return list(frontend_flow_profile(value).steps)
