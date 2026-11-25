"""Verilator lint step: run `verilator --lint-only` and report what it found."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.common.process import run_tool, tool_timeout
from fecompiler.tools.common.rtl_inputs import (
    incdirs,
    rtl_files,
    verilator_incdir_args,
    verilator_library_args,
    verilator_lint_define_args,
    verilator_lint_defines,
)
from fecompiler.tools.common.rtl_ownership import (
    RTL_OWNERSHIPS,
    is_actionable_rtl_ownership,
    rtl_ownership_map,
    rtl_source_ownership,
    user_rtl_ownership,
)
from fecompiler.tools.fe.base import BaseStep
from fecompiler.tools.fe.subflow import update_substep_ok
from fecompiler.tools.verilator.command import verilator_cmd as _verilator_cmd
from fecompiler.tools.verilator.subflow import LintSubFlowEnum, init_lint_subflow
from fecompiler.tools.common.tool_config import (
    config_list,
    config_paths,
    config_text,
    design_parameter_overrides,
)
from fecompiler.utility.json import json_read, json_write

LINT_PROFILES = {
    # Every Verilator lint and style warning.
    "all": ("-Wall",),
    # Verilator's default set: the warnings it considers likely bugs.
    "basic": (),
}
BASELINE_SCHEMA_VERSION = 1
_RULE_NAME = re.compile(r"[A-Z][A-Z0-9_]*")

_VERILATOR_DIAGNOSTIC_RE = re.compile(
    r"^%(?P<severity>Error|Warning)(?:-(?P<code>[A-Za-z0-9_]+))?:\s+"
    r"(?P<source>.+?):(?P<line>\d+):(?:(?P<column>\d+):)?\s*(?P<message>.*)$"
)


class VerilatorLintStep(BaseStep):
    """Run verilator --lint-only on the RTL.

    Sub-steps: lint → report
    Success condition: log.txt exists and contains no %Error.
    """

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_lint_subflow(step)
        run_info = self._run_lint(step, workspace)
        self._write_report(step, workspace, run_info)

    def check_result(self, step: WorkspaceStep) -> bool:
        summary_path = Path(step.report["dir"]) / "lint_summary.json"
        if summary_path.exists():
            summary = json_read(str(summary_path))
            if isinstance(summary, dict):
                return str(summary.get("status", "")).lower() == "pass"
        lint_path = Path(step.report["dir"]) / "log.txt"
        return lint_path.exists() and "%Error" not in lint_path.read_text(encoding="utf-8")

    def _run_lint(self, step: WorkspaceStep, workspace: dict[str, Any]) -> dict[str, Any]:
        files = rtl_files(workspace)
        top   = workspace.get("top_module", "top")
        lint_path = Path(step.report["dir"]) / "log.txt"

        lint_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            options = lint_options(workspace)
        except ValueError as error:
            # A setting that cannot be used is reported like a tool failure.
            lint_path.write_text(f"%Error: {error}", encoding="utf-8")
            update_substep_ok(
                step,
                LintSubFlowEnum.lint.value,
                False,
                info={"top_module": top, "rtl_files": len(files), "error": str(error)},
            )
            return {
                "command": [],
                "returncode": 2,
                "rtl_files": files,
                "top_module": top,
                "log_path": str(lint_path),
            }
        cmd = [
            _verilator_cmd(),
            "--lint-only",
            *options["warning_args"],
            "-Wno-fatal",
            "-Wno-DECLFILENAME",
            *verilator_incdir_args(workspace),
            *verilator_library_args(workspace),
            *verilator_lint_define_args(workspace),
            *options["parameter_args"],
            *options["extra_args"],
            "--top",
            top,
            *options["waiver_files"],
        ] + files
        timeout = tool_timeout("lint")
        try:
            result = run_tool(
                cmd,
                log_path=lint_path,
                timeout=timeout,
                session_dir=Path(step.log["dir"]),
            )
            returncode = result.returncode
            output = result.output.strip() or "lint OK"
            if result.timed_out:
                output += f"\n%Error: verilator lint did not finish within {timeout:g}s"
        except OSError as exc:
            returncode = 127
            output = f"%Error: failed to execute verilator: {exc}"
        lint_path.write_text(output, encoding="utf-8")
        update_substep_ok(
            step,
            LintSubFlowEnum.lint.value,
            returncode == 0,
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
            "log_path": str(lint_path),
        }

    def _write_report(
        self,
        step: WorkspaceStep,
        workspace: dict[str, Any],
        run_info: dict[str, Any],
    ) -> None:
        lint_path = Path(step.report["dir"]) / "log.txt"
        log_content = lint_path.read_text(encoding="utf-8") if lint_path.exists() else ""
        lint_summary_path = Path(step.report["dir"]) / "lint_summary.json"
        lint_summary = build_lint_summary(
            workspace,
            run_info,
            log_content,
            summary_path=lint_summary_path,
        )
        json_write(lint_summary_path, lint_summary)
        gate = lint_summary["baseline"]
        if gate["gate"] == "failed":
            # Say why lint failed where the run output and `log` show it.
            reason = gate["error"] or (
                f"{gate['new_actionable']} warning(s) are not in the lint baseline "
                f"{gate['path']}; fix them, waive them, or accept them with "
                "`ecc-fe report lint-baseline`"
            )
            with lint_path.open("a", encoding="utf-8") as stream:
                stream.write(f"\necc-fe: lint baseline gate failed: {reason}\n")
        lint_ok = lint_summary["status"] == "pass"
        json_write(step.report["step"], {
            "lint": "pass" if lint_ok else "fail",
            "report": str(lint_path),
            "summary": str(lint_summary_path),
            "errors": lint_summary["summary"]["errors"],
            "warnings": lint_summary["summary"]["warnings"],
            **_ownership_counts(lint_summary["summary"]),
            "rules": lint_summary["summary"]["rules"],
            "files": lint_summary["summary"]["files"],
            "baseline": lint_summary["baseline"],
        })
        update_substep_ok(
            step,
            LintSubFlowEnum.report.value,
            True,
            info={
                "summary": str(lint_summary_path),
                "errors": lint_summary["summary"]["errors"],
                "warnings": lint_summary["summary"]["warnings"],
                **_ownership_counts(lint_summary["summary"]),
                "rules": lint_summary["summary"]["rules"],
                "files": lint_summary["summary"]["files"],
            },
        )


_OWNERSHIP_COUNT_FIELDS = ("cpu_errors", "cpu_warnings", "design_errors", "design_warnings")


def _ownership_counts(summary: dict[str, Any]) -> dict[str, Any]:
    return {field: summary[field] for field in _OWNERSHIP_COUNT_FIELDS if field in summary}


def build_lint_summary(
    workspace: dict[str, Any],
    run_info: dict[str, Any],
    log_content: str,
    *,
    summary_path: Path,
) -> dict[str, Any]:
    """Build a structured Verilator lint report for GUI consumption."""
    files = [str(path) for path in run_info.get("rtl_files", [])]
    diagnostics = parse_verilator_diagnostics(log_content)
    if int(run_info.get("returncode", 0)) != 0 and not diagnostics:
        diagnostics.append({
            "severity": "error",
            "code": "TOOL",
            "message": _first_nonempty_log_line(log_content) or "Verilator exited with a non-zero return code.",
            "source": "",
            "line": 0,
            "column": 0,
            "raw": _first_nonempty_log_line(log_content),
            "category": "tool",
        })
    ownership_by_path = rtl_ownership_map(workspace)
    for diagnostic in diagnostics:
        ownership = (
            "tool"
            if diagnostic.get("category") == "tool"
            else rtl_source_ownership(
                workspace,
                str(diagnostic.get("source", "")),
                ownership_by_path,
            )
        )
        diagnostic["ownership"] = ownership
        diagnostic["actionable"] = is_actionable_rtl_ownership(workspace, ownership)
    errors = len([item for item in diagnostics if item.get("severity") == "error"])
    warnings = len([item for item in diagnostics if item.get("severity") == "warning"])
    baseline = apply_lint_baseline(workspace, diagnostics)
    status = "pass" if int(run_info.get("returncode", 1)) == 0 and errors == 0 else "fail"
    if baseline["gate"] == "failed":
        # Warnings the baseline does not know are not allowed in.
        status = "fail"
    top_module = str(run_info.get("top_module") or workspace.get("top_module") or "top")
    rules = _lint_rule_breakdown(diagnostics)
    file_hotspots = _lint_file_hotspots(diagnostics)
    ownership = _lint_ownership_breakdown(diagnostics)
    cpu_diagnostics = [item for item in diagnostics if item.get("ownership") == "cpu"]
    design_diagnostics = [item for item in diagnostics if item.get("ownership") == "design"]
    actionable_diagnostics = [item for item in diagnostics if item.get("actionable") is True]

    return {
        "schema_version": 1,
        "path": str(summary_path),
        "tool": "verilator",
        "status": status,
        "returncode": int(run_info.get("returncode", 1)),
        "top_module": top_module,
        "command": [str(part) for part in run_info.get("command", [])],
        "inputs": {
            "rtl_files": files,
            "rtl_file_count": len(files),
            "incdirs": incdirs(workspace),
            "defines": verilator_lint_defines(workspace),
        },
        "summary": {
            "status": status,
            "errors": errors,
            "warnings": warnings,
            "diagnostics": len(diagnostics),
            "rules": len(rules),
            "files": len(file_hotspots),
            "rtl_files": len(files),
            "top_module": top_module,
            # A design that is not a CPU has no CPU sources to count.
            **(
                {
                    "cpu_errors": len([item for item in cpu_diagnostics if item.get("severity") == "error"]),
                    "cpu_warnings": len([item for item in cpu_diagnostics if item.get("severity") == "warning"]),
                }
                if user_rtl_ownership(workspace) == "cpu"
                else {}
            ),
            "design_errors": len([item for item in design_diagnostics if item.get("severity") == "error"]),
            "design_warnings": len([item for item in design_diagnostics if item.get("severity") == "warning"]),
            "actionable_ownership": user_rtl_ownership(workspace),
            "actionable_diagnostics": len(actionable_diagnostics),
            "new_diagnostics": baseline["new"],
            "baseline_gate": baseline["gate"],
        },
        "settings": _lint_settings(workspace),
        "baseline": baseline,
        "diagnostics": diagnostics,
        "rules": rules,
        "files": file_hotspots,
        "ownership": ownership,
        "reports": {
            "log": str(run_info.get("log_path", "")),
            "summary": str(summary_path),
        },
    }


def parse_verilator_diagnostics(content: str) -> list[dict[str, Any]]:
    """Parse Verilator lint diagnostics into clickable records."""
    diagnostics: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int, int, str]] = set()

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _VERILATOR_DIAGNOSTIC_RE.match(line)
        if not match:
            generic = _parse_generic_verilator_diagnostic(line)
            if not generic:
                continue
            key = (
                str(generic.get("severity", "")),
                str(generic.get("code", "")),
                str(generic.get("source", "")),
                int(generic.get("line", 0) or 0),
                int(generic.get("column", 0) or 0),
                str(generic.get("message", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            diagnostics.append(generic)
            continue
        severity = "error" if match.group("severity").lower() == "error" else "warning"
        code = (match.group("code") or severity.upper()).strip()
        source = match.group("source").strip()
        line_number = int(match.group("line") or 0)
        column = int(match.group("column") or 1)
        message = match.group("message").strip()
        key = (severity, code, source, line_number, column, message)
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append({
            "severity": severity,
            "code": code,
            "message": message,
            "source": source,
            "line": line_number,
            "column": column,
            "raw": line,
            "category": _lint_category(code, message),
        })

    return diagnostics


# Verilator's closing lines repeat that errors happened; they are not errors.
_VERILATOR_CLOSING_RE = re.compile(r"(Exiting due to \d+ (error|warning)\(s\)|Command Failed)", re.IGNORECASE)


def _parse_generic_verilator_diagnostic(line: str) -> dict[str, Any] | None:
    generic = re.match(r"^%(?P<severity>Error|Warning)(?:-(?P<code>[A-Za-z0-9_]+))?:\s*(?P<message>.*)$", line)
    if not generic:
        return None
    if _VERILATOR_CLOSING_RE.match(generic.group("message").strip()):
        return None
    severity = "error" if generic.group("severity").lower() == "error" else "warning"
    code = (generic.group("code") or severity.upper()).strip()
    message = generic.group("message").strip()
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "source": "",
        "line": 0,
        "column": 0,
        "raw": line,
        "category": _lint_category(code, message),
    }


def _lint_rule_breakdown(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_code: dict[str, dict[str, Any]] = {}
    for item in diagnostics:
        code = str(item.get("code") or item.get("severity") or "UNKNOWN")
        record = by_code.setdefault(
            code,
            {
                "code": code,
                "category": _lint_category(code, str(item.get("message", ""))),
                "errors": 0,
                "warnings": 0,
                "total": 0,
                "example": str(item.get("message", "")),
                "ownership": Counter(),
            },
        )
        if item.get("severity") == "error":
            record["errors"] += 1
        elif item.get("severity") == "warning":
            record["warnings"] += 1
        record["total"] += 1
        record["ownership"][str(item.get("ownership", "unknown"))] += 1

    for record in by_code.values():
        record["ownership"] = dict(sorted(record["ownership"].items()))

    return sorted(
        by_code.values(),
        key=lambda item: (-int(item.get("errors", 0)), -int(item.get("warnings", 0)), str(item.get("code", ""))),
    )


def _lint_file_hotspots(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source: dict[str, dict[str, Any]] = {}
    for item in diagnostics:
        source = str(item.get("source", "")).strip()
        if not source:
            continue
        record = by_source.setdefault(
            source,
            {
                "path": source,
                "errors": 0,
                "warnings": 0,
                "total": 0,
                "rules": set(),
                "ownership": str(item.get("ownership", "unknown")),
            },
        )
        if item.get("severity") == "error":
            record["errors"] += 1
        elif item.get("severity") == "warning":
            record["warnings"] += 1
        record["total"] += 1
        record["rules"].add(str(item.get("code") or item.get("severity") or "UNKNOWN"))

    hotspots: list[dict[str, Any]] = []
    for source, record in by_source.items():
        hotspots.append({
            "path": source,
            "label": Path(source).name,
            "errors": int(record["errors"]),
            "warnings": int(record["warnings"]),
            "total": int(record["total"]),
            "rules": sorted(record["rules"]),
            "ownership": str(record["ownership"]),
            "actionable": any(
                item.get("actionable") is True
                for item in diagnostics
                if str(item.get("source", "")).strip() == source
            ),
        })
    return sorted(
        hotspots,
        key=lambda item: (-int(item.get("errors", 0)), -int(item.get("warnings", 0)), str(item.get("path", ""))),
    )


def _lint_ownership_breakdown(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for ownership in RTL_OWNERSHIPS:
        owned = [item for item in diagnostics if item.get("ownership") == ownership]
        if not owned:
            continue
        records.append({
            "ownership": ownership,
            "errors": len([item for item in owned if item.get("severity") == "error"]),
            "warnings": len([item for item in owned if item.get("severity") == "warning"]),
            "total": len(owned),
            "actionable": any(item.get("actionable") is True for item in owned),
        })
    return records


def _lint_category(code: str, message: str) -> str:
    text = f"{code} {message}".lower()
    if "failed to execute" in text or "non-zero" in text or code.lower() == "tool":
        return "tool"
    if "syntax" in text or "unexpected" in text:
        return "syntax"
    if "width" in text or "bit" in text or "range" in text:
        return "width"
    if "unused" in text or "unuse" in text:
        return "unused"
    if "undriven" in text or "unconnected" in text or "pinmissing" in text:
        return "connectivity"
    if "multidriven" in text or "driven" in text:
        return "drivers"
    if "case" in text:
        return "case"
    if "latch" in text:
        return "latch"
    if "timing" in text or "delay" in text:
        return "timing"
    if "unsupported" in text or "unsup" in text:
        return "unsupported"
    return "lint"


def _first_nonempty_log_line(content: str) -> str:
    for line in content.splitlines():
        text = line.strip()
        if text:
            return text
    return ""


# ── settings ──────────────────────────────────────────────────────────────────


def lint_options(workspace: dict[str, Any]) -> dict[str, list[str]]:
    """Turn the lint settings of a workspace into Verilator arguments.

    Raises ``ValueError`` naming the setting when one cannot be used.
    """
    profile = config_text(workspace, "lint_profile", "all").lower()
    if profile not in LINT_PROFILES:
        raise ValueError(
            f"lint.profile must be one of {', '.join(LINT_PROFILES)}; got {profile}"
        )
    rules_off = config_list(workspace, "lint_rules_off")
    invalid = [rule for rule in rules_off if not _RULE_NAME.fullmatch(rule)]
    if invalid:
        raise ValueError(
            "lint.rules_off entries must be Verilator rule names such as "
            "UNUSEDSIGNAL: " + ", ".join(invalid)
        )
    waiver_files = config_paths(workspace, "lint_waivers")
    missing = [str(path) for path in waiver_files if not path.is_file()]
    if missing:
        raise ValueError("lint.waiver_files not found: " + ", ".join(missing))
    return {
        "warning_args": [*LINT_PROFILES[profile], *(f"-Wno-{rule}" for rule in rules_off)],
        "parameter_args": [f"-G{item}" for item in design_parameter_overrides(workspace)],
        "extra_args": config_list(workspace, "lint_extra_args"),
        "waiver_files": [str(path) for path in waiver_files],
    }


def _lint_settings(workspace: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": config_text(workspace, "lint_profile", "all").lower(),
        "rules_off": config_list(workspace, "lint_rules_off"),
        "waiver_files": [str(path) for path in config_paths(workspace, "lint_waivers")],
        "extra_args": config_list(workspace, "lint_extra_args"),
        "parameters": config_list(workspace, "design_parameters"),
    }


# ── baseline ──────────────────────────────────────────────────────────────────


def diagnostic_fingerprint(diagnostic: dict[str, Any]) -> tuple[str, str, str]:
    """Identify a diagnostic by rule, file name, and message.

    Line and column are left out so that editing code above a known warning
    does not turn it into a new one, and only the file name is used so a
    baseline stays valid when the design is checked out elsewhere.
    """
    return (
        str(diagnostic.get("code", "")),
        Path(str(diagnostic.get("source", "") or "")).name,
        str(diagnostic.get("message", "")),
    )


def lint_baseline_entries(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return baseline entries: each fingerprint with how often it occurs."""
    counts = Counter(
        diagnostic_fingerprint(item)
        for item in diagnostics
        if item.get("severity") == "warning"
    )
    return [
        {"code": code, "file": name, "message": message, "count": count}
        for (code, name, message), count in sorted(counts.items())
    ]


