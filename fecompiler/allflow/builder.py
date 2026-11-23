"""The default flow: the steps of the CPU core profile."""

from __future__ import annotations

from typing import Final

from fecompiler.allflow.profile import CPU_CORE, flow_steps_for

# (step_name, tool) pairs of the default frontend flow.
DEFAULT_FLOW_STEPS: Final[list[tuple[str, str]]] = flow_steps_for(CPU_CORE)
