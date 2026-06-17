"""Lightweight static RTL review analyzer.

The first implementation intentionally avoids external synthesis tools.  It
builds a structured report from source text so ECOS Studio can provide useful
IC/FPGA readiness feedback even before Verilator/Yosys/OpenSTA are wired in.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SOURCE_EXTENSIONS = {".v", ".sv", ".vh", ".svh"}
_KEYWORDS = {
    "always",
    "always_comb",
    "always_ff",
    "assign",
    "begin",
    "case",
    "casex",
    "casez",
    "default",
    "else",
    "end",
    "endcase",
    "endmodule",
    "if",
    "input",
    "logic",
    "module",
    "negedge",
    "or",
    "output",
    "posedge",
    "reg",
    "wire",
}


@dataclass(slots=True)
class SourceFile:
    path: Path
    text: str


def build_rtl_review(workspace: dict[str, Any]) -> dict[str, Any]:
    """Return a structured RTL review report for the workspace."""
    sources = _load_sources(workspace)
    metrics = _metrics(sources, workspace)
    issues = _issues(sources, metrics, workspace)
    summary = _summary(issues, metrics)

    return {
        "schema_version": 1,
        "title": "RTL Review Center",
        "scope": "cpu",
        "profiles": ["IC", "FPGA"],
        "summary": summary,
        "metrics": metrics,
        "issues": issues,
        "source_files": [
            {
                "path": str(source.path),
                "label": _source_label(source.path, workspace),
                "lines": _line_count(source.text),
            }
            for source in sources
        ],
        "structural_probe": {},
        "yosys_precheck": {},
        "next_analyzers": [
            "Verilator SARIF diagnostics",
            "Yosys logic depth/fanout probe",
            "OpenSTA constraint/timing checks",
            "CDC/RDC structural checks",
            "VCD toggle and power hints",
        ],
    }


def merge_structural_probe(report: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    """Return *report* with structural probe data folded into summary/issues."""
    if not probe:
        return report

    merged = dict(report)
    merged["structural_probe"] = probe
    merged["yosys_precheck"] = probe

    issues = list(merged.get("issues", []))
    issues.extend(_normalize_probe_issue(issue) for issue in probe.get("issues", []) if isinstance(issue, dict))
    merged["issues"] = sorted(issues, key=_issue_sort_key)

    metrics = dict(merged.get("metrics", {}))
    probe_metrics = probe.get("metrics", {})
    if isinstance(probe_metrics, dict):
        metrics["structural"] = probe_metrics
    merged["metrics"] = metrics

    summary = _summary(merged["issues"], metrics)
    precheck_summary = {
        "status": probe.get("status", ""),
        "tool": probe.get("tool", ""),
        "reason": probe.get("reason", ""),
        "cells": probe_metrics.get("cells", 0) if isinstance(probe_metrics, dict) else 0,
        "wires": probe_metrics.get("wires", 0) if isinstance(probe_metrics, dict) else 0,
        "modules": probe_metrics.get("modules", 0) if isinstance(probe_metrics, dict) else 0,
    }
    summary["structural_probe"] = precheck_summary
    summary["yosys_precheck"] = precheck_summary
    merged["summary"] = summary
    return merged


def _load_sources(workspace: dict[str, Any]) -> list[SourceFile]:
    out: list[SourceFile] = []
    seen: set[str] = set()
    for raw in _candidate_rtl_files(workspace):
        path = Path(str(raw)).expanduser().resolve()
        if path.suffix.lower() not in _SOURCE_EXTENSIONS or not path.is_file():
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        out.append(SourceFile(path=path, text=text))
    return out


def _candidate_rtl_files(workspace: dict[str, Any]) -> list[str]:
    """Return RTL sources that belong to the user's CPU, not the SoC harness.

    A frontend workspace often has a prepared merged filelist that contains both
    CPU RTL and SoC wrapper/harness RTL.  Review must ignore the harness side so
    users are not blamed for integration collateral.
    """
    cpu_filelist = str(workspace.get("cpu_filelist", "")).strip()
    if cpu_filelist:
        return _filelist_rtl_files(cpu_filelist)

    # Legacy/non-catalog workspaces may only provide one filelist or one RTL
    # source.  Use these only when there is no explicit SoC filelist that would
    # make the input ambiguous.
    if not str(workspace.get("soc_filelist", "")).strip():
        input_filelist = str(workspace.get("input_filelist", "")).strip()
        if input_filelist:
            return _filelist_rtl_files(input_filelist)

        origin_verilog = str(workspace.get("origin_verilog", "")).strip()
        if origin_verilog:
            return [origin_verilog]

    return []


def _filelist_rtl_files(filelist: str) -> list[str]:
    collected: list[str] = []
    seen: set[str] = set()
    try:
        from fecompiler.tools.prepare.runner import PrepareStep

        parsed = PrepareStep._parse_sv_filelist(filelist)
    except Exception:
        return collected

    for raw in parsed.get("rtl_files", []):
        text = str(raw)
        if text in seen:
            continue
        seen.add(text)
        collected.append(text)
    return collected


def _metrics(sources: list[SourceFile], workspace: dict[str, Any]) -> dict[str, Any]:
    module_names: list[str] = []
    always_blocks = 0
    sequential_blocks = 0
    combinational_blocks = 0
    assign_count = 0
    case_count = 0
    reset_refs = 0
    clock_refs = 0
    signal_refs: Counter[str] = Counter()

    for source in sources:
        text = _strip_comments(source.text)
        module_names.extend(match.group(1) for match in re.finditer(r"\bmodule\s+([A-Za-z_]\w*)", text))
        always_blocks += len(re.findall(r"\balways(?:_[a-z]+)?\b", text))
        sequential_blocks += len(re.findall(r"\balways(?:_ff)?\s*@?\s*\([^)]*(?:posedge|negedge)[^)]*\)", text))
        combinational_blocks += len(re.findall(r"\balways_(?:comb|latch)\b|\balways\s*@\s*\*", text))
        assign_count += len(re.findall(r"\bassign\b", text))
        case_count += len(re.findall(r"\bcase[zx]?\b", text))
        reset_refs += len(re.findall(r"\b(?:rst|reset|areset|resetn|rst_n|reset_n)\w*\b", text, flags=re.I))
        clock_refs += len(re.findall(r"\b(?:clk|clock|aclk)\w*\b", text, flags=re.I))
        signal_refs.update(_identifiers(text))

    hot_signals = [
        {"name": name, "references": count}
        for name, count in signal_refs.most_common(12)
        if name not in _KEYWORDS and count >= 12
    ]

    return {
        "design": str(workspace.get("design", "")),
        "top_module": str(workspace.get("top_module", "")),
        "source_files": len(sources),
        "total_lines": sum(_line_count(source.text) for source in sources),
        "modules": len(set(module_names)),
        "module_names": sorted(set(module_names))[:80],
        "always_blocks": always_blocks,
        "sequential_blocks": sequential_blocks,
        "combinational_blocks": combinational_blocks,
        "continuous_assigns": assign_count,
        "case_statements": case_count,
        "clock_references": clock_refs,
        "reset_references": reset_refs,
        "hot_signal_references": hot_signals,
    }


def _issues(
    sources: list[SourceFile],
    metrics: dict[str, Any],
    workspace: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not sources:
        issues.append(_issue(
            "error",
            ["IC", "FPGA"],
            "input",
            "No RTL source files found",
            "Provide a valid CPU filelist so RTL Review can inspect the user CPU code.",
            recommendation="Check cpu_filelist. SoC filelist and prepared merged filelist are intentionally ignored by RTL Review.",
        ))
        return issues

    for source in sources:
        _scan_source(source, issues)

    _design_level_issues(issues, metrics, workspace)
    return sorted(issues, key=_issue_sort_key)


def _scan_source(source: SourceFile, issues: list[dict[str, Any]]) -> None:
    lines = source.text.splitlines()
    stripped_text = _strip_comments(source.text)

    for idx, line in enumerate(lines, start=1):
        clean = line.strip()
        if not clean:
            continue
        lower = clean.lower()
        if re.search(r"\bassign\s+\w*clk\w*\s*=", clean, flags=re.I):
            issues.append(_issue(
                "error",
                ["IC", "FPGA"],
                "clock",
                "Clock generated by RTL logic",
                "Generated or gated clocks make STA, CDC, and FPGA clock routing fragile.",
                source.path,
                idx,
                recommendation="Use clock-enable logic, or route generated clocks through explicit clocking resources and constraints.",
            ))
        if re.search(r"\b(?:clk|clock)\w*\s*=", clean, flags=re.I) and "assign" not in lower and "<=" in clean:
            issues.append(_issue(
                "warning",
                ["IC", "FPGA"],
                "clock",
                "Clock-like signal assigned in sequential logic",
                "Clock-like nets should not be treated as ordinary data without a clear generated-clock plan.",
                source.path,
                idx,
                recommendation="Review whether this is really a clock; prefer clock enables for datapath control.",
            ))
        if re.search(r"\bif\s*\([^)]*(?:clk|clock)[^)]*\)", clean, flags=re.I):
            issues.append(_issue(
                "warning",
                ["IC", "FPGA"],
                "clock",
                "Clock used in data condition",
                "Clock signals used as boolean data can hide CDC or generated-clock intent.",
                source.path,
                idx,
                recommendation="Keep clocks in event controls and constraints; avoid using clocks as datapath conditions.",
            ))
        if re.search(r"\b(posedge|negedge)\s+.*\b(posedge|negedge)\b", clean):
            issues.append(_issue(
                "warning",
                ["IC", "FPGA"],
                "reset",
                "Sequential block has multiple edge controls",
                "Async reset is legal, but reset release must be synchronized and constrained.",
                source.path,
                idx,
                recommendation="Document async reset intent, synchronize reset release per clock domain, and add RDC checks.",
            ))
        if re.search(r"\b(?:rst|reset)\w*\s*(?:&|\||\^)", clean, flags=re.I):
            issues.append(_issue(
                "warning",
                ["IC", "FPGA"],
                "reset",
                "Reset participates in combinational logic",
                "Reset gating can create recovery/removal and RDC risks.",
                source.path,
                idx,
                recommendation="Prefer a clean reset tree and local synchronized reset release.",
            ))
        if re.search(r"\balways\s*@\s*\*", clean) and idx <= len(lines):
            block = "\n".join(lines[idx - 1:min(idx + 28, len(lines))])
            if re.search(r"\bif\s*\(", block) and not re.search(r"\belse\b", block):
                issues.append(_issue(
                    "warning",
                    ["IC", "FPGA"],
                    "combinational",
                    "Combinational if without visible else",
                    "Incomplete combinational assignments may infer latches or create stale values.",
                    source.path,
                    idx,
                    recommendation="Assign defaults at block entry or cover all branches with else.",
                ))
        if re.search(r"\bcase[zx]?\s*\(", clean):
            block = "\n".join(lines[idx - 1:min(idx + 60, len(lines))])
            if "default" not in block:
                issues.append(_issue(
                    "warning",
                    ["IC", "FPGA"],
                    "state-machine",
                    "Case statement lacks a visible default branch",
                    "Uncovered cases can infer latches, trap FSMs, or hide illegal states.",
                    source.path,
                    idx,
                    recommendation="Add an explicit default branch or prove full coverage with unique/priority semantics.",
                ))
        if _operator_count(clean) >= 10:
            issues.append(_issue(
                "info",
                ["IC", "FPGA"],
                "timing",
                "Dense expression may create a long combinational path",
                "Many operators on one line often mean a deep logic cone.",
                source.path,
                idx,
                evidence={"operators": _operator_count(clean)},
                recommendation="Consider staging this expression or splitting the control/data cones.",
            ))
        if len(clean) > 180:
            issues.append(_issue(
                "info",
                ["IC", "FPGA"],
                "style",
                "Very long RTL line",
                "Long lines are harder to review and often hide wide mux or arithmetic logic.",
                source.path,
                idx,
                evidence={"characters": len(clean)},
                recommendation="Break the expression into named intermediate signals.",
            ))

    for assign_match in re.finditer(r"\bassign\s+[^=]+=\s*([^;]+);", stripped_text, flags=re.S):
        rhs = assign_match.group(1)
        line = stripped_text.count("\n", 0, assign_match.start()) + 1
        condition_count = rhs.count("?")
        if condition_count >= 3:
            issues.append(_issue(
                "warning",
                ["IC", "FPGA"],
                "timing",
                "Nested ternary mux chain",
                "Deep mux chains can dominate timing and FPGA LUT levels.",
                source.path,
                line,
                evidence={"ternary_levels": condition_count},
                recommendation="Review mux structure; consider one-hot selects, staged muxing, or registered boundaries.",
            ))
        if _operator_count(rhs) >= 18:
            issues.append(_issue(
                "warning",
                ["IC", "FPGA"],
                "timing",
                "Large continuous assignment cone",
                "A wide assign expression may become a long critical path.",
                source.path,
                line,
                evidence={"operators": _operator_count(rhs)},
                recommendation="Pipeline or factor the expression if it sits on a clock-to-clock path.",
            ))


def _design_level_issues(
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
    workspace: dict[str, Any],
) -> None:
    frequency = _float_or_zero(workspace.get("Frequency max [MHz]") or workspace.get("frequency_max"))
    if frequency >= 200 and int(metrics.get("combinational_blocks", 0)) > int(metrics.get("sequential_blocks", 0)) * 2:
        issues.append(_issue(
            "warning",
            ["IC", "FPGA"],
            "timing",
            "High target frequency with many combinational blocks",
            "The RTL shape suggests limited pipeline boundaries relative to combinational logic.",
            evidence={
                "target_mhz": frequency,
                "combinational_blocks": metrics.get("combinational_blocks", 0),
                "sequential_blocks": metrics.get("sequential_blocks", 0),
            },
            recommendation="Inspect top timing cones and consider adding pipeline stages before synthesis.",
        ))
    if int(metrics.get("reset_references", 0)) > max(80, int(metrics.get("sequential_blocks", 0)) * 4):
        issues.append(_issue(
            "warning",
            ["IC", "FPGA"],
            "reset",
            "Reset appears heavily distributed",
            "High reset fanout hurts routing, recovery/removal timing, and FPGA retiming.",
            evidence={"reset_references": metrics.get("reset_references", 0)},
            recommendation="Reset only architecturally required state; prefer local reset synchronization.",
        ))
    if int(metrics.get("clock_references", 0)) > max(80, int(metrics.get("sequential_blocks", 0)) * 5):
        issues.append(_issue(
            "info",
            ["IC", "FPGA"],
            "clock",
            "Clock references are widespread",
            "Large clock/control reach should be checked against clock domain and generated-clock intent.",
            evidence={"clock_references": metrics.get("clock_references", 0)},
            recommendation="Group clock domains and verify every generated or derived clock has a constraint.",
        ))
    for hot in metrics.get("hot_signal_references", [])[:6]:
        references = int(hot.get("references", 0))
        if references < 24:
            continue
        name = str(hot.get("name", ""))
        category = "reset" if re.search(r"rst|reset", name, flags=re.I) else "fanout"
        issues.append(_issue(
            "info",
            ["IC", "FPGA"],
            category,
            f"High-reference signal candidate: {name}",
            "A heavily referenced control signal may become a high-fanout net after elaboration.",
            evidence=hot,
            recommendation="After elaboration/synthesis, confirm real fanout and insert buffering/replication if needed.",
        ))


def _summary(issues: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    severity_counts = Counter(str(issue.get("severity", "info")) for issue in issues)
    profile_counts: dict[str, int] = {"IC": 0, "FPGA": 0}
    category_counts: Counter[str] = Counter()
    for issue in issues:
        category_counts[str(issue.get("category", "other"))] += 1
        profiles = issue.get("profiles", [])
        if isinstance(profiles, list):
            for profile in profiles:
                text = str(profile)
                if text in profile_counts:
                    profile_counts[text] += 1

    return {
        "status": "needs_attention" if severity_counts["error"] or severity_counts["warning"] else "clean",
        "errors": severity_counts["error"],
        "warnings": severity_counts["warning"],
        "infos": severity_counts["info"],
        "total_issues": len(issues),
        "profile_counts": profile_counts,
        "category_counts": dict(sorted(category_counts.items())),
        "source_files": metrics.get("source_files", 0),
        "modules": metrics.get("modules", 0),
        "total_lines": metrics.get("total_lines", 0),
    }


def _issue(
    severity: str,
    profiles: list[str],
    category: str,
    title: str,
    detail: str,
    path: Path | None = None,
    line: int | None = None,
    *,
    evidence: dict[str, Any] | None = None,
    recommendation: str = "",
) -> dict[str, Any]:
    return {
        "severity": severity,
        "profiles": profiles,
        "category": category,
        "title": title,
        "detail": detail,
        "source": str(path) if path else "",
        "line": line or 0,
        "column": 1 if line else 0,
        "evidence": evidence or {},
        "recommendation": recommendation,
    }


def _normalize_probe_issue(issue: dict[str, Any]) -> dict[str, Any]:
    severity = str(issue.get("severity", "info"))
    if severity not in {"error", "warning", "info"}:
        severity = "info"
    profiles = issue.get("profiles", ["IC", "FPGA"])
    if not isinstance(profiles, list) or not profiles:
        profiles = ["IC", "FPGA"]
    return {
        "severity": severity,
        "profiles": [str(profile) for profile in profiles],
        "category": str(issue.get("category", "structural")),
        "title": str(issue.get("title", "Yosys precheck issue")),
        "detail": str(issue.get("detail", "")),
        "source": str(issue.get("source", "")),
        "line": int(issue.get("line", 0) or 0),
        "column": int(issue.get("column", 0) or 0),
        "evidence": issue.get("evidence", {}) if isinstance(issue.get("evidence", {}), dict) else {},
        "recommendation": str(issue.get("recommendation", "")),
    }


def _issue_sort_key(issue: dict[str, Any]) -> tuple[int, str, str, str, int]:
    severity_rank = {"error": 0, "warning": 1, "info": 2}
    return (
        severity_rank.get(str(issue.get("severity", "info")), 3),
        str(issue.get("category", "")),
        str(issue.get("source", "")),
        int(issue.get("line", 0) or 0),
        len(str(issue.get("title", ""))),
    )


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


def _identifiers(text: str) -> list[str]:
    return [item for item in re.findall(r"\b[A-Za-z_]\w*\b", text) if item not in _KEYWORDS]


def _operator_count(text: str) -> int:
    return len(re.findall(r"&&|\|\||==|!=|<=|>=|<<|>>|[+\-*/%&|^~?:<>]", text))


def _line_count(text: str) -> int:
    return len(text.splitlines())


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _source_label(path: Path, workspace: dict[str, Any]) -> str:
    root = str(workspace.get("cpu_filelist", "")).strip()
    if root:
        try:
            rel = path.relative_to(Path(root).expanduser().resolve().parent)
            return f"CPU RTL · {rel.as_posix()}"
        except ValueError:
            pass
    return path.name
