"""CPU-only Yosys precheck for RTL Review.

This analyzer intentionally stops before backend implementation.  It uses Yosys
only as an RTL structure checker for Review Center: read CPU RTL, run hierarchy,
process lowering, check, and stat, then summarize risks.  It does not run tech
mapping, ABC, PDK-aware implementation, STA, or netlist handoff.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fecompiler.tools.prepare.runner import PrepareStep

_STRUCTURAL_STAT = "yosys_precheck_stat.json"
_STRUCTURAL_LOG = "yosys_precheck.log"
_STRUCTURAL_REPORT = "yosys_precheck.json"
_STRUCTURAL_SCRIPT = "yosys_precheck.ys"
_TIMEOUT_SECONDS = 45


def run_structural_probe(workspace: dict[str, Any], step: Any) -> dict[str, Any]:
    """Run a bounded Yosys precheck for CPU RTL only."""
    report_dir = Path(step.report["dir"])
    script_dir = Path(step.script["dir"])
    report_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "report": str(report_dir / _STRUCTURAL_REPORT),
        "log": str(report_dir / _STRUCTURAL_LOG),
        "stat": str(report_dir / _STRUCTURAL_STAT),
        "script": str(script_dir / _STRUCTURAL_SCRIPT),
    }

    inputs = _cpu_probe_inputs(workspace)
    if not inputs["rtl_files"]:
        return _write_probe(paths, {
            "status": "skipped",
            "mode": "cpu_only_yosys_precheck",
            "scope": "cpu",
            "tool": "yosys",
            "title": "Yosys Precheck",
            "reason": "no CPU RTL files found",
            "top_module": _cpu_top(workspace),
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
            "mode": "cpu_only_yosys_precheck",
            "scope": "cpu",
            "tool": "yosys",
            "title": "Yosys Precheck",
            "reason": "yosys executable not found",
            "top_module": _cpu_top(workspace),
            "inputs": inputs,
            "metrics": {},
            "module_risks": [],
            "quality": _quality("unavailable"),
            "diagnostics": [],
            "issues": [],
            "artifacts": paths,
        })

    script_path = Path(paths["script"])
    log_path = Path(paths["log"])
    stat_path = Path(paths["stat"])
    script_path.write_text(_build_yosys_script(inputs, workspace, stat_path), encoding="utf-8")

    try:
        command = [yosys, "-s", str(script_path)]
        result = subprocess.run(
            command,
            cwd=str(script_dir),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        output = (result.stdout or "") + (result.stderr or "")
        log_path.write_text(output, encoding="utf-8")
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + (
            (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        )
        log_path.write_text(output + f"\n[rtl-review] Yosys precheck timed out after {_TIMEOUT_SECONDS}s\n", encoding="utf-8")
        return _write_probe(paths, {
            "status": "timeout",
            "mode": "cpu_only_yosys_precheck",
            "scope": "cpu",
            "tool": "yosys",
            "title": "Yosys Precheck",
            "reason": "Yosys precheck timed out",
            "top_module": _cpu_top(workspace),
            "inputs": inputs,
            "metrics": {},
            "module_risks": [],
            "quality": _quality("timeout"),
            "diagnostics": _diagnostics_from_log(output),
            "issues": [_issue(
                "warning",
                ["IC", "FPGA"],
                "tooling",
                "Yosys precheck timed out",
                "The CPU RTL Yosys precheck did not finish within the bounded review timeout.",
                recommendation="Run elaboration/lint for detailed diagnostics, then retry Review after reducing unsupported constructs.",
            )],
            "artifacts": paths,
        })
    except OSError as exc:
        log_path.write_text(f"[rtl-review] failed to launch yosys: {exc}\n", encoding="utf-8")
        return _write_probe(paths, {
            "status": "failed",
            "mode": "cpu_only_yosys_precheck",
            "scope": "cpu",
            "tool": "yosys",
            "title": "Yosys Precheck",
            "reason": str(exc),
            "top_module": _cpu_top(workspace),
            "inputs": inputs,
            "metrics": {},
            "module_risks": [],
            "quality": _quality("failed"),
            "diagnostics": [],
            "issues": [_issue(
                "warning",
                ["IC", "FPGA"],
                "tooling",
                "Yosys precheck could not start",
                "Yosys was found but could not be launched by the Review step.",
                recommendation="Check Yosys permissions and runtime dependencies.",
            )],
            "artifacts": paths,
        })

    stat = _read_json(stat_path)
    diagnostics = _diagnostics_from_log(output)
    metrics = _metrics_from_stat(stat, output)
    module_risks = _module_risks_from_stat(stat)
    quality = _quality_from_run(result.returncode, diagnostics, metrics)
    issues = _issues_from_probe(output, diagnostics, metrics, result.returncode)
    status = "success" if result.returncode == 0 else "failed"
    reason = "" if status == "success" else "Yosys returned a non-zero exit code"

    return _write_probe(paths, {
        "status": status,
        "mode": "cpu_only_yosys_precheck",
        "scope": "cpu",
        "tool": "yosys",
        "title": "Yosys Precheck",
        "reason": reason,
        "returncode": int(result.returncode),
        "command": command,
        "top_module": _cpu_top(workspace),
        "inputs": inputs,
        "diagnostics": diagnostics,
        "metrics": metrics,
        "module_risks": module_risks,
        "quality": quality,
        "issues": issues,
        "artifacts": paths,
    })


def _cpu_probe_inputs(workspace: dict[str, Any]) -> dict[str, Any]:
    cpu_filelist = str(workspace.get("cpu_filelist", "")).strip()
    if cpu_filelist:
        parsed = _parse_filelist(cpu_filelist)
        parsed["filelist"] = str(Path(cpu_filelist).expanduser().resolve())
        return parsed

    if not str(workspace.get("soc_filelist", "")).strip():
        input_filelist = str(workspace.get("input_filelist", "")).strip()
        if input_filelist:
            parsed = _parse_filelist(input_filelist)
            parsed["filelist"] = str(Path(input_filelist).expanduser().resolve())
            return parsed

        origin_verilog = str(workspace.get("origin_verilog", "")).strip()
        if origin_verilog and Path(origin_verilog).exists():
            path = Path(origin_verilog).expanduser().resolve()
            return {
                "filelist": "",
                "rtl_files": [str(path)],
                "incdirs": [str(path.parent)],
                "defines": [],
            }

    return {"filelist": "", "rtl_files": [], "incdirs": [], "defines": []}


def _parse_filelist(filelist: str) -> dict[str, Any]:
    try:
        parsed = PrepareStep._parse_sv_filelist(filelist)
    except Exception:
        return {"rtl_files": [], "incdirs": [], "defines": []}

    return {
        "rtl_files": [str(Path(path).expanduser().resolve()) for path in parsed.get("rtl_files", [])],
        "incdirs": [str(Path(path).expanduser().resolve()) for path in parsed.get("incdirs", [])],
        "defines": [str(define) for define in parsed.get("defines", [])],
    }


def _resolve_yosys() -> str:
    explicit = os.getenv("YOSYS", "").strip()
    if explicit and Path(explicit).exists():
        return explicit

    oss_cad = os.getenv("CHIPCOMPILER_OSS_CAD_DIR", "").strip()
    if oss_cad:
        candidate = Path(oss_cad) / "bin" / "yosys"
        if candidate.exists():
            return str(candidate)

    return shutil.which("yosys") or ""


def _build_yosys_script(inputs: dict[str, Any], workspace: dict[str, Any], stat_path: Path) -> str:
    top = _cpu_top(workspace)
    read_args: list[str] = ["read_verilog", "-sv", "-defer"]
    for incdir in inputs.get("incdirs", []):
        read_args.append(f"-I{_quote_yosys(str(incdir), quote=False)}")
    for define in inputs.get("defines", []):
        read_args.append(f"-D{_quote_yosys(str(define), quote=False)}")
    read_args.extend(_quote_yosys(str(path)) for path in inputs.get("rtl_files", []))

    hierarchy = f"hierarchy -top {_quote_yosys(top)} -check" if top else "hierarchy -auto-top -check"
    return "\n".join([
        "# Auto-generated by ECOS frontend RTL Review.",
        "# CPU-only Yosys precheck. This script stops before implementation and netlist handoff.",
        " ".join(read_args),
        hierarchy,
        "proc",
        "opt_expr",
        "opt_clean",
        "check",
        f"tee -o {_quote_yosys(str(stat_path))} stat -json",
        "",
    ])


def _quote_yosys(value: str, *, quote: bool = True) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"' if quote else escaped


def _cpu_top(workspace: dict[str, Any]) -> str:
    for field in ("cpu_wrapper_top", "cpu_top_module"):
        text = str(workspace.get(field, "")).strip()
        if text:
            return text
    top = str(workspace.get("top_module", "")).strip()
    return "" if top == "ecos_sim_top" else top


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _metrics_from_stat(stat: dict[str, Any], log: str) -> dict[str, Any]:
    design = stat.get("design", {}) if isinstance(stat.get("design"), dict) else {}
    modules = stat.get("modules", {}) if isinstance(stat.get("modules"), dict) else {}
    module_values = [item for item in modules.values() if isinstance(item, dict)]

    cell_types: dict[str, int] = {}
    for module in module_values:
        for key, value in _extract_cell_types(module).items():
            cell_types[key] = cell_types.get(key, 0) + value

    num_cells = _int_or_zero(design.get("num_cells"))
    if not num_cells:
        num_cells = sum(_int_or_zero(module.get("num_cells")) for module in module_values)
    if not num_cells and cell_types:
        num_cells = sum(cell_types.values())

    num_wires = _int_or_zero(design.get("num_wires"))
    if not num_wires:
        num_wires = sum(_int_or_zero(module.get("num_wires")) for module in module_values)

    num_ports = _int_or_zero(design.get("num_port_bits"))
    if not num_ports:
        num_ports = sum(_int_or_zero(module.get("num_port_bits")) for module in module_values)

    num_processes = sum(_int_or_zero(module.get("num_processes")) for module in module_values)
    memory_cells = sum(count for name, count in cell_types.items() if "$mem" in name.lower() or "mem" in name.lower())
    mux_cells = sum(count for name, count in cell_types.items() if "$mux" in name.lower() or "$pmux" in name.lower())
    arithmetic_cells = sum(
        count
        for name, count in cell_types.items()
        if any(op in name.lower() for op in ("$add", "$sub", "$mul", "$div", "$mod", "$alu"))
    )

    return {
        "source": "yosys_stat",
        "modules": len(modules),
        "module_names": sorted(str(name).lstrip("\\") for name in modules.keys())[:80],
        "cells": num_cells,
        "wires": num_wires,
        "port_bits": num_ports,
        "processes": num_processes,
        "memory_cells": memory_cells,
        "mux_cells": mux_cells,
        "arithmetic_cells": arithmetic_cells,
        "cell_types": dict(sorted(cell_types.items())[:80]),
        "log_warnings": len(re.findall(r"\bwarning\b", log, flags=re.I)),
        "log_errors": len(re.findall(r"\berror\b", log, flags=re.I)),
    }


def _module_risks_from_stat(stat: dict[str, Any]) -> list[dict[str, Any]]:
    modules = stat.get("modules", {}) if isinstance(stat.get("modules"), dict) else {}
    risks: list[dict[str, Any]] = []
    for raw_name, raw_module in modules.items():
        if not isinstance(raw_module, dict):
            continue
        cell_types = _extract_cell_types(raw_module)
        cells = _int_or_zero(raw_module.get("num_cells")) or sum(cell_types.values())
        wires = _int_or_zero(raw_module.get("num_wires"))
        ports = _int_or_zero(raw_module.get("num_port_bits"))
        processes = _int_or_zero(raw_module.get("num_processes"))
        mux_cells = sum(count for name, count in cell_types.items() if "$mux" in name.lower() or "$pmux" in name.lower())
        memory_cells = sum(count for name, count in cell_types.items() if "$mem" in name.lower() or "mem" in name.lower())
        arithmetic_cells = sum(
            count
            for name, count in cell_types.items()
            if any(op in name.lower() for op in ("$add", "$sub", "$mul", "$div", "$mod", "$alu"))
        )
        score = cells + wires // 2 + mux_cells * 3 + arithmetic_cells * 4 + memory_cells * 5 + processes * 12
        reasons = _module_risk_reasons(
            cells=cells,
            wires=wires,
            processes=processes,
            mux_cells=mux_cells,
            arithmetic_cells=arithmetic_cells,
            memory_cells=memory_cells,
        )
        if not reasons and score < 100:
            continue
        risks.append({
            "module": str(raw_name).lstrip("\\"),
            "score": score,
            "risk": _module_risk_bucket(score, reasons),
            "cells": cells,
            "wires": wires,
            "ports": ports,
            "processes": processes,
            "mux_cells": mux_cells,
            "arithmetic_cells": arithmetic_cells,
            "memory_cells": memory_cells,
            "reasons": reasons,
            "top_cell_types": [
                {"type": name, "count": count}
                for name, count in sorted(cell_types.items(), key=lambda item: item[1], reverse=True)[:8]
            ],
        })
    return sorted(risks, key=lambda item: int(item.get("score", 0)), reverse=True)[:12]


def _module_risk_reasons(
    *,
    cells: int,
    wires: int,
    processes: int,
    mux_cells: int,
    arithmetic_cells: int,
    memory_cells: int,
) -> list[str]:
    reasons: list[str] = []
    if cells >= 2000:
        reasons.append("large cell population")
    if wires >= 4000:
        reasons.append("large wire population")
    if mux_cells >= 200:
        reasons.append("mux-heavy control/data selection")
    if arithmetic_cells >= 40:
        reasons.append("arithmetic-heavy datapath")
    if memory_cells:
        reasons.append("inferred memory candidate")
    if processes >= 20:
        reasons.append("many lowered procedural blocks")
    return reasons


def _module_risk_bucket(score: int, reasons: list[str]) -> str:
    if score >= 8000 or len(reasons) >= 3:
        return "high"
    if score >= 2500 or len(reasons) >= 2:
        return "medium"
    return "low"


def _extract_cell_types(module: dict[str, Any]) -> dict[str, int]:
    cells = module.get("num_cells_by_type")
    if isinstance(cells, dict):
        return {str(key): _int_or_zero(value) for key, value in cells.items()}
    cells = module.get("cells")
    if isinstance(cells, dict):
        out: dict[str, int] = {}
        for value in cells.values():
            if isinstance(value, dict):
                cell_type = str(value.get("type", ""))
                if cell_type:
                    out[cell_type] = out.get(cell_type, 0) + 1
        return out
    return {}


def _diagnostics_from_log(log: str) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for raw in log.splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        severity = ""
        if "error:" in lower or lower.startswith("error") or "syntax error" in lower:
            severity = "error"
        elif "warning:" in lower or lower.startswith("warning"):
            severity = "warning"
        if not severity:
            continue
        location = _diagnostic_location(line)
        diagnostics.append({
            "severity": severity,
            "message": line,
            "category": _diagnostic_category(line),
            "source": location.get("source", ""),
            "line": location.get("line", 0),
            "column": location.get("column", 0),
        })
        if len(diagnostics) >= 80:
            break
    return diagnostics


def _diagnostic_location(message: str) -> dict[str, Any]:
    patterns = (
        r"(?P<source>/[^:\s]+?\.(?:sv|svh|v|vh)):(?P<line>\d+):(?P<column>\d+)",
        r"(?P<source>/[^:\s]+?\.(?:sv|svh|v|vh)):(?P<line>\d+)",
        r"(?P<source>[^:\s]+?\.(?:sv|svh|v|vh)):(?P<line>\d+):(?P<column>\d+)",
        r"(?P<source>[^:\s]+?\.(?:sv|svh|v|vh)):(?P<line>\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if not match:
            continue
        source = str(match.group("source"))
        return {
            "source": source,
            "line": _int_or_zero(match.group("line")),
            "column": _int_or_zero(match.groupdict().get("column")) or 1,
        }
    return {"source": "", "line": 0, "column": 0}


def _diagnostic_category(message: str) -> str:
    text = message.lower()
    if "syntax" in text or "parse" in text:
        return "syntax"
    if "module" in text and ("not found" in text or "missing" in text):
        return "hierarchy"
    if "latch" in text:
        return "combinational"
    if "driver" in text or "driven" in text:
        return "structural"
    if "loop" in text:
        return "combinational"
    return "tooling"


def _quality_from_run(
    returncode: int,
    diagnostics: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    errors = sum(1 for item in diagnostics if item.get("severity") == "error")
    warnings = sum(1 for item in diagnostics if item.get("severity") == "warning")
    complexity_score = (
        _int_or_zero(metrics.get("cells"))
        + _int_or_zero(metrics.get("mux_cells")) * 2
        + _int_or_zero(metrics.get("arithmetic_cells")) * 3
        + _int_or_zero(metrics.get("memory_cells")) * 4
    )
    if returncode != 0 or errors:
        gate = "failed"
    elif warnings:
        gate = "warnings"
    else:
        gate = "clean"
    return {
        "gate": gate,
        "frontend_parse": "pass" if returncode == 0 or not errors else "fail",
        "hierarchy": "pass" if returncode == 0 else "fail",
        "structural_check": "warnings" if warnings else ("fail" if errors else "pass"),
        "diagnostic_errors": errors,
        "diagnostic_warnings": warnings,
        "complexity": _complexity_bucket(complexity_score),
        "complexity_score": complexity_score,
    }


def _quality(status: str) -> dict[str, Any]:
    return {
        "gate": status,
        "frontend_parse": status,
        "hierarchy": status,
        "structural_check": status,
        "diagnostic_errors": 0,
        "diagnostic_warnings": 0,
        "complexity": "unknown",
        "complexity_score": 0,
    }


def _complexity_bucket(score: int) -> str:
    if score >= 20000:
        return "very_high"
    if score >= 8000:
        return "high"
    if score >= 2500:
        return "medium"
    return "low"


def _issues_from_probe(
    log: str,
    diagnostics: list[dict[str, Any]],
    metrics: dict[str, Any],
    returncode: int,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    text = log.lower()

    if returncode != 0:
        has_error = any(item.get("severity") == "error" for item in diagnostics)
        first_location = _first_diagnostic_location(diagnostics)
        issues.append(_issue(
            "error" if has_error else "warning",
            ["IC", "FPGA"],
            "structural",
            "Yosys precheck failed before completion",
            "Yosys could not complete the CPU RTL precheck.",
            path=first_location.get("source", ""),
            line=first_location.get("line", 0),
            column=first_location.get("column", 0),
            evidence={"returncode": returncode},
            recommendation="Check the Yosys precheck log, then run Elab/Lint for source-level diagnostics.",
        ))

    diagnostic_categories = {str(item.get("category", "")) for item in diagnostics}
    if "syntax" in diagnostic_categories:
        first_location = _first_diagnostic_location(diagnostics, category="syntax")
        issues.append(_issue(
            "error",
            ["IC", "FPGA"],
            "syntax",
            "Yosys reported RTL syntax/front-end errors",
            "The CPU RTL cannot pass the Yosys Verilog frontend in its current form.",
            path=first_location.get("source", ""),
            line=first_location.get("line", 0),
            column=first_location.get("column", 0),
            evidence={"diagnostic_errors": sum(1 for item in diagnostics if item.get("severity") == "error")},
            recommendation="Open the Yosys precheck log, fix the first syntax or unsupported construct error, then rerun Review.",
        ))

    if "hierarchy" in diagnostic_categories:
        issues.append(_issue(
            "error",
            ["IC", "FPGA"],
            "hierarchy",
            "Yosys could not resolve the CPU top hierarchy",
            "A missing or mismatched top module prevents downstream quality checks from being reliable.",
            recommendation="Confirm the selected CPU wrapper/top exists in the CPU filelist.",
        ))

    if "logic loop" in text or "combinational loop" in text:
        issues.append(_issue(
            "error",
            ["IC", "FPGA"],
            "combinational",
            "Combinational loop reported by Yosys precheck",
            "A combinational feedback loop can break simulation convergence and timing analysis.",
            recommendation="Break the loop with a register or correct the ready/valid/control dependency.",
        ))

    if "multiple conflicting drivers" in text or "multiple drivers" in text:
        issues.append(_issue(
            "error",
            ["IC", "FPGA"],
            "structural",
            "Multiple drivers reported by Yosys precheck",
            "Multiple drivers on the same net can create X propagation or non-synthesizable logic.",
            recommendation="Ensure each signal has exactly one procedural or continuous driver.",
        ))

    if "no driver" in text or "undriven" in text:
        issues.append(_issue(
            "warning",
            ["IC", "FPGA"],
            "structural",
            "Undriven signal reported by Yosys precheck",
            "Undriven nets can become constants, X sources, or backend optimization surprises.",
            recommendation="Tie off unused inputs explicitly and drive every architecturally visible net.",
        ))

    if "latch inferred" in text or "inferring latch" in text:
        issues.append(_issue(
            "warning",
            ["IC", "FPGA"],
            "combinational",
            "Latch inference reported by Yosys precheck",
            "Unintended latches make timing, reset, and FPGA mapping harder to control.",
            recommendation="Assign defaults in combinational blocks and cover all control branches.",
        ))

    if _int_or_zero(metrics.get("mux_cells")) >= 200:
        issues.append(_issue(
            "info",
            ["IC", "FPGA"],
            "timing",
            "Large mux population candidate",
            "A high mux count often points to deep decode, bypass, or CSR selection cones.",
            evidence={"mux_cells": metrics.get("mux_cells")},
            recommendation="Inspect the largest select cones and consider staging or one-hot structure.",
        ))

    if _int_or_zero(metrics.get("memory_cells")) > 0:
        issues.append(_issue(
            "info",
            ["IC", "FPGA"],
            "memory",
            "Inferred memory candidate",
            "Inferred memories should be checked against IC SRAM macros or FPGA block RAM inference style.",
            evidence={"memory_cells": metrics.get("memory_cells")},
            recommendation="Confirm memory templates, read/write latency, and reset behavior match the target platform.",
        ))

    if _int_or_zero(metrics.get("arithmetic_cells")) >= 40:
        issues.append(_issue(
            "info",
            ["IC", "FPGA"],
            "timing",
            "Arithmetic-heavy structure candidate",
            "Large arithmetic populations can dominate frequency or FPGA DSP/LUT usage.",
            evidence={"arithmetic_cells": metrics.get("arithmetic_cells")},
            recommendation="Check multiplier/divider implementation choices and pipeline long arithmetic paths.",
        ))

    return issues


def _first_diagnostic_location(
    diagnostics: list[dict[str, Any]],
    *,
    category: str = "",
) -> dict[str, Any]:
    for diagnostic in diagnostics:
        if category and diagnostic.get("category") != category:
            continue
        if diagnostic.get("source"):
            return {
                "source": diagnostic.get("source", ""),
                "line": diagnostic.get("line", 0),
                "column": diagnostic.get("column", 0),
            }
    return {"source": "", "line": 0, "column": 0}


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


def _issue(
    severity: str,
    profiles: list[str],
    category: str,
    title: str,
    detail: str,
    *,
    path: str = "",
    line: int = 0,
    column: int = 0,
    evidence: dict[str, Any] | None = None,
    recommendation: str = "",
) -> dict[str, Any]:
    return {
        "severity": severity,
        "profiles": profiles,
        "category": category,
        "title": title,
        "detail": detail,
        "source": path,
        "line": line,
        "column": column,
        "evidence": evidence or {},
        "recommendation": recommendation,
    }


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
