"""Slang elaboration step implementation."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from fecompiler.tools.fe.base import BaseStep
from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.common.rtl_inputs import rtl_files, slang_define_args, slang_incdir_args
from fecompiler.tools.fe.subflow import update_substep_ok
from fecompiler.tools.slang.subflow import SlangSubFlowEnum, init_slang_subflow
from fecompiler.utility.json import json_write


# ── slang binary location ─────────────────────────────────────────────────────

_SLANG_BIN = Path(__file__).parent / "bin" / "slang"
_WORKSPACE_REL_SLANG_BIN = Path("fecompiler/tools/slang/bin/slang")


def _slang_cmd() -> str:
    """Return path to slang binary (built or system)."""
    workspace_dir = os.getenv("BUILD_WORKSPACE_DIRECTORY", "").strip()
    workspace_bin = (
        Path(workspace_dir) / _WORKSPACE_REL_SLANG_BIN if workspace_dir else None
    )
    cwd_bin = Path.cwd() / _WORKSPACE_REL_SLANG_BIN

    if _SLANG_BIN.exists():
        return str(_SLANG_BIN)
    if workspace_bin is not None and workspace_bin.exists():
        return str(workspace_bin)
    if cwd_bin.exists():
        return str(cwd_bin)
    return "slang"   # fall back to system PATH


# ── SlangElabStep ─────────────────────────────────────────────────────────────

class SlangElabStep(BaseStep):
    """Run slang elaboration check on RTL.

    Sub-steps: elaborate → report
    Success: log.txt exists and contains no 'error:'
    """

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_slang_subflow(step)
        self._run_elaborate(step, workspace)
        self._write_report(step)

    def check_result(self, step: WorkspaceStep) -> bool:
        log_path = Path(step.report["dir"]) / "log.txt"
        if not log_path.exists():
            return False
        content = log_path.read_text(encoding="utf-8")
        return self._is_elab_log_ok(content)

    def _run_elaborate(self, step: WorkspaceStep,
                       workspace: dict[str, Any]) -> None:
        files = rtl_files(workspace)
        top       = workspace.get("top_module", "top")
        log_path = Path(step.report["dir"]) / "log.txt"

        cmd = [
            _slang_cmd(),
            "--lint-only",
            "--timescale", "1ns/1ps",
            "--top", top,
            "--diag-column",
            "--diag-location",
            "--diag-source",
            *slang_incdir_args(workspace),
            *slang_define_args(workspace),
        ] + files

        result = subprocess.run(cmd, capture_output=True, text=True)
        output = (result.stdout + result.stderr).strip() or "Build succeeded: 0 errors, 0 warnings"
        log_path.write_text(output, encoding="utf-8")

        ok = result.returncode == 0
        update_substep_ok(step, SlangSubFlowEnum.elaborate.value, ok)

    def _write_report(self, step: WorkspaceStep) -> None:
        log_path = Path(step.report["dir"]) / "log.txt"
        content = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        ok = self._is_elab_log_ok(content)

        json_write(step.report["step"], {
            "elaborate": "pass" if ok else "fail",
            "report":    str(log_path),
        })
        update_substep_ok(step, SlangSubFlowEnum.report.value, True)

    @staticmethod
    def _is_elab_log_ok(content: str) -> bool:
        text = content.lower()
        if "error:" not in text:
            return True
        return re.search(r"\b0\s+errors\b", text) is not None
