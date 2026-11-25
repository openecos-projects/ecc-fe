"""QoR of the prepare step: are the inputs usable."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.analysis import scoring
from fecompiler.analysis.qor.common import (
    _gate,
    _hotspot,
    _is_count,
    _metric,
    _number,
    _QorResult,
    _read_record,
    _record,
    _records,
    _source,
    _valid_count_fields,
)
from fecompiler.analysis.qor.fingerprint import (
    _prepared_input_fingerprint,
)
from fecompiler.analysis.scoring import scoring_policy

_PASSING_CONTRACT_STATUSES = frozenset(
    {"module_only", "not_required", "ok", "pass", "success"}
)


_FAILING_CONTRACT_STATUSES = frozenset({"error", "fail", "failed"})


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

    policy = scoring_policy()["prepare"]
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
    # A design without include directories has none to resolve: full credit.
    source_possible = policy["rtl_sources"] + policy["include_directories"]
    source_earned = policy["rtl_sources"] * rtl_resolved / rtl_total
    source_earned += policy["include_directories"] * (
        1 if incdir_total == 0 else incdir_resolved / incdir_total
    )

    top_required = bool(top["required"])
    top_definitions = int(top["definitions"])
    top_source_in_inputs = bool(top["source_in_inputs"])
    top_possible = policy["top_defined_once"] + policy["top_in_inputs"]
    top_earned = (policy["top_defined_once"] if top_definitions == 1 else 0) + (
        policy["top_in_inputs"] if top_source_in_inputs else 0
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
    interface_possible = policy["interface_ports"] + policy["interface_no_extra_ports"]
    interface_earned = 0.0
    if not interface_applicable:
        interface_summary = "Not evaluated: this design has no interface contract to meet."
    elif not interface_verified:
        interface_summary = (
            "Not evaluated: no expected CPU interface contract was available to verify."
        )
    else:
        interface_earned = policy["interface_ports"] * matched_ports / expected_ports
        interface_earned += policy["interface_no_extra_ports"] if extra_ports == 0 else 0
        interface_summary = (
            f"{matched_ports} of {expected_ports} required ports matched; "
            f"{extra_ports} unexpected."
        )

    fingerprint_recorded = bool(reproducibility["input_fingerprint"])
    merged_filelist = bool(reproducibility["merged_filelist"])
    prepared_manifest = bool(reproducibility["prepared_manifest"])
    reproducibility_parts = (
        ("input_fingerprint", fingerprint_recorded),
        ("merged_filelist", merged_filelist),
        ("prepared_manifest", prepared_manifest),
    )
    components = [
        scoring.component(
            "source_resolution",
            "Source resolution",
            source_earned,
            source_possible,
            f"{rtl_resolved} of {rtl_total} RTL sources and "
            f"{incdir_resolved} of {incdir_total} include directories resolved.",
        ),
        scoring.component(
            "top_resolution",
            "Top resolution",
            top_earned,
            top_possible,
            (
                "Not evaluated: top-module validation is not required for this workspace."
                if not top_required
                else f"{top_definitions} matching definition found; "
                f"source {'is' if top_source_in_inputs else 'is not'} in prepared inputs."
            ),
            evaluated=top_required,
        ),
        scoring.component(
            "interface_contract",
            "Interface contract",
            interface_earned,
            interface_possible,
            interface_summary,
            evaluated=interface_applicable and interface_verified,
        ),
        scoring.component(
            "reproducibility",
            "Reproducibility",
            sum(policy[name] for name, present in reproducibility_parts if present),
            sum(policy[name] for name, _ in reproducibility_parts),
            (
                f"Input snapshot {'tracked' if fingerprint_recorded else 'not tracked'}; "
                f"normalized input manifest and file list "
                f"{'persisted' if merged_filelist and prepared_manifest else 'incomplete'}."
            ),
        ),
    ]
    return scoring.score("Preparation readiness", components)


def _valid_readiness_counts(value: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return bool(value) and all(_is_count(value.get(field)) for field in fields)


def _valid_prepare_contract(value: dict[str, Any]) -> bool:
    status = value.get("status")
    return (
        isinstance(value.get("id"), str)
        and bool(value["id"].strip())
        and isinstance(status, str)
        and status.strip().lower()
        in _PASSING_CONTRACT_STATUSES | _FAILING_CONTRACT_STATUSES
    )
