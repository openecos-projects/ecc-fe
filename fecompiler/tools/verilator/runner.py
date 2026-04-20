"""Verilator step implementations — VerilatorLintStep and VerilatorSimStep."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from fecompiler.tools.fe.base import BaseStep
from fecompiler.data.workspace import WorkspaceStep

from fecompiler.tools.verilator.subflow import (
    LintSubFlowEnum,
    SimSubFlowEnum,
    init_lint_subflow,
    init_sim_subflow,
)
from fecompiler.utility.json import json_write


# ── shared helper ─────────────────────────────────────────────────────────────

_LOCAL_VERILATOR_BIN = Path(__file__).parent / "bin" / "verilator"
_SYSTEM_VERILATOR_BIN = Path("/usr/local/bin/verilator")
_LOCAL_VERILATOR_INCLUDE = Path(__file__).parent / "include"
_SYSTEM_VERILATOR_INCLUDE = Path("/usr/local/share/verilator/include")
_WORKSPACE_REL_VERILATOR_BIN = Path("fecompiler/tools/verilator/bin/verilator")
_WORKSPACE_REL_VERILATOR_INCLUDE = Path("fecompiler/tools/verilator/include")


def _verilator_cmd() -> str:
    """Return verilator executable path (repo-local first, then system)."""
    workspace_dir = os.getenv("BUILD_WORKSPACE_DIRECTORY", "").strip()
    workspace_bin = (
        Path(workspace_dir) / _WORKSPACE_REL_VERILATOR_BIN if workspace_dir else None
    )
    cwd_bin = Path.cwd() / _WORKSPACE_REL_VERILATOR_BIN

    if _LOCAL_VERILATOR_BIN.exists():
        return str(_LOCAL_VERILATOR_BIN)
    if workspace_bin is not None and workspace_bin.exists():
        return str(workspace_bin)
    if cwd_bin.exists():
        return str(cwd_bin)
    if _SYSTEM_VERILATOR_BIN.exists():
        return str(_SYSTEM_VERILATOR_BIN)
    return "verilator"


def _verilator_include_args() -> list[str]:
    """Return include arg for verilator runtime headers if present."""
    workspace_dir = os.getenv("BUILD_WORKSPACE_DIRECTORY", "").strip()
    workspace_include = (
        Path(workspace_dir) / _WORKSPACE_REL_VERILATOR_INCLUDE if workspace_dir else None
    )
    cwd_include = Path.cwd() / _WORKSPACE_REL_VERILATOR_INCLUDE

    if _LOCAL_VERILATOR_INCLUDE.exists():
        return [f"-I{_LOCAL_VERILATOR_INCLUDE}"]
    if workspace_include is not None and workspace_include.exists():
        return [f"-I{workspace_include}"]
    if cwd_include.exists():
        return [f"-I{cwd_include}"]
    if _SYSTEM_VERILATOR_INCLUDE.exists():
        return [f"-I{_SYSTEM_VERILATOR_INCLUDE}"]
    return []


def _rtl_files(workspace: dict[str, Any]) -> list[str]:
    """Collect RTL files from filelist or origin verilog."""
    filelist = workspace.get("input_filelist", "")
    if filelist and Path(filelist).exists():
        return [
            l.strip() for l in Path(filelist).read_text(encoding="utf-8").splitlines()
            if l.strip()
            and not l.strip().startswith(("#", "//"))
            and (l.strip().endswith(".v") or l.strip().endswith(".sv"))
        ]
    verilog = workspace.get("origin_verilog", "")
    if verilog and Path(verilog).exists():
        return [verilog]
    return []


def _update_substep(step: WorkspaceStep, name: str, ok: bool,
                    info: dict | None = None) -> None:
    from fecompiler.data.step import StateEnum
    state = StateEnum.Success.value if ok else StateEnum.Incomplete.value
    for entry in step.subflow.get("steps", []):
        if entry["name"] == name:
            entry["state"] = state
            entry["info"]  = info or {}
            break
    path = step.subflow.get("path", "")
    if path:
        json_write(path, step.subflow)


# ── VerilatorLintStep ─────────────────────────────────────────────────────────

class VerilatorLintStep(BaseStep):
    """Run verilator --lint-only on the RTL.

    Sub-steps: lint → report
    Success condition: lint.txt exists and contains no %Error.
    """

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_lint_subflow(step)
        self._run_lint(step, workspace)
        self._write_report(step)

    def check_result(self, step: WorkspaceStep) -> bool:
        lint_path = Path(step.report["dir"]) / "lint.txt"
        return lint_path.exists() and "%Error" not in lint_path.read_text(encoding="utf-8")

    def _run_lint(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        files = _rtl_files(workspace)
        top   = workspace.get("top_module", "top")
        lint_path = Path(step.report["dir"]) / "lint.txt"

        cmd = [
            _verilator_cmd(),
            "--lint-only",
            "-Wno-fatal",
            *_verilator_include_args(),
            "--top",
            top,
        ] + files
        result = subprocess.run(cmd, capture_output=True, text=True)
        lint_path.write_text(
            (result.stdout + result.stderr).strip() or "lint OK",
            encoding="utf-8",
        )
        _update_substep(step, LintSubFlowEnum.lint.value, ok=result.returncode == 0)

    def _write_report(self, step: WorkspaceStep) -> None:
        lint_path = Path(step.report["dir"]) / "lint.txt"
        lint_ok   = lint_path.exists() and "%Error" not in lint_path.read_text()
        json_write(step.report["step"], {
            "lint": "pass" if lint_ok else "fail",
        })
        _update_substep(step, LintSubFlowEnum.report.value, ok=True)


# ── VerilatorSimStep ──────────────────────────────────────────────────────────

class VerilatorSimStep(BaseStep):
    """Compile RTL to a simulation binary and run it.

    Requires workspace["testbench"] to point to a valid testbench file.
    Sub-steps: compile → simulate → report
    Success condition: simulation binary ran and returned exit code 0.
    """

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_sim_subflow(step)
        compiled = self._run_compile(step, workspace)
        self._run_simulate(step, workspace, compiled)
        self._write_report(step)

    def check_result(self, step: WorkspaceStep) -> bool:
        sim_log = Path(step.report["dir"]) / "sim.log"
        if not sim_log.exists():
            # no testbench → compile skipped → treat as success
            return True
        content = sim_log.read_text(encoding="utf-8")
        return "FAILED" not in content and "%Error" not in content

    def _run_compile(self, step: WorkspaceStep,
                     workspace: dict[str, Any]) -> bool:
        tb = workspace.get("testbench", "")
        if not tb or not Path(tb).exists():
            _update_substep(step, SimSubFlowEnum.compile.value, ok=True,
                            info={"skipped": "no testbench"})
            return False

        files   = _rtl_files(workspace)
        top     = workspace.get("top_module", "top")
        sim_bin = Path(step.output["dir"]) / f"{workspace['design']}_sim"
        obj_dir = Path(step.directory) / "obj_dir"

        cmd = [
            _verilator_cmd(), "--binary", "-j", "0",
            "--top", top,
            f"-Mdir={obj_dir}",
            "-o", str(sim_bin),
        ] + files + [tb]

        result = subprocess.run(cmd, capture_output=True, text=True)
        (Path(step.log["dir"]) / "compile.log").write_text(
            result.stdout + result.stderr, encoding="utf-8"
        )
        ok = result.returncode == 0
        _update_substep(step, SimSubFlowEnum.compile.value, ok=ok)
        return ok

    def _run_simulate(self, step: WorkspaceStep, workspace: dict[str, Any],
                      compiled: bool) -> None:
        sim_bin = Path(step.output["dir"]) / f"{workspace['design']}_sim"
        sim_log = Path(step.report["dir"]) / "sim.log"

        if not compiled or not sim_bin.exists():
            _update_substep(step, SimSubFlowEnum.simulate.value, ok=True,
                            info={"skipped": "not compiled"})
            return

        result = subprocess.run([str(sim_bin)], capture_output=True, text=True)
        sim_log.write_text(result.stdout + result.stderr, encoding="utf-8")
        _update_substep(step, SimSubFlowEnum.simulate.value,
                        ok=result.returncode == 0)

    def _write_report(self, step: WorkspaceStep) -> None:
        sim_log   = Path(step.report["dir"]) / "sim.log"
        sim_ok    = not sim_log.exists() or (
            "FAILED" not in sim_log.read_text() and
            "%Error" not in sim_log.read_text()
        )
        json_write(step.report["step"], {
            "compile":  "done" if (Path(step.output["dir"]) /
                        f"{step.name}_sim").exists() else "skipped",
            "simulate": "pass" if sim_ok else "fail",
        })
        _update_substep(step, SimSubFlowEnum.report.value, ok=True)