def apply_lint_baseline(
    workspace: dict[str, Any], diagnostics: list[dict[str, Any]]
) -> dict[str, Any]:
    """Mark each warning as known or new against the configured baseline.

    The gate fails when a warning the user owns is not covered by the
    baseline. Without a baseline nothing is marked and the gate is off.
    """
    configured = config_paths(workspace, "lint_baseline")
    result: dict[str, Any] = {
        "path": str(configured[0]) if configured else "",
        "gate": "off",
        "known": 0,
        "new": 0,
        "new_actionable": 0,
        "fixed": 0,
        "error": "",
    }
    if not configured:
        return result
    try:
        payload = json_read(str(configured[0]))
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != BASELINE_SCHEMA_VERSION
            or not isinstance(entries, list)
        ):
            raise ValueError("not a lint baseline written by `ecc-fe report lint-baseline`")
        remaining = Counter({
            (str(entry["code"]), str(entry["file"]), str(entry["message"])): int(entry.get("count", 1))
            for entry in entries
        })
    except (OSError, ValueError, KeyError, TypeError) as error:
        # A baseline that cannot be read must not let everything through.
        result.update(
            {
                "gate": "failed",
                "error": f"cannot read lint baseline {configured[0]}: {error}",
            }
        )
        return result
    for diagnostic in diagnostics:
        if diagnostic.get("severity") != "warning":
            continue
        fingerprint = diagnostic_fingerprint(diagnostic)
        if remaining[fingerprint] > 0:
            remaining[fingerprint] -= 1
            diagnostic["baseline"] = "known"
            result["known"] += 1
        else:
            diagnostic["baseline"] = "new"
            result["new"] += 1
            if diagnostic.get("actionable") is True:
                result["new_actionable"] += 1
    result["fixed"] = sum(count for count in remaining.values() if count > 0)
    result["gate"] = "failed" if result["new_actionable"] else "passed"
    return result
