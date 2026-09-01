"""Build stable, machine-readable QoR artifacts for ECC-FE flow steps."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA_REVISION = "frontend-quality-gates-v1"
_HDL_INCLUDE_SUFFIXES = frozenset({".h", ".inc", ".orig", ".sv", ".svh", ".v", ".vh"})
_PASSING_CONTRACT_STATUSES = frozenset(
    {"module_only", "not_required", "ok", "pass", "success"}
)
_FAILING_CONTRACT_STATUSES = frozenset({"error", "fail", "failed"})


def write_step_qor(step: Any, workspace: dict[str, Any], success: bool) -> None:
    """Write the standard QoR triplet for one completed frontend step."""
    builder = _STEP_BUILDERS.get(str(step.name))
    result = builder(step, workspace) if builder else _QorResult()
    analysis = step.analysis
    design = str(workspace.get("design", ""))
    context = {
        "comparison": {
            "fingerprint": _comparison_fingerprint(step, workspace, result.comparison),
            "inputs": result.comparison,
        },
    }
    metrics_payload = {
        "schema_version": 3,
        "analysis_revision": SCHEMA_REVISION,
        "tool": str(step.tool),
        "step": str(step.name),
        "design": design,
        "status": "success" if success else "failed",
        "metrics": result.metrics,
        "details": result.details,
        "sources": _unique_sources(result.metrics),
        "integrity": {
            "status": "pass" if result.source_available else "incomplete",
            "invalid_metric_source_ids": [],
            "invalid_detail_ids": [],
        },
        "context": context,
    }
    gates = result.gates
    has_failed_gate = any(gate["state"] == "failed" for gate in gates)
    has_incomplete_gate = any(
        gate["state"] in {"incomplete", "unavailable"} for gate in gates
    )
    if not result.source_available:
        quality_status = "incomplete"
        analysis_status = "incomplete"
    elif has_failed_gate:
        quality_status = "blocked"
        analysis_status = "valid"
    elif has_incomplete_gate or not success:
        quality_status = "incomplete"
        analysis_status = "incomplete"
    else:
        quality_status = "pass"
        analysis_status = "valid"
    summary_payload = {
        "schema_version": 4,
        "analysis_revision": SCHEMA_REVISION,
        "tool": str(step.tool),
        "step": str(step.name),
        "design": design,
        "analysis_status": analysis_status,
        "quality_status": quality_status,
        "metric_count": len(result.metrics),
        "dimensions": sorted({str(metric["category"]) for metric in result.metrics}),
        "gates": gates,
        "missing_metrics": result.missing_metrics,
        "metrics_file": "qor_metrics.json",
        "context": context,
    }
    hotspots_payload = {
        "schema_version": 3,
        "analysis_revision": SCHEMA_REVISION,
        "tool": str(step.tool),
        "step": str(step.name),
        "design": design,
        "hotspots": result.hotspots,
    }
    generation = _qor_generation(metrics_payload, summary_payload, hotspots_payload)
    metrics_payload["generation"] = generation
    summary_payload["generation"] = generation
    hotspots_payload["generation"] = generation
    _write_qor_triplet_atomic(
        (
            (analysis["qor_metrics"], metrics_payload),
            (analysis["qor_summary"], summary_payload),
            (analysis["qor_hotspots"], hotspots_payload),
        ),
    )


def clear_step_qor(step_directory: str | Path) -> None:
    """Remove QoR and its embedded frontend snapshot before a new run."""
    root = Path(step_directory)
    for path in (
        root / "analysis" / "qor_metrics.json",
        root / "analysis" / "qor_summary.json",
        root / "analysis" / "qor_hotspots.json",
        root / "report" / "frontend_detail.json",
    ):
        path.unlink(missing_ok=True)


def step_qor_source_revision(step: Any) -> str:
    """Return a revision token for the report consumed by a step's QoR builder."""
    path = _step_qor_source_path(step)
    if path is None:
        return "unsupported"
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    identity = {
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
        "changed_ns": stat.st_ctime_ns,
        "sha256": _file_sha256(path),
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _qor_generation(*payloads: dict[str, Any]) -> str:
    content = json.dumps(
        payloads,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _write_qor_triplet_atomic(
    entries: tuple[tuple[str | Path, dict[str, Any]], ...],
) -> None:
    staged: list[tuple[Path, Path]] = []
    originals: dict[Path, bytes | None] = {}
    published: list[Path] = []
    try:
        for file_path, payload in entries:
            destination = Path(file_path)
            originals[destination] = (
                destination.read_bytes() if destination.is_file() else None
            )
            staged.append((destination, _stage_json(destination, payload)))
        for destination, temporary_path in staged:
            os.replace(temporary_path, destination)
            published.append(destination)
    except Exception as exc:
        rollback_errors: list[OSError] = []
        for destination in reversed(published):
            try:
                original = originals[destination]
                if original is None:
                    destination.unlink(missing_ok=True)
                else:
                    _restore_bytes(destination, original)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        message = "failed to persist QoR artifact triplet"
        if rollback_errors:
            message += "; rollback also failed"
        raise OSError(message) from exc
    finally:
        for _, temporary_path in staged:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _stage_json(destination: Path, payload: dict[str, Any]) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        return Path(temporary_name)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _stage_bytes(destination: Path, content: bytes) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".rollback",
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return Path(temporary_name)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _restore_bytes(destination: Path, content: bytes) -> None:
    temporary_path = _stage_bytes(destination, content)
    try:
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


class _QorResult:
    def __init__(self, *, source_available: bool = False) -> None:
        self.source_available = source_available
        self.metrics: list[dict[str, Any]] = []
        self.gates: list[dict[str, Any]] = []
        self.hotspots: list[dict[str, Any]] = []
        self.details: list[dict[str, Any]] = []
        self.missing_metrics: list[dict[str, Any]] = []
        self.comparison: dict[str, Any] = {}


def _prepare_qor(step: Any, workspace: dict[str, Any]) -> _QorResult:
    path = Path(step.report["step"])
    report = _read_record(path)
    result = _QorResult(source_available=report is not None)
    if report is None:
        return result
    raw_contracts = report.get("contracts")
    contracts = _records(raw_contracts)
    result.source_available = (
        _valid_count_fields(report, ("rtl_files", "incdirs", "defines"))
        and isinstance(raw_contracts, list)
        and bool(contracts)
        and len(contracts) == len(raw_contracts)
        and all(_valid_prepare_contract(item) for item in contracts)
    )
    failures = [
        item
        for item in contracts
        if str(item.get("status", "")).lower() not in _PASSING_CONTRACT_STATUSES
    ]
    source = _source("report/prepare.rpt", "/contracts")
    result.metrics.extend(
        [
            _metric(
                "rtl_file_count",
                "RTL Files",
                _number(report.get("rtl_files")),
                "count",
                "readiness",
                "trend_only",
                "prepared_inputs",
                source,
            ),
            _metric(
                "include_dir_count",
                "Include Directories",
                _number(report.get("incdirs")),
                "count",
                "readiness",
                "trend_only",
                "prepared_inputs",
                source,
            ),
            _metric(
                "define_count",
                "Defines",
                _number(report.get("defines")),
                "count",
                "readiness",
                "trend_only",
                "prepared_inputs",
                source,
            ),
            _metric(
                "contract_failure_count",
                "Contract Failures",
                len(failures),
                "count",
                "readiness",
                "lower_is_better",
                "interface_contracts",
                source,
                gate=True,
            ),
        ]
    )
    result.gates.append(
        _gate(
            "frontend_contracts",
            "Frontend input contracts",
            len(failures),
            "==",
            0,
            source,
        )
    )
    result.hotspots.extend(
        _hotspot(
            "contract_failure_count",
            str(item.get("id") or "contract"),
            "critical",
            str(item.get("status") or "failed"),
            source,
            str(
                item.get("detail")
                or item.get("reason")
                or "Frontend contract did not pass."
            ),
        )
        for item in failures
    )
    result.comparison = {
        "input_fingerprint": _prepared_input_fingerprint(step),
        "cpu_top": str(
            workspace.get("required_cpu_top_module")
            or workspace.get("cpu_top_module")
            or ""
        ),
    }
    return result


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
    source = _source("report/rtl_review.json", "/summary")
    precheck_source = _source(
        "report/rtl_review.json",
        f"/{precheck_key}"
        if precheck_key
        else f"/summary/{summary_precheck_key or 'yosys_precheck'}",
    )
    actionable_errors = _number(summary.get("actionable_errors"))
    warnings = _number(summary.get("actionable_warnings"))
    precheck_state = _review_precheck_gate_state(precheck)
    precheck_ok = 1 if precheck_state == "pass" else 0
    result.metrics.extend(
        [
            _metric(
                "actionable_error_count",
                "Actionable Errors",
                actionable_errors,
                "count",
                "rtl_quality",
                "lower_is_better",
                "cpu_review",
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
                "cpu_review",
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
        source,
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
        source,
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
        source,
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
        if item.get("waived") is not True and str(item.get("ownership", "cpu")) == "cpu"
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
                or "Review this CPU RTL issue."
            ),
        )
        for index, item in issues
    )
    result.comparison = {
        "input_fingerprint": _prepared_input_fingerprint(step),
        "review_waivers": workspace.get("review_waivers", []),
    }
    return result


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
    result.comparison = {
        "input_fingerprint": _prepared_input_fingerprint(step),
        "top_module": str(
            summary.get("top_module") or workspace.get("top_module") or ""
        ),
    }
    return result


def _lint_qor(step: Any, workspace: dict[str, Any]) -> _QorResult:
    path = Path(step.report["dir"]) / "lint_summary.json"
    report = _read_record(path)
    result = _QorResult(source_available=report is not None)
    if report is None:
        return result
    summary = _record(report.get("summary"))
    result.source_available = _valid_count_fields(
        summary, ("cpu_errors", "cpu_warnings", "warnings")
    )
    source = _source("report/lint_summary.json", "/summary")
    errors = _number(summary.get("cpu_errors"))
    warnings = _number(summary.get("cpu_warnings"))
    result.metrics.extend(
        [
            _metric(
                "cpu_lint_error_count",
                "CPU Lint Errors",
                errors,
                "count",
                "lint",
                "lower_is_better",
                "cpu_lint",
                source,
                gate=True,
            ),
            _metric(
                "cpu_lint_warning_count",
                "CPU Lint Warnings",
                warnings,
                "count",
                "lint",
                "lower_is_better",
                "cpu_lint",
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
        _gate("no_cpu_lint_errors", "No CPU-owned lint errors", errors, "==", 0, source)
    )
    diagnostics = [
        (index, item)
        for index, item in enumerate(_records(report.get("diagnostics")))
        if item.get("actionable") is True and str(item.get("ownership", "")) == "cpu"
    ][:50]
    result.hotspots.extend(
        _hotspot(
            "cpu_lint_diagnostic",
            str(item.get("code") or "Lint diagnostic"),
            _hotspot_severity(item.get("severity")),
            None,
            _source(
                _relative_report_source(item.get("source"), "report/lint_summary.json"),
                f"/diagnostics/{index}",
            ),
            str(item.get("message") or "Review this CPU-owned lint diagnostic."),
        )
        for index, item in diagnostics
    )
    result.comparison = {"input_fingerprint": _prepared_input_fingerprint(step)}
    return result


def _sim_qor(step: Any, workspace: dict[str, Any]) -> _QorResult:
    path = Path(step.report["dir"]) / "cases.json"
    report = _read_record(path)
    result = _QorResult(source_available=report is not None)
    if report is None:
        return result
    raw_cases = report.get("cases")
    case_records = _records(raw_cases)
    valid_case_output = (
        isinstance(raw_cases, list)
        and bool(case_records)
        and len(case_records) == len(raw_cases)
        and all(_valid_sim_case(item) for item in case_records)
    )
    result.source_available = valid_case_output
    cases = case_records if valid_case_output else []
    total = len(cases)
    passed = sum(item.get("ok") is True for item in cases)
    failed = total - passed
    difftest_cases = [
        item
        for item in cases
        if bool(_record(_record(item.get("metrics")).get("difftest")).get("enabled"))
    ]
    difftest_failures = sum(
        str(
            _record(_record(item.get("metrics")).get("difftest")).get("status", "")
        ).lower()
        != "passed"
        for item in difftest_cases
    )
    cycle_values = [
        _optional_number(_record(item.get("metrics")).get("cycles")) for item in cases
    ]
    pass_rate = passed / total if total else 0.0
    source = _source("report/cases.json", "/cases")
    result.metrics.extend(
        [
            _metric(
                "simulation_pass_rate",
                "Simulation Pass Rate",
                pass_rate,
                "ratio",
                "verification",
                "higher_is_better",
                "test_suite",
                source,
                gate=True,
            ),
            _metric(
                "failed_case_count",
                "Failed Cases",
                failed,
                "count",
                "verification",
                "lower_is_better",
                "test_suite",
                source,
                gate=True,
            ),
            _metric(
                "passed_case_count",
                "Passed Cases",
                passed,
                "count",
                "verification",
                "higher_is_better",
                "test_suite",
                source,
            ),
        ]
    )
    if total > 0 and all(value is not None for value in cycle_values):
        result.metrics.append(
            _metric(
                "total_cycles",
                "Total Cycles",
                sum(value for value in cycle_values if value is not None),
                "cycles",
                "performance",
                "lower_is_better",
                "test_suite",
                source,
            )
        )
    if difftest_cases:
        result.metrics.append(
            _metric(
                "difftest_failure_count",
                "Difftest Failures",
                difftest_failures,
                "count",
                "verification",
                "lower_is_better",
                "difftest",
                source,
                gate=True,
            )
        )
    coremark_values = [
        _number(_record(item.get("metrics")).get("coremark_per_mhz"))
        for item in cases
        if _record(item.get("metrics")).get("coremark_per_mhz") is not None
    ]
    if coremark_values:
        result.metrics.append(
            _metric(
                "coremark_per_mhz",
                "CoreMark / MHz",
                max(coremark_values),
                "CoreMark/MHz",
                "performance",
                "higher_is_better",
                "coremark",
                source,
            )
        )
    result.gates.extend(
        [
            _gate(
                "all_required_cases_pass",
                "All required simulation cases pass",
                failed,
                "==",
                0,
                source,
            ),
            _gate(
                "simulation_cases_present",
                "Simulation produced test cases",
                total,
                ">",
                0,
                source,
            ),
        ]
    )
    if difftest_cases:
        result.gates.append(
            _gate(
                "difftest_matches_reference",
                "Difftest matches reference",
                difftest_failures,
                "==",
                0,
                source,
            )
        )
    for index, item in enumerate(cases):
        metrics = _record(item.get("metrics"))
        diff = _record(metrics.get("difftest"))
        if item.get("ok") is True and (
            not diff or str(diff.get("status", "")).lower() in {"", "passed"}
        ):
            continue
        failure = _record(item.get("failure"))
        first_mismatch = _record(diff.get("first_mismatch"))
        reason = str(
            failure.get("message")
            or first_mismatch.get("message")
            or "Simulation case failed."
        )
        result.hotspots.append(
            _hotspot(
                "simulation_failure",
                str(item.get("name") or f"case {index + 1}"),
                "critical",
                _number(metrics.get("cycles")),
                _source("report/cases.json", f"/cases/{index}"),
                reason,
            )
        )
    result.comparison = {
        "input_fingerprint": _prepared_input_fingerprint(step),
        "suite": str(report.get("suite") or workspace.get("test_suite_id") or ""),
        "cases": sorted(
            (
                {
                    "name": str(item.get("name") or ""),
                    "image_sha256": _file_sha256(item.get("image")),
                }
                for item in cases
            ),
            key=lambda item: (item["name"], item["image_sha256"]),
        ),
        "compile_preset": str(workspace.get("sim_compile_preset") or ""),
        "compile_opt_level": str(workspace.get("sim_compile_opt_level") or ""),
        "compile_march": str(workspace.get("sim_compile_march") or ""),
        "compile_mabi": str(workspace.get("sim_compile_mabi") or ""),
        "compile_extra_cflags": _normalized_compile_flags(
            workspace.get("sim_compile_extra_cflags"),
            workspace.get("directory"),
        ),
        "run_args": _normalized_run_arguments(
            workspace.get("sim_run_args"), workspace.get("directory")
        ),
        "resource_versions": _record(workspace.get("resource_versions")),
    }
    return result


def _metric(
    metric_id: str,
    display_name: str,
    value: float,
    unit: str,
    category: str,
    direction: str,
    scope: str,
    source: dict[str, str],
    *,
    gate: bool = False,
) -> dict[str, Any]:
    return {
        "id": metric_id,
        "display_name": display_name,
        "value": value,
        "unit": unit,
        "category": category,
        "direction": direction,
        "scope": scope,
        "corner": None,
        "project_role": "gate" if gate else "trend",
        "step_role": "primary" if gate else "secondary",
        "analysis_group": category,
        "rating": {"gate": gate, "score": False, "trend": not gate},
        "confidence": "high",
        "source": source,
    }


def _append_optional_metric(
    result: _QorResult,
    metric_id: str,
    display_name: str,
    value: Any,
    unit: str,
    category: str,
    direction: str,
    scope: str,
    source: dict[str, str],
) -> None:
    numeric_value = _optional_number(value)
    if numeric_value is None:
        return
    result.metrics.append(
        _metric(
            metric_id,
            display_name,
            numeric_value,
            unit,
            category,
            direction,
            scope,
            source,
        )
    )


def _gate(
    gate_id: str,
    title: str,
    actual: float,
    operator: str,
    expected: float,
    source: dict[str, str],
    *,
    state: str | None = None,
) -> dict[str, Any]:
    passed = _compare(actual, operator, expected)
    return {
        "id": gate_id,
        "title": title,
        "state": state or ("pass" if passed else "failed"),
        "metrics": [
            {
                "id": gate_id,
                "actual": actual,
                "operator": operator,
                "expected": expected,
                "source": source,
            }
        ],
        "evidence": [source],
    }


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


def _hotspot(
    metric_id: str,
    display_name: str,
    severity: str,
    value: Any,
    source: dict[str, str],
    description: str,
) -> dict[str, Any]:
    return {
        "kind": "frontend_quality",
        "severity": severity,
        "metric_id": metric_id,
        "display_name": display_name,
        "value": value,
        "source": source,
        "description": description,
    }


def _hotspot_severity(value: Any) -> str:
    severity = str(value or "").strip().lower()
    if severity in {"error", "fatal", "critical"}:
        return "critical"
    if severity in {"warning", "warn"}:
        return "warning"
    return "info"


def _diagnostic_hotspots(
    report: dict[str, Any], metric_id: str, path: str
) -> list[dict[str, Any]]:
    return [
        _hotspot(
            metric_id,
            str(item.get("code") or item.get("severity") or "Diagnostic"),
            _hotspot_severity(item.get("severity")),
            None,
            _source(
                _relative_report_source(item.get("source"), path),
                f"/diagnostics/{index}",
            ),
            str(item.get("message") or item.get("text") or "Elaboration diagnostic."),
        )
        for index, item in enumerate(_records(report.get("diagnostics"))[:50])
    ]


def _normalized_run_arguments(value: Any, base_directory: Any = None) -> list[Any]:
    return _normalize_path_arguments(
        _string_list(value),
        {
            "--image": "file",
            "--ref": "file",
            "--wave": "output",
        },
        base_directory,
    )


def _normalized_compile_flags(value: Any, base_directory: Any = None) -> list[Any]:
    return _normalize_path_arguments(
        _string_list(value),
        {
            "--sysroot": "directory",
            "-B": "directory",
            "-I": "directory",
            "-L": "directory",
            "-include": "file",
            "-iquote": "directory",
            "-isystem": "directory",
        },
        base_directory,
    )


def _normalize_path_arguments(
    arguments: list[str], path_options: dict[str, str], base_directory: Any = None
) -> list[Any]:
    normalized: list[Any] = []
    index = 0
    attached_options = sorted(path_options, key=len, reverse=True)
    while index < len(arguments):
        argument = arguments[index]
        if argument in path_options and index + 1 < len(arguments):
            normalized.append(
                {
                    "option": argument,
                    "value": _path_argument_identity(
                        arguments[index + 1], path_options[argument], base_directory
                    ),
                }
            )
            index += 2
            continue

        attached = next(
            (
                option
                for option in attached_options
                if (
                    argument.startswith(f"{option}=")
                    or (
                        option.startswith("-")
                        and not option.startswith("--")
                        and argument.startswith(option)
                        and len(argument) > len(option)
                    )
                )
            ),
            None,
        )
        if attached is not None:
            raw_value = argument[len(attached) :].removeprefix("=")
            normalized.append(
                {
                    "option": attached,
                    "value": _path_argument_identity(
                        raw_value, path_options[attached], base_directory
                    ),
                }
            )
        else:
            normalized.append(argument)
        index += 1
    return normalized


def _path_argument_identity(
    value: str, kind: str, base_directory: Any = None
) -> dict[str, Any]:
    if kind == "output":
        return {"kind": "output"}
    path = Path(value).expanduser()
    if not path.is_absolute() and str(base_directory or "").strip():
        path = Path(str(base_directory)).expanduser() / path
    if kind == "file" and path.is_file():
        return {"kind": "file", "sha256": _file_sha256(path)}
    if kind == "directory" and path.is_dir():
        return {"kind": "directory", "contents": _directory_identity(path)}
    return {"kind": "missing", "name": path.name}


def _directory_identity(value: Any) -> list[dict[str, str]]:
    root = Path(str(value or "")).expanduser().resolve()
    files: list[dict[str, str]] = []
    visited_directories: set[tuple[int, int]] = set()
    for directory, child_directories, filenames in os.walk(root, followlinks=True):
        current = Path(directory)
        try:
            directory_stat = current.stat()
        except OSError:
            child_directories.clear()
            continue
        directory_key = (directory_stat.st_dev, directory_stat.st_ino)
        if directory_key in visited_directories:
            child_directories.clear()
            continue
        visited_directories.add(directory_key)
        child_directories.sort()
        for filename in sorted(filenames):
            path = current / filename
            try:
                if path.is_file():
                    files.append(
                        {
                            "path": path.relative_to(root).as_posix(),
                            "sha256": _file_sha256(path),
                        }
                    )
            except (OSError, ValueError):
                continue
    return files


def _comparison_fingerprint(
    step: Any, workspace: dict[str, Any], comparison: dict[str, Any]
) -> str:
    payload = {
        "step": str(step.name),
        "tool": str(step.tool),
        "comparison": comparison,
        "design": str(workspace.get("design", "")),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _prepared_input_fingerprint(step: Any) -> str:
    manifest = (
        Path(step.directory).parent / "prepare_fe" / "output" / "prepared_inputs.json"
    )
    payload = _read_record(manifest)
    if payload is None:
        return ""
    sources = _records(payload.get("rtl_sources"))
    if not sources:
        sources = [{"path": path} for path in _string_list(payload.get("rtl_files"))]
    identity = {
        "rtl": [
            {
                "name": Path(str(item.get("path") or "")).name,
                "ownership": str(item.get("ownership") or ""),
                "source": str(item.get("source") or ""),
                "sha256": _file_sha256(item.get("path")),
            }
            for item in sources
        ],
        "include_dirs": [
            _include_directory_identity(path)
            for path in _prepared_include_directories(payload)
        ],
        "defines": _string_list(payload.get("defines")),
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _file_sha256(value: Any) -> str:
    path = Path(str(value or "")).expanduser()
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _include_directory_identity(value: Any) -> dict[str, Any]:
    root = Path(str(value or "")).expanduser().resolve()
    if not root.is_dir():
        return {"status": "missing", "headers": []}

    headers: list[dict[str, str]] = []
    visited_directories: set[tuple[int, int]] = set()
    walk_errors: list[OSError] = []
    for directory, child_directories, filenames in os.walk(
        root,
        followlinks=True,
        onerror=walk_errors.append,
    ):
        current = Path(directory)
        try:
            directory_stat = current.stat()
        except OSError as exc:
            walk_errors.append(exc)
            child_directories.clear()
            continue
        directory_identity = (directory_stat.st_dev, directory_stat.st_ino)
        if directory_identity in visited_directories:
            child_directories.clear()
            continue
        visited_directories.add(directory_identity)
        child_directories.sort()

        for filename in sorted(filenames):
            path = current / filename
            if path.suffix and path.suffix.lower() not in _HDL_INCLUDE_SUFFIXES:
                continue
            try:
                if not path.is_file():
                    continue
                relative_path = path.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            headers.append(
                {"path": relative_path, "sha256": _file_sha256(path)}
            )
    headers.sort(key=lambda item: (item["path"], item["sha256"]))
    return {
        "status": "unreadable" if walk_errors else "available",
        "headers": headers,
    }


def _prepared_include_directories(payload: dict[str, Any]) -> list[str]:
    directories = _string_list(payload.get("incdirs"))
    directories.extend(
        str(Path(path).expanduser().resolve().parent)
        for path in _string_list(payload.get("rtl_files"))
        if path.strip()
    )
    seen: set[Path] = set()
    ordered: list[str] = []
    for value in directories:
        text = value.strip()
        if not text:
            continue
        canonical = Path(text).expanduser().resolve()
        if canonical in seen:
            continue
        seen.add(canonical)
        ordered.append(text)
    return ordered


def _unique_sources(metrics: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    sources: list[dict[str, str]] = []
    for metric in metrics:
        source = _record(metric.get("source"))
        key = (str(source.get("kind", "")), str(source.get("path", "")))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        sources.append({"kind": key[0], "path": key[1]})
    return sources


def _source(path: str, selector: str) -> dict[str, str]:
    return {"kind": "report", "path": path, "selector": selector}


def _relative_report_source(value: Any, fallback: str) -> str:
    path = str(value or "").strip()
    return (
        path
        if path and not Path(path).is_absolute() and ".." not in Path(path).parts
        else fallback
    )


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _records(value: Any) -> list[dict[str, Any]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _valid_sim_case(value: dict[str, Any]) -> bool:
    name = value.get("name")
    metrics = value.get("metrics")
    failure = value.get("failure")
    return (
        isinstance(name, str)
        and bool(name.strip())
        and isinstance(value.get("ok"), bool)
        and _valid_sim_metrics(metrics)
        and (failure is None or isinstance(failure, dict))
    )


def _valid_sim_metrics(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    for field in (
        "cycles",
        "max_cycles",
        "coremark_per_mhz",
        "coremark_per_second",
        "cycles_per_iteration",
        "frequency_mhz",
    ):
        if field in value and value[field] is not None and not _is_number(value[field]):
            return False
    if "iterations" in value and value["iterations"] is not None and not _is_count(
        value["iterations"]
    ):
        return False
    if "timeout_accepted" in value and not isinstance(value["timeout_accepted"], bool):
        return False
    if "difftest" not in value:
        return True
    difftest = value.get("difftest")
    if not isinstance(difftest, dict) or not isinstance(difftest.get("enabled"), bool):
        return False
    status = difftest.get("status")
    if status is None:
        return difftest["enabled"] is False
    if not isinstance(status, str) or status.strip().lower() not in {
        "disabled",
        "incomplete",
        "mismatch",
        "passed",
    }:
        return False
    normalized_status = status.strip().lower()
    if difftest["enabled"] is False and normalized_status != "disabled":
        return False
    if difftest["enabled"] is True and normalized_status == "disabled":
        return False
    for field in ("commits", "compared"):
        if field in difftest and difftest[field] is not None and not _is_count(
            difftest[field]
        ):
            return False
    for field in ("last_pc", "last_npc"):
        if field in difftest and difftest[field] is not None and not isinstance(
            difftest[field], str
        ):
            return False
    first_mismatch = difftest.get("first_mismatch")
    if first_mismatch is not None:
        if not isinstance(first_mismatch, dict):
            return False
        if not isinstance(first_mismatch.get("message"), str):
            return False
        if first_mismatch.get("pc") is not None and not isinstance(
            first_mismatch["pc"], str
        ):
            return False
    if normalized_status == "mismatch" and first_mismatch is None:
        return False
    if normalized_status == "passed":
        return all(_is_count(difftest.get(field)) for field in ("commits", "compared"))
    return True


def _valid_prepare_contract(value: dict[str, Any]) -> bool:
    status = value.get("status")
    return (
        isinstance(value.get("id"), str)
        and bool(value["id"].strip())
        and isinstance(status, str)
        and status.strip().lower()
        in _PASSING_CONTRACT_STATUSES | _FAILING_CONTRACT_STATUSES
    )


def _valid_count_fields(value: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return all(_is_count(value.get(field)) for field in fields)


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]


def _number(value: Any) -> int | float:
    return (
        value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
    )


def _optional_number(value: Any) -> int | float | None:
    return (
        value
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _compare(actual: float, operator: str, expected: float) -> bool:
    comparators: dict[str, Callable[[int | float, int | float], bool]] = {
        "==": lambda left, right: left == right,
        ">": lambda left, right: left > right,
        ">=": lambda left, right: left >= right,
        "<=": lambda left, right: left <= right,
    }
    return comparators[operator](actual, expected)


_STEP_BUILDERS: dict[str, Callable[[Any, dict[str, Any]], _QorResult]] = {
    "prepare": _prepare_qor,
    "review": _review_qor,
    "elab": _elab_qor,
    "lint": _lint_qor,
    "sim": _sim_qor,
}


def _step_qor_source_path(step: Any) -> Path | None:
    step_name = str(step.name)
    if step_name == "prepare":
        return Path(step.report["step"])
    report_name = {
        "review": "rtl_review.json",
        "elab": "elab_summary.json",
        "lint": "lint_summary.json",
        "sim": "cases.json",
    }.get(step_name)
    return Path(step.report["dir"]) / report_name if report_name else None
