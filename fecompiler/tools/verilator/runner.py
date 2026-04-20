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
from fecompiler.utility.json import json_read, json_write


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
    """Collect RTL files (prefer prepare manifest, then filelist / origin)."""
    prepared = _prepared_inputs(workspace)
    if prepared:
        return [str(p) for p in prepared.get("rtl_files", [])]

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


def _prepared_inputs(workspace: dict[str, Any]) -> dict[str, Any]:
    """Load normalized prepare artifact if available."""
    manifest = str(workspace.get("prepared_manifest", "")).strip()
    if manifest and Path(manifest).exists():
        data = json_read(manifest)
        if isinstance(data, dict) and data.get("rtl_files"):
            return data
    return {}


def _incdir_args(workspace: dict[str, Any]) -> list[str]:
    """Return verilator include-dir args from prepare manifest + RTL parent dirs."""
    prepared = _prepared_inputs(workspace)
    seen: set[str] = set()
    incdirs: list[str] = []

    for inc in prepared.get("incdirs", []) if prepared else []:
        text = str(inc).strip()
        if text and text not in seen:
            seen.add(text)
            incdirs.append(text)

    # Real-world RTL often uses relative includes from each source directory.
    for rtl in _rtl_files(workspace):
        parent = str(Path(rtl).expanduser().resolve().parent)
        if parent and parent not in seen:
            seen.add(parent)
            incdirs.append(parent)

    return [f"+incdir+{inc}" for inc in incdirs]


def _define_args(workspace: dict[str, Any]) -> list[str]:
    """Return verilator preprocessor define args from prepare manifest."""
    prepared = _prepared_inputs(workspace)
    return [f"+define+{define}" for define in prepared.get("defines", [])] if prepared else []


def _sim_cpp_sources(workspace: dict[str, Any]) -> list[str]:
    """Return C++ simulation sources (testbench first, then extras)."""
    seen: set[str] = set()
    ordered: list[str] = []

    tb = str(workspace.get("testbench", "")).strip()
    if tb:
        seen.add(tb)
        ordered.append(tb)

    for src in workspace.get("sim_cpp_sources", []) or []:
        s = str(src).strip()
        if s and s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def _sim_cflags_args(workspace: dict[str, Any]) -> list[str]:
    user_flags = [str(f).strip() for f in workspace.get("sim_cflags", []) or [] if str(f).strip()]
    has_std = any(flag.startswith("-std=") for flag in user_flags)
    flags = ([] if has_std else ["-std=c++20"]) + user_flags
    if not flags:
        return []
    return ["-CFLAGS", " ".join(flags)]


def _sim_ldflags_args(workspace: dict[str, Any]) -> list[str]:
    flags = [str(f).strip() for f in workspace.get("sim_ldflags", []) or [] if str(f).strip()]
    if not flags:
        return []
    return ["-LDFLAGS", " ".join(flags)]


def _sim_run_args(workspace: dict[str, Any]) -> list[str]:
    return [str(arg) for arg in workspace.get("sim_run_args", []) or []]


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
            *_incdir_args(workspace),
            *_define_args(workspace),
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
        compile_state, compile_info = self._substep_status(step, SimSubFlowEnum.compile.value)
        if compile_state == "Incomplete":
            return False
        if compile_info.get("skipped") == "no testbench":
            return True

        sim_log = Path(step.report["dir"]) / "sim.log"
        if not sim_log.exists():
            return False
        content = sim_log.read_text(encoding="utf-8")
        return "FAILED" not in content and "%Error" not in content

    def _run_compile(self, step: WorkspaceStep,
                     workspace: dict[str, Any]) -> bool:
        cpp_sources = _sim_cpp_sources(workspace)
        if not cpp_sources:
            _update_substep(step, SimSubFlowEnum.compile.value, ok=True,
                            info={"skipped": "no testbench"})
            return False
        missing = [src for src in cpp_sources if not Path(src).exists()]
        if missing:
            _update_substep(
                step,
                SimSubFlowEnum.compile.value,
                ok=False,
                info={"error": "missing sim C++ source", "missing_sources": missing},
            )
            return False

        files   = _rtl_files(workspace)
        top     = workspace.get("top_module", "top")
        sim_bin = Path(step.output["dir"]) / f"{workspace['design']}_sim"
        obj_dir = Path(step.directory) / "obj_dir"

        cmd = [
            _verilator_cmd(), "--binary", "-j", "8",
            "-Wno-fatal",
            "--trace",
            *_verilator_include_args(),
            *_incdir_args(workspace),
            *_define_args(workspace),
            *_sim_cflags_args(workspace),
            *_sim_ldflags_args(workspace),
            "--top", top,
            "--Mdir", str(obj_dir),
            "-o", str(sim_bin),
        ] + files + cpp_sources

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

        result = subprocess.run(
            [str(sim_bin), *_sim_run_args(workspace)],
            capture_output=True,
            text=True,
        )
        sim_log.write_text(result.stdout + result.stderr, encoding="utf-8")
        _update_substep(step, SimSubFlowEnum.simulate.value,
                        ok=result.returncode == 0)

    def _write_report(self, step: WorkspaceStep) -> None:
        sim_log   = Path(step.report["dir"]) / "sim.log"
        compile_state, compile_info = self._substep_status(step, SimSubFlowEnum.compile.value)
        if compile_info.get("skipped") == "no testbench":
            sim_ok = True
        elif not sim_log.exists():
            sim_ok = False
        else:
            sim_ok = (
            "FAILED" not in sim_log.read_text() and
            "%Error" not in sim_log.read_text()
        )

        if compile_info.get("skipped"):
            compile_status = "skipped"
        elif compile_state == "Success":
            compile_status = "done"
        else:
            compile_status = "fail"

        json_write(step.report["step"], {
            "compile":  compile_status,
            "simulate": "pass" if sim_ok else "fail",
        })
        _update_substep(step, SimSubFlowEnum.report.value, ok=True)

    @staticmethod
    def _substep_status(step: WorkspaceStep, name: str) -> tuple[str, dict[str, Any]]:
        data = json_read(step.subflow.get("path", ""))
        for entry in data.get("steps", []):
            if entry.get("name") == name:
                return str(entry.get("state", "")), dict(entry.get("info", {}) or {})
        return "", {}
