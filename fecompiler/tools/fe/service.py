"""Step info service — mirrors chipcompiler/tools/ecc/service.py in ecos-studio/ecc.

Provides get_step_info() to query any resource of a completed step by ID,
without depending on any real EDA tool.
"""

from __future__ import annotations

import os
from typing import Any

from fecompiler.data.workspace import WorkspaceStep
from fecompiler.utility.json import json_read


def get_step_info(workspace: dict[str, Any],
                  step: WorkspaceStep,
                  id: str) -> dict:
    """Return resource info for *step* identified by *id*.

    Supported IDs:
        views     — layout image + json + metrics path
        layout    — layout image + json path
        metrics   — analysis metrics path
        subflow   — subflow.json path
        analysis  — all analysis / feature / report paths
        maps      — congestion / density map paths (step-dependent)
        checklist — checklist.json path
        sta       — STA report paths
    """
    match id:
        case "views":
            return _build_views(workspace, step)
        case "layout":
            return _build_layout(workspace, step)
        case "metrics":
            return _build_metrics(workspace, step)
        case "subflow":
            return _build_subflow(workspace, step)
        case "analysis":
            return _build_analysis(workspace, step)
        case "maps":
            return _build_maps(workspace, step)
        case "checklist":
            return _build_checklist(workspace, step)
        case "sta":
            return _build_sta(workspace, step)
        case _:
            return {}


# ── builders ───────────────────────────────────────────────────────────────────

def _build_views(workspace: dict, step: WorkspaceStep) -> dict:
    return {
        "image":       step.output.get("image", ""),
        "json":        step.output.get("json", ""),
        "metrics":     step.analysis.get("metrics", ""),
        "information": {},
    }


def _build_layout(workspace: dict, step: WorkspaceStep) -> dict:
    return {
        "image": step.output.get("image", ""),
        "json":  step.output.get("json", ""),
    }


def _build_metrics(workspace: dict, step: WorkspaceStep) -> dict:
    return {
        "metrics": step.analysis.get("metrics", ""),
    }


def _build_subflow(workspace: dict, step: WorkspaceStep) -> dict:
    return {
        "path": step.subflow.get("path", ""),
    }


def _build_analysis(workspace: dict, step: WorkspaceStep) -> dict:
    return {
        "metrics":      step.analysis.get("metrics", ""),
        "statis":       step.analysis.get("statis_csv", ""),
        "data summary": step.feature.get("db", ""),
        "step feature": step.feature.get("step", ""),
        "step report":  step.report.get("db", ""),
    }


def _build_maps(workspace: dict, step: WorkspaceStep) -> dict:
    """Return map info for steps that produce congestion / density maps.

    In ecc-fe steps are generic (step1…step7); the map content depends on
    what the registered tool actually writes into feature["map"].  We read
    the map JSON and return whatever is present, mirroring the ecc behaviour.
    """
    info: dict = {}
    json_data = json_read(step.feature.get("map", ""))
    if not json_data:
        return info

    # congestion maps
    json_cong = json_data.get("Congestion", {})
    if json_cong:
        json_map      = json_cong.get("map", {})
        json_overflow = json_cong.get("overflow", {})
        json_util     = json_cong.get("utilization", {})

        for kind in ("egr", "lutrudy", "rudy"):
            for axis in ("horizontal", "vertical", "union"):
                key = f"{kind}-{axis}"
                path = _csv2png(json_map.get(kind, {}).get(axis, ""))
                util_val = json_util.get(kind, {}).get("max", {}).get(axis, 0)
                avg_val  = json_util.get(kind, {}).get("top_average", {}).get(axis, 0)
                info[key] = {
                    "path": path,
                    "info": [
                        f"max utilization : {util_val}",
                        f"top average : {avg_val}",
                    ] if kind != "egr" else [""],
                }

    # density maps
    json_density = json_data.get("Density", {})
    if json_density:
        density_map = {
            "cell density":        ("cell",  "allcell_density"),
            "macro density":       ("cell",  "macro_density"),
            "stdcell density":     ("cell",  "stdcell_density"),
            "net density":         ("net",   "allnet_density"),
            "global net density":  ("net",   "global_net_density"),
            "local net density":   ("net",   "local_net_density"),
            "pin density":         ("pin",   "allcell_pin_density"),
        }
        for label, (group, field) in density_map.items():
            info[label] = {
                "path": _csv2png(json_density.get(group, {}).get(field, "")),
                "info": [],
            }

    return info


def _build_checklist(workspace: dict, step: WorkspaceStep) -> dict:
    return {
        "path": step.checklist.get("path", ""),
    }


def _build_sta(workspace: dict, step: WorkspaceStep) -> dict:
    top_module   = workspace.get("top_module", "top")
    sta_data_dir = step.data.get("sta", os.path.join(step.directory, "data", "sta"))
    sta_report   = step.report.get("sta", {})

    return {
        "timing": sta_report.get("timing", os.path.join(sta_data_dir, f"{top_module}.rpt")),
        "hold":   sta_report.get("hold",   os.path.join(sta_data_dir, f"{top_module}_hold.skew")),
        "setup":  sta_report.get("setup",  os.path.join(sta_data_dir, f"{top_module}_setup.skew")),
        "cap":    sta_report.get("cap",    os.path.join(sta_data_dir, f"{top_module}.cap")),
        "fanout": sta_report.get("fanout", os.path.join(sta_data_dir, f"{top_module}.fanout")),
        "trans":  sta_report.get("trans",  os.path.join(sta_data_dir, f"{top_module}.trans")),
    }


# ── helpers ────────────────────────────────────────────────────────────────────

def _csv2png(csv: str) -> str:
    return csv.replace(".csv", ".png")
