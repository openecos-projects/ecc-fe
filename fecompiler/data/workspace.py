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
    cpu_filelist: str = ""
    soc_filelist: str = ""
    testbench: str = ""
    sim_cpp_sources: list[str] = field(default_factory=list)
    sim_cflags: list[str] = field(default_factory=list)
    sim_ldflags: list[str] = field(default_factory=list)
    sim_run_args: list[str] = field(default_factory=list)
    sim_images: list[str] = field(default_factory=list)
    sim_all_tests: bool = False
    sim_tests_dir: str = ""
    sim_build_all_programs: bool = False
    sim_program_names: list[str] = field(default_factory=list)
    sim_program_sources: list[str] = field(default_factory=list)
    sim_programs_dir: str = ""
    sim_tests_out_dir: str = ""
    sim_soc_root: str = ""
    sim_build_test_script: str = ""
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
    cpu_filelist = str(parameters.get("cpu_filelist", "")).strip()
    soc_filelist = str(parameters.get("soc_filelist", "")).strip()
    prepared_manifest = str(parameters.get("prepared_manifest", "")).strip()
    testbench = str(parameters.get("testbench", "")).strip()
    sim_cpp_sources = _to_str_list(parameters.get("sim_cpp_sources", []))
    sim_cflags = _to_str_list(parameters.get("sim_cflags", []))
    sim_ldflags = _to_str_list(parameters.get("sim_ldflags", []))
    sim_run_args = _to_str_list(parameters.get("sim_run_args", []))
    sim_images = _to_str_list(parameters.get("sim_images", []))
    sim_all_tests = _to_bool(parameters.get("sim_all_tests", False))
    sim_tests_dir = str(parameters.get("sim_tests_dir", "")).strip()
    sim_build_all_programs = _to_bool(parameters.get("sim_build_all_programs", False))
    sim_program_names = _to_str_list(parameters.get("sim_program_names", []))
    sim_program_sources = _to_str_list(parameters.get("sim_program_sources", []))
    sim_programs_dir = str(parameters.get("sim_programs_dir", "")).strip()
    sim_tests_out_dir = str(parameters.get("sim_tests_out_dir", "")).strip()
    sim_soc_root = str(parameters.get("sim_soc_root", "")).strip()
    sim_build_test_script = str(parameters.get("sim_build_test_script", "")).strip()

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
        "cpu_filelist":    cpu_filelist,
        "soc_filelist":    soc_filelist,
        "prepared_manifest": prepared_manifest,
        "testbench":       testbench,
        "sim_cpp_sources": sim_cpp_sources,
        "sim_cflags":      sim_cflags,
        "sim_ldflags":     sim_ldflags,
        "sim_run_args":    sim_run_args,
        "sim_images":      sim_images,
        "sim_all_tests":   sim_all_tests,
        "sim_tests_dir":   sim_tests_dir,
        "sim_build_all_programs": sim_build_all_programs,
        "sim_program_names": sim_program_names,
        "sim_program_sources": sim_program_sources,
        "sim_programs_dir": sim_programs_dir,
        "sim_tests_out_dir": sim_tests_out_dir,
        "sim_soc_root": sim_soc_root,
        "sim_build_test_script": sim_build_test_script,
    }


def load_flow(flow_path: Path) -> dict[str, Any]:
    return _read_json(flow_path)


def save_flow(flow_path: Path, flow: dict[str, Any]) -> None:
    _write_json(flow_path, flow)


