"""Build stable, machine-readable QoR artifacts for ECC-FE flow steps."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import tempfile
from collections.abc import Callable
from pathlib import Path
from subprocess import SubprocessError, run as run_subprocess
from typing import Any

from fecompiler.tools.verilator.runner import (
    effective_sim_cflags,
    effective_sim_ldflags,
    sim_cpp_sources,
)

SCHEMA_REVISION = "frontend-quality-gates-v1"
_HDL_INCLUDE_SUFFIXES = frozenset({".h", ".inc", ".orig", ".sv", ".svh", ".v", ".vh"})
_PASSING_CONTRACT_STATUSES = frozenset(
    {"module_only", "not_required", "ok", "pass", "success"}
)
_FAILING_CONTRACT_STATUSES = frozenset({"error", "fail", "failed"})
_CPP_INCLUDE_RE = re.compile(
    r'^\s*#\s*include\s*(?:"(?P<quoted>[^"]+)"|<(?P<system>[^>]+)>)',
    re.MULTILINE,
)
_LINKER_PATH_OPTIONS = {
    "--dynamic-list": "file",
    "--just-symbols": "file",
    "--output": "output",
    "--retain-symbols-file": "file",
    "--script": "file",
    "--version-script": "file",
    "-Map": "output",
    "-T": "file",
    "-o": "output",
    "-rpath": "search_directory",
    "-rpath-link": "search_directory",
}
_MAKE_LINK_DRIVER_MARKER = "__ECC_FE_LINK_DRIVER__="
_GLOBAL_STATIC_LINK_ARGUMENTS = frozenset({"--static", "-static"})
_POSITIONAL_STATIC_LINK_ARGUMENTS = frozenset(
    {"--static", "-Bstatic", "-dn", "-non_shared", "-static"}
)
_POSITIONAL_DYNAMIC_LINK_ARGUMENTS = frozenset(
    {"-Bdynamic", "-call_shared", "-dy"}
)


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
    if result.score is not None:
        summary_payload["score"] = result.score
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
        self.score: dict[str, Any] | None = None


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
    report_path = "report/prepare.rpt"
    rtl_source = _source(report_path, "/rtl_files")
    incdir_source = _source(report_path, "/incdirs")
    define_source = _source(report_path, "/defines")
    contract_source = _source(report_path, "/contracts")
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
                rtl_source,
            ),
            _metric(
                "include_dir_count",
                "Include Directories",
                _number(report.get("incdirs")),
                "count",
                "readiness",
                "trend_only",
                "prepared_inputs",
                incdir_source,
            ),
            _metric(
                "define_count",
                "Defines",
                _number(report.get("defines")),
                "count",
                "readiness",
                "trend_only",
                "prepared_inputs",
                define_source,
            ),
            _metric(
                "contract_failure_count",
                "Contract Failures",
                len(failures),
                "count",
                "readiness",
                "lower_is_better",
                "interface_contracts",
                contract_source,
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
            contract_source,
        )
    )
    result.hotspots.extend(
        _hotspot(
            "contract_failure_count",
            str(item.get("id") or "contract"),
            "critical",
            str(item.get("status") or "failed"),
            contract_source,
            str(
                item.get("detail")
                or item.get("reason")
                or "Frontend contract did not pass."
            ),
        )
        for item in failures
    )
    result.score = _prepare_readiness_score(report.get("readiness"))
    result.comparison = {
        "input_fingerprint": _prepared_input_fingerprint(
            step,
            _record(report.get("comparison_inputs")) or None,
        ),
        "cpu_top": str(
            workspace.get("required_cpu_top_module")
            or workspace.get("cpu_top_module")
            or ""
        ),
    }
    return result


def _prepare_readiness_score(value: Any) -> dict[str, Any] | None:
    readiness = _record(value)
    sources = _record(readiness.get("sources"))
    top = _record(readiness.get("top"))
    interface = _record(readiness.get("interface"))
    reproducibility = _record(readiness.get("reproducibility"))
    if (
        readiness.get("schema_version") != 1
        or not _valid_readiness_counts(
            sources,
            ("rtl_total", "rtl_resolved", "include_dir_total", "include_dir_resolved"),
        )
        or not _valid_readiness_counts(top, ("definitions",))
        or not _valid_readiness_counts(
            interface,
            (
                "expected_ports",
                "matched_ports",
                "missing_ports",
                "extra_ports",
                "mismatched_ports",
            ),
        )
        or not all(
            isinstance(item, bool)
            for item in (
                top.get("required"),
                top.get("source_in_inputs"),
                interface.get("applicable"),
                interface.get("verified"),
                reproducibility.get("input_fingerprint"),
                reproducibility.get("merged_filelist"),
                reproducibility.get("prepared_manifest"),
            )
        )
    ):
        return None

    rtl_total = int(sources["rtl_total"])
    rtl_resolved = int(sources["rtl_resolved"])
    incdir_total = int(sources["include_dir_total"])
    incdir_resolved = int(sources["include_dir_resolved"])
    if (
        rtl_total <= 0
        or rtl_resolved > rtl_total
        or incdir_resolved > incdir_total
    ):
        return None
    source_earned = 20 * rtl_resolved / rtl_total
    source_earned += 10 if incdir_total == 0 else 10 * incdir_resolved / incdir_total

    top_required = bool(top["required"])
    top_definitions = int(top["definitions"])
    top_source_in_inputs = bool(top["source_in_inputs"])
    top_earned = (
        20
        if not top_required
        else (15 if top_definitions == 1 else 0) + (5 if top_source_in_inputs else 0)
    )

    interface_applicable = bool(interface["applicable"])
    interface_verified = bool(interface["verified"])
    expected_ports = int(interface["expected_ports"])
    matched_ports = int(interface["matched_ports"])
    missing_ports = int(interface["missing_ports"])
    extra_ports = int(interface["extra_ports"])
    mismatched_ports = int(interface["mismatched_ports"])
    if (
        matched_ports > expected_ports
        or (interface_verified and expected_ports == 0)
        or (
            interface_verified
            and matched_ports + missing_ports + mismatched_ports != expected_ports
        )
        or (
            not interface_verified
            and any((expected_ports, matched_ports, missing_ports, mismatched_ports))
        )
    ):
        return None
    if not interface_applicable:
        interface_earned = 40.0
        interface_summary = "Interface contract is not required for this workspace."
    elif not interface_verified:
        interface_earned = 0.0
        interface_summary = "No expected CPU interface contract was available to verify."
    else:
        interface_earned = 35 * matched_ports / expected_ports
        interface_earned += 5 if extra_ports == 0 else 0
        interface_summary = (
            f"{matched_ports} of {expected_ports} required ports matched; "
            f"{extra_ports} unexpected."
        )

    fingerprint_recorded = bool(reproducibility["input_fingerprint"])
    merged_filelist = bool(reproducibility["merged_filelist"])
    prepared_manifest = bool(reproducibility["prepared_manifest"])
    reproducibility_earned = (
        (5 if fingerprint_recorded else 0)
        + (2.5 if merged_filelist else 0)
        + (2.5 if prepared_manifest else 0)
    )
    components = [
        _score_component(
            "source_resolution",
            "Source resolution",
            source_earned,
            30,
            f"{rtl_resolved} of {rtl_total} RTL sources and "
            f"{incdir_resolved} of {incdir_total} include directories resolved.",
        ),
        _score_component(
            "top_resolution",
            "Top resolution",
            top_earned,
            20,
            (
                "Top-module validation is not required for this workspace."
                if not top_required
                else f"{top_definitions} matching definition found; "
                f"source {'is' if top_source_in_inputs else 'is not'} in prepared inputs."
            ),
        ),
        _score_component(
            "interface_contract",
            "Interface contract",
            interface_earned,
            40,
            interface_summary,
        ),
        _score_component(
            "reproducibility",
            "Reproducibility",
            reproducibility_earned,
            10,
            (
                f"Input fingerprint {'recorded' if fingerprint_recorded else 'missing'}; "
                f"normalized outputs {'persisted' if merged_filelist and prepared_manifest else 'incomplete'}."
            ),
        ),
    ]
    return {
        "label": "Preparation readiness",
        "value": _round_score(sum(component["earned"] for component in components)),
        "maximum": 100,
        "scoring_version": 1,
        "components": components,
    }


def _valid_readiness_counts(value: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return bool(value) and all(_is_count(value.get(field)) for field in fields)


def _score_component(
    component_id: str,
    label: str,
    earned: float,
    possible: float,
    summary: str,
) -> dict[str, Any]:
    return {
        "id": component_id,
        "label": label,
        "earned": _round_score(earned),
        "possible": possible,
        "summary": summary,
    }


def _round_score(value: float) -> int | float:
    rounded = round(float(value), 1)
    return int(rounded) if rounded.is_integer() else rounded


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
    thresholds = _record(precheck.get("risk_thresholds"))
    structural_fields = ("max_fanout", "max_fanin", "max_comb_depth")
    if (
        not _valid_count_fields(
            summary, ("actionable_errors", "actionable_warnings")
        )
        or not _valid_count_fields(structural, structural_fields)
        or not _valid_count_fields(thresholds, structural_fields)
        or any(int(thresholds[field]) <= 0 for field in structural_fields)
    ):
        return None

    errors = int(summary["actionable_errors"])
    warnings = int(summary["actionable_warnings"])
    precheck_earned = 30 if precheck_state == "pass" else 0
    error_earned = max(0, 30 - errors * 10)
    warning_earned = max(0, 15 - warnings * 3)

    headroom_parts = (
        ("max_fanout", 8.3),
        ("max_fanin", 8.3),
        ("max_comb_depth", 8.4),
    )
    headroom_available = precheck_state == "pass"
    headroom_earned = (
        sum(
            possible
            * min(
                1.0,
                int(thresholds[field]) / max(int(structural[field]), 1),
            )
            for field, possible in headroom_parts
        )
        if headroom_available
        else 0
    )
    components = [
        _score_component(
            "structural_precheck",
            "Structural precheck",
            precheck_earned,
            30,
            (
                "Yosys completed the CPU-only structural precheck."
                if precheck_state == "pass"
                else f"Yosys precheck state is {precheck_state}."
            ),
        ),
        _score_component(
            "actionable_errors",
            "Actionable errors",
            error_earned,
            30,
            f"{errors} actionable RTL error{'s' if errors != 1 else ''} reported.",
        ),
        _score_component(
            "warning_hygiene",
            "Warning hygiene",
            warning_earned,
            15,
            f"{warnings} actionable RTL warning{'s' if warnings != 1 else ''} reported.",
        ),
        _score_component(
            "structural_headroom",
            "Structural headroom",
            headroom_earned,
            25,
            (
                f"Fanout {structural['max_fanout']}/{thresholds['max_fanout']}, "
                f"fanin {structural['max_fanin']}/{thresholds['max_fanin']}, "
                f"depth {structural['max_comb_depth']}/{thresholds['max_comb_depth']} "
                "(measured/target)."
                if headroom_available
                else "Structural headroom is unavailable because the precheck did not pass."
            ),
        ),
    ]
    return {
        "label": "RTL review quality",
        "value": _round_score(sum(component["earned"] for component in components)),
        "maximum": 100,
        "scoring_version": 1,
        "components": components,
    }


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
    result.score = _elaboration_score(report, summary)
    result.comparison = {
        "input_fingerprint": _prepared_input_fingerprint(step),
        "top_module": str(
            summary.get("top_module") or workspace.get("top_module") or ""
        ),
    }
    return result


def _elaboration_score(
    report: dict[str, Any], summary: dict[str, Any]
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

    compiler_earned = 25 if compiler_passed else 0
    error_earned = max(0, 30 - errors * 10)
    hierarchy_earned = max(0, 20 - unresolved * 5) if compiler_passed else 0
    top_earned = 15 if compiler_passed and summary["top_found"] is True else 0
    warning_earned = max(0, 10 - warnings * 2)
    components = [
        _score_component(
            "compiler_execution",
            "Compiler execution",
            compiler_earned,
            25,
            (
                "Slang completed an authoritative full elaboration."
                if compiler_passed
                else f"Slang full elaboration failed with return code {returncode}."
            ),
        ),
        _score_component(
            "diagnostic_errors",
            "Diagnostic errors",
            error_earned,
            30,
            f"{errors} compiler error{'s' if errors != 1 else ''} reported.",
        ),
        _score_component(
            "hierarchy_closure",
            "Hierarchy closure",
            hierarchy_earned,
            20,
            (
                f"{unresolved} unresolved module{'s' if unresolved != 1 else ''} "
                "in the authoritative compiler result."
                if compiler_passed
                else "Hierarchy closure is unproven because full elaboration did not pass."
            ),
        ),
        _score_component(
            "top_resolution",
            "Top resolution",
            top_earned,
            15,
            (
                f"Top module {summary.get('top_module') or '<unknown>'} resolved by Slang."
                if compiler_passed and summary["top_found"] is True
                else "Top resolution is unproven by a successful full elaboration."
            ),
        ),
        _score_component(
            "warning_hygiene",
            "Warning hygiene",
            warning_earned,
            10,
            f"{warnings} compiler warning{'s' if warnings != 1 else ''} reported.",
        ),
    ]
    return {
        "label": "Elaboration quality",
        "value": _round_score(sum(component["earned"] for component in components)),
        "maximum": 100,
        "scoring_version": 1,
        "components": components,
    }


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
    result.score = _lint_score(report, summary)
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
    report: dict[str, Any], summary: dict[str, Any]
) -> dict[str, Any] | None:
    raw_diagnostics = report.get("diagnostics")
    diagnostics = _records(raw_diagnostics)
    status = str(report.get("status", "")).strip().lower()
    returncode = report.get("returncode")
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
                "cpu_errors",
                "cpu_warnings",
                "actionable_diagnostics",
            ),
        )
        or str(summary.get("status", "")).strip().lower() != status
    ):
        return None

    cpu_diagnostics = [
        item
        for item in diagnostics
        if item.get("actionable") is True and item.get("ownership") == "cpu"
    ]
    cpu_errors = sum(item.get("severity") == "error" for item in cpu_diagnostics)
    cpu_warnings = sum(item.get("severity") == "warning" for item in cpu_diagnostics)
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
        or int(summary["cpu_errors"]) != cpu_errors
        or int(summary["cpu_warnings"]) != cpu_warnings
        or int(summary["actionable_diagnostics"]) != len(cpu_diagnostics)
        or (status == "pass") != (returncode == 0 and total_errors == 0)
    ):
        return None

    # A non-zero Verilator exit can contain unclassified fatal diagnostics (for
    # example, a missing top module).  Those records do not prove that the CPU
    # was fully analyzed, so cleanliness credit is only valid after a clean exit.
    analysis_completed = returncode == 0
    cpu_rule_count = len(
        {
            str(item["code"]).strip().upper()
            for item in cpu_diagnostics
            if str(item["code"]).strip()
        }
    )
    execution_earned = 25 if analysis_completed else 0
    error_earned = max(0, 40 - cpu_errors * 20) if analysis_completed else 0
    warning_earned = max(0, 25 - cpu_warnings * 2.5) if analysis_completed else 0
    rule_earned = max(0, 10 - cpu_rule_count * 2) if analysis_completed else 0
    components = [
        _score_component(
            "analysis_execution",
            "Analysis execution",
            execution_earned,
            25,
            (
                "Verilator completed and produced classified lint diagnostics."
                if analysis_completed
                else "Verilator analysis did not complete; CPU cleanliness is unproven."
            ),
        ),
        _score_component(
            "cpu_errors",
            "CPU errors",
            error_earned,
            40,
            f"{cpu_errors} actionable CPU error{'s' if cpu_errors != 1 else ''} reported.",
        ),
        _score_component(
            "cpu_warnings",
            "CPU warnings",
            warning_earned,
            25,
            f"{cpu_warnings} actionable CPU warning{'s' if cpu_warnings != 1 else ''} reported.",
        ),
        _score_component(
            "cpu_rule_breadth",
            "CPU rule breadth",
            rule_earned,
            10,
            f"{cpu_rule_count} distinct lint rule{'s' if cpu_rule_count != 1 else ''} affecting CPU-owned RTL.",
        ),
    ]
    return {
        "label": "CPU lint quality",
        "value": _round_score(sum(component["earned"] for component in components)),
        "maximum": 100,
        "scoring_version": 1,
        "components": components,
    }


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
    sim_cflags = effective_sim_cflags(workspace)
    sim_build_directory = Path(step.directory) / "obj_dir"
    sim_dependencies = _simulation_dependency_map(workspace, sim_build_directory)
    result.comparison = {
        "input_fingerprint": _prepared_input_fingerprint(step),
        "top_module": str(workspace.get("top_module") or ""),
        "harness_sources": _simulation_harness_sources(
            workspace,
            sim_cflags,
            sim_build_directory,
            sim_dependencies,
        ),
        "forced_headers": _simulation_forced_header_identities(
            sim_cflags,
            sim_build_directory,
            sim_dependencies,
        ),
        "sim_cflags": _normalized_shell_compile_flags(
            sim_cflags, sim_build_directory
        ),
        "sim_ldflags": _normalized_shell_link_flags(
            effective_sim_ldflags(workspace), sim_build_directory
        ),
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
    return _normalize_compile_arguments(
        _string_list(value), base_directory, response_files=set()
    )


def _normalize_compile_arguments(
    arguments: list[str],
    base_directory: Any = None,
    *,
    response_files: set[Path],
) -> list[Any]:
    def normalize_response(argument: str) -> dict[str, Any] | None:
        if not argument.startswith("@") or len(argument) == 1:
            return None
        return _response_file_identity(
            argument[1:],
            base_directory,
            response_files,
            lambda nested, visited: _normalize_compile_arguments(
                nested,
                base_directory,
                response_files=visited,
            ),
        )

    return _normalize_path_arguments(
        arguments,
        {
            "--sysroot": "directory",
            "-B": "directory",
            "-I": "search_directory",
            "-L": "search_directory",
            "-include": "file",
            "-iquote": "search_directory",
            "-isystem": "search_directory",
        },
        base_directory,
        embedded_normalizer=normalize_response,
    )


def _normalized_shell_compile_flags(
    value: Any, base_directory: Any = None
) -> list[Any]:
    return _normalize_compile_arguments(
        _shell_flag_tokens(value), base_directory, response_files=set()
    )


def _normalized_shell_link_flags(
    value: Any, base_directory: Any = None
) -> list[Any]:
    arguments = _shell_flag_tokens(value)
    link_driver = _verilator_link_driver(base_directory)
    link_state: dict[str, Any] = {
        "static": _link_has_global_static(arguments, base_directory),
        "stack": [],
    }
    return _normalize_link_arguments(
        arguments,
        _link_search_directories(arguments, base_directory),
        base_directory,
        link_state=link_state,
        response_files=set(),
        library_probe_command=[*link_driver, *arguments],
    )


def _shell_flag_tokens(value: Any) -> list[str]:
    tokens: list[str] = []
    for flag in _string_list(value):
        try:
            tokens.extend(shlex.split(flag))
        except ValueError:
            tokens.append(flag)
    return tokens


def _normalized_wl_flag(
    argument: str,
    library_directories: list[Path],
    link_state: dict[str, Any],
    base_directory: Any = None,
    response_files: set[Path] | None = None,
    library_probe_command: list[str] | None = None,
) -> dict[str, Any] | None:
    if not argument.startswith("-Wl,"):
        return None
    return {
        "option": "-Wl",
        "arguments": _normalize_link_arguments(
            argument[len("-Wl,") :].split(","),
            library_directories,
            base_directory,
            allow_wl=False,
            link_state=link_state,
            response_files=response_files,
            library_probe_command=library_probe_command,
        ),
    }


def _normalize_link_arguments(
    arguments: list[str],
    library_directories: list[Path],
    base_directory: Any = None,
    *,
    allow_wl: bool = True,
    link_state: dict[str, Any] | None = None,
    response_files: set[Path] | None = None,
    library_probe_command: list[str] | None = None,
) -> list[Any]:
    if link_state is None:
        link_state = {"static": False, "stack": []}
    if response_files is None:
        response_files = set()
    normalized: list[Any] = []
    index = 0
    path_options = sorted(_LINKER_PATH_OPTIONS, key=len, reverse=True)
    while index < len(arguments):
        argument = arguments[index]
        if allow_wl:
            wl_flag = _normalized_wl_flag(
                argument,
                library_directories,
                link_state,
                base_directory,
                response_files,
                library_probe_command,
            )
            if wl_flag is not None:
                normalized.append(wl_flag)
                index += 1
                continue
        if argument in _POSITIONAL_STATIC_LINK_ARGUMENTS:
            if not allow_wl or argument not in _GLOBAL_STATIC_LINK_ARGUMENTS:
                link_state["static"] = True
            normalized.append(argument)
            index += 1
            continue
        if argument in _POSITIONAL_DYNAMIC_LINK_ARGUMENTS:
            link_state["static"] = False
            normalized.append(argument)
            index += 1
            continue
        if argument == "--push-state":
            link_state["stack"].append(link_state["static"])
            normalized.append(argument)
            index += 1
            continue
        if argument == "--pop-state":
            if link_state["stack"]:
                link_state["static"] = link_state["stack"].pop()
            normalized.append(argument)
            index += 1
            continue
        if (
            argument in {"--sysroot", "-L", *_LINKER_PATH_OPTIONS}
            and index + 1 < len(arguments)
        ):
            kind = (
                "directory"
                if argument == "--sysroot"
                else "search_directory"
                if argument == "-L"
                else _LINKER_PATH_OPTIONS[argument]
            )
            normalized.append(
                {
                    "option": argument,
                    "value": _path_argument_identity(
                        arguments[index + 1], kind, base_directory
                    ),
                }
            )
            index += 2
            continue
        if argument == "-l" and index + 1 < len(arguments):
            normalized.append(
                _library_argument_identity(
                    arguments[index + 1],
                    library_directories,
                    static=link_state["static"],
                    base_directory=base_directory,
                    compiler_command=library_probe_command,
                )
            )
            index += 2
            continue
        if argument.startswith("-l") and argument != "-l":
            normalized.append(
                _library_argument_identity(
                    argument[len("-l") :],
                    library_directories,
                    static=link_state["static"],
                    base_directory=base_directory,
                    compiler_command=library_probe_command,
                )
            )
            index += 1
            continue

        attached = next(
            (
                option
                for option in ("--sysroot", "-L", *path_options)
                if argument.startswith(f"{option}=")
                or (
                    option.startswith("-")
                    and not option.startswith("--")
                    and argument.startswith(option)
                    and len(argument) > len(option)
                )
            ),
            None,
        )
        if attached is not None:
            kind = (
                "directory"
                if attached == "--sysroot"
                else "search_directory"
                if attached == "-L"
                else _LINKER_PATH_OPTIONS[attached]
            )
            normalized.append(
                {
                    "option": attached,
                    "value": _path_argument_identity(
                        argument[len(attached) :].removeprefix("="),
                        kind,
                        base_directory,
                    ),
                }
            )
        elif argument.startswith("@") and len(argument) > 1:
            normalized.append(
                _response_file_identity(
                    argument[1:],
                    base_directory,
                    response_files,
                    lambda nested, visited: _normalize_link_arguments(
                        nested,
                        library_directories,
                        base_directory,
                        allow_wl=allow_wl,
                        link_state=link_state,
                        response_files=visited,
                        library_probe_command=library_probe_command,
                    ),
                )
            )
        elif not argument.startswith("-") and _resolved_path(
            argument, base_directory
        ).is_file():
            normalized.append(_path_argument_identity(argument, "file", base_directory))
        else:
            normalized.append(argument)
        index += 1
    return normalized


def _link_search_directories(
    arguments: list[str], base_directory: Any = None
) -> list[Path]:
    directories: list[Path] = []
    seen: set[Path] = set()

    def add(raw_value: str) -> None:
        directory = _resolved_path(raw_value, base_directory)
        if directory.is_dir() and directory not in seen:
            seen.add(directory)
            directories.append(directory)

    def visit(tokens: list[str], response_files: set[Path]) -> None:
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.startswith("-Wl,"):
                visit(token[len("-Wl,") :].split(","), response_files)
                index += 1
            elif token.startswith("@") and len(token) > 1:
                response_path = _resolved_path(token[1:], base_directory)
                if response_path not in response_files:
                    response_tokens = _read_response_file(response_path)
                    if response_tokens is not None:
                        visit(response_tokens, {*response_files, response_path})
                index += 1
            elif token == "-L" and index + 1 < len(tokens):
                add(tokens[index + 1])
                index += 2
            elif token.startswith("-L") and token != "-L":
                add(token[len("-L") :].removeprefix("="))
                index += 1
            else:
                index += 1

    visit(arguments, set())
    for value in os.environ.get("LIBRARY_PATH", "").split(os.pathsep):
        if value.strip():
            add(value)
    return directories


def _library_argument_identity(
    name: str,
    library_directories: list[Path],
    *,
    static: bool,
    base_directory: Any = None,
    compiler_command: list[str] | None = None,
) -> dict[str, Any]:
    filenames = (
        [name[1:]]
        if name.startswith(":")
        else [f"lib{name}.a"]
        if static
        else [f"lib{name}.so", f"lib{name}.a"]
    )
    library = next(
        (
            candidate
            for directory in library_directories
            for filename in filenames
            if (candidate := directory / filename).is_file()
        ),
        None,
    )
    if library is None:
        library = _compiler_library_file(
            filenames,
            base_directory,
            compiler_command=compiler_command,
        )
    return {
        "option": "-l",
        "name": name,
        "mode": "static" if static else "dynamic-preferred",
        "value": (
            {"kind": "file", "sha256": _file_sha256(library)}
            if library is not None
            else {"kind": "unresolved"}
        ),
    }


def _compiler_library_file(
    filenames: list[str],
    base_directory: Any = None,
    *,
    compiler_command: list[str] | None = None,
) -> Path | None:
    compiler = compiler_command or ["c++"]
    if not compiler:
        return None

    working_directory = _resolved_path(".", base_directory)
    cwd = str(working_directory) if working_directory.is_dir() else None
    for filename in filenames:
        try:
            result = run_subprocess(
                [*compiler, f"-print-file-name={filename}"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, SubprocessError):
            return None
        resolved = result.stdout.strip()
        if result.returncode != 0 or not resolved or resolved == filename:
            continue
        candidate = _resolved_path(resolved, base_directory)
        if candidate.is_file():
            return candidate
    return None


def _verilator_link_driver(base_directory: Any = None) -> list[str]:
    build_directory = _resolved_path(".", base_directory)
    if not build_directory.is_dir():
        return ["c++"]
    makefile = next(
        (
            path
            for path in sorted(build_directory.glob("V*.mk"))
            if not path.name.endswith("_classes.mk")
        ),
        None,
    )
    if makefile is None:
        return ["c++"]
    try:
        result = run_subprocess(
            [
                "make",
                "--no-print-directory",
                "-f",
                makefile.name,
                "-f",
                "-",
                "-n",
            ],
            cwd=str(build_directory),
            input=f"$(info {_MAKE_LINK_DRIVER_MARKER}$(LINK))\n",
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, SubprocessError):
        return ["c++"]
    match = re.search(
        rf"^{re.escape(_MAKE_LINK_DRIVER_MARKER)}(?P<command>\S.*)$",
        result.stdout,
        re.MULTILINE,
    )
    if match is None:
        return ["c++"]
    try:
        command = shlex.split(match.group("command"))
    except ValueError:
        return ["c++"]
    return command or ["c++"]


def _link_has_global_static(
    arguments: list[str],
    base_directory: Any = None,
    response_files: set[Path] | None = None,
) -> bool:
    visited = response_files or set()
    for argument in arguments:
        if argument in _GLOBAL_STATIC_LINK_ARGUMENTS:
            return True
        if argument.startswith("@") and len(argument) > 1:
            response_path = _resolved_path(argument[1:], base_directory)
            if response_path in visited:
                continue
            response_arguments = _read_response_file(response_path)
            if response_arguments is not None and _link_has_global_static(
                response_arguments,
                base_directory,
                {*visited, response_path},
            ):
                return True
    return False


def _response_file_identity(
    value: str,
    base_directory: Any,
    response_files: set[Path],
    normalize_arguments: Callable[[list[str], set[Path]], list[Any]],
) -> dict[str, Any]:
    path = _resolved_path(value, base_directory)
    identity: dict[str, Any] = {
        "option": "@",
        "value": _path_argument_identity(value, "file", base_directory),
    }
    if not path.is_file():
        return identity
    if path in response_files:
        identity["cycle"] = True
        return identity
    arguments = _read_response_file(path)
    if arguments is not None:
        identity["arguments"] = normalize_arguments(
            arguments, {*response_files, path}
        )
    return identity


def _read_response_file(path: Path) -> list[str] | None:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        return shlex.split(content, comments=False, posix=True)
    except ValueError:
        return None


def _simulation_harness_sources(
    workspace: dict[str, Any],
    compile_flags: Any = None,
    build_directory: Any = None,
    dependency_map: dict[Path, set[Path]] | None = None,
) -> list[dict[str, Any]]:
    include_directories, _ = _cpp_compile_inputs(
        effective_sim_cflags(workspace) if compile_flags is None else compile_flags,
        build_directory,
    )
    sources: list[dict[str, Any]] = []
    for source in sim_cpp_sources(workspace):
        source_path = _resolved_path(source)
        compiler_dependencies = (
            dependency_map.get(source_path) if dependency_map is not None else None
        )
        sources.append(
            {
                "name": Path(source).name,
                "sha256": _file_sha256(source_path),
                "local_headers": (
                    _compiler_dependency_identities(
                        source_path,
                        compiler_dependencies,
                        include_directories,
                        build_directory,
                    )
                    if compiler_dependencies is not None
                    else _cpp_local_header_identities(
                        source_path,
                        include_directories,
                    )
                ),
            }
        )
    return sources


def _simulation_forced_header_identities(
    compile_flags: Any,
    build_directory: Any = None,
    dependency_map: dict[Path, set[Path]] | None = None,
) -> list[dict[str, Any]]:
    include_directories, forced_includes = _cpp_compile_inputs(
        compile_flags, build_directory
    )
    compiler_dependencies = (
        set().union(*dependency_map.values()) if dependency_map else None
    )
    identities: list[dict[str, Any]] = []
    for include_name in forced_includes:
        portable_name = (
            Path(include_name).name if Path(include_name).is_absolute() else include_name
        )
        header = _resolve_cpp_include(
            include_name,
            None,
            include_directories,
            quoted=True,
            base_directory=build_directory,
        )
        if header is None:
            identities.append({"include": portable_name, "kind": "missing"})
            continue
        identities.append(
            {
                "include": portable_name,
                "sha256": _file_sha256(header),
                "local_headers": _cpp_local_header_identities(
                    header,
                    include_directories,
                    active_dependencies=compiler_dependencies,
                ),
            }
        )
    return identities


def _cpp_compile_inputs(
    compile_flags: Any,
    base_directory: Any = None,
) -> tuple[dict[str, list[Path]], list[str]]:
    tokens = _shell_flag_tokens(compile_flags)
    directories = {"quote": [], "user": [], "system": []}
    seen = {"quote": set(), "user": set(), "system": set()}
    forced_includes: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-include" and index + 1 < len(tokens):
            forced_includes.append(tokens[index + 1])
            index += 2
            continue
        if token.startswith("-include") and token != "-include":
            forced_includes.append(token[len("-include") :].removeprefix("="))
            index += 1
            continue
        value = ""
        option = ""
        if token in {"-I", "-iquote", "-isystem"} and index + 1 < len(tokens):
            option = token
            value = tokens[index + 1]
            index += 2
        else:
            option = next(
                (
                    prefix
                    for prefix in ("-isystem", "-iquote", "-I")
                    if token.startswith(prefix) and token != prefix
                ),
                "",
            )
            if option:
                value = token[len(option) :].removeprefix("=")
            index += 1
        if not value:
            continue
        kind = {"-iquote": "quote", "-I": "user", "-isystem": "system"}[option]
        directory = _resolved_path(value, base_directory)
        if directory in seen[kind] or not directory.is_dir():
            continue
        seen[kind].add(directory)
        directories[kind].append(directory)
    return directories, forced_includes


def _cpp_local_header_identities(
    source: Any,
    include_directories: dict[str, list[Path]],
    *,
    active_dependencies: set[Path] | None = None,
) -> list[dict[str, str]]:
    source_path = Path(str(source or "")).expanduser().resolve()
    headers: list[dict[str, str]] = []
    visited: set[Path] = {source_path}

    def visit(path: Path) -> None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for match in _CPP_INCLUDE_RE.finditer(content):
            quoted_include = match.group("quoted")
            include_name = quoted_include or match.group("system")
            header = _resolve_cpp_include(
                include_name,
                path.parent,
                include_directories,
                quoted=bool(quoted_include),
            )
            if header is None:
                continue
            canonical = header.resolve()
            if canonical in visited or (
                active_dependencies is not None
                and canonical not in active_dependencies
            ):
                continue
            visited.add(canonical)
            headers.append(
                {
                    "include": include_name,
                    "sha256": _file_sha256(canonical),
                }
            )
            visit(canonical)

    if source_path.is_file():
        visit(source_path)
    headers.sort(key=lambda item: (item["include"], item["sha256"]))
    return headers


def _resolve_cpp_include(
    include_name: str,
    source_directory: Path | None,
    include_directories: dict[str, list[Path]],
    *,
    quoted: bool,
    base_directory: Any = None,
) -> Path | None:
    include_path = Path(include_name).expanduser()
    if include_path.is_absolute():
        return include_path.resolve() if include_path.is_file() else None
    candidates: list[Path] = []
    if quoted and source_directory is not None:
        candidates.append(source_directory / include_path)
    if quoted:
        candidates.extend(
            directory / include_path for directory in include_directories["quote"]
        )
    candidates.extend(
        directory / include_path for directory in include_directories["user"]
    )
    candidates.extend(
        directory / include_path for directory in include_directories["system"]
    )
    if quoted and source_directory is None:
        candidates.insert(0, _resolved_path(include_name, base_directory))
    return next(
        (candidate.resolve() for candidate in candidates if candidate.is_file()), None
    )


def _simulation_dependency_map(
    workspace: dict[str, Any], build_directory: Any
) -> dict[Path, set[Path]]:
    root = _resolved_path(build_directory)
    source_paths = {_resolved_path(source) for source in sim_cpp_sources(workspace)}
    dependencies_by_source: dict[Path, set[Path]] = {}
    if not root.is_dir():
        return dependencies_by_source
    for depfile in sorted(root.glob("*.d")):
        dependencies = _read_make_dependencies(depfile, root)
        for source in source_paths & dependencies:
            dependencies_by_source.setdefault(source, set()).update(dependencies)
    return dependencies_by_source


def _read_make_dependencies(path: Path, base_directory: Path) -> set[Path]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        tokens = shlex.split(content.replace("\\\n", " "), comments=False)
    except (OSError, ValueError):
        return set()
    separator = next(
        (index for index, token in enumerate(tokens) if token.endswith(":")), None
    )
    if separator is None:
        return set()
    return {
        _resolved_path(token, base_directory)
        for token in tokens[separator + 1 :]
        if token and token != "\\"
    }


def _compiler_dependency_identities(
    source: Path,
    dependencies: set[Path],
    include_directories: dict[str, list[Path]],
    build_directory: Any,
) -> list[dict[str, str]]:
    build_root = _resolved_path(build_directory)
    roots = sorted(
        {
            source.parent,
            *include_directories["quote"],
            *include_directories["user"],
            *include_directories["system"],
        },
        key=lambda path: len(path.parts),
        reverse=True,
    )
    identities: set[tuple[str, str]] = set()
    for dependency in dependencies:
        canonical = dependency.resolve()
        if canonical == source or canonical.is_relative_to(build_root):
            continue
        try:
            if not canonical.is_file():
                continue
        except OSError:
            continue
        portable_name = canonical.name
        for root in roots:
            try:
                portable_name = canonical.relative_to(root).as_posix()
                break
            except ValueError:
                continue
        identities.add((portable_name, _file_sha256(canonical)))
    return [
        {"include": name, "sha256": sha256}
        for name, sha256 in sorted(identities)
    ]


def _normalize_path_arguments(
    arguments: list[str],
    path_options: dict[str, str],
    base_directory: Any = None,
    *,
    identify_bare_files: bool = False,
    embedded_normalizer: Callable[[str], Any | None] | None = None,
) -> list[Any]:
    normalized: list[Any] = []
    index = 0
    attached_options = sorted(path_options, key=len, reverse=True)
    while index < len(arguments):
        argument = arguments[index]
        embedded = embedded_normalizer(argument) if embedded_normalizer else None
        if embedded is not None:
            normalized.append(embedded)
            index += 1
            continue
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
        elif identify_bare_files and not argument.startswith("-"):
            path = Path(argument).expanduser()
            if not path.is_absolute() and str(base_directory or "").strip():
                path = Path(str(base_directory)).expanduser() / path
            normalized.append(
                _path_argument_identity(argument, "file", base_directory)
                if path.is_file()
                else argument
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
    path = _resolved_path(value, base_directory)
    if kind == "file" and path.is_file():
        return {"kind": "file", "sha256": _file_sha256(path)}
    if kind == "search_directory" and path.is_dir():
        return {"kind": "directory"}
    if kind == "directory" and path.is_dir():
        return {"kind": "directory", "contents": _directory_identity(path)}
    return {"kind": "missing", "name": path.name}


def _resolved_path(value: Any, base_directory: Any = None) -> Path:
    path = Path(str(value or "")).expanduser()
    if path.is_absolute():
        return path.resolve()
    configured_base = str(base_directory or "").strip()
    if not configured_base:
        configured_base = os.getenv("BUILD_WORKSPACE_DIRECTORY", "").strip()
    root = Path(configured_base).expanduser() if configured_base else Path.cwd()
    return (root / path).resolve()


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


def _prepared_input_fingerprint(
    step: Any,
    comparison_inputs: dict[str, Any] | None = None,
) -> str:
    payload = comparison_inputs
    if payload is None:
        manifest = (
            Path(step.directory).parent
            / "prepare_fe"
            / "output"
            / "prepared_inputs.json"
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
