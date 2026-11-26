"""QoR of the sim step: cases, Difftest and cycle counts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fecompiler.analysis import scoring
from fecompiler.analysis.qor.common import (
    _gate,
    _hotspot,
    _is_count,
    _is_number,
    _metric,
    _number,
    _optional_number,
    _QorResult,
    _read_record,
    _record,
    _records,
    _source,
)
from fecompiler.analysis.qor.fingerprint import (
    _file_sha256,
    _normalized_compile_flags,
    _normalized_run_arguments,
    _normalized_shell_compile_flags,
    _normalized_shell_link_flags,
    _prepared_input_fingerprint,
    _simulation_dependency_map,
    _simulation_forced_header_identities,
    _simulation_harness_sources,
)
from fecompiler.analysis.scoring import scoring_policy
from fecompiler.tools.verilator.coverage import REPORT_NAME as COVERAGE_REPORT
from fecompiler.tools.verilator.coverage import design_coverage
from fecompiler.tools.verilator.sim_settings import (
    effective_sim_cflags,
    effective_sim_ldflags,
)


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
    coverage = _design_coverage(step)
    if coverage is not None:
        coverage_source = _source(f"report/{COVERAGE_REPORT}", "/by_ownership")
        for kind, label in (("line", "Line Coverage"), ("toggle", "Toggle Coverage")):
            if coverage[kind]["total"]:
                result.metrics.append(
                    _metric(
                        f"{kind}_coverage",
                        label,
                        coverage[kind]["covered"] / coverage[kind]["total"],
                        "ratio",
                        "verification",
                        "higher_is_better",
                        "design",
                        coverage_source,
                    )
                )
    if report.get("kind") == "testbench":
        # One run of the user's testbench: gated, not scored.
        result.comparison = {
            "input_fingerprint": _prepared_input_fingerprint(step),
            "testbench": str(cases[0].get("name") or "") if cases else "",
        }
        return result
    result.score = _simulation_score(report, cases, coverage)
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


def _design_coverage(step: Any) -> dict[str, dict[str, Any]] | None:
    """Coverage of the design under test, when this run collected any."""
    report = _read_record(Path(step.report["dir"]) / COVERAGE_REPORT)
    return design_coverage(report) if report is not None else None


def _coverage_component(
    coverage: dict[str, dict[str, Any]] | None, policy: dict[str, Any]
) -> dict[str, Any]:
    """Credit for how much of the design the cases exercised.

    Each kind earns its share in proportion to its coverage, in full from the
    percentage the policy names. Without coverage data the component is not
    evaluated: collecting coverage is a choice, not a fault.
    """
    weight = policy["weight"]
    label = "Code coverage"
    if coverage is None:
        return scoring.component(
            "code_coverage",
            label,
            0,
            weight,
            "Not evaluated: this run collected no coverage (sim.coverage).",
            evaluated=False,
        )
    shares = {"line": policy["line_share"], "toggle": policy["toggle_share"]}
    measured = {kind: share for kind, share in shares.items() if coverage[kind]["total"]}
    earned = 0.0
    parts: list[str] = []
    for kind, share in measured.items():
        percent = 100.0 * coverage[kind]["covered"] / coverage[kind]["total"]
        full_at = policy[f"{kind}_full_at_percent"]
        # A kind the design has no point of passes its share to the other.
        earned += weight * share / sum(measured.values()) * min(1.0, percent / full_at)
        parts.append(
            f"{kind} {percent:.1f}% ({coverage[kind]['covered']}/{coverage[kind]['total']}, "
            f"full credit from {full_at:g}%)"
        )
    return scoring.component(
        "code_coverage",
        label,
        earned,
        weight,
        "Coverage of the design under test, merged over the cases: " + "; ".join(parts) + ".",
    )


def _simulation_score(
    report: dict[str, Any],
    cases: list[dict[str, Any]],
    coverage: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    suite = report.get("suite")
    run_id = report.get("run_id")
    case_names = [str(item.get("name", "")) for item in cases]
    if (
        report.get("schema_version") != 1
        or not isinstance(suite, str)
        or not suite.strip()
        or not isinstance(run_id, str)
        or not run_id.strip()
        or not cases
        or len(set(case_names)) != len(case_names)
        or not all(_valid_scored_sim_case(item, suite, run_id) for item in cases)
    ):
        return None

    policy = scoring_policy()["sim"]
    total = len(cases)
    passed = sum(item["ok"] is True for item in cases)

    difftest_cases = [
        _record(_record(item["metrics"]).get("difftest"))
        for item in cases
        if _record(_record(item["metrics"]).get("difftest")).get("enabled") is True
    ]
    difftest_passed = sum(
        str(item.get("status", "")).lower() == "passed" for item in difftest_cases
    )
    measured_cases = sum(
        item["ok"] is True and _is_count(_record(item["metrics"]).get("cycles"))
        for item in cases
    )
    # The score describes this run only. How it compares with an earlier run
    # is reported under ``regression`` and plays no part here.
    components = [
        scoring.component(
            "required_cases",
            "Required cases",
            policy["required_cases"] * passed / total,
            policy["required_cases"],
            f"{passed} of {total} required simulation cases passed.",
        ),
        scoring.component(
            "difftest_confidence",
            "Difftest confidence",
            policy["difftest_confidence"] * difftest_passed / len(difftest_cases)
            if difftest_cases
            else 0,
            policy["difftest_confidence"],
            (
                f"{difftest_passed} of {len(difftest_cases)} Difftest-enabled cases "
                "matched the architectural reference."
                if difftest_cases
                else "Not evaluated: Difftest was not enabled for this run."
            ),
            evaluated=bool(difftest_cases),
        ),
        scoring.component(
            "cycle_telemetry",
            "Cycle telemetry",
            policy["cycle_telemetry"] * measured_cases / total,
            policy["cycle_telemetry"],
            f"{measured_cases} of {total} cases passed and reported a cycle count.",
        ),
        _coverage_component(coverage, policy["code_coverage"]),
    ]
    return scoring.score("Simulation verification", components)


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


def _valid_scored_sim_case(value: dict[str, Any], suite: str, run_id: str) -> bool:
    returncode = value.get("returncode")
    image_sha256 = value.get("image_sha256")
    metrics = _record(value.get("metrics"))
    termination = metrics.get("termination")
    difftest = _record(metrics.get("difftest"))
    ok = value.get("ok") is True
    return (
        isinstance(returncode, int)
        and not isinstance(returncode, bool)
        and isinstance(image_sha256, str)
        and (not image_sha256 or re.fullmatch(r"[0-9a-f]{64}", image_sha256) is not None)
        and value.get("suite") == suite
        and value.get("run_id") == run_id
        and isinstance(termination, str)
        and termination
        in {
            "bad_trap",
            "difftest_incomplete",
            "difftest_mismatch",
            "good_trap",
            "process_error",
            "timeout",
            "unknown",
        }
        and isinstance(metrics.get("timeout_accepted"), bool)
        and isinstance(difftest.get("enabled"), bool)
        and isinstance(difftest.get("status"), str)
        and (not ok or returncode == 0)
        and (not ok or termination == "good_trap")
        and (
            (
                difftest["enabled"] is False
                and str(difftest["status"]).lower() == "disabled"
            )
            or (
                difftest["enabled"] is True
                and ok
                and str(difftest["status"]).lower() == "passed"
                and _is_count(difftest.get("compared"))
                and difftest["compared"] > 0
            )
            or (
                difftest["enabled"] is True
                and not ok
                and str(difftest["status"]).lower() in {"incomplete", "mismatch"}
            )
        )
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
