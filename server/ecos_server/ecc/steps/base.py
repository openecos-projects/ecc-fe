"""Abstract base class for all ECC step handlers."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseStep(ABC):
    """Every step handler must inherit from this and implement run()."""

    @abstractmethod
    def run(self, ws_step: dict[str, Any], workspace: dict[str, Any]) -> None:
        """Execute the step logic and produce all required output files."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Helpers shared by all steps
    # ------------------------------------------------------------------

    def write_standard_outputs(self, ws_step: dict[str, Any]) -> None:
        """Write the boilerplate files that every step is expected to produce
        (log, script stub, output json, analysis metrics, feature, report).
        Call this at the end of run() after producing the real outputs.
        """
        step_name = ws_step["name"]
        tool = ws_step["tool"]

        Path(ws_step["log"]["file"]).write_text(
            f"[BEGIN] step={step_name} tool={tool}\n"
            f"[END]   step={step_name} tool={tool}\n",
            encoding="utf-8",
        )

        Path(ws_step["script"]["main"]).write_text(
            "# auto-generated\n"
            f'puts "running {step_name} ({tool})"\n',
            encoding="utf-8",
        )

        Path(ws_step["output"]["json"]).write_text(
            json.dumps({"step": step_name, "tool": tool, "state": "Success"}, indent=2),
            encoding="utf-8",
        )

        Path(ws_step["analysis"]["metrics"]).write_text(
            json.dumps({"step": step_name, "status": "Success"}, indent=2),
            encoding="utf-8",
        )

        Path(ws_step["feature"]["step"]).write_text(
            json.dumps({"name": step_name, "tool": tool}, indent=2),
            encoding="utf-8",
        )

        Path(ws_step["report"]["step"]).write_text(
            f"report: {step_name}\n",
            encoding="utf-8",
        )
