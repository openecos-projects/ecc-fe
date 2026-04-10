"""Step workspace builder aligned with ecos-studio path style."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...flow_spec import sanitize_step_token


def build_step(
    *,
    workspace: dict[str, Any],
    step_name: str,
    tool: str,
    input_def: str,
    input_verilog: str,
    output_def: str | None = None,
    output_verilog: str | None = None,
    output_gds: str | None = None,
) -> dict[str, Any]:
    design = workspace["design"]
    top_module = workspace.get("top_module", "top")
    step_token = sanitize_step_token(step_name)
    step_dir = Path(workspace["directory"]) / f"{step_name}_{tool}"

    if output_def is None:
        output_def = str(step_dir / "output" / f"{design}_{step_token}.def.gz")
    if output_verilog is None:
        output_verilog = str(step_dir / "output" / f"{design}_{step_token}.v")
    if output_gds is None:
        output_gds = str(step_dir / "output" / f"{design}_{step_token}.gds")

    return {
        "name": step_name,
        "tool": tool,
        "version": "0.1",
        "directory": str(step_dir),
        "config": {
            "dir": str(step_dir / "config"),
            "flow": str(step_dir / "config" / "flow_config.json"),
            "db": str(step_dir / "config" / "db_default_config.json"),
        },
        "input": {
            "def": input_def,
            "verilog": input_verilog,
        },
        "output": {
            "dir": str(step_dir / "output"),
            "def": output_def,
            "verilog": output_verilog,
            "gds": output_gds,
            "image": str(step_dir / "output" / f"{design}_{step_token}.png"),
            "json": str(step_dir / "output" / f"{design}_{step_token}.json"),
        },
        "data": {
            "dir": str(step_dir / "data"),
            "fp": str(step_dir / "data" / "fp"),
            "pnp": str(step_dir / "data" / "pnp"),
            "pl": str(step_dir / "data" / "pl"),
            "cts": str(step_dir / "data" / "cts"),
            "no": str(step_dir / "data" / "no"),
            "to": str(step_dir / "data" / "to"),
            "rt": str(step_dir / "data" / "rt"),
            "sta": str(step_dir / "data" / "sta"),
            "drc": str(step_dir / "data" / "drc"),
        },
        "feature": {
            "dir": str(step_dir / "feature"),
            "db": str(step_dir / "feature" / f"{step_token}.db.json"),
            "step": str(step_dir / "feature" / f"{step_token}.step.json"),
            "map": str(step_dir / "feature" / f"{step_token}.map.json"),
            "timing": str(step_dir / "data" / "sta" / f"{top_module}.rpt.json"),
        },
        "report": {
            "dir": str(step_dir / "report"),
            "db": str(step_dir / "report" / f"{step_token}.db.rpt"),
            "step": str(step_dir / "report" / f"{step_token}.rpt"),
            "sta": {
                "timing": str(step_dir / "data" / "sta" / f"{top_module}.rpt"),
                "hold": str(step_dir / "data" / "sta" / f"{top_module}_hold.skew"),
                "setup": str(step_dir / "data" / "sta" / f"{top_module}_setup.skew"),
                "cap": str(step_dir / "data" / "sta" / f"{top_module}.cap"),
                "fanout": str(step_dir / "data" / "sta" / f"{top_module}.fanout"),
                "trans": str(step_dir / "data" / "sta" / f"{top_module}.trans"),
            },
        },
        "log": {
            "dir": str(step_dir / "log"),
            "file": str(step_dir / "log" / f"{step_token}.log"),
        },
        "script": {
            "dir": str(step_dir / "script"),
            "main": str(step_dir / "script" / f"{step_token}_main.tcl"),
        },
        "analysis": {
            "dir": str(step_dir / "analysis"),
            "metrics": str(step_dir / "analysis" / f"{step_token}_metrics.json"),
            "statis_csv": str(step_dir / "analysis" / f"{step_token}_statis.csv"),
        },
        "subflow": {
            "path": str(step_dir / "subflow.json"),
            "steps": [],
        },
        "checklist": {
            "path": str(step_dir / "checklist.json"),
            "checklist": [],
        },
    }


def build_step_space(step: dict[str, Any]) -> None:
    step_dir = Path(step["directory"])
    step_dir.mkdir(parents=True, exist_ok=True)

    dirs = [
        step["config"]["dir"],
        step["output"]["dir"],
        step["data"]["dir"],
        step["feature"]["dir"],
        step["report"]["dir"],
        step["log"]["dir"],
        step["script"]["dir"],
        step["analysis"]["dir"],
    ]
    for data_dir in step["data"].values():
        dirs.append(data_dir)

    for path in dirs:
        Path(path).mkdir(parents=True, exist_ok=True)

    pl_dir = Path(step["data"]["pl"])
    for sub in ("density", "gui", "log", "plot", "report"):
        (pl_dir / sub).mkdir(parents=True, exist_ok=True)


def build_step_config(step: dict[str, Any]) -> None:
    config_flow = Path(step["config"]["flow"])
    config_db = Path(step["config"]["db"])
    if not config_flow.exists():
        config_flow.write_text(json.dumps({"step": step["name"]}, indent=2), encoding="utf-8")
    if not config_db.exists():
        config_db.write_text(json.dumps({"step": step["name"]}, indent=2), encoding="utf-8")

    subflow = Path(step["subflow"]["path"])
    if not subflow.exists():
        subflow.write_text(json.dumps({"steps": []}, indent=2), encoding="utf-8")

    checklist = Path(step["checklist"]["path"])
    if not checklist.exists():
        checklist.write_text(json.dumps({"checklist": []}, indent=2), encoding="utf-8")
