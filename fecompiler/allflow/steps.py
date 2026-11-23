"""The registry of frontend flow steps.

Each :class:`StepDefinition` is the single description of one step: which tool
runs it, where its directory lives, which handler executes it, and what makes
its result stale. Flow profiles, the engine, provenance, and the CLI all read
this registry instead of keeping their own tables of step names.

Adding a step means adding one definition here, listing it in the profiles
that run it (``allflow/profile.py``), and giving it a QoR builder
(``analysis/qor.py``).

This module imports nothing from the rest of the package, so every layer can
depend on it; handlers are imported only when a step is about to run.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Final


@dataclass(frozen=True, slots=True)
class StepDefinition:
    name: str
    tool: str
    # "package.module:ClassName" of the BaseStep subclass that runs the step.
    handler: str
    # File below the step's report/ directory that QoR analysis reads.
    qor_report: str
    # Workspace parameters whose change makes a successful result stale.
    config_fields: tuple[str, ...] = ()
    # Every parameter whose name starts with one of these is tracked as well.
    config_prefixes: tuple[str, ...] = ()
    # Runner sources, relative to the package root, hashed into the provenance.
    source_files: tuple[str, ...] = ()
    # External programs whose identity is part of the provenance.
    executables: tuple[str, ...] = ()
    # Environment checks `ecc-fe doctor --step <name>` performs.
    doctor_checks: tuple[str, ...] = ()

    @property
    def directory_name(self) -> str:
        return step_directory_name(self.name, self.tool)

    def directory(self, workspace_dir: str | Path) -> Path:
        return Path(workspace_dir) / self.directory_name

    def create_handler(self) -> Any:
        module_name, _, attribute = self.handler.partition(":")
        return getattr(import_module(module_name), attribute)()


def step_directory_name(name: str, tool: str) -> str:
    """Return the directory name of a step inside a workspace."""
    return f"{name}_{tool}"


_DEFINITIONS: Final[tuple[StepDefinition, ...]] = (
    StepDefinition(
        name="prepare",
        tool="fe",
        handler="fecompiler.tools.prepare.runner:PrepareStep",
        qor_report="prepare.rpt",
        config_fields=(
            "top_module",
            "cpu_wrapper_id",
            "cpu_wrapper_contract",
            "cpu_socket_contract",
            "cpu_wrapper_top",
            "required_cpu_top_module",
            "required_cpu_top_ports",
            "required_cpu_top_port_contract",
            "cpu_reset_vector",
            "soc_cpu_reset_vector",
            "soc_wrapper_id",
            "soc_wrapper_contract",
            "soc_wrapper_top",
        ),
        source_files=("tools/prepare/runner.py",),
    ),
    StepDefinition(
        name="review",
        tool="fe",
        handler="fecompiler.tools.review.runner:RtlReviewStep",
        qor_report="rtl_review.json",
        config_fields=("review_waivers",),
        source_files=(
            "tools/review/runner.py",
            "tools/review/analyzer.py",
            "tools/review/structural_probe.py",
        ),
        executables=("yosys",),
    ),
    StepDefinition(
        name="elab",
        tool="slang",
        handler="fecompiler.tools.slang.runner:SlangElabStep",
        qor_report="elab_summary.json",
        config_fields=("top_module",),
        source_files=("tools/slang/runner.py",),
        executables=("slang",),
        doctor_checks=("slang",),
    ),
    StepDefinition(
        name="lint",
        tool="verilator",
        handler="fecompiler.tools.verilator.runner:VerilatorLintStep",
        qor_report="lint_summary.json",
        config_fields=("top_module", "lint_profile", "lint_baseline", "lint_waivers"),
        source_files=("tools/verilator/runner.py",),
        executables=("verilator",),
        doctor_checks=("verilator",),
    ),
    StepDefinition(
        name="sim",
        tool="verilator",
        handler="fecompiler.tools.verilator.runner:VerilatorSimStep",
        qor_report="cases.json",
        config_fields=(
            "testbench",
            "frequency_max",
            "Frequency max [MHz]",
            "cpu_supports_difftest",
            "soc_supports_difftest",
        ),
        config_prefixes=("sim_",),
        source_files=("tools/verilator/runner.py",),
        executables=("verilator",),
        doctor_checks=("verilator", "cxx", "make", "riscv"),
    ),
)

_BY_NAME: Final[dict[str, StepDefinition]] = {
    definition.name: definition for definition in _DEFINITIONS
}
if len(_BY_NAME) != len(_DEFINITIONS):  # pragma: no cover - registry authoring error
    raise RuntimeError("duplicate frontend flow step name")


def step_definitions() -> tuple[StepDefinition, ...]:
    """Return every known step in canonical flow order."""
    return _DEFINITIONS


def step_names() -> tuple[str, ...]:
    return tuple(definition.name for definition in _DEFINITIONS)


def is_step(name: object) -> bool:
    return name in _BY_NAME


def find_step_definition(name: object) -> StepDefinition | None:
    return _BY_NAME.get(name)  # type: ignore[arg-type]


def step_definition(name: str) -> StepDefinition:
    """Return the definition of *name*; ``KeyError`` names the known steps."""
    try:
        return _BY_NAME[name]
    except KeyError:
        known = ", ".join(step_names())
        raise KeyError(f"unknown frontend flow step {name!r}; known steps: {known}") from None


def step_directory(workspace_dir: str | Path, name: str) -> Path:
    """Return the directory of a registered step inside *workspace_dir*."""
    return step_definition(name).directory(workspace_dir)


def step_directory_suffixes() -> tuple[str, ...]:
    """Return the ``_<tool>`` suffixes that mark step directories in a workspace."""
    return tuple(dict.fromkeys(f"_{definition.tool}" for definition in _DEFINITIONS))


__all__ = [
    "StepDefinition",
    "find_step_definition",
    "is_step",
    "step_definition",
    "step_definitions",
    "step_directory",
    "step_directory_name",
    "step_directory_suffixes",
    "step_names",
]
