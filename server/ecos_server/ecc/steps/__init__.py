"""Step registry — maps step names to their handler instances.

To add a new step:
  1. Create steps/<your_step>.py  (inherit BaseStep, implement run())
  2. Import it here and add one line to STEP_REGISTRY.
  3. Update flow_spec.py to use the new name.
"""

from __future__ import annotations

from .base import BaseStep
from .copyfiles import CopyFilesStep

STEP_REGISTRY: dict[str, BaseStep] = {
    "copyfiles": CopyFilesStep(),
}

__all__ = ["STEP_REGISTRY", "BaseStep"]
