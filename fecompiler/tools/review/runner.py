"""RTL review step implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.fe.base import BaseStep
from fecompiler.tools.fe.subflow import update_substep_ok
from fecompiler.tools.review.analyzer import build_rtl_review, finalize_review_report, merge_structural_probe
from fecompiler.tools.review.structural_probe import run_structural_probe
from fecompiler.tools.review.subflow import ReviewSubFlowEnum, init_review_subflow
from fecompiler.utility.json import json_read, json_write


class RtlReviewStep(BaseStep):
    """Generate a structured static RTL quality review report."""

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_review_subflow(step)
        previous_report = _load_previous_report(step)
        report = build_rtl_review(workspace)
        probe = run_structural_probe(workspace, step)
        report = merge_structural_probe(report, probe)
        report = finalize_review_report(
            report,
            previous_report,
            workspace.get("review_waivers", []),
            workspace,
        )
        self._write_outputs(step, report)
        update_substep_ok(
            step,
            ReviewSubFlowEnum.collect_sources.value,
            bool(report.get("source_files")),
            info={"source_files": len(report.get("source_files", []))},
        )
        update_substep_ok(
            step,
            ReviewSubFlowEnum.scan_rtl.value,
            not _review_is_blocked_by_yosys_precheck(report),
            info={
                "total_lines": report.get("metrics", {}).get("total_lines", 0),
                "yosys_precheck": probe.get("status", ""),
            },
        )
        update_substep_ok(
            step,
            ReviewSubFlowEnum.analyze_quality.value,
            not _review_is_blocked_by_yosys_precheck(report),
            info=report.get("summary", {}),
        )
        update_substep_ok(step, ReviewSubFlowEnum.report.value, True)

    def check_result(self, step: WorkspaceStep) -> bool:
        review_path = Path(step.report["dir"]) / "rtl_review.json"
        if not review_path.is_file():
            return False
        try:
            import json

            data = json.loads(review_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return bool(data.get("source_files")) and not _review_is_blocked_by_yosys_precheck(data)

    def _write_outputs(self, step: WorkspaceStep, report: dict[str, Any]) -> None:
        review_path = Path(step.report["dir"]) / "rtl_review.json"
        summary_md_path = Path(step.report["dir"]) / "rtl_review_summary.md"
        summary_path = Path(step.report["step"])
        metrics_path = Path(step.analysis["metrics"])
        output_path = Path(step.output["json"])
        log_path = Path(step.log["file"])

        json_write(review_path, report)
        json_write(output_path, report)
        summary_md_path.write_text(_format_summary_markdown(report), encoding="utf-8")
        status = "Success" if report.get("source_files") and not _review_is_blocked_by_yosys_precheck(report) else "Incomplete"
        json_write(metrics_path, {
            "step": step.name,
            "status": status,
            "summary": report.get("summary", {}),
            "metrics": report.get("metrics", {}),
        })
        json_write(summary_path, {
            "review": "pass" if status == "Success" else "fail",
            "report": str(review_path),
            "summary": report.get("summary", {}),
        })
        log_path.write_text(_format_log(report), encoding="utf-8")


def _format_log(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "[rtl-review] static RTL review completed",
        f"[rtl-review] source_files={summary.get('source_files', 0)} modules={summary.get('modules', 0)} lines={summary.get('total_lines', 0)}",
        f"[rtl-review] errors={summary.get('errors', 0)} warnings={summary.get('warnings', 0)} infos={summary.get('infos', 0)}",
        (
            f"[rtl-review] delta new={summary.get('new_issues', 0)} "
            f"existing={summary.get('existing_issues', 0)} "
            f"resolved={summary.get('resolved_issues', 0)} waived={summary.get('waived_issues', 0)}"
        ),
    ]
    probe = report.get("yosys_precheck") or report.get("structural_probe") or {}
    if isinstance(probe, dict) and probe:
        metrics = probe.get("metrics", {}) if isinstance(probe.get("metrics"), dict) else {}
        lines.extend([
            f"[rtl-review] yosys_status={probe.get('status', '')} reason={probe.get('reason', '')}",
            (
                "[rtl-review] structural "
                f"max_fanout={metrics.get('max_fanout', 0)} "
                f"max_fanin={metrics.get('max_fanin', 0)} "
                f"max_comb_depth={metrics.get('max_comb_depth', 0)}"
            ),
        ])
    for issue in report.get("issues", [])[:40]:
        source = str(issue.get("source", ""))
        line = int(issue.get("line", 0) or 0)
        location = f"{source}:{line}" if source and line else source
        prefix = str(issue.get("severity", "info")).upper()
        evidence = _issue_evidence_label(issue)
        parts = [f"[rtl-review][{prefix}]", str(issue.get("category", "other"))]
        if location:
            parts.append(location)
        parts.append(str(issue.get("title", "")))
        if evidence:
            parts.append(f"({evidence})")
        lines.append(" ".join(parts))
    return "\n".join(lines) + "\n"


def _format_summary_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    issues = [issue for issue in report.get("issues", []) if isinstance(issue, dict)]
    probe = report.get("yosys_precheck") or report.get("structural_probe") or {}
    probe = probe if isinstance(probe, dict) else {}
    metrics = probe.get("metrics", {}) if isinstance(probe.get("metrics"), dict) else {}

    lines = [
        "# RTL Review Summary",
        "",
        "## Result",
        "",
        f"- Scope: {report.get('scope', 'cpu')}",
        f"- Sources: {summary.get('source_files', 0)}",
        f"- Modules: {summary.get('modules', 0)}",
        f"- Errors: {summary.get('errors', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        f"- Infos: {summary.get('infos', 0)}",
        f"- New: {summary.get('new_issues', 0)}",
        f"- Resolved: {summary.get('resolved_issues', 0)}",
        f"- Waived: {summary.get('waived_issues', 0)}",
        "",
        "## Yosys Precheck",
        "",
        f"- Status: {probe.get('status', 'not run')}",
        f"- Reason: {probe.get('reason', '') or 'OK'}",
        f"- Cells: {metrics.get('cells', 0)}",
        f"- Wires: {metrics.get('wires', 0)}",
        f"- Max fanout: {metrics.get('max_fanout', 0)}",
        f"- Max fanin: {metrics.get('max_fanin', 0)}",
        f"- Max combinational depth: {metrics.get('max_comb_depth', 0)}",
        "",
        "## Top Problems",
        "",
    ]

    if not issues:
        lines.append("- No issues reported.")
    for issue in issues[:20]:
        location = _issue_location_label(issue)
        evidence = _issue_evidence_label(issue)
        title = str(issue.get("title", "RTL issue"))
        severity = str(issue.get("severity", "info")).upper()
        recommendation = str(issue.get("recommendation", "")).strip()
        detail = str(issue.get("detail", "")).strip()
        line = f"- [{severity}] {title}"
        if location:
            line += f" @ {location}"
        if evidence:
            line += f" ({evidence})"
        lines.append(line)
        if detail:
            lines.append(f"  - Detail: {detail}")
        if recommendation:
            lines.append(f"  - Fix: {recommendation}")

    return "\n".join(lines) + "\n"


def _issue_location_label(issue: dict[str, Any]) -> str:
    source = str(issue.get("source", "")).strip()
    line = int(issue.get("line", 0) or 0)
    if source and line:
        return f"{source}:{line}"
    return source


def _issue_evidence_label(issue: dict[str, Any]) -> str:
    evidence = issue.get("evidence", {})
    if not isinstance(evidence, dict):
        return ""
    parts = [
        f"module={evidence['module']}" if evidence.get("module") else "",
        f"net={evidence['net']}" if evidence.get("net") else "",
        f"cell={evidence['cell']}" if evidence.get("cell") else "",
        f"endpoint={evidence['endpoint']}" if evidence.get("endpoint") else "",
        f"fanout={evidence['fanout']}" if evidence.get("fanout") else "",
        f"fanin={evidence['fanin']}" if evidence.get("fanin") else "",
        f"depth={evidence['depth']}" if evidence.get("depth") else "",
    ]
    return ", ".join(part for part in parts if part)


def _review_is_blocked_by_yosys_precheck(report: dict[str, Any]) -> bool:
    probe = report.get("yosys_precheck") or report.get("structural_probe") or {}
    if not isinstance(probe, dict):
        return False

    status = str(probe.get("status", "")).strip().lower()
    if status in {"unavailable", "skipped", ""}:
        return False

    quality = probe.get("quality", {})
    gate = str(quality.get("gate", "") if isinstance(quality, dict) else "").strip().lower()
    if gate == "failed":
        return True

    if status in {"failed", "timeout"}:
        diagnostics = probe.get("diagnostics", [])
        return any(
            isinstance(item, dict)
            and str(item.get("severity", "")).lower() == "error"
            and str(item.get("category", "")).lower() != "tool-limit"
            for item in diagnostics if isinstance(diagnostics, list)
        ) or gate == "failed"

    return False


def _load_previous_report(step: WorkspaceStep) -> dict[str, Any] | None:
    path = Path(step.report["dir"]) / "rtl_review.json"
    if not path.is_file():
        return None
    data = json_read(str(path))
    return data if isinstance(data, dict) else None
