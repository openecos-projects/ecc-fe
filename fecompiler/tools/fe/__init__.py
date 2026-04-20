"""FE tool wrappers — mirrors chipcompiler/tools/ecc/ in ecos-studio/ecc (renamed to tools/fe in fecompiler)."""

from .builder import build_step, build_step_space, build_step_config
from .base import BaseStep
from .subflow import EccSubFlowEnum, build_subflow, init_subflow, update_substep
from .service import get_step_info

from fecompiler.tools.prepare.runner import PrepareStep
from fecompiler.tools.slang.runner import SlangElabStep
from fecompiler.tools.verilator.runner import VerilatorLintStep, VerilatorSimStep

# Step registry: maps step_name → handler instance.
STEP_REGISTRY: dict[str, BaseStep] = {
    "prepare": PrepareStep(),
    "elab": SlangElabStep(),
    "lint": VerilatorLintStep(),
    "sim":  VerilatorSimStep(),
}

__all__ = [
    "build_step",
    "build_step_space",
    "build_step_config",
    "BaseStep",
    "STEP_REGISTRY",
    "EccSubFlowEnum",
    "build_subflow",
    "init_subflow",
    "update_substep",
    "get_step_info",
    "PrepareStep",
    "SlangElabStep",
    "VerilatorLintStep",
    "VerilatorSimStep",
]
