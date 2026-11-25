"""QoR of the elab step: does the design elaborate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.analysis import scoring
from fecompiler.analysis.qor.common import (
    _diagnostic_hotspots,
    _gate,
    _metric,
    _number,
    _QorResult,
    _read_record,
    _record,
    _rtl_lines,
    _source,
    _valid_count_fields,
)
from fecompiler.analysis.qor.fingerprint import (
    _prepared_input_fingerprint,
)
from fecompiler.analysis.scoring import scoring_policy


def _elab_qor(step: Any, workspace: dict[str, Any]) -> _QorResult:
    path = Path(step.report["dir"]) / "elab_summary.json"
    report = _read_record(path)
    result = _QorResult(source_available=report is not None)
    if report is None:
        return result
    summary = _record(report.get("summary"))
    result.source_available = (
        _valid_count_fields(
            summary, ("errors", "warnings", "modules", "unresolved_modules")
        )
        and isinstance(summary.get("top_found"), bool)
    )
    source = _source("report/elab_summary.json", "/summary")
    errors = _number(summary.get("errors"))
    unresolved = _number(summary.get("unresolved_modules"))
    top_found = 1 if summary.get("top_found") is True else 0
    result.metrics.extend(
        [
            _metric(
                "elaboration_error_count",
                "Elaboration Errors",
                errors,
                "count",
                "elaboration",
                "lower_is_better",
                "elaboration",
                source,
                gate=True,
            ),
            _metric(
                "unresolved_module_count",
                "Unresolved Modules",
                unresolved,
                "count",
                "elaboration",
                "lower_is_better",
                "elaboration",
                source,
                gate=True,
            ),
            _metric(
                "top_module_found",
                "Top Module Found",
                top_found,
                "boolean",
                "elaboration",
                "higher_is_better",
                "elaboration",
                source,
                gate=True,
            ),
            _metric(
                "elaboration_warning_count",
                "Elaboration Warnings",
                _number(summary.get("warnings")),
                "count",
                "elaboration",
                "lower_is_better",
                "elaboration",
                source,
            ),
            _metric(
                "elaborated_module_count",
                "Elaborated Modules",
                _number(summary.get("modules")),
                "count",
                "elaboration",
                "trend_only",
                "design_inventory",
                source,
            ),
        ]
    )
    result.gates.extend(
        [
            _gate(
                "no_elaboration_errors",
                "No elaboration errors",
                errors,
                "==",
                0,
                source,
            ),
            _gate(
                "all_modules_resolved",
                "All referenced modules resolved",
                unresolved,
                "==",
                0,
                source,
            ),
            _gate(
                "top_module_resolved", "Top module resolved", top_found, "==", 1, source
            ),
        ]
    )
    result.hotspots.extend(
        _diagnostic_hotspots(
            report, "elaboration_diagnostic", "report/elab_summary.json"
        )
    )
    result.score = _elaboration_score(
        report, summary, _rtl_lines(step, workspace, user_owned=False)
    )
    result.comparison = {
        "input_fingerprint": _prepared_input_fingerprint(step),
        "top_module": str(
            summary.get("top_module") or workspace.get("top_module") or ""
        ),
    }
    return result


def _elaboration_score(
    report: dict[str, Any], summary: dict[str, Any], lines: int
) -> dict[str, Any] | None:
    compiler = _record(report.get("compiler"))
    status = str(report.get("status", "")).strip().lower()
    summary_status = str(summary.get("status", "")).strip().lower()
    unresolved_modules = compiler.get("unresolved_modules")
    if (
        report.get("schema_version") != 2
        or status not in {"pass", "fail"}
        or summary_status != status
        or not isinstance(report.get("returncode"), int)
        or isinstance(report.get("returncode"), bool)
        or not _valid_count_fields(
            summary, ("errors", "warnings", "modules", "unresolved_modules")
        )
        or not isinstance(summary.get("top_found"), bool)
        or compiler.get("source") != "slang"
        or compiler.get("authoritative") is not True
        or compiler.get("elaboration_mode") != "full"
        or summary.get("elaboration_mode") != "full"
        or not isinstance(unresolved_modules, list)
        or not all(isinstance(item, str) for item in unresolved_modules)
        or len(unresolved_modules) != int(summary["unresolved_modules"])
    ):
        return None

    errors = int(summary["errors"])
    warnings = int(summary["warnings"])
    unresolved = int(summary["unresolved_modules"])
    returncode = int(report["returncode"])
    compiler_passed = status == "pass" and returncode == 0 and errors == 0
    if status == "pass" and not compiler_passed:
        return None
    if status == "fail" and returncode == 0 and errors == 0:
        return None

    policy = scoring_policy()["elab"]
    compiler_earned = policy["compiler_execution"] if compiler_passed else 0
    error_earned = max(0, policy["diagnostic_errors"] - errors * policy["points_per_error"])
    hierarchy_earned = (
        max(0, policy["hierarchy_closure"] - unresolved * policy["points_per_unresolved_module"])
        if compiler_passed
        else 0
    )
    top_earned = (
        policy["top_resolution"] if compiler_passed and summary["top_found"] is True else 0
    )
    warning_earned = scoring.density_credit(
        warnings, lines, policy["warning_hygiene"], policy["warnings_per_kloc_for_zero"]
    )
    components = [
        scoring.component(
            "compiler_execution",
            "Compiler execution",
            compiler_earned,
            policy["compiler_execution"],
            (
                "Slang completed an authoritative full elaboration."
                if compiler_passed
                else f"Slang full elaboration failed with return code {returncode}."
            ),
        ),
        scoring.component(
            "diagnostic_errors",
            "Diagnostic errors",
            error_earned,
            policy["diagnostic_errors"],
            f"{errors} compiler error{'s' if errors != 1 else ''} reported.",
        ),
        scoring.component(
            "hierarchy_closure",
            "Hierarchy closure",
            hierarchy_earned,
            policy["hierarchy_closure"],
            (
                f"{unresolved} unresolved module{'s' if unresolved != 1 else ''} "
                "in the authoritative compiler result."
                if compiler_passed
                else "Hierarchy closure is unproven because full elaboration did not pass."
            ),
        ),
        scoring.component(
            "top_resolution",
            "Top resolution",
            top_earned,
            policy["top_resolution"],
            (
                f"Top module {summary.get('top_module') or '<unknown>'} resolved by Slang."
                if compiler_passed and summary["top_found"] is True
                else "Top resolution is unproven by a successful full elaboration."
            ),
        ),
        scoring.component(
            "warning_hygiene",
            "Warning hygiene",
            warning_earned,
            policy["warning_hygiene"],
            scoring.density_summary(warnings, lines, "compiler warning"),
        ),
    ]
    return scoring.score("Elaboration quality", components)
