"""Frontend design profiles and their supported flow capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from fecompiler.allflow.steps import step_definition, step_names


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

# An RTL design whose project names a testbench is simulated with it.
_GENERIC_RTL_TESTBENCH_PROFILE = FrontendFlowProfile(
    design_kind=GENERIC_RTL,
    steps=_steps("prepare", "review", "elab", "lint", "sim"),
    capabilities=frozenset({"source", "qor", "simulation"}),
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


def has_testbench(parameters: Mapping[str, object]) -> bool:
    """Whether the parameters of an RTL design name a testbench to simulate."""
    return bool(str(parameters.get("testbench_top") or "").strip())


def flow_profile(parameters: Mapping[str, object]) -> FrontendFlowProfile:
    """Return the profile of the design *parameters* describe.

    The design kind chooses it; an RTL design gains the ``sim`` step by
    naming a testbench (``testbench_top``).
    """
    profile = frontend_flow_profile(parameters.get("frontend_design_kind"))
    if profile.design_kind == GENERIC_RTL and has_testbench(parameters):
        return _GENERIC_RTL_TESTBENCH_PROFILE
    return profile


def flow_steps(parameters: Mapping[str, object]) -> list[tuple[str, str]]:
    return list(flow_profile(parameters).steps)


def parameter_groups_outside(parameters: Mapping[str, object]) -> frozenset[str]:
    """Return the parameter groups that do not apply to this design.

    These are the groups of steps its flow does not have, ``sim`` (how CPU
    programs are built and run) for an RTL design, and ``testbench`` for a
    CPU, which is simulated on its SoC harness.
    """
    profile = flow_profile(parameters)
    outside = set(step_names()) - {name for name, _ in profile.steps}
    outside.add("sim" if profile.design_kind == GENERIC_RTL else "testbench")
    return frozenset(outside)
