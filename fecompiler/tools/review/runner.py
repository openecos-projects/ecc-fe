"""RTL review step implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.fe.base import BaseStep
from fecompiler.tools.fe.subflow import update_substep_ok
from fecompiler.tools.review.analyzer import build_rtl_review, merge_structural_probe
from fecompiler.tools.review.structural_probe import run_structural_probe
from fecompiler.tools.review.subflow import ReviewSubFlowEnum, init_review_subflow
from fecompiler.utility.json import json_write


class RtlReviewStep(BaseStep):
    """Generate a structured IC/FPGA static RTL review report."""

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_review_subflow(step)
        report = build_rtl_review(workspace)
        probe = run_structural_probe(workspace, step)
        report = merge_structural_probe(report, probe)
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
            True,
            info={
                "total_lines": report.get("metrics", {}).get("total_lines", 0),
                "yosys_precheck": probe.get("status", ""),
            },
        )
        update_substep_ok(
            step,
            ReviewSubFlowEnum.analyze_profiles.value,
            True,
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
        return bool(data.get("source_files"))

    def _write_outputs(self, step: WorkspaceStep, report: dict[str, Any]) -> None:
        review_path = Path(step.report["dir"]) / "rtl_review.json"
        summary_path = Path(step.report["step"])
        metrics_path = Path(step.analysis["metrics"])
        output_path = Path(step.output["json"])
        log_path = Path(step.log["file"])

        json_write(review_path, report)
        json_write(output_path, report)
        status = "Success" if report.get("source_files") else "Incomplete"
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
    ]
    for issue in report.get("issues", [])[:40]:
        source = str(issue.get("source", ""))
        line = int(issue.get("line", 0) or 0)
        location = f"{source}:{line}" if source and line else source
        prefix = str(issue.get("severity", "info")).upper()
        lines.append(f"[rtl-review][{prefix}] {issue.get('category', 'other')} {location} {issue.get('title', '')}")
    return "\n".join(lines) + "\n"
