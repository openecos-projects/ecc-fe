"""Frontend design profiles and their supported flow capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from fecompiler.allflow.steps import step_definition


CPU_CORE: Final = "cpu_core"
GENERIC_RTL: Final = "generic_rtl"


@dataclass(frozen=True, slots=True)
class FrontendFlowProfile:
    design_kind: str
    steps: tuple[tuple[str, str], ...]
    capabilities: frozenset[str]


def _steps(*names: str) -> tuple[tuple[str, str], ...]:
    """Resolve registered step names to the (name, tool) pairs of a profile."""
    return tuple(
        (definition.name, definition.tool)
        for definition in map(step_definition, names)
    )


_CPU_CORE_PROFILE = FrontendFlowProfile(
    design_kind=CPU_CORE,
    steps=_steps("prepare", "review", "elab", "lint", "sim"),
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
    steps=_steps("prepare", "review", "elab", "lint"),
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
