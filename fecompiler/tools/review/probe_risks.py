"""Turn the measurements and diagnostics of the precheck into risks, a quality
verdict and review issues.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from fecompiler.tools.review.netlist_graph import (
    _extract_cell_types,
    _netlist_location,
    _one_per_location,
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
    netlist: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

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

    issues.extend(_check_issues(log, netlist or {}))
    issues.extend(_latch_issues(metrics))

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

    # Cells are whole words here, so a bus whose bits feed one another looks
    # like a loop. It is a place to look, not a finding: a real loop is
    # reported by Yosys `check` (above), so this is a note, not a warning.
    for cycle in _one_per_location(_records(metrics.get("comb_cycles"))):
        issues.append(_issue(
            "info",
            "combinational",
            "Combinational cycle candidate in structural graph",
            f"The word-level netlist graph of module {cycle.get('module', '')} could not be "
            "fully ordered. Bits of one bus feeding other bits of it look the same as a "
            "loop at this level.",
            path=str(cycle.get("source", "")),
            line=_int_or_zero(cycle.get("line")),
            column=_int_or_zero(cycle.get("column")),
            evidence={"module": cycle.get("module", "")},
            recommendation="Check ready/valid feedback and combinational dependencies in this module.",
        ))

    return issues


class _CheckFinding(NamedTuple):
    """A problem Yosys `check` logs, and how its log line names the place."""

    pattern: re.Pattern[str]
    phrases: tuple[str, ...]
    severity: str
    category: str
    title: str
    detail: str
    recommendation: str


# Group 1 is the module as Yosys logs it; group 2, when there is one, the signal.
_CHECK_FINDINGS = (
    _CheckFinding(
        re.compile(r"found logic loop in module (\S+?):"),
        ("logic loop", "combinational loop"),
        "error",
        "combinational",
        "Combinational loop reported by Yosys precheck",
        "A combinational feedback loop can break simulation convergence and timing analysis.",
        "Break the loop with a register or correct the ready/valid/control dependency.",
    ),
    _CheckFinding(
        re.compile(r"multiple conflicting drivers for (\S+?)\.([\\$][^\s\[:]*)"),
        ("multiple conflicting drivers", "multiple drivers"),
        "error",
        "structural",
        "Multiple drivers reported by Yosys precheck",
        "Multiple drivers on the same net can create X propagation or non-synthesizable logic.",
        "Ensure each signal has exactly one procedural or continuous driver.",
    ),
    _CheckFinding(
        re.compile(r"Wire (\S+?)\.([\\$]\S+) is used but has no driver"),
        ("no driver", "undriven"),
        "warning",
        "structural",
        "Undriven signal reported by Yosys precheck",
        "Undriven nets can become constants, X sources, or backend optimization surprises.",
        "Tie off unused inputs explicitly and drive every architecturally visible net.",
    ),
)


def _check_issues(log: str, netlist: dict[str, Any]) -> list[dict[str, Any]]:
    """One issue per module a `check` finding names, placed in that module's source.

    Without a place an issue counts as the user's, whoever owns the module.
    Only when the log has the finding in a form no pattern reads is it
    reported without one.
    """
    issues: list[dict[str, Any]] = []
    text = log.lower()
    for finding in _CHECK_FINDINGS:
        named: dict[str, list[str]] = {}
        for match in finding.pattern.finditer(log):
            nets = named.setdefault(match.group(1), [])
            net = match.group(2) if finding.pattern.groups > 1 else ""
            if net and net not in nets:
                nets.append(net)
        if not named:
            if any(phrase in text for phrase in finding.phrases):
                issues.append(_issue(
                    finding.severity,
                    finding.category,
                    finding.title,
                    finding.detail,
                    recommendation=finding.recommendation,
                ))
            continue
        places = [
            {
                "module": module,
                "nets": [net.lstrip("\\") for net in nets],
                **_netlist_location(netlist, module, nets[0] if nets else ""),
            }
            for module, nets in named.items()
        ]
        for place in _one_per_location(places):
            nets = place["nets"]
            where = f"module {place['module']}" + (f" ({', '.join(nets[:5])})" if nets else "")
            issues.append(_issue(
                finding.severity,
                finding.category,
                finding.title,
                f"{finding.detail} Yosys reports it in {where}.",
                path=str(place.get("source", "")),
                line=_int_or_zero(place.get("line")),
                column=_int_or_zero(place.get("column")),
                evidence={"module": place["module"], **({"net": nets[0]} if nets else {})},
                recommendation=finding.recommendation,
            ))
    return issues


def _latch_issues(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _issue(
            "warning",
            "combinational",
            "Latch inferred by Yosys precheck",
            f"Yosys inferred a latch for {latch.get('net') or 'a signal'} in module "
            f"{latch.get('module', '')}. Unintended latches make timing, reset, and "
            "implementation mapping harder to control.",
            path=str(latch.get("source", "")),
            line=_int_or_zero(latch.get("line")),
            column=_int_or_zero(latch.get("column")),
            evidence={key: latch[key] for key in ("module", "net", "cell") if latch.get(key)},
            recommendation=(
                "Assign defaults in combinational blocks and cover all control branches; "
                "keep a latch only where one is intended."
            ),
        )
        for latch in _records(metrics.get("latches"))
    ]


def _records(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


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
