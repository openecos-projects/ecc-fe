"""Bounded Yosys structural precheck for RTL Review.

This analyzer intentionally stops before backend implementation.  It uses Yosys
only as an RTL structure checker for Review Center: read user RTL, run hierarchy,
process lowering, check, and stat, then summarize risks.  It does not run tech
mapping, ABC, PDK-aware implementation, STA, or netlist handoff.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fecompiler.allflow.profile import GENERIC_RTL, normalize_frontend_design_kind
from fecompiler.tools.common.process import run_tool, tool_timeout
from fecompiler.tools.review.netlist_graph import (
    _metrics_from_stat,
    _read_json,
    _structural_metrics_from_netlist,
)
from fecompiler.tools.review.probe_diagnostics import (
    _diagnostics_from_log,
    _yosys_failure_reason,
)
from fecompiler.tools.review.probe_inputs import (
    _cpu_probe_inputs,
    _cpu_top,
    _dpi_blackbox_modules,
)
from fecompiler.tools.review.probe_risks import (
    _issue,
    _issues_from_probe,
    _module_risks_from_stat,
    _only_tool_limit_errors,
    _quality,
    _quality_from_run,
)
from fecompiler.tools.review.probe_script import (
    _as_frontend_limits,
    _build_yosys_script,
    _paths_with_whitespace,
)
from fecompiler.tools.review.yosys_env import (
    _has_systemverilog_sources,
    _resolve_yosys,
    _select_yosys_frontend,
    _yosys_not_found_reason,
    _yosys_resolution_report,
    _yosys_runtime_env,
)

_STRUCTURAL_STAT = "yosys_precheck_stat.json"


_STRUCTURAL_NETLIST = "yosys_precheck_netlist.json"


_STRUCTURAL_LOG = "yosys_precheck.log"


_STRUCTURAL_REPORT = "yosys_precheck.json"


_STRUCTURAL_SCRIPT = "yosys_precheck.ys"




def run_structural_probe(workspace: dict[str, Any], step: Any) -> dict[str, Any]:
    """Run a bounded Yosys precheck for the profile's user-owned RTL."""
    report_dir = Path(step.report["dir"])
    script_dir = Path(step.script["dir"])
    report_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "report": str(report_dir / _STRUCTURAL_REPORT),
        "log": str(report_dir / _STRUCTURAL_LOG),
        "stat": str(report_dir / _STRUCTURAL_STAT),
        "netlist": str(report_dir / _STRUCTURAL_NETLIST),
        "script": str(script_dir / _STRUCTURAL_SCRIPT),
    }

    mode = _probe_mode(workspace)
    scope = _probe_scope(workspace)
    subject = "design RTL" if scope == "design" else "CPU RTL"
    inputs = _cpu_probe_inputs(workspace)
    top_module = _cpu_top(workspace, inputs)
    inputs["blackboxed_modules"] = _dpi_blackbox_modules(
        inputs,
        exclude={top_module} if top_module else set(),
    )
    if not inputs["rtl_files"]:
        return _write_probe(paths, {
            "status": "skipped",
            "mode": mode,
            "scope": scope,
            "tool": "yosys",
            "title": "Yosys Precheck",
            "reason": f"no {subject} files found",
            "top_module": top_module,
            "inputs": inputs,
            "metrics": {},
            "module_risks": [],
            "quality": _quality("skipped"),
            "diagnostics": [],
            "issues": [],
            "artifacts": paths,
        })

    yosys = _resolve_yosys()
    if not yosys:
        return _write_probe(paths, {
            "status": "unavailable",
            "mode": mode,
            "scope": scope,
            "tool": "yosys",
            "title": "Yosys Precheck",
            "reason": _yosys_not_found_reason(),
            "resolution": _yosys_resolution_report(),
            "top_module": top_module,
            "inputs": inputs,
            "metrics": {},
            "module_risks": [],
            "quality": _quality("unavailable"),
            "diagnostics": [],
            "issues": [],
            "artifacts": paths,
        })

    spaced = _paths_with_whitespace(inputs)
    if spaced:
        # A Yosys script cannot express such a path; elab and lint can, so
        # the structural metrics are skipped rather than failing review.
        return _write_probe(paths, {
            "status": "skipped",
            "mode": mode,
            "scope": scope,
            "tool": "yosys",
            "title": "Yosys Precheck",
            "reason": "Yosys scripts cannot name paths that contain spaces: " + spaced[0],
            "top_module": top_module,
            "inputs": inputs,
            "metrics": {},
            "module_risks": [],
            "quality": _quality("skipped"),
            "diagnostics": [],
            "issues": [_issue(
                "info",
                "tooling",
                "Yosys precheck skipped",
                "A source or include path contains spaces, which a Yosys script cannot express.",
                recommendation="Structural metrics are unavailable; move the sources to a path without spaces to get them.",
            )],
            "artifacts": paths,
        })

    script_path = Path(paths["script"])
    log_path = Path(paths["log"])
    stat_path = Path(paths["stat"])
    netlist_path = Path(paths["netlist"])
    frontend = _select_yosys_frontend(yosys, inputs)
    script_path.write_text(
        _build_yosys_script(inputs, workspace, stat_path, netlist_path, frontend=frontend),
        encoding="utf-8",
    )

    timeout = tool_timeout("review")
    try:
        command = [yosys, "-s", str(script_path)]
        result = run_tool(
            command,
            log_path=log_path,
            cwd=str(script_dir),
            env=_yosys_runtime_env(yosys),
            timeout=timeout,
            session_dir=script_dir,
        )
        output = result.output
    except OSError as exc:
        log_path.write_text(f"[rtl-review] failed to launch yosys: {exc}\n", encoding="utf-8")
        return _write_probe(paths, {
            "status": "failed",
            "mode": mode,
            "scope": scope,
            "tool": "yosys",
            "frontend": frontend,
            "title": "Yosys Precheck",
            "reason": str(exc),
            "top_module": top_module,
            "inputs": inputs,
            "metrics": {},
            "module_risks": [],
            "quality": _quality("failed"),
            "diagnostics": [],
            "issues": [_issue(
                "warning",
                "tooling",
                "Yosys precheck could not start",
                "Yosys was found but could not be launched by the Review step.",
                recommendation="Check Yosys permissions and runtime dependencies.",
            )],
            "artifacts": paths,
        })
    if result.timed_out:
        log_path.write_text(
            output
            + f"\n[rtl-review] Yosys precheck timed out after {timeout:g}s"
            + " (ECOS_FE_TIMEOUT_REVIEW sets the limit in seconds; 0 removes it)\n",
            encoding="utf-8",
        )
        return _write_probe(paths, {
            "status": "timeout",
            "mode": mode,
            "scope": scope,
            "tool": "yosys",
            "frontend": frontend,
            "title": "Yosys Precheck",
            "reason": f"Yosys precheck timed out after {timeout:g} seconds",
            "top_module": top_module,
            "inputs": inputs,
            "metrics": {},
            "module_risks": [],
            "quality": _quality("timeout"),
            "diagnostics": _diagnostics_from_log(output),
            "issues": [_issue(
                "warning",
                "tooling",
                "Yosys precheck timed out",
                f"The {subject} Yosys precheck did not finish within {timeout:g} seconds.",
                recommendation=(
                    "A large design can need longer: set ECOS_FE_TIMEOUT_REVIEW to more seconds "
                    "(0 removes the limit) and run review again. Elaboration and lint are not affected."
                ),
            )],
            "artifacts": paths,
        })

    stat = _read_json(stat_path)
    diagnostics = _diagnostics_from_log(output)
    if (
        result.returncode != 0
        and frontend == "read_verilog"
        and _has_systemverilog_sources(inputs)
    ):
        # Without the slang plugin Yosys reads SystemVerilog with its own
        # limited parser. What it rejects may be valid: elab decides, not this.
        diagnostics = _as_frontend_limits(diagnostics)
    netlist = _read_json(netlist_path)
    metrics = _metrics_from_stat(stat, output)
    metrics.update(_structural_metrics_from_netlist(netlist))
    module_risks = _module_risks_from_stat(stat, metrics)
    quality = _quality_from_run(result.returncode, diagnostics, metrics)
    issues = _issues_from_probe(output, diagnostics, metrics, result.returncode)
    if scope == "design":
        issues = [_design_wording(issue) for issue in issues]
    if result.returncode == 0:
        status = "success"
    elif _only_tool_limit_errors(diagnostics):
        status = "tool_limited"
    else:
        status = "failed"
    reason = "" if status == "success" else _yosys_failure_reason(diagnostics, output, result.returncode)

    return _write_probe(paths, {
        "status": status,
        "mode": mode,
        "scope": scope,
        "tool": "yosys",
        "frontend": frontend,
        "title": "Yosys Precheck",
        "reason": reason,
        "returncode": int(result.returncode),
        "command": command,
        "top_module": top_module,
        "inputs": inputs,
        "diagnostics": diagnostics,
        "metrics": metrics,
        "module_risks": module_risks,
        "quality": quality,
        "issues": issues,
        "artifacts": paths,
    })


