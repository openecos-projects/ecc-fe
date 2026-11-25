"""Read the Yosys log of the structural precheck into diagnostics.
"""

from __future__ import annotations

import re
from typing import Any

from fecompiler.tools.review.probe_common import (
    _int_or_zero,
)


def _diagnostics_from_log(log: str) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for raw in log.splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        severity = ""
        if "error:" in lower or lower.startswith("error") or "syntax error" in lower:
            severity = "error"
        elif "warning:" in lower or lower.startswith("warning"):
            severity = "warning"
        if not severity:
            continue
        location = _diagnostic_location(line)
        diagnostics.append({
            "severity": severity,
            "message": line,
            "category": _diagnostic_category(line),
            "source": location.get("source", ""),
            "line": location.get("line", 0),
            "column": location.get("column", 0),
        })
        if len(diagnostics) >= 80:
            break
    return diagnostics


def _diagnostic_location(message: str) -> dict[str, Any]:
    patterns = (
        r"(?P<source>/[^:\s]+?\.(?:sv|svh|v|vh)):(?P<line>\d+):(?P<column>\d+)",
        r"(?P<source>/[^:\s]+?\.(?:sv|svh|v|vh)):(?P<line>\d+)",
        r"(?P<source>[^:\s]+?\.(?:sv|svh|v|vh)):(?P<line>\d+):(?P<column>\d+)",
        r"(?P<source>[^:\s]+?\.(?:sv|svh|v|vh)):(?P<line>\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if not match:
            continue
        source = str(match.group("source"))
        return {
            "source": source,
            "line": _int_or_zero(match.group("line")),
            "column": _int_or_zero(match.groupdict().get("column")) or 1,
        }
    return {"source": "", "line": 0, "column": 0}


def _diagnostic_category(message: str) -> str:
    text = message.lower()
    if _is_yosys_tool_limit_message(text):
        return "tool-limit"
    if "syntax" in text or "parse" in text:
        return "syntax"
    if "module" in text and ("not found" in text or "missing" in text):
        return "hierarchy"
    if "latch" in text:
        return "combinational"
    if "driver" in text or "driven" in text:
        return "structural"
    if "loop" in text:
        return "combinational"
    return "tooling"


def _is_yosys_tool_limit_message(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            "feature unimplemented",
            "failed condition",
            "slang_frontend.cc",
            "yosys-slang-plugin",
            "unsupported",
            "not supported",
        )
    )


def _yosys_failure_reason(diagnostics: list[dict[str, Any]], log: str, returncode: int) -> str:
    first_error = next((item for item in diagnostics if item.get("severity") == "error"), None)
    first_warning = next((item for item in diagnostics if item.get("severity") == "warning"), None)
    diagnostic = first_error or first_warning
    if diagnostic:
        source = str(diagnostic.get("source") or "")
        line = _int_or_zero(diagnostic.get("line"))
        prefix = f"{source}:{line}: " if source and line else ""
        return prefix + str(diagnostic.get("message") or "Yosys reported a diagnostic").strip()

    for raw in log.splitlines():
        line = raw.strip()
        if line:
            return f"Yosys exited with code {returncode}: {line[:180]}"
    return f"Yosys exited with code {returncode}"
