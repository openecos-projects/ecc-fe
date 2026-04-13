"""Workspace persistence aligned with ecos-studio directory conventions."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..flow_spec import DEFAULT_FLOW_STEPS
from ..schemas.ecc import CreateWorkspaceData, StateEnum

WORKSPACE_DIRS = ("log", "origin", "home")

HOME_TEMPLATE = {
    "parameters": "",
    "flow": "",
    "layout": "",
    "GDS merge": "",
    "checklist": "",
    "metrics": {},
    "monitor": {
        "step": [],
        "memory": [],
        "runtime": [],
        "instance": [],
        "frequency": [],
    },
}


def create_workspace(spec: CreateWorkspaceData) -> dict[str, Any]:
    project_dir = spec.project_dir
    project_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_legacy_step_dirs(project_dir)

    for dirname in WORKSPACE_DIRS:
        (project_dir / dirname).mkdir(parents=True, exist_ok=True)

    parameters = _build_parameters(spec)
    copied_origin = _prepare_origin(project_dir=project_dir, spec=spec, parameters=parameters)
    files = _write_home_files(project_dir=project_dir, parameters=parameters)
    _init_project_log(project_dir / "log", parameters["Design"])

    return {
        "directory": str(project_dir),
        "design": parameters["Design"],
        "origin": copied_origin,
        "files": files,
    }


def load_workspace(directory: str) -> dict[str, Any] | None:
    project_dir = Path(directory).expanduser().resolve()
    if not project_dir.exists():
        return None

    params_path = project_dir / "home" / "parameters.json"
    flow_path = project_dir / "home" / "flow.json"
    home_path = project_dir / "home" / "home.json"
    checklist_path = project_dir / "home" / "checklist.json"
    origin_dir = project_dir / "origin"

    parameters = _read_json(params_path)
    design = str(parameters.get("Design", project_dir.name))
    top_module = str(parameters.get("Top module", "top"))

    origin_def = _pick_first(origin_dir, [".def", ".def.gz"]) or str(origin_dir / f"{design}.def")
    origin_verilog = _pick_first(origin_dir, [".v", ".v.gz"]) or str(origin_dir / f"{design}.v")

    # Prefer the original filelist path stored in parameters (preserves relative .v paths),
    # fall back to any filelist file found in origin/.
    filelist = str(parameters.get("input_filelist", "")).strip()
    if not filelist or not Path(filelist).exists():
        filelist = _pick_first(origin_dir, [".f"]) or ""
        if not filelist:
            plain = origin_dir / "filelist"
            filelist = str(plain) if plain.exists() else ""

    return {
        "directory": str(project_dir),
        "design": design,
        "top_module": top_module,
        "parameters_path": str(params_path),
        "flow_path": str(flow_path),
        "home_path": str(home_path),
        "checklist_path": str(checklist_path),
        "origin_def": origin_def,
        "origin_verilog": origin_verilog,
        "input_filelist": filelist,
    }


def init_flow_if_empty(flow_path: Path) -> None:
    flow = _read_json(flow_path)
    if len(flow.get("steps", [])) > 0:
        return

    flow["steps"] = [
        {
            "name": name,
            "tool": tool,
            "state": StateEnum.Unstart.value,
            "runtime": "",
            "peak memory (mb)": 0,
            "info": {},
        }
        for name, tool in DEFAULT_FLOW_STEPS
    ]
    _write_json(flow_path, flow)


def load_flow(flow_path: Path) -> dict[str, Any]:
    return _read_json(flow_path)


def save_flow(flow_path: Path, flow: dict[str, Any]) -> None:
    _write_json(flow_path, flow)


def update_home_pointer(home_path: Path, *, flow_path: Path, params_path: Path, checklist_path: Path) -> None:
    home = _read_json(home_path)
    if not home:
        home = json.loads(json.dumps(HOME_TEMPLATE))
    home["flow"] = str(flow_path)
    home["parameters"] = str(params_path)
    home["checklist"] = str(checklist_path)
    _write_json(home_path, home)


def _build_parameters(spec: CreateWorkspaceData) -> dict[str, Any]:
    design = spec.design_name
    params = dict(spec.parameters)
    params.setdefault("Design", design)
    params.setdefault("Top module", "top")
    params.setdefault("Clock", "clk")
    params.setdefault("Frequency max [MHz]", 100)
    params.setdefault("PDK", spec.pdk or "ics55")
    params.setdefault("Core", {"Utilitization": 0.5})
    params.setdefault("Target density", 0.6)
    params.setdefault("Max fanout", 20)
    if spec.pdk_root:
        params["PDK Root"] = spec.pdk_root
    if spec.filelist:
        params["input_filelist"] = spec.filelist
    return params


def _cleanup_legacy_step_dirs(project_dir: Path) -> None:
    keep = {f"{name}_{tool}" for name, tool in DEFAULT_FLOW_STEPS}
    reserved = {"log", "origin", "home"}
    for child in project_dir.iterdir():
        if not child.is_dir():
            continue
        if child.name in reserved or child.name in keep:
            continue
        # Heuristic: only delete step-like directories with core workspace subfolders.
        if (child / "config").is_dir() and (child / "output").is_dir() and (child / "data").is_dir():
            shutil.rmtree(child, ignore_errors=True)


def _prepare_origin(project_dir: Path, spec: CreateWorkspaceData, parameters: dict[str, Any]) -> dict[str, str]:
    design = parameters["Design"]
    origin_dir = project_dir / "origin"
    copied: dict[str, str] = {}

    origin_def = _copy_or_touch(
        src=spec.origin_def,
        dst=origin_dir / Path(spec.origin_def).name if spec.origin_def else origin_dir / f"{design}.def",
        placeholder=f"# auto-generated placeholder DEF for {design}\n",
    )
    copied["origin_def"] = str(origin_def)

    origin_verilog = _copy_or_touch(
        src=spec.origin_verilog,
        dst=origin_dir / Path(spec.origin_verilog).name if spec.origin_verilog else origin_dir / f"{design}.v",
        placeholder=f"module {parameters['Top module']}(); endmodule\n",
    )
    copied["origin_verilog"] = str(origin_verilog)

    copied_rtl = _copy_rtl_list(origin_dir=origin_dir, rtl_list=spec.rtl_list)
    if copied_rtl:
        filelist = origin_dir / "filelist"
        filelist.write_text("\n".join(copied_rtl) + "\n", encoding="utf-8")
        copied["input_filelist"] = str(filelist)
    elif spec.filelist:
        filelist_target = origin_dir / Path(spec.filelist).name
        try:
            shutil.copy2(spec.filelist, filelist_target)
            copied["input_filelist"] = str(filelist_target)
        except OSError:
            pass

    sdc = origin_dir / f"{design}.sdc"
    if not sdc.exists():
        sdc.write_text(
            "# Auto-generated SDC file\n"
            f"set clk_name {parameters['Clock']}\n"
            f"set clk_port_name {parameters['Clock']}\n"
            f"set clk_freq_mhz {parameters['Frequency max [MHz]']}\n",
            encoding="utf-8",
        )
    copied["sdc"] = str(sdc)
    return copied


def _copy_rtl_list(origin_dir: Path, rtl_list: list[str]) -> list[str]:
    copied_names: list[str] = []
    for item in rtl_list:
        src = Path(item).expanduser().resolve()
        if not src.exists() or not src.is_file():
            continue
        dst = origin_dir / src.name
        try:
            shutil.copy2(src, dst)
            copied_names.append(src.name)
        except OSError:
            continue
    return copied_names


def _write_home_files(project_dir: Path, parameters: dict[str, Any]) -> dict[str, str]:
    home_dir = project_dir / "home"
    flow_path = home_dir / "flow.json"
    params_path = home_dir / "parameters.json"
    home_path = home_dir / "home.json"
    checklist_path = home_dir / "checklist.json"

    _write_json(flow_path, {"steps": []})
    _write_json(params_path, parameters)
    _write_json(home_path, json.loads(json.dumps(HOME_TEMPLATE)))
    _write_json(checklist_path, {"path": str(checklist_path), "checklist": []})

    update_home_pointer(
        home_path=home_path,
        flow_path=flow_path,
        params_path=params_path,
        checklist_path=checklist_path,
    )
    return {
        "flow": str(flow_path),
        "parameters": str(params_path),
        "home": str(home_path),
        "checklist": str(checklist_path),
    }


def _init_project_log(log_dir: Path, design_name: str) -> None:
    server_log = log_dir / "server.log"
    if not server_log.exists():
        server_log.write_text(
            f"[INIT] workspace created for design={design_name}\n",
            encoding="utf-8",
        )


def _copy_or_touch(src: str, dst: Path, placeholder: str) -> Path:
    if src:
        src_path = Path(src).expanduser().resolve()
        if src_path.exists() and src_path.is_file():
            try:
                shutil.copy2(src_path, dst)
                return dst
            except OSError:
                pass
    dst.write_text(placeholder, encoding="utf-8")
    return dst


def _pick_first(origin_dir: Path, suffixes: list[str]) -> str | None:
    for suffix in suffixes:
        matches = sorted(origin_dir.glob(f"*{suffix}"))
        if matches:
            return str(matches[0])
    return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
