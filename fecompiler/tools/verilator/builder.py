"""Verilator step workspace builder."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.data.workspace import WorkspaceStep


def build_verilator_step(
    *,
    workspace: dict[str, Any],
    step_name: str = "sim",
) -> dict[str, Any]:
    """Return path dict specific to the verilator simulation step.

    Extends the standard WorkspaceStep paths with verilator-specific outputs:
      output/sim_bin   — compiled simulation binary
      output/vcd       — waveform dump
      report/log.txt   — lint / simulation report log
    """
    design    = workspace["design"]
    step_dir  = Path(workspace["directory"]) / f"{step_name}_verilator"
    sd        = str(step_dir)

    return {
        "sim_bin": f"{sd}/output/{design}_sim",
        "vcd":     f"{sd}/output/{design}.vcd",
        "lint":    f"{sd}/report/log.txt",
        "sim_log": f"{sd}/report/log.txt",
    }
