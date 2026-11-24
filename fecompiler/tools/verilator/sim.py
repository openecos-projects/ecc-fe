"""The sim step: a design is simulated in the environment it has.

A CPU runs programs on its SoC harness; any other RTL design runs the
testbench its project names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.allflow.profile import has_testbench
from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.common.tool_config import tool_config
from fecompiler.tools.fe.base import BaseStep
from fecompiler.tools.verilator.testbench import REPORT_KIND, TestbenchSimStep
from fecompiler.utility.json import json_read


class SimStep(BaseStep):
    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        _handler(has_testbench(tool_config(workspace))).run(step, workspace)

    def check_result(self, step: WorkspaceStep) -> bool:
        report = json_read(str(Path(step.report["dir"]) / "cases.json"))
        by_testbench = isinstance(report, dict) and report.get("kind") == REPORT_KIND
        return _handler(by_testbench).check_result(step)


def _handler(testbench: bool) -> BaseStep:
    if testbench:
        return TestbenchSimStep()
    # Imported on use: the harness runner is large and an RTL design never needs it.
    from fecompiler.tools.verilator.runner import VerilatorSimStep

    return VerilatorSimStep()


__all__ = ["SimStep"]
