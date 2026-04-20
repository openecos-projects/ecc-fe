"""Slang elaboration step implementation."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from fecompiler.tools.fe.base import BaseStep
from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.slang.subflow import SlangSubFlowEnum, init_slang_subflow
from fecompiler.utility.json import json_write


# ── slang binary location ─────────────────────────────────────────────────────

_SLANG_BIN = Path(__file__).parent / "bin" / "slang"


def _slang_cmd() -> str:
    """Return path to slang binary (built or system)."""
    if _SLANG_BIN.exists():
        return str(_SLANG_BIN)
    return "slang"   # fall back to system PATH


# ── SlangElabStep ─────────────────────────────────────────────────────────────

class SlangElabStep(BaseStep):
    """Run slang elaboration check on RTL.

    Sub-steps: elaborate → report
    Success: elab.txt exists and contains no 'error:'
    """

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_slang_subflow(step)
        self._run_elaborate(step, workspace)
        self._write_report(step)

    def check_result(self, step: WorkspaceStep) -> bool:
        elab_path = Path(step.report["dir"]) / "elab.txt"
        if not elab_path.exists():
            return False
        content = elab_path.read_text(encoding="utf-8")
        return "error:" not in content.lower() or "0 errors" in content

    # ── internal ──────────────────────────────────────────────────────────────

    def _rtl_files(self, workspace: dict[str, Any]) -> list[str]:
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

    def _run_elaborate(self, step: WorkspaceStep,
                       workspace: dict[str, Any]) -> None:
        files     = self._rtl_files(workspace)
        top       = workspace.get("top_module", "top")
        elab_path = Path(step.report["dir"]) / "elab.txt"

        cmd = [
            _slang_cmd(),
            "--lint-only",
            "--top", top,
            "--diag-column",
            "--diag-location",
            "--diag-source",
        ] + files

        result = subprocess.run(cmd, capture_output=True, text=True)
        output = (result.stdout + result.stderr).strip() or "Build succeeded: 0 errors, 0 warnings"
        elab_path.write_text(output, encoding="utf-8")

        ok = result.returncode == 0
        self._update_substep(step, SlangSubFlowEnum.elaborate.value, ok=ok)

    def _write_report(self, step: WorkspaceStep) -> None:
        elab_path = Path(step.report["dir"]) / "elab.txt"
        content   = elab_path.read_text(encoding="utf-8") if elab_path.exists() else ""
        ok        = "error:" not in content.lower() or "0 errors" in content

        json_write(step.report["step"], {
            "elaborate": "pass" if ok else "fail",
            "report":    str(elab_path),
        })
        self._update_substep(step, SlangSubFlowEnum.report.value, ok=True)

    @staticmethod
    def _update_substep(step: WorkspaceStep, name: str,
                        ok: bool, info: dict | None = None) -> None:
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
