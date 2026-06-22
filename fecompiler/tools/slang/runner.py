"""Slang elaboration step implementation."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from fecompiler.tools.fe.base import BaseStep
from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.common.rtl_inputs import (
    incdirs,
    rtl_files,
    slang_define_args,
    slang_defines,
    slang_incdir_args,
)
from fecompiler.tools.fe.subflow import update_substep_ok
from fecompiler.tools.slang.subflow import SlangSubFlowEnum, init_slang_subflow
from fecompiler.utility.json import json_write


# ── slang binary location ─────────────────────────────────────────────────────

_SLANG_BIN = Path(__file__).parent / "bin" / "slang"
_WORKSPACE_REL_SLANG_BIN = Path("fecompiler/tools/slang/bin/slang")

_MODULE_BLOCK_RE = re.compile(
    r"\bmodule\s+(?P<name>[A-Za-z_][\w$]*)\b(?P<body>.*?)(?:\bendmodule\b|$)",
    re.DOTALL | re.MULTILINE,
)
_INSTANCE_RE = re.compile(
    r"(?:^|[;\n])\s*(?P<type>[A-Za-z_][\w$]*)\s*"
    r"(?:#\s*\([^;]*?\)\s*)?(?P<name>[A-Za-z_][\w$]*)\s*\(",
    re.DOTALL | re.MULTILINE,
)
_DIAGNOSTIC_RE = re.compile(
    r"(?P<source>(?:/|\.{1,2}/)?[^:\n]+?\.(?:sv|svh|v|vh)):"
    r"(?P<line>\d+)(?::(?P<column>\d+))?:\s*"
    r"(?P<severity>error|warning|note|info):\s*(?P<message>.*)",
    re.IGNORECASE,
)
_DIAGNOSTIC_FALLBACK_RE = re.compile(
    r"(?P<severity>error|warning|note|info):\s*(?P<message>.*)",
    re.IGNORECASE,
)
_SUMMARY_ERRORS_RE = re.compile(r"\b(?P<count>\d+)\s+errors?\b", re.IGNORECASE)
_SUMMARY_WARNINGS_RE = re.compile(r"\b(?P<count>\d+)\s+warnings?\b", re.IGNORECASE)
_INSTANCE_KEYWORDS = {
    "always",
    "assign",
    "begin",
    "case",
    "else",
    "end",
    "for",
    "forever",
    "fork",
    "function",
    "generate",
    "if",
    "initial",
    "interface",
    "module",
    "package",
    "primitive",
    "property",
    "repeat",
    "task",
    "while",
}
_SYSTEMVERILOG_PRIMITIVES = {
    "and",
    "buf",
    "bufif0",
    "bufif1",
    "cmos",
    "nand",
    "nmos",
    "nor",
    "not",
    "notif0",
    "notif1",
    "or",
    "pmos",
    "pullup",
    "pulldown",
    "rcmos",
    "rnmos",
    "rpmos",
    "rtran",
    "rtranif0",
    "rtranif1",
    "tran",
    "tranif0",
    "tranif1",
    "xnor",
    "xor",
}


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
        run_info = self._run_elaborate(step, workspace)
        self._write_report(step, workspace, run_info)

    def check_result(self, step: WorkspaceStep) -> bool:
        log_path = Path(step.report["dir"]) / "log.txt"
        if not log_path.exists():
            return False
        content = log_path.read_text(encoding="utf-8")
        return self._is_elab_log_ok(content)

    def _run_elaborate(
        self,
        step: WorkspaceStep,
        workspace: dict[str, Any],
    ) -> dict[str, Any]:
        files = rtl_files(workspace)
        top       = workspace.get("top_module", "top")
        log_path = Path(step.report["dir"]) / "log.txt"

        cmd = [
            _slang_cmd(),
            "--lint-only",
            "--allow-use-before-declare",
            "--timescale", "1ns/1ps",
            "--top", top,
            "--diag-column",
            "--diag-location",
            "--diag-source",
            *slang_incdir_args(workspace),
            *slang_define_args(workspace),
        ] + files

        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            returncode = result.returncode
            output = (result.stdout + result.stderr).strip() or "Build succeeded: 0 errors, 0 warnings"
        except OSError as exc:
            returncode = 127
            output = f"error: failed to execute slang: {exc}"
        log_path.write_text(output, encoding="utf-8")

        ok = returncode == 0
        update_substep_ok(
            step,
            SlangSubFlowEnum.elaborate.value,
            ok,
            info={
                "top_module": top,
                "rtl_files": len(files),
                "returncode": returncode,
            },
        )
        return {
            "command": cmd,
            "returncode": returncode,
            "rtl_files": files,
            "top_module": top,
            "log_path": str(log_path),
        }

    def _write_report(
        self,
        step: WorkspaceStep,
        workspace: dict[str, Any],
        run_info: dict[str, Any],
    ) -> None:
        log_path = Path(step.report["dir"]) / "log.txt"
        content = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        ok = self._is_elab_log_ok(content)
        summary_path = Path(step.report["dir"]) / "elab_summary.json"
        elab_summary = build_elab_summary(
            workspace,
            run_info,
            content,
            summary_path=summary_path,
        )
        json_write(summary_path, elab_summary)

        json_write(step.report["step"], {
            "elaborate": "pass" if ok else "fail",
            "report":    str(log_path),
            "summary":   str(summary_path),
            "errors":    elab_summary["summary"]["errors"],
            "warnings":  elab_summary["summary"]["warnings"],
            "modules":   elab_summary["summary"]["modules"],
            "unresolved_modules": elab_summary["summary"]["unresolved_modules"],
        })
        update_substep_ok(
            step,
            SlangSubFlowEnum.report.value,
            True,
            info={
                "summary": str(summary_path),
                "errors": elab_summary["summary"]["errors"],
                "warnings": elab_summary["summary"]["warnings"],
                "modules": elab_summary["summary"]["modules"],
                "unresolved_modules": elab_summary["summary"]["unresolved_modules"],
            },
        )

    @staticmethod
    def _is_elab_log_ok(content: str) -> bool:
        text = content.lower()
        if "error:" not in text:
            return True
        return re.search(r"\b0\s+errors\b", text) is not None


def build_elab_summary(
    workspace: dict[str, Any],
    run_info: dict[str, Any],
    log_content: str,
    *,
    summary_path: Path,
) -> dict[str, Any]:
    """Build a human-readable structural ELAB summary from Slang and RTL inputs."""
    files = [str(path) for path in run_info.get("rtl_files", [])]
    structure = scan_rtl_structure(files)
    diagnostics = parse_slang_diagnostics(log_content)
    errors = _diagnostic_count(log_content, diagnostics, "error")
    warnings = _diagnostic_count(log_content, diagnostics, "warning")
    status = "pass" if int(run_info.get("returncode", 1)) == 0 and errors == 0 else "fail"
    top_module = str(run_info.get("top_module") or workspace.get("top_module") or "top")

    return {
        "path": str(summary_path),
        "tool": "slang",
        "status": status,
        "returncode": int(run_info.get("returncode", 1)),
        "top_module": top_module,
        "command": [str(part) for part in run_info.get("command", [])],
        "inputs": {
            "rtl_files": files,
            "rtl_file_count": len(files),
            "incdirs": incdirs(workspace),
            "defines": slang_defines(workspace),
        },
        "summary": {
            "status": status,
            "errors": errors,
            "warnings": warnings,
            "rtl_files": len(files),
            "modules": len(structure["modules"]),
            "referenced_modules": len(structure["referenced_modules"]),
            "unresolved_modules": len(structure["unresolved_modules"]),
            "top_module": top_module,
            "top_found": top_module in {str(item.get("module")) for item in structure["modules"]},
        },
        "diagnostics": diagnostics,
        "modules": structure["modules"],
        "referenced_modules": structure["referenced_modules"],
        "unresolved_modules": structure["unresolved_modules"],
        "reports": {
            "log": str(run_info.get("log_path", "")),
            "summary": str(summary_path),
        },
    }


def scan_rtl_structure(files: list[str]) -> dict[str, Any]:
    """Collect a lightweight module / instance inventory from RTL sources."""
    module_by_name: dict[str, dict[str, Any]] = {}
    referenced: set[str] = set()

    for raw_path in files:
        path = Path(raw_path)
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        stripped = _strip_comments(content)
        for block in _MODULE_BLOCK_RE.finditer(stripped):
            name = block.group("name")
            body = block.group("body") or ""
            module = module_by_name.setdefault(
                name,
                {
                    "module": name,
                    "path": str(path),
                    "line": _line_number(stripped, block.start()),
                    "ports": _count_ports(body),
                    "parameters": _count_parameters(body),
                    "instances": 0,
                    "instantiates": [],
                },
            )
            instantiates = _module_instantiates(body)
            module["instances"] = len(instantiates)
            module["instantiates"] = sorted(set(instantiates))
            referenced.update(instantiates)

    defined = set(module_by_name)
    unresolved = sorted(
        item
        for item in referenced
        if item not in defined
        and item not in _SYSTEMVERILOG_PRIMITIVES
        and item.lower() not in _INSTANCE_KEYWORDS
    )
    modules = sorted(
        module_by_name.values(),
        key=lambda item: (-int(item.get("instances", 0)), str(item.get("module", ""))),
    )
    return {
        "modules": modules,
        "referenced_modules": sorted(referenced),
        "unresolved_modules": unresolved,
    }


def parse_slang_diagnostics(content: str) -> list[dict[str, Any]]:
    """Parse Slang log diagnostics into clickable records."""
    diagnostics: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int, str]] = set()
    for line in content.splitlines():
        text = line.strip()
        if not text:
            continue
        diagnostic = _parse_slang_diagnostic_line(text)
        if not diagnostic:
            continue
        key = (
            diagnostic["severity"],
            str(diagnostic.get("source", "")),
            int(diagnostic.get("line", 0) or 0),
            int(diagnostic.get("column", 0) or 0),
            diagnostic["message"],
        )
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append(diagnostic)
    return diagnostics


def _parse_slang_diagnostic_line(line: str) -> dict[str, Any] | None:
    match = _DIAGNOSTIC_RE.search(line)
    if match:
        severity = _normalize_diagnostic_severity(match.group("severity"))
        return {
            "severity": severity,
            "message": match.group("message").strip(),
            "source": match.group("source").strip(),
            "line": int(match.group("line") or 0),
            "column": int(match.group("column") or 1),
        }

    fallback = _DIAGNOSTIC_FALLBACK_RE.search(line)
    if not fallback:
        return None
    severity = _normalize_diagnostic_severity(fallback.group("severity"))
    return {
        "severity": severity,
        "message": fallback.group("message").strip(),
        "source": "",
        "line": 0,
        "column": 0,
    }


def _normalize_diagnostic_severity(value: str) -> str:
    lowered = value.lower()
    if lowered == "warning":
        return "warning"
    if lowered == "error":
        return "error"
    return "info"


def _diagnostic_count(
    content: str,
    diagnostics: list[dict[str, Any]],
    severity: str,
) -> int:
    pattern = _SUMMARY_ERRORS_RE if severity == "error" else _SUMMARY_WARNINGS_RE
    matches = [int(match.group("count")) for match in pattern.finditer(content)]
    if matches:
        return matches[-1]
    return len([item for item in diagnostics if item.get("severity") == severity])


def _strip_comments(content: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), content, flags=re.DOTALL)
    return re.sub(r"//.*", "", without_block)


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _count_parameters(module_body: str) -> int:
    return len(re.findall(r"\bparameter\b|\blocalparam\b", module_body))


def _count_ports(module_body: str) -> int:
    header = module_body.split(";", 1)[0]
    match = re.search(r"\((?P<ports>.*)\)", header, flags=re.DOTALL)
    if not match:
        return 0
    ports = match.group("ports")
    names = re.findall(
        r"(?:input|output|inout|ref)?\s*(?:wire|reg|logic|signed|unsigned|\[[^\]]+\]\s*)*"
        r"(?P<name>[A-Za-z_][\w$]*)\s*(?:,|$)",
        ports,
        flags=re.MULTILINE,
    )
    return len([name for name in names if name not in {"input", "output", "inout", "wire", "reg", "logic"}])


def _module_instantiates(module_body: str) -> list[str]:
    candidates: list[str] = []
    for match in _INSTANCE_RE.finditer(module_body):
        module_type = match.group("type")
        instance_name = match.group("name")
        if module_type.lower() in _INSTANCE_KEYWORDS:
            continue
        if module_type in {"logic", "wire", "reg", "assign"}:
            continue
        if instance_name in {"if", "for", "while"}:
            continue
        candidates.append(module_type)
    return candidates
