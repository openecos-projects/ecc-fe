"""FE tool wrappers — mirrors chipcompiler/tools/ecc/ in ecos-studio/ecc (renamed to tools/fe in fecompiler)."""

from .builder import build_step, build_step_space, build_step_config
from .base import BaseStep
from .copyfiles import CopyFilesStep
from .subflow import EccSubFlowEnum, build_subflow, init_subflow, update_substep
from .service import get_step_info

# Step registry: maps step_name → handler instance.
# All EDA steps are stubs (no-op); only copyfiles is a real implementation.
STEP_REGISTRY: dict[str, BaseStep] = {}

__all__ = [
    "build_step",
    "build_step_space",
    "build_step_config",
    "BaseStep",
    "CopyFilesStep",
    "STEP_REGISTRY",
    "EccSubFlowEnum",
    "build_subflow",
    "init_subflow",
    "update_substep",
    "get_step_info",
]
