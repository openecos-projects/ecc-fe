"""Verilator step implementation."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from fecompiler.tools.fe.base import BaseStep
from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.verilator.subflow import (
    VerilatorSubFlowEnum,
    init_verilator_subflow,
)
from fecompiler.utility.json import json_write


class VerilatorStep(BaseStep):
    """Run verilator lint + simulation for the sim step."""

    # ── BaseStep interface ────────────────────────────────────────────────────

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_verilator_subflow(step)
        self._run_lint(step, workspace)
        self._run_compile(step, workspace)
        self._run_simulate(step, workspace)
        self._write_report(step)

    def check_result(self, step: WorkspaceStep) -> bool:
        """Success = lint report exists and contains no errors."""
        lint_path = Path(step.report["dir"]) / "lint.txt"
        if not lint_path.exists():
            return False
        content = lint_path.read_text(encoding="utf-8")
        return "%Error" not in content

    # ── internal steps ────────────────────────────────────────────────────────

    def _rtl_files(self, workspace: dict[str, Any]) -> list[str]:
        """Collect RTL files from filelist or origin verilog."""
        filelist = workspace.get("input_filelist", "")
        if filelist and Path(filelist).exists():
            lines = Path(filelist).read_text(encoding="utf-8").splitlines()
            return [
                l.strip() for l in lines
                if l.strip() and not l.strip().startswith(("#", "//"))
                and (l.strip().endswith(".v") or l.strip().endswith(".sv"))
            ]
        verilog = workspace.get("origin_verilog", "")
        if verilog and Path(verilog).exists():
            return [verilog]
        return []

    def _run_lint(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        rtl_files = self._rtl_files(workspace)
        lint_path = Path(step.report["dir"]) / "lint.txt"
        top       = workspace.get("top_module", "top")

        cmd = ["verilator", "--lint-only", "-Wno-fatal", "--top", top] + rtl_files
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        lint_path.write_text(
            (result.stdout + result.stderr).strip() or "lint OK",
            encoding="utf-8",
        )
        self._update_substep(step, VerilatorSubFlowEnum.lint.value,
                             ok=result.returncode == 0)

    def _run_compile(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        """Compile RTL to simulation binary (skip if no testbench)."""
        rtl_files = self._rtl_files(workspace)
        top       = workspace.get("top_module", "top")
        sim_bin   = Path(step.output["dir"]) / f"{workspace['design']}_sim"
        obj_dir   = Path(step.directory) / "obj_dir"

        # check if testbench exists
        tb = workspace.get("testbench", "")
        if not tb or not Path(tb).exists():
            self._update_substep(step, VerilatorSubFlowEnum.compile.value, ok=True,
                                 info={"skipped": "no testbench"})
            return

        cmd = [
            "verilator", "--binary", "-j", "0",
            "--top", top,
            f"-Mdir={obj_dir}",
            f"-o", str(sim_bin),
        ] + rtl_files + [tb]

        result = subprocess.run(cmd, capture_output=True, text=True)
        log = Path(step.log["dir"]) / "compile.log"
        log.write_text((result.stdout + result.stderr), encoding="utf-8")
        self._update_substep(step, VerilatorSubFlowEnum.compile.value,
                             ok=result.returncode == 0)

    def _run_simulate(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        """Run compiled simulation binary (skip if not built)."""
        sim_bin = Path(step.output["dir"]) / f"{workspace['design']}_sim"
        sim_log = Path(step.report["dir"]) / "sim.log"

        if not sim_bin.exists():
            self._update_substep(step, VerilatorSubFlowEnum.simulate.value, ok=True,
                                 info={"skipped": "binary not built"})
            return

        result = subprocess.run(
            [str(sim_bin)],
            capture_output=True,
            text=True,
        )
        sim_log.write_text((result.stdout + result.stderr), encoding="utf-8")
        self._update_substep(step, VerilatorSubFlowEnum.simulate.value,
                             ok=result.returncode == 0)

    def _write_report(self, step: WorkspaceStep) -> None:
        """Summarise results into step.report["step"] JSON."""
        lint_path = Path(step.report["dir"]) / "lint.txt"
        lint_ok   = lint_path.exists() and "%Error" not in lint_path.read_text()

        summary = {
            "lint":     "pass" if lint_ok else "fail",
            "compile":  "skipped" if not (Path(step.output["dir"]) /
                        "").exists() else "done",
            "simulate": "skipped",
        }
        json_write(step.report["step"], summary)
        self._update_substep(step, VerilatorSubFlowEnum.report.value, ok=True)

    # ── helper ────────────────────────────────────────────────────────────────

    @staticmethod
    def _update_substep(
        step: WorkspaceStep,
        name: str,
        ok: bool,
        info: dict | None = None,
    ) -> None:
        from fecompiler.data.step import StateEnum
        from fecompiler.utility.json import json_write as _jw

        state = StateEnum.Success.value if ok else StateEnum.Incomplete.value
        for entry in step.subflow.get("steps", []):
            if entry["name"] == name:
                entry["state"] = state
                entry["info"]  = info or {}
                break
        path = step.subflow.get("path", "")
        if path:
            _jw(path, step.subflow)
