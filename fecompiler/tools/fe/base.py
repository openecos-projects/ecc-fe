"""Abstract base class for all step handlers."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseStep(ABC):
    """Every step handler implements run() and check_result()."""

    @abstractmethod
    def run(self, step: Any, workspace: dict[str, Any]) -> None:
        """Execute the step logic and produce all required output files."""
        raise NotImplementedError

    @abstractmethod
    def check_result(self, step: Any) -> bool:
        """Return True if the last run() produced a passing result."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Helpers shared by all steps
    # ------------------------------------------------------------------

    def write_standard_outputs(self, ws_step: Any) -> None:
        """Write the initial log, result, metrics, and report files of a step."""
        step_name = ws_step.name
        tool      = ws_step.tool

        Path(ws_step.log["file"]).write_text(
            f"[BEGIN] step={step_name} tool={tool}\n"
            f"[END]   step={step_name} tool={tool}\n",
            encoding="utf-8",
        )

        Path(ws_step.output["json"]).write_text(
            json.dumps({"step": step_name, "tool": tool, "state": "Success"}, indent=2),
            encoding="utf-8",
        )

        Path(ws_step.analysis["metrics"]).write_text(
            json.dumps({"step": step_name, "status": "Success"}, indent=2),
            encoding="utf-8",
        )

        Path(ws_step.report["step"]).write_text(
            f"report: {step_name}\n",
            encoding="utf-8",
        )
