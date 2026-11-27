"""Turn the measurements and diagnostics of the precheck into risks, a quality
verdict and review issues.
"""

from __future__ import annotations

from typing import Any

from fecompiler.tools.review.netlist_graph import (
    _extract_cell_types,
)
from fecompiler.tools.review.probe_common import _int_or_zero


def _module_risks_from_stat(stat: dict[str, Any], metrics: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    modules = stat.get("modules", {}) if isinstance(stat.get("modules"), dict) else {}
    structure_by_module = {
        str(item.get("module", "")): item
        for item in (metrics or {}).get("module_structure", [])
        if isinstance(item, dict)
    }
    risks: list[dict[str, Any]] = []
    for raw_name, raw_module in modules.items():
        if not isinstance(raw_module, dict):
            continue
        module_name = str(raw_name).lstrip("\\")
        structure = structure_by_module.get(module_name, {})
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
        score += _int_or_zero(structure.get("max_comb_depth")) * 50
        score += _int_or_zero(structure.get("max_fanout")) * 8
        score += _int_or_zero(structure.get("max_fanin")) * 6
        reasons = _module_risk_reasons(
            cells=cells,
            wires=wires,
            processes=processes,
            mux_cells=mux_cells,
            arithmetic_cells=arithmetic_cells,
            memory_cells=memory_cells,
            comb_cycle=bool(structure.get("comb_cycle")),
        )
        if not reasons and score < 100:
            continue
        risks.append({
            "module": module_name,
            "score": score,
            "risk": _module_risk_bucket(score, reasons),
            "cells": cells,
            "wires": wires,
            "ports": ports,
            "processes": processes,
            "mux_cells": mux_cells,
            "arithmetic_cells": arithmetic_cells,
            "memory_cells": memory_cells,
            "max_fanout": _int_or_zero(structure.get("max_fanout")),
            "max_fanin": _int_or_zero(structure.get("max_fanin")),
            "max_comb_depth": _int_or_zero(structure.get("max_comb_depth")),
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
    comb_cycle: bool = False,
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
    # Fanout, fanin and depth are word-level counts with no limit that holds
    # across designs (see netlist_graph), so they only rank the modules.
    if comb_cycle:
        reasons.append("combinational cycle candidate")
    return reasons


def _module_risk_bucket(score: int, reasons: list[str]) -> str:
    if score >= 8000 or len(reasons) >= 3:
        return "high"
    if score >= 2500 or len(reasons) >= 2:
        return "medium"
    return "low"


def _quality_from_run(
    returncode: int,
    diagnostics: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    errors = sum(1 for item in diagnostics if item.get("severity") == "error")
    warnings = sum(1 for item in diagnostics if item.get("severity") == "warning")
    blocking_errors = sum(1 for item in diagnostics if _diagnostic_blocks_review(item))
    complexity_score = (
        _int_or_zero(metrics.get("cells"))
        + _int_or_zero(metrics.get("mux_cells")) * 2
        + _int_or_zero(metrics.get("arithmetic_cells")) * 3
        + _int_or_zero(metrics.get("memory_cells")) * 4
    )
    if returncode != 0 and not blocking_errors:
        gate = "warnings"
    elif blocking_errors:
        gate = "failed"
    elif warnings:
        gate = "warnings"
    else:
        gate = "clean"
    return {
        "gate": gate,
        "frontend_parse": "pass" if not blocking_errors else "fail",
        "hierarchy": "pass" if not blocking_errors else "fail",
        "structural_check": "warnings" if warnings or errors else "pass",
        "diagnostic_errors": errors,
        "diagnostic_warnings": warnings,
        "complexity": _complexity_bucket(complexity_score),
        "complexity_score": complexity_score,
    }


def _diagnostic_blocks_review(diagnostic: dict[str, Any]) -> bool:
    if str(diagnostic.get("severity", "")).lower() != "error":
        return False
    if str(diagnostic.get("category", "")).lower() == "tool-limit":
        return False
    return True


def _only_tool_limit_errors(diagnostics: list[dict[str, Any]]) -> bool:
    errors = [item for item in diagnostics if str(item.get("severity", "")).lower() == "error"]
    return bool(errors) and all(str(item.get("category", "")).lower() == "tool-limit" for item in errors)


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
        blocking_error = any(_diagnostic_blocks_review(item) for item in diagnostics)
        tool_limited = _only_tool_limit_errors(diagnostics)
        first_location = _first_diagnostic_location(diagnostics)
        issues.append(_issue(
            "error" if blocking_error else "warning",
            "tooling" if tool_limited else "structural",
            "Yosys precheck hit a tool frontend limitation" if tool_limited else "Yosys precheck failed before completion",
            (
                "Yosys could not complete the optional structural probe because the selected frontend does not support one RTL construct."
                if tool_limited
                else "Yosys could not complete the CPU RTL precheck."
            ),
            path=first_location.get("source", ""),
            line=first_location.get("line", 0),
            column=first_location.get("column", 0),
            evidence={"returncode": returncode},
            recommendation=(
                "Use Elab/Lint as the source-level gate and treat Yosys structural metrics as unavailable for this run."
                if tool_limited
                else "Check the Yosys precheck log, then run Elab/Lint for source-level diagnostics."
            ),
        ))

    diagnostic_categories = {str(item.get("category", "")) for item in diagnostics}
    if "syntax" in diagnostic_categories:
        first_location = _first_diagnostic_location(diagnostics, category="syntax")
        issues.append(_issue(
            "error",
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
            "hierarchy",
            "Yosys could not resolve the CPU top hierarchy",
            "A missing or mismatched top module prevents downstream quality checks from being reliable.",
            recommendation="Confirm the selected CPU wrapper/top exists in the CPU filelist.",
        ))

    if "logic loop" in text or "combinational loop" in text:
        issues.append(_issue(
            "error",
            "combinational",
            "Combinational loop reported by Yosys precheck",
            "A combinational feedback loop can break simulation convergence and timing analysis.",
            recommendation="Break the loop with a register or correct the ready/valid/control dependency.",
        ))

    if "multiple conflicting drivers" in text or "multiple drivers" in text:
        issues.append(_issue(
            "error",
            "structural",
            "Multiple drivers reported by Yosys precheck",
            "Multiple drivers on the same net can create X propagation or non-synthesizable logic.",
            recommendation="Ensure each signal has exactly one procedural or continuous driver.",
        ))

    if "no driver" in text or "undriven" in text:
        issues.append(_issue(
            "warning",
            "structural",
            "Undriven signal reported by Yosys precheck",
            "Undriven nets can become constants, X sources, or backend optimization surprises.",
            recommendation="Tie off unused inputs explicitly and drive every architecturally visible net.",
        ))

    if "latch inferred" in text or "inferring latch" in text:
        issues.append(_issue(
            "warning",
            "combinational",
            "Latch inference reported by Yosys precheck",
            "Unintended latches make timing, reset, and implementation mapping harder to control.",
            recommendation="Assign defaults in combinational blocks and cover all control branches.",
        ))

    if _int_or_zero(metrics.get("mux_cells")) >= 200:
        issues.append(_issue(
            "info",
            "timing",
            "Large mux population candidate",
            "A high mux count often points to deep decode, bypass, or CSR selection cones.",
            evidence={"mux_cells": metrics.get("mux_cells")},
            recommendation="Inspect the largest select cones and consider staging or one-hot structure.",
        ))

    if _int_or_zero(metrics.get("memory_cells")) > 0:
        issues.append(_issue(
            "info",
            "memory",
            "Inferred memory candidate",
            "Inferred memories should be checked against the target memory template and inference style.",
            evidence={"memory_cells": metrics.get("memory_cells")},
            recommendation="Confirm memory templates, read/write latency, and reset behavior match the target platform.",
        ))

    if _int_or_zero(metrics.get("arithmetic_cells")) >= 40:
        issues.append(_issue(
            "info",
            "timing",
            "Arithmetic-heavy structure candidate",
            "Large arithmetic populations can dominate frequency and implementation resource usage.",
            evidence={"arithmetic_cells": metrics.get("arithmetic_cells")},
            recommendation="Check multiplier/divider implementation choices and pipeline long arithmetic paths.",
        ))

    if metrics.get("comb_cycle_modules"):
        # Cells are whole words here, so a bus whose bits feed one another
        # looks like a loop. It is a place to look, not a finding: a real
        # loop is reported by Yosys `check` as a diagnostic of its own.
        issues.append(_issue(
            "warning",
            "combinational",
            "Combinational cycle candidate in structural graph",
            "The word-level netlist graph could not be fully ordered. Bits of one "
            "bus feeding other bits of it look the same as a loop at this level.",
            evidence={"modules": metrics.get("comb_cycle_modules")},
            recommendation="Check ready/valid feedback and combinational dependencies in the reported modules.",
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


def _issue(
    severity: str,
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
        "category": category,
        "title": title,
        "detail": detail,
        "source": path,
        "line": line,
        "column": column,
        "evidence": evidence or {},
        "recommendation": recommendation,
    }
