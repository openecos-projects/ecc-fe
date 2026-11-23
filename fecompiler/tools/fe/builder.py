"""Step workspace builder — mirrors chipcompiler/tools/ecc/builder.py in ecos-studio/ecc."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fecompiler.data.workspace import WorkspaceStep
from fecompiler.utility.atomic_write import atomic_write_json


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
) -> WorkspaceStep:
    """Return a WorkspaceStep for *step_name* inside *workspace*."""
    design = workspace["design"]
    step_dir = Path(workspace["directory"]) / f"{step_name}_{tool}"
    sd = str(step_dir)

    if output_def is None:
        output_def = f"{sd}/output/{design}_{step_name}.def.gz"
    if output_verilog is None:
        output_verilog = f"{sd}/output/{design}_{step_name}.v"
    if output_gds is None:
        output_gds = f"{sd}/output/{design}_{step_name}.gds"

    return WorkspaceStep(
        name=step_name,
        tool=tool,
        version="0.1",
        directory=sd,
        config={
            "dir": f"{sd}/config",
            "flow": f"{sd}/config/flow_config.json",
        },
        input={
            "def":     input_def,
            "verilog": input_verilog,
        },
        output={
            "dir":     f"{sd}/output",
            "def":     output_def,
            "verilog": output_verilog,
            "gds":     output_gds,
            "image":   f"{sd}/output/{design}_{step_name}.png",
            "json":    f"{sd}/output/{design}_{step_name}.json",
        },
        data={
            "dir": f"{sd}/data",
        },
        feature={
            "dir": f"{sd}/feature",
            "step": f"{sd}/feature/{step_name}.step.json",
        },
        report={
            "dir": f"{sd}/report",
            "step": f"{sd}/report/{step_name}.rpt",
        },
        log={
            "dir":  f"{sd}/log",
            "file": f"{sd}/log/log.txt",
        },
        script={
            "dir":  f"{sd}/script",
            "main": f"{sd}/script/{step_name}_main.tcl",
        },
        analysis={
            "dir":        f"{sd}/analysis",
            "metrics":    f"{sd}/analysis/{step_name}_metrics.json",
            "qor_metrics": f"{sd}/analysis/qor_metrics.json",
            "qor_summary": f"{sd}/analysis/qor_summary.json",
            "qor_hotspots": f"{sd}/analysis/qor_hotspots.json",
            "statis_csv": f"{sd}/analysis/{step_name}_statis.csv",
        },
        subflow={
            "path":  f"{sd}/subflow.json",
            "steps": [],
        },
        checklist={
            "path":      f"{sd}/checklist.json",
            "checklist": [],
        },
    )


def build_step_space(step: dict[str, Any]) -> None:
    """Create all directories for *step* on disk."""
    for section in ("config", "output", "data", "feature", "report", "log", "script", "analysis"):
        d = step.get(section, {}).get("dir", "")
        if d:
            os.makedirs(d, exist_ok=True)


def build_step_config(step: dict[str, Any]) -> None:
    """Create the subflow and checklist files of a step that has none yet.

    Existing files hold the stages recorded by the step's last execution and
    are left untouched, so loading a workspace never erases them.
    """
    for path, initial in _initial_step_config(Path(step["subflow"]["path"]).parent):
        if not path.exists():
            _write_json(str(path), initial)


def reset_step_config(step_dir: Path) -> None:
    """Discard the recorded stages of a step whose result was invalidated."""
    if not step_dir.is_dir():
        return
    for path, initial in _initial_step_config(step_dir):
        _write_json(str(path), initial)


def _initial_step_config(step_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    checklist = step_dir / "checklist.json"
    return [
        (step_dir / "subflow.json", {"steps": []}),
        (checklist, {"path": str(checklist), "checklist": []}),
    ]


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_json(path: str, data: dict) -> None:
    atomic_write_json(path, data)