def build_parameter_overrides(
    *,
    cpu_filelist: str = "",
    soc_filelist: str = "",
    testbench: str = "",
    sim_cpp_sources: list[str] | None = None,
    sim_cflags: list[str] | None = None,
    sim_ldflags: list[str] | None = None,
    sim_run_args: list[str] | None = None,
    sim_images: list[str] | None = None,
    sim_all_tests: bool = False,
    sim_tests_dir: str = "",
    sim_build_all_programs: bool = False,
    sim_program_names: list[str] | None = None,
    sim_program_sources: list[str] | None = None,
    sim_programs_dir: str = "",
    sim_tests_out_dir: str = "",
    sim_soc_root: str = "",
    sim_build_test_script: str = "",
) -> dict[str, Any]:
    """Normalize runtime option fields into parameters/home schema."""
    updates: dict[str, Any] = {}

    if cpu_filelist:
        updates["cpu_filelist"] = str(Path(cpu_filelist).expanduser().resolve())
    if soc_filelist:
        updates["soc_filelist"] = str(Path(soc_filelist).expanduser().resolve())
    if testbench:
        updates["testbench"] = str(Path(testbench).expanduser().resolve())

    sim_cpp = [str(Path(p).expanduser().resolve()) for p in (sim_cpp_sources or []) if str(p).strip()]
    if sim_cpp:
        updates["sim_cpp_sources"] = sim_cpp

    cflags = [str(x).strip() for x in (sim_cflags or []) if str(x).strip()]
    if cflags:
        updates["sim_cflags"] = cflags

    ldflags = [str(x).strip() for x in (sim_ldflags or []) if str(x).strip()]
    if ldflags:
        updates["sim_ldflags"] = ldflags

    run_args = [str(x) for x in (sim_run_args or []) if str(x)]
    if run_args:
        updates["sim_run_args"] = run_args

    images = [str(Path(p).expanduser().resolve()) for p in (sim_images or []) if str(p).strip()]
    if images:
        updates["sim_images"] = images

    if sim_all_tests:
        updates["sim_all_tests"] = True
    if sim_tests_dir:
        updates["sim_tests_dir"] = str(Path(sim_tests_dir).expanduser().resolve())

    if sim_build_all_programs:
        updates["sim_build_all_programs"] = True

    program_names = [str(x).strip() for x in (sim_program_names or []) if str(x).strip()]
    if program_names:
        updates["sim_program_names"] = program_names

    program_sources = [str(Path(p).expanduser().resolve()) for p in (sim_program_sources or []) if str(p).strip()]
    if program_sources:
        updates["sim_program_sources"] = program_sources

    if sim_programs_dir:
        updates["sim_programs_dir"] = str(Path(sim_programs_dir).expanduser().resolve())
    if sim_tests_out_dir:
        updates["sim_tests_out_dir"] = str(Path(sim_tests_out_dir).expanduser().resolve())
    if sim_soc_root:
        updates["sim_soc_root"] = str(Path(sim_soc_root).expanduser().resolve())
    if sim_build_test_script:
        updates["sim_build_test_script"] = str(Path(sim_build_test_script).expanduser().resolve())

    return updates


# ── private helpers ────────────────────────────────────────────────────────────

def _build_parameters(spec: CreateWorkspaceData) -> dict[str, Any]:
    params = dict(spec.parameters)
    params.setdefault("Design",              spec.design_name)
    params.setdefault("Top module",          "top")
    params.setdefault("Clock",               "clk")
    params.setdefault("Frequency max [MHz]", 100)
    params.update(
        build_parameter_overrides(
            cpu_filelist=spec.cpu_filelist,
            soc_filelist=spec.soc_filelist,
            testbench=spec.testbench,
            sim_cpp_sources=spec.sim_cpp_sources,
            sim_cflags=spec.sim_cflags,
            sim_ldflags=spec.sim_ldflags,
            sim_run_args=spec.sim_run_args,
            sim_images=spec.sim_images,
            sim_all_tests=spec.sim_all_tests,
            sim_tests_dir=spec.sim_tests_dir,
            sim_build_all_programs=spec.sim_build_all_programs,
            sim_program_names=spec.sim_program_names,
            sim_program_sources=spec.sim_program_sources,
            sim_programs_dir=spec.sim_programs_dir,
            sim_tests_out_dir=spec.sim_tests_out_dir,
            sim_soc_root=spec.sim_soc_root,
            sim_build_test_script=spec.sim_build_test_script,
        )
    )
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


def _to_str_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    return []


def _to_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        token = raw.strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
    return False
