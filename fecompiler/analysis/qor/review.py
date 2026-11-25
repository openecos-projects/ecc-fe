"""QoR of the review step: structural precheck and review issues."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.analysis import scoring
from fecompiler.analysis.qor.common import (
    _append_optional_metric,
    _gate,
    _hotspot,
    _hotspot_severity,
    _is_count,
    _metric,
    _number,
    _QorResult,
    _read_record,
    _record,
    _records,
    _relative_report_source,
    _source,
    _valid_count_fields,
)
from fecompiler.analysis.qor.fingerprint import (
    _prepared_input_fingerprint,
)
from fecompiler.analysis.scoring import scoring_policy
from fecompiler.tools.common.rtl_ownership import user_rtl_ownership


def _review_qor(step: Any, workspace: dict[str, Any]) -> _QorResult:
    path = Path(step.report["dir"]) / "rtl_review.json"
    report = _read_record(path)
    result = _QorResult(source_available=report is not None)
    if report is None:
        return result
    summary = _record(report.get("summary"))
    structural = _record(_record(report.get("metrics")).get("structural"))
    precheck_key = next(
        (
            key
            for key in ("yosys_precheck", "structural_probe")
            if isinstance(report.get(key), dict)
        ),
        "",
    )
    summary_precheck_key = next(
        (
            key
            for key in ("yosys_precheck", "structural_probe")
            if isinstance(summary.get(key), dict)
        ),
        "",
    )
    precheck = _record(
        report.get(precheck_key)
        if precheck_key
        else summary.get(summary_precheck_key)
    )
    result.source_available = _valid_count_fields(
        summary, ("actionable_errors", "actionable_warnings")
    ) and bool(precheck)
    report_path = "report/rtl_review.json"
    source = _source(report_path, "/summary")
    precheck_source = _source(
        report_path,
        f"/{precheck_key}"
        if precheck_key
        else f"/summary/{summary_precheck_key or 'yosys_precheck'}",
    )
    actionable_errors = _number(summary.get("actionable_errors"))
    warnings = _number(summary.get("actionable_warnings"))
    precheck_state = _review_precheck_gate_state(precheck)
    precheck_ok = 1 if precheck_state == "pass" else 0
    ownership = user_rtl_ownership(workspace)
    result.metrics.extend(
        [
            _metric(
                "actionable_error_count",
                "Actionable Errors",
                actionable_errors,
                "count",
                "rtl_quality",
                "lower_is_better",
                f"{ownership}_review",
                source,
                gate=True,
            ),
            _metric(
                "actionable_warning_count",
                "Actionable Warnings",
                warnings,
                "count",
                "rtl_quality",
                "lower_is_better",
                f"{ownership}_review",
                source,
            ),
            _metric(
                "yosys_precheck_passed",
                "Yosys Precheck",
                precheck_ok,
                "boolean",
                "rtl_quality",
                "higher_is_better",
                "structural_precheck",
                precheck_source,
                gate=True,
            ),
        ]
    )
    _append_optional_metric(
        result,
        "max_fanout",
        "Maximum Fanout",
        structural.get("max_fanout"),
        "count",
        "rtl_quality",
        "lower_is_better",
        "structural_risk",
        _source(report_path, "/metrics/structural/max_fanout"),
    )
    _append_optional_metric(
        result,
        "max_fanin",
        "Maximum Fanin",
        structural.get("max_fanin"),
        "count",
        "rtl_quality",
        "lower_is_better",
        "structural_risk",
        _source(report_path, "/metrics/structural/max_fanin"),
    )
    _append_optional_metric(
        result,
        "max_combinational_depth",
        "Maximum Combinational Depth",
        structural.get("max_comb_depth"),
        "levels",
        "rtl_quality",
        "lower_is_better",
        "structural_risk",
        _source(report_path, "/metrics/structural/max_comb_depth"),
    )
    result.gates.extend(
        [
            _gate(
                "no_actionable_errors",
                "No actionable RTL errors",
                actionable_errors,
                "==",
                0,
                source,
            ),
            _gate(
                "yosys_precheck",
                "Yosys structural precheck",
                precheck_ok,
                "==",
                1,
                precheck_source,
                state=precheck_state,
            ),
        ]
    )
    issues = [
        (index, item)
        for index, item in enumerate(_records(report.get("issues")))
        if item.get("waived") is not True
        and str(item.get("ownership", ownership)) == ownership
    ][:50]
    result.hotspots.extend(
        _hotspot(
            "rtl_review_issue",
            str(item.get("title") or item.get("category") or "RTL review issue"),
            _hotspot_severity(item.get("severity")),
            _number(
                _record(item.get("evidence")).get("fanout")
                or _record(item.get("evidence")).get("fanin")
                or _record(item.get("evidence")).get("depth")
            ),
            _source(
                _relative_report_source(item.get("source"), "report/rtl_review.json"),
                f"/issues/{index}",
            ),
            str(
                item.get("detail")
                or item.get("recommendation")
                or f"Review this {ownership}-owned RTL issue."
            ),
        )
        for index, item in issues
    )
    result.score = _rtl_review_score(summary, structural, precheck, precheck_state)
    result.comparison = {
        "input_fingerprint": _prepared_input_fingerprint(step),
        "top_module": str(precheck.get("top_module") or ""),
        "review_waivers": workspace.get("review_waivers", []),
    }
    return result


def _rtl_review_score(
    summary: dict[str, Any],
    structural: dict[str, Any],
    precheck: dict[str, Any],
    precheck_state: str,
) -> dict[str, Any] | None:
    if not _valid_count_fields(summary, ("actionable_errors", "actionable_warnings")):
        return None
    policy = scoring_policy()["review"]
    thresholds = _record(precheck.get("risk_thresholds"))
    headroom_parts = tuple(policy["structural_headroom"].items())
    structural_fields = tuple(field for field, _ in headroom_parts)
    # The precheck is evaluated when Yosys ran: it then passed or failed.
    precheck_evaluated = precheck_state in {"pass", "failed"}
    headroom_available = (
        precheck_state == "pass"
        and _valid_count_fields(structural, structural_fields)
        and _valid_count_fields(thresholds, structural_fields)
        and all(int(thresholds[field]) > 0 for field in structural_fields)
    )
    if precheck_state == "pass" and not headroom_available:
        return None

    errors = int(summary["actionable_errors"])
    warnings = int(summary["actionable_warnings"])
    lines = int(summary["total_lines"]) if _is_count(summary.get("total_lines")) else 0
    headroom_possible = sum(possible for _, possible in headroom_parts)
    headroom_earned = (
        sum(
            possible
            * min(1.0, int(thresholds[field]) / max(int(structural[field]), 1))
            for field, possible in headroom_parts
        )
        if headroom_available
        else 0
    )
    components = [
        scoring.component(
            "structural_precheck",
            "Structural precheck",
            policy["structural_precheck"] if precheck_state == "pass" else 0,
            policy["structural_precheck"],
            (
                (
                    "Yosys completed the design structural precheck."
                    if precheck.get("scope") == "design"
                    else "Yosys completed the CPU-only structural precheck."
                )
                if precheck_state == "pass"
                else (
                    "Yosys reported a structural problem."
                    if precheck_state == "failed"
                    else "Not evaluated: the Yosys structural precheck did not run."
                )
            ),
            evaluated=precheck_evaluated,
        ),
        scoring.component(
            "actionable_errors",
            "Actionable errors",
            max(0, policy["actionable_errors"] - errors * policy["points_per_error"]),
            policy["actionable_errors"],
            f"{errors} actionable RTL error{'s' if errors != 1 else ''} reported.",
        ),
        scoring.component(
            "warning_hygiene",
            "Warning hygiene",
            scoring.density_credit(
                warnings, lines, policy["warning_hygiene"], policy["warnings_per_kloc_for_zero"]
            ),
            policy["warning_hygiene"],
            scoring.density_summary(warnings, lines, "actionable RTL warning"),
        ),
        scoring.component(
            "structural_headroom",
            "Structural headroom",
            headroom_earned,
            headroom_possible,
            (
                f"Fanout {structural['max_fanout']}/{thresholds['max_fanout']}, "
                f"fanin {structural['max_fanin']}/{thresholds['max_fanin']}, "
                f"depth {structural['max_comb_depth']}/{thresholds['max_comb_depth']} "
                "(measured/target); clock and reset nets are not counted as fanout."
                if headroom_available
                else "Not evaluated: structural measurements need a passing Yosys precheck."
            ),
            evaluated=headroom_available,
        ),
    ]
    return scoring.score("RTL review quality", components)


def _review_precheck_gate_state(precheck: dict[str, Any]) -> str:
    status = str(precheck.get("status", "")).strip().lower()
    quality_gate = str(_record(precheck.get("quality")).get("gate", "")).strip().lower()
    diagnostics = _records(precheck.get("diagnostics"))
    has_blocking_diagnostic = any(
        str(item.get("severity", "")).lower() == "error"
        and str(item.get("category", "")).lower() != "tool-limit"
        for item in diagnostics
    )
    if status in {"unavailable", "skipped", ""}:
        return "incomplete"
    if quality_gate == "failed":
        return "failed"
    if status in {"failed", "timeout"} and has_blocking_diagnostic:
        return "failed"
    if status == "success":
        return "pass"
    return "incomplete"
