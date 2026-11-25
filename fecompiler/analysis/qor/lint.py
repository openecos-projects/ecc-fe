"""QoR of the lint step: errors and warning density."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.analysis import scoring
from fecompiler.analysis.qor.common import (
    _gate,
    _hotspot,
    _hotspot_severity,
    _metric,
    _number,
    _QorResult,
    _read_record,
    _record,
    _records,
    _relative_report_source,
    _rtl_lines,
    _source,
    _valid_count_fields,
)
from fecompiler.analysis.qor.fingerprint import (
    _prepared_input_fingerprint,
)
from fecompiler.analysis.scoring import scoring_policy
from fecompiler.tools.common.rtl_ownership import user_rtl_ownership


def _lint_qor(step: Any, workspace: dict[str, Any]) -> _QorResult:
    path = Path(step.report["dir"]) / "lint_summary.json"
    report = _read_record(path)
    result = _QorResult(source_available=report is not None)
    if report is None:
        return result
    summary = _record(report.get("summary"))
    ownership = user_rtl_ownership(workspace)
    error_field = f"{ownership}_errors"
    warning_field = f"{ownership}_warnings"
    result.source_available = _valid_count_fields(
        summary, (error_field, warning_field, "warnings")
    )
    source = _source("report/lint_summary.json", "/summary")
    errors = _number(summary.get(error_field))
    warnings = _number(summary.get(warning_field))
    ownership_label = "CPU" if ownership == "cpu" else "Design"
    result.metrics.extend(
        [
            _metric(
                f"{ownership}_lint_error_count",
                f"{ownership_label} Lint Errors",
                errors,
                "count",
                "lint",
                "lower_is_better",
                f"{ownership}_lint",
                source,
                gate=True,
            ),
            _metric(
                f"{ownership}_lint_warning_count",
                f"{ownership_label} Lint Warnings",
                warnings,
                "count",
                "lint",
                "lower_is_better",
                f"{ownership}_lint",
                source,
            ),
            _metric(
                "all_lint_warning_count",
                "All Lint Warnings",
                _number(summary.get("warnings")),
                "count",
                "lint",
                "trend_only",
                "full_design_lint",
                source,
            ),
        ]
    )
    result.gates.append(
        _gate(
            f"no_{ownership}_lint_errors",
            f"No {ownership_label}-owned lint errors",
            errors,
            "==",
            0,
            source,
        )
    )
    diagnostics = [
        (index, item)
        for index, item in enumerate(_records(report.get("diagnostics")))
        if item.get("actionable") is True
        and str(item.get("ownership", "")) == ownership
    ][:50]
    result.hotspots.extend(
        _hotspot(
            f"{ownership}_lint_diagnostic",
            str(item.get("code") or "Lint diagnostic"),
            _hotspot_severity(item.get("severity")),
            None,
            _source(
                _relative_report_source(item.get("source"), "report/lint_summary.json"),
                f"/diagnostics/{index}",
            ),
            str(item.get("message") or f"Review this {ownership_label}-owned lint diagnostic."),
        )
        for index, item in diagnostics
    )
    result.score = _lint_score(
        report, summary, workspace, _rtl_lines(step, workspace, user_owned=True)
    )
    result.comparison = {
        "input_fingerprint": _prepared_input_fingerprint(step),
        "top_module": str(
            report.get("top_module")
            or summary.get("top_module")
            or workspace.get("top_module")
            or ""
        ),
    }
    return result


def _lint_score(
    report: dict[str, Any], summary: dict[str, Any], workspace: dict[str, Any], lines: int
) -> dict[str, Any] | None:
    raw_diagnostics = report.get("diagnostics")
    diagnostics = _records(raw_diagnostics)
    status = str(report.get("status", "")).strip().lower()
    returncode = report.get("returncode")
    ownership = user_rtl_ownership(workspace)
    ownership_label = "CPU" if ownership == "cpu" else "Design"
    error_field = f"{ownership}_errors"
    warning_field = f"{ownership}_warnings"
    if (
        report.get("schema_version") != 1
        or report.get("tool") != "verilator"
        or status not in {"pass", "fail"}
        or not isinstance(returncode, int)
        or isinstance(returncode, bool)
        or not isinstance(raw_diagnostics, list)
        or len(diagnostics) != len(raw_diagnostics)
        or not _valid_count_fields(
            summary,
            (
                "errors",
                "warnings",
                "diagnostics",
                error_field,
                warning_field,
                "actionable_diagnostics",
            ),
        )
        or str(summary.get("status", "")).strip().lower() != status
    ):
        return None

    actionable_diagnostics = [
        item
        for item in diagnostics
        if item.get("actionable") is True and item.get("ownership") == ownership
    ]
    owned_errors = sum(item.get("severity") == "error" for item in actionable_diagnostics)
    owned_warnings = sum(item.get("severity") == "warning" for item in actionable_diagnostics)
    total_errors = sum(item.get("severity") == "error" for item in diagnostics)
    total_warnings = sum(item.get("severity") == "warning" for item in diagnostics)
    if (
        any(
            item.get("severity") not in {"error", "warning"}
            or not isinstance(item.get("code"), str)
            or not isinstance(item.get("ownership"), str)
            or not isinstance(item.get("actionable"), bool)
            for item in diagnostics
        )
        or int(summary["diagnostics"]) != len(diagnostics)
        or int(summary["errors"]) != total_errors
        or int(summary["warnings"]) != total_warnings
        or int(summary[error_field]) != owned_errors
        or int(summary[warning_field]) != owned_warnings
        or int(summary["actionable_diagnostics"]) != len(actionable_diagnostics)
        or (status == "pass") != (returncode == 0 and total_errors == 0)
    ):
        return None

    # A non-zero Verilator exit can contain unclassified fatal diagnostics (for
    # example, a missing top module). Those records do not prove that user RTL
    # was fully analyzed, so cleanliness credit is only valid after a clean exit.
    analysis_completed = returncode == 0
    rule_count = len(
        {
            str(item["code"]).strip().upper()
            for item in actionable_diagnostics
            if str(item["code"]).strip()
        }
    )
    policy = scoring_policy()["lint"]
    execution_earned = policy["analysis_execution"] if analysis_completed else 0
    error_earned = (
        max(0, policy["errors"] - owned_errors * policy["points_per_error"])
        if analysis_completed
        else 0
    )
    warning_earned = (
        scoring.density_credit(
            owned_warnings, lines, policy["warnings"], policy["warnings_per_kloc_for_zero"]
        )
        if analysis_completed
        else 0
    )
    rule_earned = (
        max(0, policy["rule_breadth"] - rule_count * policy["points_per_rule"])
        if analysis_completed
        else 0
    )
    components = [
        scoring.component(
            "analysis_execution",
            "Analysis execution",
            execution_earned,
            policy["analysis_execution"],
            (
                "Verilator completed and produced classified lint diagnostics."
                if analysis_completed
                else f"Verilator analysis did not complete; {ownership_label} cleanliness is unproven."
            ),
        ),
        scoring.component(
            f"{ownership}_errors",
            f"{ownership_label} errors",
            error_earned,
            policy["errors"],
            f"{owned_errors} actionable {ownership_label} error{'s' if owned_errors != 1 else ''} reported.",
        ),
        scoring.component(
            f"{ownership}_warnings",
            f"{ownership_label} warnings",
            warning_earned,
            policy["warnings"],
            scoring.density_summary(owned_warnings, lines, f"actionable {ownership_label} warning"),
        ),
        scoring.component(
            f"{ownership}_rule_breadth",
            f"{ownership_label} rule breadth",
            rule_earned,
            policy["rule_breadth"],
            f"{rule_count} distinct lint rule{'s' if rule_count != 1 else ''} affecting {ownership_label}-owned RTL.",
        ),
    ]
    return scoring.score(f"{ownership_label} lint quality", components)
