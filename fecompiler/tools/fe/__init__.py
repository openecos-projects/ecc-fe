"""FE tool wrappers — mirrors chipcompiler/tools/ecc/ in ecos-studio/ecc (renamed to tools/fe in fecompiler)."""

from .builder import build_step, build_step_space, build_step_config
from .base import BaseStep
from .subflow import EccSubFlowEnum, build_subflow, init_subflow, update_substep, update_substep_ok
from .service import get_step_info

_STEP_REGISTRY: dict[str, BaseStep] | None = None


def get_step_registry() -> dict[str, BaseStep]:
    """Return step handlers lazily to avoid runner import cycles."""
    global _STEP_REGISTRY
    if _STEP_REGISTRY is None:
        from fecompiler.tools.prepare.runner import PrepareStep
        from fecompiler.tools.slang.runner import SlangElabStep
        from fecompiler.tools.verilator.runner import VerilatorLintStep, VerilatorSimStep

        _STEP_REGISTRY = {
            "prepare": PrepareStep(),
            "elab": SlangElabStep(),
            "lint": VerilatorLintStep(),
            "sim": VerilatorSimStep(),
        }
    return _STEP_REGISTRY

__all__ = [
    "build_step",
    "build_step_space",
    "build_step_config",
    "BaseStep",
    "get_step_registry",
    "EccSubFlowEnum",
    "build_subflow",
    "init_subflow",
    "update_substep",
    "update_substep_ok",
    "get_step_info",
]
