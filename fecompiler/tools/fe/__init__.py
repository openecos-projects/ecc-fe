"""Shared step infrastructure: path tables, the handler base class, and subflows."""

from .builder import build_step, build_step_space, build_step_config
from .base import BaseStep
from .subflow import (
    init_subflow,
    reset_subflow,
    update_substep,
    update_substep_ok,
)

_STEP_REGISTRY: dict[str, BaseStep] | None = None


def get_step_registry() -> dict[str, BaseStep]:
    """Return step handlers lazily to avoid runner import cycles."""
    global _STEP_REGISTRY
    if _STEP_REGISTRY is None:
        from fecompiler.allflow.steps import step_definitions

        _STEP_REGISTRY = {
            definition.name: definition.create_handler()
            for definition in step_definitions()
        }
    return _STEP_REGISTRY

__all__ = [
    "build_step",
    "build_step_space",
    "build_step_config",
    "BaseStep",
    "get_step_registry",
    "init_subflow",
    "reset_subflow",
    "update_substep",
    "update_substep_ok",
]
