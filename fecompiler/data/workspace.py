"""Workspace persistence — mirrors chipcompiler/data/workspace.py in ecos-studio/ecc."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fecompiler.data.step import StateEnum
from fecompiler.allflow.builder import DEFAULT_FLOW_STEPS


# ── single-step workspace paths ────────────────────────────────────────────────

@dataclass(slots=True)
class WorkspaceStep:
    """All paths that belong to one flow step's working directory.

    Mirrors the dict returned by fecompiler/tools/fe/builder.py::build_step().
    Nested groups (config, input, output, data, …) are kept as plain dicts so the
    structure stays identical to what ecc uses — callers that already do
    ``ws_step["output"]["def"]`` continue to work unchanged.
    """

    name: str
    tool: str
    version: str
    directory: str

    config:    dict[str, str]
    input:     dict[str, str]
    output:    dict[str, str]
    data:      dict[str, str]
    feature:   dict[str, str]
    report:    dict[str, Any]   # "sta" sub-key is itself a dict
    log:       dict[str, str]
    script:    dict[str, str]
    analysis:  dict[str, str]
    subflow:   dict[str, Any]
    checklist: dict[str, Any]

    def __getitem__(self, key: str) -> Any:          # keep dict-style access working
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            return default


# ── workspace creation input ───────────────────────────────────────────────────

@dataclass(slots=True)
class CreateWorkspaceData:
    directory: str
    parameters: dict[str, Any] = field(default_factory=dict)
    origin_def: str = ""
    origin_verilog: str = ""
    filelist: str = ""
    rtl_list: list[str] = field(default_factory=list)

    @property
    def project_dir(self) -> Path:
        return Path(self.directory).expanduser().resolve()

    @property
    def design_name(self) -> str:
        raw = str(self.parameters.get("Design", "")).strip()
        return raw if raw else (self.project_dir.name or "New_Chip_Design")


# ── public API ─────────────────────────────────────────────────────────────────

def create_workspace(spec: CreateWorkspaceData) -> dict[str, Any]:
    """Create workspace directory structure and return workspace info dict.

    Directory layout (mirrors ecc):
        <workspace>/
          origin/          ← original RTL / DEF / filelist / SDC
          log/             ← server log
          home/
            parameters.json
            flow.json
            home.json
    """
    project_dir = spec.project_dir
    project_dir.mkdir(parents=True, exist_ok=True)

    for d in ("origin", "log", "home"):
        (project_dir / d).mkdir(exist_ok=True)

    parameters = _build_parameters(spec)
    _prepare_origin(project_dir, spec, parameters)
    _write_home_files(project_dir, parameters)

    return load_workspace(str(project_dir))


def load_workspace(directory: str) -> dict[str, Any] | None:
    """Load an existing workspace from disk."""
    project_dir = Path(directory).expanduser().resolve()
    if not project_dir.exists():
        return None

    params_path  = project_dir / "home" / "parameters.json"
    flow_path    = project_dir / "home" / "flow.json"
    home_path    = project_dir / "home" / "home.json"
    origin_dir   = project_dir / "origin"

    parameters   = _read_json(params_path)
    design       = str(parameters.get("Design", project_dir.name))
    top_module   = str(parameters.get("Top module", "top"))

    origin_def     = _pick_first(origin_dir, [".def", ".def.gz"]) or str(origin_dir / f"{design}.def")
    origin_verilog = _pick_first(origin_dir, [".v", ".v.gz"])     or str(origin_dir / f"{design}.v")

    # filelist: prefer path stored in parameters, then scan origin/
    filelist = str(parameters.get("input_filelist", "")).strip()
    if not filelist or not Path(filelist).exists():
        filelist = _pick_first(origin_dir, [".f", ".fl", ".filelist"]) or ""

    return {
        "directory":       str(project_dir),
        "design":          design,
        "top_module":      top_module,
        "parameters_path": str(params_path),
        "flow_path":       str(flow_path),
        "home_path":       str(home_path),
        "origin_def":      origin_def,
        "origin_verilog":  origin_verilog,
        "input_filelist":  filelist,
    }


def load_flow(flow_path: Path) -> dict[str, Any]:
    return _read_json(flow_path)


def save_flow(flow_path: Path, flow: dict[str, Any]) -> None:
    _write_json(flow_path, flow)


# ── private helpers ────────────────────────────────────────────────────────────

def _build_parameters(spec: CreateWorkspaceData) -> dict[str, Any]:
    params = dict(spec.parameters)
    params.setdefault("Design",              spec.design_name)
    params.setdefault("Top module",          "top")
    params.setdefault("Clock",               "clk")
    params.setdefault("Frequency max [MHz]", 100)
    # NOTE: input_filelist is NOT stored in parameters.json
    # load_workspace() discovers it by scanning origin/ for .f files,
    # which contain absolute paths after _copy_filelist_sources().
    return params


def _prepare_origin(project_dir: Path, spec: CreateWorkspaceData, parameters: dict[str, Any]) -> None:
    design     = parameters["Design"]
    top_module = parameters["Top module"]
    origin_dir = project_dir / "origin"

    # DEF
    _copy_or_touch(
        src=spec.origin_def,
        dst=origin_dir / (Path(spec.origin_def).name if spec.origin_def else f"{design}.def"),
        placeholder=f"# placeholder DEF for {design}\n",
    )

    # Verilog (skip if filelist provided)
    if spec.origin_verilog or not spec.filelist:
        _copy_or_touch(
            src=spec.origin_verilog,
            dst=origin_dir / (Path(spec.origin_verilog).name if spec.origin_verilog else f"{design}.v"),
            placeholder=f"module {top_module}(); endmodule\n",
        )

    # rtl_list → copy files + write filelist
    if spec.rtl_list:
        copied = _copy_rtl_list(origin_dir, spec.rtl_list)
        if copied:
            (origin_dir / "filelist").write_text("\n".join(copied) + "\n", encoding="utf-8")
    elif spec.filelist and os.path.isfile(spec.filelist):
        _copy_filelist_sources(origin_dir, spec.filelist)

    # SDC
    sdc = origin_dir / f"{design}.sdc"
    if not sdc.exists():
        freq = parameters.get("Frequency max [MHz]", 100)
        clk  = parameters.get("Clock", "clk")
        sdc.write_text(
            f"# Auto-generated SDC\n"
            f"set clk_name {clk}\n"
            f"set clk_port_name {clk}\n"
            f"set clk_freq_mhz {freq}\n"
            f"set clk_period [expr 1000.0 / $clk_freq_mhz]\n"
            f"set clk_io_pct 0.2\n"
            f"set clk_port [get_ports $clk_port_name]\n"
            f"create_clock -name $clk_name -period $clk_period $clk_port\n",
            encoding="utf-8",
        )


def _write_home_files(project_dir: Path, parameters: dict[str, Any]) -> None:
    home_dir       = project_dir / "home"
    flow_path      = home_dir / "flow.json"
    params_path    = home_dir / "parameters.json"
    home_path      = home_dir / "home.json"

    # flow.json — one entry per default step
    _write_json(flow_path, {
        "steps": [
            {
                "name":              name,
                "tool":              tool,
                "state":             StateEnum.Unstart.value,
                "runtime":           "",
                "peak memory (mb)":  0,
                "info":              {},
            }
            for name, tool in DEFAULT_FLOW_STEPS
        ]
    })

    # parameters.json
    _write_json(params_path, parameters)

    # home.json — index file pointing to the other two
    _write_json(home_path, {
        "parameters": str(params_path),
        "flow":       str(flow_path),
        "layout":     "",
        "metrics":    {},
        "monitor": {
            "step": [], "memory": [], "runtime": [], "instance": [], "frequency": [],
        },
    })


def _copy_filelist_sources(origin_dir: Path, filelist_path: str) -> None:
    """Parse filelist, copy every .v/.sv to origin/, write new filelist with absolute paths."""
    src_filelist = Path(filelist_path).expanduser().resolve()
    base_dir     = src_filelist.parent
    copied: list[str] = []

    for raw in src_filelist.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if not (line.endswith(".v") or line.endswith(".sv")):
            continue
        src = Path(line) if Path(line).is_absolute() else base_dir / line
        src = src.resolve()
        if not src.is_file():
            continue
        dst = origin_dir / src.name
        try:
            shutil.copy2(src, dst)
            copied.append(str(dst))   # absolute path in origin/
        except OSError:
            pass

    # also copy the filelist itself
    try:
        shutil.copy2(src_filelist, origin_dir / src_filelist.name)
    except OSError:
        pass

    # write a clean filelist in origin/ with absolute paths
    if copied:
        (origin_dir / src_filelist.name).write_text(
            "\n".join(copied) + "\n", encoding="utf-8"
        )


def _copy_rtl_list(origin_dir: Path, rtl_list: list[str]) -> list[str]:
    names: list[str] = []
    for item in rtl_list:
        src = Path(item).expanduser().resolve()
        if not src.is_file():
            continue
        dst = origin_dir / src.name
        try:
            shutil.copy2(src, dst)
            names.append(src.name)
        except OSError:
            pass
    return names


def _copy_or_touch(src: str, dst: Path, placeholder: str) -> None:
    if src:
        sp = Path(src).expanduser().resolve()
        if sp.is_file():
            try:
                shutil.copy2(sp, dst)
                return
            except OSError:
                pass
    dst.write_text(placeholder, encoding="utf-8")


def _pick_first(origin_dir: Path, suffixes: list[str]) -> str | None:
    for suffix in suffixes:
        matches = sorted(origin_dir.glob(f"*{suffix}"))
        if matches:
            return str(matches[0])
    return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