_DESIGN_WORDING = (
    ("selected CPU wrapper/top exists in the CPU filelist", "selected top module exists in the design sources"),
    ("the CPU top hierarchy", "the top hierarchy"),
    ("The CPU RTL", "The design RTL"),
    ("the CPU RTL", "the design RTL"),
)


def _design_wording(issue: dict[str, Any]) -> dict[str, Any]:
    """Reword an issue written for a CPU so it reads right for any RTL design."""
    reworded = dict(issue)
    for key, value in issue.items():
        if isinstance(value, str):
            for cpu_text, design_text in _DESIGN_WORDING:
                value = value.replace(cpu_text, design_text)
            reworded[key] = value
    return reworded


def _probe_scope(workspace: dict[str, Any]) -> str:
    return (
        "design"
        if normalize_frontend_design_kind(workspace.get("frontend_design_kind")) == GENERIC_RTL
        else "cpu"
    )


def _probe_mode(workspace: dict[str, Any]) -> str:
    return "design_yosys_precheck" if _probe_scope(workspace) == "design" else "cpu_only_yosys_precheck"


def _write_probe(paths: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    report_path = Path(paths["report"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if not Path(paths["log"]).exists():
        Path(paths["log"]).write_text(_format_probe_log(payload), encoding="utf-8")
    return payload


def _format_probe_log(payload: dict[str, Any]) -> str:
    lines = [
        "[rtl-review] Yosys precheck",
        f"status={payload.get('status', '')}",
        f"reason={payload.get('reason', '')}",
    ]
    return "\n".join(lines) + "\n"
