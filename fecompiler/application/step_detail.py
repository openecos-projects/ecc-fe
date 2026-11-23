"""Build the per-step detail, report, and failure payloads shown to users."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fecompiler.allflow.profile import GENERIC_RTL, normalize_frontend_design_kind
from fecompiler.application.sim_config import (
    DEFAULT_FRONTEND_COREMARK_CASES,
    _default_cpu_test_cases,
    _supports_difftest,
)
from fecompiler.application.values import (
    _existing_path_item,
    _first_existing,
    _json_read,
    _normalize_bool,
    _normalize_str_list,
    _optional_path,
    _read_text_tail,
    _resolve_optional_path,
    _step_section,
)
from fecompiler.data.step import StateEnum
from fecompiler.engine.flow import EngineFlow
from fecompiler.utility.json import json_write


CLI_LOG_TAIL_BYTES = 24 * 1024
def _build_step_info(
    workspace: dict[str, Any],
    engine: EngineFlow,
    step: Any,
    info_id: str,
) -> dict[str, Any]:
    if info_id == "subflow":
        return {"path": _step_section(step, "subflow").get("path", "")}
    if info_id == "frontend_detail":
        flow_step = engine.get_step(step.name, step.tool)
        return _build_frontend_step_detail(workspace, step, flow_step)
    if info_id == "config":
        config_path = _step_section(step, "config").get("flow", "")
        return {"config": config_path} if config_path and os.path.exists(config_path) else {}
    if info_id == "checklist":
        path = _step_section(step, "checklist").get("path", "")
        return {"path": path} if path and os.path.exists(path) else {}
    if info_id in {"analysis", "metrics"}:
        analysis = _step_section(step, "analysis")
        info: dict[str, Any] = {}
        for key in (
            "metrics",
            "qor_metrics",
            "qor_summary",
            "qor_hotspots",
            "statis_csv",
        ):
            path = analysis.get(key, "")
            if path and os.path.exists(path):
                info[key] = path
        return info
    return {}


def _step_report_payload(workspace: dict[str, Any], workspace_step: Any, state: StateEnum) -> dict[str, Any]:
    step_log = str(workspace_step.log.get("file", ""))
    report_path = str(workspace_step.report.get("step", ""))
    report_dir = _optional_path(workspace_step.report.get("dir", ""))
    report_log = str(report_dir / "log.txt") if report_dir else ""
    return {
        "step": workspace_step.name,
        "tool": workspace_step.tool,
        "state": state.value,
        "log_file": step_log,
        "report_file": report_path,
        "subflow_path": workspace_step.subflow.get("path", ""),
        "home_page": workspace.get("home_path", ""),
        "artifacts": _build_frontend_step_artifacts(workspace, workspace_step),
        "logs": _build_frontend_step_logs(step_log, report_log),
    }


def _failure_payload(
    workspace: dict[str, Any],
    workspace_step: Any,
    step_name: str,
    state: StateEnum,
) -> dict[str, Any]:
    report_dir = _optional_path(_step_section(workspace_step, "report").get("dir", ""))
    candidate_logs = [
        _step_section(workspace_step, "log").get("file", ""),
        report_dir / "log.txt" if report_dir else "",
        report_dir / "build_programs.log.txt" if report_dir else "",
        report_dir / "cases.json" if report_dir else "",
    ]
    logs = [
        item for item in (
            _existing_path_item(path, label)
            for label, path in (
                ("Step log", candidate_logs[0]),
                ("Tool log", candidate_logs[1]),
                ("Build programs log", candidate_logs[2]),
                ("Simulation cases", candidate_logs[3]),
            )
        )
        if item
    ]
    return {
        "step": step_name,
        "state": state.value,
        "logs": logs,
        "artifacts": _build_frontend_step_artifacts(workspace, workspace_step),
        "log_tail": _failure_log_tail(logs),
    }


def _failure_messages(prefix: str, failure: Any) -> list[str]:
    messages = [prefix]
    if not isinstance(failure, dict):
        return messages
    logs = failure.get("logs")
    if isinstance(logs, list) and logs:
        preferred = _failure_log_item([item for item in logs if isinstance(item, dict)])
        if preferred.get("path"):
            messages.append(f"log: {preferred['path']}")
    tail = str(failure.get("log_tail", "")).strip()
    if tail:
        tail_lines = tail.splitlines()[-8:]
        messages.extend(tail_lines)
    return messages


def _failure_log_tail(logs: list[dict[str, str]]) -> str:
    preferred = _failure_log_item(logs)
    if preferred.get("path"):
        tail = _read_text_tail(preferred.get("path", ""), CLI_LOG_TAIL_BYTES)
        if tail.strip():
            return tail
    return ""


def _failure_log_item(logs: list[dict[str, str]]) -> dict[str, str]:
    preferred_labels = ("Build programs log", "Tool log", "Step log")
    for label in preferred_labels:
        for item in logs:
            if item.get("label") != label:
                continue
            if _read_text_tail(item.get("path", ""), CLI_LOG_TAIL_BYTES).strip():
                return item
    for item in logs:
        if str(item.get("path", "")).endswith(".json"):
            continue
        if _read_text_tail(item.get("path", ""), CLI_LOG_TAIL_BYTES).strip():
            return item
    return {}


def _build_frontend_step_detail(
    workspace: dict[str, Any],
    step: Any,
    flow_step: dict[str, Any] | None,
) -> dict[str, Any]:
    state = str((flow_step or {}).get("state", "Unstart"))
    runtime = str((flow_step or {}).get("runtime", ""))
    peak_memory = (flow_step or {}).get("peak memory (mb)", 0)
    step_name = str(step.name)
    step_log_path = str(_step_section(step, "log").get("file", ""))
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    report_log_path = str(report_dir / "log.txt") if report_dir else ""

    detail: dict[str, Any] = {
        "step": step_name,
        "tool": str(step.tool),
        "state": state,
        "runtime": runtime,
        "peak_memory_mb": peak_memory,
        "summary": _build_frontend_step_summary(step, state, runtime),
        "qor": _build_frontend_step_qor(step),
        "logs": _build_frontend_step_logs(step_log_path, report_log_path),
        "reports": _build_frontend_step_reports(step),
        "artifacts": _build_frontend_step_artifacts(workspace, step),
        "log": step_log_path,
        "report": str(_step_section(step, "report").get("step", "")),
        "subflow": str(_step_section(step, "subflow").get("path", "")),
        "home_page": str(workspace.get("home_path", "")),
        "info": dict((flow_step or {}).get("info", {}) or {}),
        "provenance": dict((flow_step or {}).get("provenance", {}) or {}),
    }

    if step_name == "sim":
        sim = _build_frontend_sim_payload(step)
        cases = sim["cases"]
        detail["cases"] = cases
        detail["sim"] = sim
        passed = len([case for case in cases if case.get("ok") is True])
        failed = len([case for case in cases if case.get("ok") is False])
        detail["summary"].update(
            {
                "total_cases": len(cases),
                "passed_cases": passed,
                "failed_cases": failed,
                "run_id": sim.get("run_id", ""),
                "regression": sim.get("regression", {}),
                "history_runs": len(sim.get("history", [])),
                "test_suite": _sim_suite_label(workspace, cases),
                "suite_id": _sim_suite_id(workspace, cases),
                "cpu_test_mode": _sim_cpu_test_mode(workspace, cases),
                "available_cpu_tests": _available_cpu_test_cases(workspace),
                "default_cpu_tests": _default_cpu_test_cases(workspace),
            }
        )
    elif step_name == "review":
        review = _build_frontend_review_payload(step)
        detail["review"] = review
        if review:
            detail["summary"].update({
                "rtl_review": review.get("summary", {}),
                "review_report": review.get("path", ""),
            })
    elif step_name == "elab":
        elab = _build_frontend_elab_payload(step)
        detail["elab"] = elab
        if elab:
            detail["summary"].update({
                "elab": elab.get("summary", {}),
                "elab_report": elab.get("path", ""),
            })
    elif step_name == "lint":
        lint = _build_frontend_lint_payload(step)
        detail["lint"] = lint
        if lint:
            detail["summary"].update({
                "lint": lint.get("summary", {}),
                "lint_report": lint.get("path", ""),
            })
    elif step_name == "prepare":
        prepare = _build_frontend_prepare_payload(workspace, step)
        detail["prepare"] = prepare
        if prepare:
            detail["summary"].update({
                "readiness": prepare.get("readiness", {}),
                "inputs": prepare.get("inputs", {}),
                "contracts": prepare.get("contracts", []),
                "runtime_plan": prepare.get("runtime", {}),
            })

    return detail


def _build_frontend_step_qor(step: Any) -> dict[str, Any]:
    analysis = _step_section(step, "analysis")

    def read_record(key: str) -> dict[str, Any] | None:
        payload = _json_read(analysis.get(key, ""))
        return payload if isinstance(payload, dict) else None

    return {
        "metrics": read_record("qor_metrics"),
        "summary": read_record("qor_summary"),
        "hotspots": read_record("qor_hotspots"),
    }


def _write_frontend_step_detail(
    workspace: dict[str, Any],
    engine: EngineFlow,
    workspace_step: Any,
    state: StateEnum,
) -> str:
    report_dir = _optional_path(_step_section(workspace_step, "report").get("dir", ""))
    if report_dir is None:
        return ""

    flow_step = engine.get_step(workspace_step.name, workspace_step.tool)
    detail = _build_frontend_step_detail(workspace, workspace_step, flow_step)
    detail["state"] = state.value
    summary = detail.get("summary")
    if isinstance(summary, dict):
        summary["status"] = state.value

    detail_path = report_dir / "frontend_detail.json"
    return str(detail_path) if json_write(detail_path, detail) else ""


def _remove_frontend_step_detail(workspace_step: Any) -> None:
    report_dir = _optional_path(_step_section(workspace_step, "report").get("dir", ""))
    if report_dir is None:
        return
    (report_dir / "frontend_detail.json").unlink(missing_ok=True)


def _clear_frontend_step_details(engine: EngineFlow) -> None:
    for workspace_step in engine.workspace_steps:
        _remove_frontend_step_detail(workspace_step)


def _build_frontend_step_summary(step: Any, state: str, runtime: str) -> dict[str, Any]:
    report = _json_read(_step_section(step, "report").get("step", ""))
    summary: dict[str, Any] = {
        "status": state,
        "runtime": runtime,
    }
    if isinstance(report, dict):
        summary["report"] = report
    return summary


def _build_frontend_step_logs(step_log_path: str, report_log_path: str) -> list[dict[str, str]]:
    logs: list[dict[str, str]] = []
    seen: set[str] = set()
    report_dir = _optional_path(Path(report_log_path).parent if report_log_path else "")
    for item in (
        _existing_path_item(report_dir / "rtl_review_summary.md" if report_dir else "", "Review summary"),
        _existing_path_item(step_log_path, "Step log"),
        _existing_path_item(report_log_path, "Tool log"),
        _existing_path_item(_first_existing(report_dir, ("yosys_precheck.log", "structural_probe.log")) if report_dir else "", "Yosys precheck log"),
        _existing_path_item(report_dir / "build_programs.log.txt" if report_dir else "", "Build programs log"),
    ):
        path = str((item or {}).get("path", ""))
        if item and path not in seen:
            seen.add(path)
            logs.append(item)
    return logs


def _build_frontend_step_reports(step: Any) -> list[dict[str, str]]:
    reports: list[dict[str, str]] = []
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    for item in (
        _existing_path_item(report_dir / "rtl_review_summary.md" if report_dir else "", "Review summary"),
        _existing_path_item(_step_section(step, "report").get("step", ""), "Step report"),
        _existing_path_item(report_dir / "elab_summary.json" if report_dir else "", "Elab summary"),
        _existing_path_item(report_dir / "lint_summary.json" if report_dir else "", "Lint summary"),
        _existing_path_item(report_dir / "rtl_review.json" if report_dir else "", "RTL review"),
        _existing_path_item(_first_existing(report_dir, ("yosys_precheck.json", "structural_probe.json")) if report_dir else "", "Yosys precheck"),
        _existing_path_item(report_dir / "cases.json" if report_dir else "", "Simulation cases"),
        _existing_path_item(report_dir / "history.json" if report_dir else "", "Simulation history"),
        _existing_path_item(report_dir / "build_programs.log.txt" if report_dir else "", "Build programs log"),
    ):
        if item:
            reports.append(item)
    return reports


def _build_frontend_step_artifacts(workspace: dict[str, Any], step: Any) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    output_dir = _optional_path(_step_section(step, "output").get("dir", ""))
    design = str(workspace.get("design", ""))

    def append_item(item: dict[str, str] | None) -> None:
        path = str((item or {}).get("path", "")).strip()
        if not item or not path or path in seen_paths:
            return
        seen_paths.add(path)
        artifacts.append(item)

    for label, path in (
        ("Simulation binary", output_dir / f"{design}_sim" if output_dir and design else ""),
    ):
        append_item(_existing_path_item(path, label))

    if str(step.name).strip().lower() == "prepare":
        for item in _build_prepare_cpu_source_artifacts(workspace):
            append_item(item)

    if str(step.name).strip().lower() == "review":
        for item in _build_review_source_artifacts(step):
            append_item(item)

    if str(step.name).strip().lower() == "sim":
        for case in _build_frontend_sim_cases(step):
            case_name = str(case.get("name", "")).strip()
            for key, suffix in (("wave", "wave"), ("image", "image"), ("log", "log"), ("run_log", "run log")):
                path = str(case.get(key, "")).strip()
                label = f"{case_name} {suffix}".strip()
                append_item(_existing_path_item(path, label))
            program = case.get("program", {})
            if isinstance(program, dict):
                for key, suffix in (
                    ("source", "source"),
                    ("elf", "ELF"),
                    ("binary", "binary"),
                    ("disassembly", "disassembly"),
                ):
                    path = str(program.get(key, "")).strip()
                    label = f"{case_name} {suffix}".strip()
                    append_item(_existing_path_item(path, label))

    return artifacts


def _build_frontend_review_payload(step: Any) -> dict[str, Any]:
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    if not report_dir:
        return {}
    review_path = report_dir / "rtl_review.json"
    data = _json_read(review_path)
    if not isinstance(data, dict):
        return {}
    return {
        "path": str(review_path),
        "scope": data.get("scope", ""),
        "summary": data.get("summary", {}),
        "metrics": data.get("metrics", {}),
        "issues": data.get("issues", []),
        "source_files": data.get("source_files", []),
        "structural_probe": data.get("structural_probe", {}),
        "yosys_precheck": data.get("yosys_precheck", data.get("structural_probe", {})),
        "profiles": data.get("profiles", []),
        "next_analyzers": data.get("next_analyzers", []),
        "delta": data.get("delta", {}),
        "resolved_issues": data.get("resolved_issues", []),
        "waivers": data.get("waivers", {}),
    }


def _build_frontend_elab_payload(step: Any) -> dict[str, Any]:
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    if not report_dir:
        return {}
    summary_path = report_dir / "elab_summary.json"
    data = _json_read(summary_path)
    if not isinstance(data, dict):
        return {}
    data["path"] = str(summary_path)
    data["readiness"] = _build_elab_readiness(data)
    data["hierarchy"] = _build_elab_hierarchy(data)
    data["next_action"] = _build_elab_next_action(data["readiness"], data["hierarchy"])
    return data


def _build_elab_readiness(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary", {})
    summary = summary if isinstance(summary, dict) else {}
    diagnostics = data.get("diagnostics", [])
    diagnostics = diagnostics if isinstance(diagnostics, list) else []
    unresolved = data.get("unresolved_modules", [])
    unresolved = unresolved if isinstance(unresolved, list) else []
    top_module = str(summary.get("top_module") or data.get("top_module") or "").strip()
    top_found = bool(summary.get("top_found", False))
    status = str(summary.get("status") or data.get("status") or "not run").strip().lower()
    errors = int(summary.get("errors") or 0)
    warnings = int(summary.get("warnings") or 0)

    if status == "fail" or errors:
        state = "Failed"
        message = "Slang reported parse or elaboration errors. Fix diagnostics before running later steps."
    elif not top_found:
        state = "Incomplete"
        message = f"Top module {top_module or '<unset>'} was not found in the current RTL universe."
    elif unresolved:
        state = "Warning"
        message = "Some instantiated modules are not defined in the current file universe."
    else:
        state = "Ready"
        message = "Top exists and the module universe is structurally complete."

    return {
        "status": state,
        "message": message,
        "top_module": top_module or "--",
        "top_found": top_found,
        "errors": errors,
        "warnings": warnings,
        "diagnostics": len(diagnostics),
        "unresolved_modules": len(unresolved),
        "rtl_files": int(summary.get("rtl_files") or data.get("inputs", {}).get("rtl_file_count") or 0),
        "modules": int(summary.get("modules") or 0),
        "referenced_modules": int(summary.get("referenced_modules") or 0),
    }


def _build_elab_hierarchy(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary", {})
    summary = summary if isinstance(summary, dict) else {}
    top_module = str(summary.get("top_module") or data.get("top_module") or "").strip()
    modules = data.get("modules", [])
    modules = modules if isinstance(modules, list) else []
    module_records = [item for item in modules if isinstance(item, dict)]
    module_by_name = {str(item.get("module", "")): item for item in module_records}
    top_record = module_by_name.get(top_module, {})
    top_children = [
        str(item)
        for item in (top_record.get("instantiates", []) if isinstance(top_record, dict) else [])
        if str(item).strip()
    ]
    hotspots = sorted(
        module_records,
        key=lambda item: int(item.get("instances") or 0),
        reverse=True,
    )[:8]
    unresolved = data.get("unresolved_modules", [])
    unresolved = [str(item) for item in unresolved] if isinstance(unresolved, list) else []
    referenced = data.get("referenced_modules", [])
    referenced = [str(item) for item in referenced] if isinstance(referenced, list) else []
    heuristic_unresolved = data.get("heuristic_unresolved_candidates", [])
    heuristic_unresolved = (
        [str(item) for item in heuristic_unresolved]
        if isinstance(heuristic_unresolved, list)
        else []
    )
    inventory = data.get("inventory", {})
    inventory = inventory if isinstance(inventory, dict) else {}
    return {
        "top_module": top_module or "--",
        "top_children": top_children,
        "module_count": len(module_records),
        "referenced_count": len(referenced),
        "unresolved": unresolved,
        "heuristic_unresolved_candidates": heuristic_unresolved,
        "inventory_source": str(inventory.get("source", "source_scan")),
        "inventory_authoritative": bool(inventory.get("authoritative", False)),
        "largest_modules": [
            {
                "module": str(item.get("module", "")),
                "path": str(item.get("path", "")),
                "line": int(item.get("line") or 1),
                "instances": int(item.get("instances") or 0),
                "ports": int(item.get("ports") or 0),
                "parameters": int(item.get("parameters") or 0),
            }
            for item in hotspots
        ],
    }


def _build_elab_next_action(readiness: dict[str, Any], hierarchy: dict[str, Any]) -> dict[str, str]:
    status = str(readiness.get("status", "")).lower()
    if status == "failed":
        return {
            "title": "Fix Slang Diagnostics",
            "detail": "Open Diagnostics and jump to the reported source locations.",
            "target": "diagnostics",
        }
    if status == "incomplete":
        return {
            "title": "Fix Top Module",
            "detail": "Check the configured top module and the prepared filelist.",
            "target": "top",
        }
    if hierarchy.get("unresolved"):
        return {
            "title": "Resolve Missing Modules",
            "detail": "Add missing RTL files or fix module names before continuing.",
            "target": "unresolved",
        }
    return {
        "title": "Continue",
        "detail": "The design universe is complete. Continue to RTL Review or Lint.",
        "target": "next",
    }


def _build_frontend_lint_payload(step: Any) -> dict[str, Any]:
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    if not report_dir:
        return {}
    summary_path = report_dir / "lint_summary.json"
    data = _json_read(summary_path)
    if not isinstance(data, dict):
        return {}
    data["path"] = str(summary_path)
    return data


def _build_frontend_prepare_payload(workspace: dict[str, Any], step: Any) -> dict[str, Any]:
    report = _json_read(_step_section(step, "report").get("step", ""))
    manifest = _json_read(workspace.get("prepared_manifest", ""))
    report = report if isinstance(report, dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    inputs = report.get("inputs", {})
    inputs = inputs if isinstance(inputs, dict) else {}
    rtl_files = _normalize_str_list(manifest.get("rtl_files", []))
    incdirs = _normalize_str_list(manifest.get("incdirs", []))
    defines = _normalize_str_list(manifest.get("defines", []))
    is_generic = (
        normalize_frontend_design_kind(workspace.get("frontend_design_kind"))
        == GENERIC_RTL
    )
    cpu_sources = [] if is_generic else _build_prepare_cpu_source_artifacts(workspace)

    contracts = _build_prepare_contracts(workspace, report)
    failed_contracts = [
        item for item in contracts
        if str(item.get("status", "")).lower() in {"missing", "failed", "error"}
    ]
    warning_contracts = [
        item for item in contracts
        if str(item.get("status", "")).lower() in {"warning", "stub", "disabled"}
    ]
    if failed_contracts:
        readiness_status = "Failed"
        readiness_message = "Prepare found missing or incompatible runtime inputs."
    elif warning_contracts:
        readiness_status = "Warning"
        readiness_message = "Prepare completed with degraded or optional runtime capabilities."
    elif rtl_files:
        readiness_status = "Ready"
        readiness_message = (
            "Inputs are normalized and ready for RTL Review, Elaboration, and Lint."
            if is_generic
            else "Inputs are normalized and ready for ELAB, Lint, Review, and Sim."
        )
    else:
        readiness_status = "Pending"
        readiness_message = "Run Prepare to collect and normalize RTL inputs."

    configuration = (
        [
            {"label": "Design", "value": _display_workspace_value(workspace, "design")},
            {"label": "Top Module", "value": _display_workspace_value(workspace, "top_module")},
            {"label": "Clock", "value": _display_workspace_value(workspace, "clock")},
            {
                "label": "Target Frequency",
                "value": _display_workspace_value(workspace, "frequency_max"),
            },
        ]
        if is_generic
        else [
            {
                "label": "CPU",
                "value": _display_workspace_value(
                    workspace, "frontend_core_id", "cpu_wrapper_id", "core_id"
                ),
            },
            {
                "label": "SoC Harness",
                "value": _display_workspace_value(
                    workspace, "soc_harness_id", "soc_wrapper_id", "soc_variant"
                ),
            },
            {"label": "Toolchain", "value": _display_workspace_value(workspace, "toolchain_id")},
            {"label": "Test Suite", "value": _display_workspace_value(workspace, "test_suite_id")},
            {"label": "Top Module", "value": _display_workspace_value(workspace, "top_module")},
            {"label": "Reset/Link Base", "value": _prepare_reset_link_base(workspace)},
        ]
    )
    runtime = (
        [
            {
                "label": "Workdir",
                "value": str(workspace.get("directory", "")),
                "mono": True,
            },
            {"label": "Top Module", "value": str(workspace.get("top_module", ""))},
            {"label": "Step Logs", "value": "*/report/log.txt", "mono": True},
        ]
        if is_generic
        else [
            {
                "label": "Workdir",
                "value": str(workspace.get("directory", "")),
                "mono": True,
            },
            {
                "label": "Sim Top",
                "value": str(workspace.get("top_module", "") or "ecos_sim_top"),
            },
            {"label": "CPU Tests", "value": _prepare_cpu_tests_label(workspace)},
            {
                "label": "Wave Output",
                "value": "sim_verilator/report/cases/*.vcd",
                "mono": True,
            },
            {"label": "Step Logs", "value": "*/report/log.txt", "mono": True},
        ]
    )
    payload = {
        "readiness": {
            "status": readiness_status,
            "message": readiness_message,
            "rtl_files": len(rtl_files),
            "incdirs": len(incdirs),
            "defines": len(defines),
        },
        "configuration": configuration,
        "inputs": {
            **(
                {"design_rtl_files": len(rtl_files)}
                if is_generic
                else {"cpu_rtl_files": len(cpu_sources)}
            ),
            "total_rtl_files": len(rtl_files),
            "incdirs": len(incdirs),
            "defines": len(defines),
            "sources": _prepare_input_sources(inputs),
            "manifest": str(workspace.get("prepared_manifest", "")),
            "merged_filelist": str(workspace.get("prepared_filelist", "")),
            "rtl_sources": manifest.get("rtl_sources", []),
        },
        "ownership": manifest.get("ownership", report.get("ownership", {})),
        "contracts": contracts,
        "runtime": runtime,
        "reports": {
            "path": str(_step_section(step, "report").get("step", "")),
            "manifest": str(workspace.get("prepared_manifest", "")),
        },
    }
    contract_key = "design_top_contract" if is_generic else "cpu_top_contract"
    payload[contract_key] = manifest.get(contract_key, {})
    return payload


def _build_prepare_contracts(workspace: dict[str, Any], report: dict[str, Any]) -> list[dict[str, str]]:
    inputs = report.get("inputs", {})
    inputs = inputs if isinstance(inputs, dict) else {}
    cpu_input = inputs.get("cpu_filelist", {})
    soc_input = inputs.get("soc_filelist", {})
    input_filelist = inputs.get("input_filelist", {})
    origin_verilog = inputs.get("origin_verilog", {})
    cpu_input = cpu_input if isinstance(cpu_input, dict) else {}
    soc_input = soc_input if isinstance(soc_input, dict) else {}
    input_filelist = input_filelist if isinstance(input_filelist, dict) else {}
    origin_verilog = origin_verilog if isinstance(origin_verilog, dict) else {}

    contracts: list[dict[str, str]] = []
    if input_filelist or origin_verilog:
        contracts.append({
            "label": "Custom RTL Input",
            "status": "OK" if (input_filelist.get("rtl_files") or origin_verilog.get("rtl_files")) else "Missing",
            "detail": str(input_filelist.get("path") or origin_verilog.get("path") or "No custom input found"),
        })
    else:
        contracts.extend([
            {
                "label": "CPU Filelist",
                "status": "OK" if cpu_input.get("rtl_files") else "Missing",
                "detail": _prepare_contract_detail(cpu_input, workspace.get("cpu_filelist", "")),
            },
            {
                "label": "SoC Harness",
                "status": "OK" if soc_input.get("rtl_files") else "Missing",
                "detail": _prepare_contract_detail(soc_input, workspace.get("soc_filelist", "")),
            },
        ])

    if normalize_frontend_design_kind(workspace.get("frontend_design_kind")) == GENERIC_RTL:
        return contracts

    contracts.extend([
        {
            "label": "ecos_sim_top",
            "status": "OK" if str(workspace.get("top_module", "") or "") == "ecos_sim_top" else "Warning",
            "detail": str(workspace.get("top_module", "") or "Top module not configured"),
        },
        {
            "label": "Difftest",
            "status": "OK" if _supports_difftest(workspace) else "Stub",
            "detail": "Enabled" if _supports_difftest(workspace) else "Using stub or disabled for this CPU/SoC.",
        },
        {
            "label": "Test Suite",
            "status": "OK" if str(workspace.get("test_suite_id", "")).strip() else "Warning",
            "detail": str(workspace.get("test_suite_id", "") or "Default smoke suite"),
        },
    ])
    return contracts


def _prepare_contract_detail(data: dict[str, Any], fallback_path: Any, *, empty: str = "No RTL files found") -> str:
    path = str(data.get("path") or fallback_path or "").strip()
    files = data.get("rtl_files")
    try:
        count = int(files)
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        return f"{count} RTL file(s) from {path}" if path else f"{count} RTL file(s)"
    if path:
        return f"{empty}: {path}"
    skipped = str(data.get("skipped", "")).strip()
    return skipped or empty


def _prepare_input_sources(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key, label in (
        ("cpu_filelist", "CPU RTL"),
        ("soc_filelist", "SoC Harness"),
        ("input_filelist", "Custom Filelist"),
        ("origin_verilog", "Single RTL"),
    ):
        value = inputs.get(key, {})
        if not isinstance(value, dict):
            continue
        path = str(value.get("path", "")).strip()
        skipped = str(value.get("skipped", "")).strip()
        if not path and not skipped:
            continue
        result.append({
            "label": label,
            "path": path,
            "rtl_files": value.get("rtl_files", 0),
            "filtered_rtl_files": value.get("filtered_rtl_files", 0),
            "skipped": skipped,
        })
    return result


def _display_workspace_value(workspace: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(workspace.get(key, "") or "").strip()
        if value:
            return value
    return "--"


def _prepare_reset_link_base(workspace: dict[str, Any]) -> str:
    for key in ("sim_program_link_base", "reset_pc", "diff_reset_vector"):
        value = str(workspace.get(key, "") or "").strip()
        if value:
            return value
    args = _normalize_str_list(workspace.get("sim_run_args", []))
    for index, item in enumerate(args):
        if item in {"--diff-reset-vector", "--reset-vector"} and index + 1 < len(args):
            return args[index + 1]
    return "--"


def _prepare_cpu_tests_label(workspace: dict[str, Any]) -> str:
    if _normalize_bool(workspace.get("sim_build_all_programs", False)):
        return "All CPU tests"
    names = _normalize_str_list(workspace.get("sim_program_names", []))
    if names:
        return ", ".join(names)
    return ", ".join(_default_cpu_test_cases(workspace)) or "Default smoke"


def _build_review_source_artifacts(step: Any) -> list[dict[str, str]]:
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    if not report_dir:
        return []
    data = _json_read(report_dir / "rtl_review.json")
    if not isinstance(data, dict):
        return []
    raw_sources = data.get("source_files", [])
    if not isinstance(raw_sources, list):
        return []

    artifacts: list[dict[str, str]] = []
    for source in raw_sources:
        if not isinstance(source, dict):
            continue
        path = str(source.get("path", "")).strip()
        label = str(source.get("label", "")).strip() or Path(path).name
        item = _existing_path_item(path, label)
        if item:
            artifacts.append(item)
    return artifacts


def _build_prepare_cpu_source_artifacts(workspace: dict[str, Any]) -> list[dict[str, str]]:
    cpu_filelist = _resolve_optional_path(workspace.get("cpu_filelist", ""))
    if not cpu_filelist:
        return []

    cpu_sources = _collect_cpu_filelist_sources(cpu_filelist)
    if not cpu_sources:
        return []

    cpu_root = Path(cpu_filelist).expanduser().resolve().parent
    artifacts: list[dict[str, str]] = []
    for source in cpu_sources:
        rel = _cpu_source_relative_path(source, cpu_root)
        artifacts.append(
            {
                "label": f"CPU RTL · {rel}",
                "path": str(source),
            }
        )
    return artifacts


def _collect_cpu_filelist_sources(cpu_filelist: str) -> list[Path]:
    filelist_path = Path(cpu_filelist).expanduser().resolve()
    if not filelist_path.is_file():
        return []

    try:
        from fecompiler.tools.prepare.runner import PrepareStep

        parsed = PrepareStep._parse_sv_filelist(str(filelist_path))
        raw_files = parsed.get("rtl_files", []) if isinstance(parsed, dict) else []
    except Exception:
        return []

    collected: list[Path] = []
    seen: set[str] = set()
    for raw in raw_files:
        try:
            source_path = Path(str(raw)).expanduser().resolve()
        except Exception:
            continue
        if source_path.suffix.lower() not in {".v", ".sv", ".vh", ".svh"}:
            continue
        if not source_path.is_file():
            continue
        key = str(source_path)
        if key in seen:
            continue
        seen.add(key)
        collected.append(source_path)

    return collected


def _cpu_source_relative_path(source: Path, cpu_root: Path) -> str:
    try:
        return source.relative_to(cpu_root).as_posix()
    except ValueError:
        return source.name


def _build_frontend_sim_cases(step: Any) -> list[dict[str, Any]]:
    return _build_frontend_sim_payload(step)["cases"]


def _build_frontend_sim_payload(step: Any) -> dict[str, Any]:
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    if not report_dir:
        return {"cases": [], "regression": {}, "history": [], "run_id": "", "suite": ""}
    cases_json = report_dir / "cases.json"
    data = _json_read(cases_json)
    raw_cases = data.get("cases", []) if isinstance(data, dict) else []
    if not isinstance(raw_cases, list):
        raw_cases = []

    cases: list[dict[str, Any]] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            continue
        raw_program = raw_case.get("program", {})
        program = raw_program if isinstance(raw_program, dict) else {}
        case = {
            "name": str(raw_case.get("name", "")),
            "suite": str(raw_case.get("suite", "")),
            "ok": bool(raw_case.get("ok", False)),
            "returncode": raw_case.get("returncode"),
            "image": str(raw_case.get("image", "")),
            "log": str(raw_case.get("log") or raw_case.get("latest_log") or ""),
            "report_log": str(raw_case.get("report_log", "")),
            "run_log": str(raw_case.get("run_log", "")),
            "wave": str(raw_case.get("wave", "")),
            "run_id": str(raw_case.get("run_id", "")),
            "program": {
                "source": str(program.get("source", "")),
                "elf": str(program.get("elf", "")),
                "binary": str(program.get("binary", "")),
                "image": str(program.get("image") or raw_case.get("image") or ""),
                "disassembly": str(program.get("disassembly", "")),
            },
            "validation": raw_case.get("validation", {}) if isinstance(raw_case.get("validation"), dict) else {},
            "metrics": raw_case.get("metrics", {}) if isinstance(raw_case.get("metrics"), dict) else {},
        }
        failure = raw_case.get("failure")
        if isinstance(failure, dict) and failure:
            case["failure"] = failure
        cases.append(case)
    history_data = _json_read(report_dir / "history.json")
    history = history_data.get("runs", []) if isinstance(history_data, dict) else []
    return {
        "cases": cases,
        "regression": data.get("regression", {}) if isinstance(data, dict) and isinstance(data.get("regression"), dict) else {},
        "history": history if isinstance(history, list) else [],
        "run_id": str(data.get("run_id", "")) if isinstance(data, dict) else "",
        "suite": str(data.get("suite", "")) if isinstance(data, dict) else "",
        "history_path": str(report_dir / "history.json") if (report_dir / "history.json").is_file() else "",
    }


def _sim_suite_label(workspace: dict[str, Any], cases: list[dict[str, Any]] | None = None) -> str:
    case_names = [str(case.get("name", "")) for case in (cases or [])]
    if case_names:
        if case_names == ["coremark.soc"]:
            return "CoreMark"
        return "CPU Tests"
    names = _normalize_str_list(workspace.get("sim_program_names", []))
    if names == ["coremark"]:
        return "CoreMark"
    if workspace.get("sim_build_all_programs") or names:
        return "CPU Tests"
    return "Default"


def _sim_suite_id(workspace: dict[str, Any], cases: list[dict[str, Any]] | None = None) -> str:
    case_names = [str(case.get("name", "")) for case in (cases or [])]
    if case_names == ["coremark.soc"]:
        return "coremark"
    names = _normalize_str_list(workspace.get("sim_program_names", []))
    if names == ["coremark"]:
        return "coremark"
    if workspace.get("sim_build_all_programs") or names:
        return "cpu_tests"
    return "default"


def _sim_cpu_test_mode(workspace: dict[str, Any], cases: list[dict[str, Any]] | None = None) -> str:
    case_names = [str(case.get("name", "")) for case in (cases or [])]
    if case_names:
        if case_names == ["coremark.soc"]:
            return ""
        return "all" if len(case_names) >= len(_available_cpu_test_cases(workspace)) else "selected"
    if workspace.get("sim_build_all_programs"):
        return "all"
    if _normalize_str_list(workspace.get("sim_program_names", [])):
        return "selected"
    return ""


def _available_cpu_test_cases(workspace: dict[str, Any]) -> list[str]:
    programs_dir = _resolve_optional_path(workspace.get("sim_programs_dir", ""))
    if not programs_dir:
        return []
    path = Path(programs_dir)
    if not path.is_dir():
        return []
    return [
        source.stem
        for source in sorted(path.glob("*.c"))
        if source.stem not in DEFAULT_FRONTEND_COREMARK_CASES
    ]
