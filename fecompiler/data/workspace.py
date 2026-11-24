"""Workspace persistence: creating, loading, and saving a frontend workspace."""

from __future__ import annotations

import json
import os
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fecompiler.data.step import StateEnum
from fecompiler.allflow.profile import (
    CPU_CORE,
    GENERIC_RTL,
    flow_steps,
    normalize_frontend_design_kind,
)
from fecompiler.tools.common.filelist import RTL_SOURCE_SUFFIXES, parse_filelist
from fecompiler.tools.common.rtl_inputs import write_generated_rtl_filelist
from fecompiler.utility.atomic_write import atomic_write_json


_RUNTIME_LIST_PARAMETER_FIELDS = (
    "sim_cpp_sources",
    "sim_cflags",
    "sim_ldflags",
    "sim_run_args",
    "sim_images",
    "sim_program_names",
    "sim_program_sources",
    "sim_compile_extra_cflags",
    "core_supported_test_suites",
    "soc_supported_test_suites",
)
_LIST_PARAMETER_FIELDS = (
    *_RUNTIME_LIST_PARAMETER_FIELDS,
    "required_cpu_top_ports",
)
_BOOL_PARAMETER_FIELDS = (
    "sim_all_tests",
    "sim_build_all_programs",
    "cpu_supports_difftest",
    "soc_supports_difftest",
    "sim_coremark_has_float",
    "sim_coremark_use_difftest",
)
_STR_PARAMETER_FIELDS = (
    "frontend_design_kind",
    "cpu_filelist",
    "soc_filelist",
    "prepared_manifest",
    "testbench",
    "sim_tests_dir",
    "sim_programs_dir",
    "sim_tests_out_dir",
    "sim_soc_root",
    "sim_build_test_script",
    "sim_program_link_base",
    "sim_compile_preset",
    "sim_compile_opt_level",
    "sim_compile_march",
    "sim_compile_mabi",
    "sim_coremark_iterations",
    "sim_coremark_total_data_size",
    "sim_coremark_max_cycles",
    "soc_variant",
    "soc_wrapper_id",
    "soc_wrapper_contract",
    "soc_wrapper_top",
    "cpu_wrapper_id",
    "cpu_wrapper_contract",
    "cpu_socket_contract",
    "cpu_wrapper_top",
    "cpu_reset_vector",
    "soc_cpu_reset_vector",
    "soc_default_program_link_base",
    "soc_bootloader_payload_link_base",
    "frontend_core_id",
    "required_cpu_top_module",
)
_JSON_PARAMETER_FIELDS = (
    "required_cpu_top_port_contract",
    "review_waivers",
    "lint_waivers",
    "resource_versions",
)
_PARAMETER_OVERRIDE_FIELDS = (
    "cpu_filelist",
    "soc_filelist",
    "testbench",
    *_RUNTIME_LIST_PARAMETER_FIELDS,
    *_BOOL_PARAMETER_FIELDS,
    "sim_tests_dir",
    "sim_programs_dir",
    "sim_tests_out_dir",
    "sim_soc_root",
    "sim_build_test_script",
    "sim_program_link_base",
    "sim_compile_preset",
    "sim_compile_opt_level",
    "sim_compile_march",
    "sim_compile_mabi",
    "sim_coremark_iterations",
    "sim_coremark_total_data_size",
    "sim_coremark_max_cycles",
)


# ── workspace schema version ─────────────────────────────────────────────────

# Version of the workspace layout and of the state files under home/. It is
# stored once, as "schema_version" in home/home.json. Raise it when a change
# would make an older ecc-fe misread a workspace; a workspace without the field
# predates it and is version 1.
WORKSPACE_SCHEMA_VERSION = 1


class UnsupportedWorkspaceSchemaError(ValueError):
    """The workspace was written by a newer ecc-fe than this one."""

    def __init__(self, directory: Path, found: object) -> None:
        self.directory = directory
        self.found = found
        self.supported = WORKSPACE_SCHEMA_VERSION
        super().__init__(
            f"frontend workspace schema version {found!r} is not supported by "
            f"this ecc-fe (supports up to {WORKSPACE_SCHEMA_VERSION}); "
            f"update ecc-fe to use this workspace: {directory}"
        )


def workspace_schema_version(directory: str | Path) -> int:
    """Return the schema version recorded for the workspace at *directory*.

    Raises :class:`UnsupportedWorkspaceSchemaError` when the workspace is newer
    than this ecc-fe understands, so it is never read or modified by guesswork.
    """
    project_dir = Path(directory).expanduser().resolve()
    try:
        home = _read_json(project_dir / "home" / "home.json")
    except (OSError, ValueError):
        home = {}
    found = home.get("schema_version", 1) if isinstance(home, dict) else 1
    if isinstance(found, bool) or not isinstance(found, int) or found < 1:
        raise UnsupportedWorkspaceSchemaError(project_dir, found)
    if found > WORKSPACE_SCHEMA_VERSION:
        raise UnsupportedWorkspaceSchemaError(project_dir, found)
    return found


# ── single-step workspace paths ────────────────────────────────────────────────

@dataclass(slots=True)
class WorkspaceStep:
    """All paths that belong to one flow step's working directory.

    Built by fecompiler/tools/fe/builder.py::build_step(). Each group (config,
    output, report, …) is a plain dict of paths, and the step also supports
    dict-style access such as ``ws_step["report"]["dir"]``.
    """

    name: str
    tool: str
    version: str
    directory: str

    config:    dict[str, str]
    output:    dict[str, str]
    data:      dict[str, str]
    report:    dict[str, Any]
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
    origin_verilog: str = ""
    filelist: str = ""
    cpu_filelist: str = ""
    cpu_rtl_files: list[str] = field(default_factory=list)
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
    cpu_supports_difftest: bool = True
    soc_supports_difftest: bool = True
    core_supported_test_suites: list[str] = field(default_factory=list)
    soc_supported_test_suites: list[str] = field(default_factory=list)
    sim_program_names: list[str] = field(default_factory=list)
    sim_program_sources: list[str] = field(default_factory=list)
    sim_programs_dir: str = ""
    sim_tests_out_dir: str = ""
    sim_soc_root: str = ""
    sim_build_test_script: str = ""
    sim_program_link_base: str = ""
    sim_compile_preset: str = ""
    sim_compile_opt_level: str = ""
    sim_compile_march: str = ""
    sim_compile_mabi: str = ""
    sim_compile_extra_cflags: list[str] = field(default_factory=list)
    sim_coremark_iterations: str = ""
    sim_coremark_total_data_size: str = ""
    sim_coremark_max_cycles: str = ""
    sim_coremark_has_float: bool = False
    sim_coremark_use_difftest: bool = False
    rtl_list: list[str] = field(default_factory=list)
    frontend_design_kind: str = CPU_CORE

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
    # Re-creating in place must not downgrade a workspace from a newer ecc-fe.
    workspace_schema_version(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)

    for d in ("origin", "log", "home"):
        (project_dir / d).mkdir(exist_ok=True)

    parameters = _build_parameters(spec)
    _prepare_origin(project_dir, spec, parameters)
    _write_home_files(project_dir, parameters)

    return load_workspace(str(project_dir))


def load_workspace(directory: str) -> dict[str, Any] | None:
    """Load an existing workspace from disk.

    Returns ``None`` when *directory* is not a workspace and raises
    :class:`UnsupportedWorkspaceSchemaError` when it is one this ecc-fe is too
    old to understand.
    """
    project_dir = Path(directory).expanduser().resolve()
    params_path  = project_dir / "home" / "parameters.json"
    # parameters.json is what makes a directory a workspace. Without this
    # check any existing directory would load and be scaffolded as one.
    if not params_path.is_file():
        return None
    workspace_schema_version(project_dir)

    flow_path    = project_dir / "home" / "flow.json"
    home_path    = project_dir / "home" / "home.json"
    origin_dir   = project_dir / "origin"

    parameters   = _read_json(params_path)
    design       = str(parameters.get("Design", project_dir.name))
    top_module   = str(parameters.get("Top module", "top"))

    origin_verilog = _pick_first(origin_dir, [".sv", ".v", ".sv.gz", ".v.gz"]) or str(
        origin_dir / f"{design}.v"
    )

    # filelist: prefer path stored in parameters, then scan origin/
    filelist = str(parameters.get("input_filelist", "")).strip()
    if not filelist or not Path(filelist).exists():
        filelist = _pick_first(origin_dir, [".f", ".fl", ".filelist"]) or ""

    workspace = {
        "directory":       str(project_dir),
        "design":          design,
        "top_module":      top_module,
        "parameters_path": str(params_path),
        "flow_path":       str(flow_path),
        "home_path":       str(home_path),
        "origin_verilog":  origin_verilog,
        "input_filelist":  filelist,
    }
    workspace.update({
        field: _to_str_list(parameters.get(field, []))
        for field in _LIST_PARAMETER_FIELDS
    })
    workspace.update({
        field: _to_bool(parameters.get(field, _default_bool_parameter(field)))
        for field in _BOOL_PARAMETER_FIELDS
    })
    workspace.update({
        field: str(parameters.get(field, "")).strip()
        for field in _STR_PARAMETER_FIELDS
    })
    workspace.update({
        field: parameters.get(field, [] if field.endswith("_waivers") or field.endswith("_contract") else {})
        for field in _JSON_PARAMETER_FIELDS
    })
    return workspace


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
    cpu_supports_difftest: bool = True,
    soc_supports_difftest: bool = True,
    core_supported_test_suites: list[str] | None = None,
    soc_supported_test_suites: list[str] | None = None,
    sim_program_names: list[str] | None = None,
    sim_program_sources: list[str] | None = None,
    sim_programs_dir: str = "",
    sim_tests_out_dir: str = "",
    sim_soc_root: str = "",
    sim_build_test_script: str = "",
    sim_program_link_base: str = "",
    sim_compile_preset: str = "",
    sim_compile_opt_level: str = "",
    sim_compile_march: str = "",
    sim_compile_mabi: str = "",
    sim_compile_extra_cflags: list[str] | None = None,
    sim_coremark_iterations: str = "",
    sim_coremark_total_data_size: str = "",
    sim_coremark_max_cycles: str = "",
    sim_coremark_has_float: bool = False,
    sim_coremark_use_difftest: bool = False,
) -> dict[str, Any]:
    """Normalize runtime option fields into parameters/home schema."""
    updates: dict[str, Any] = {}
    values = locals()

    for field in (
        "cpu_filelist",
        "soc_filelist",
        "testbench",
        "sim_tests_dir",
        "sim_programs_dir",
        "sim_tests_out_dir",
        "sim_soc_root",
        "sim_build_test_script",
    ):
        if values[field]:
            updates[field] = _resolve_param_path(values[field])

    if sim_program_link_base:
        updates["sim_program_link_base"] = str(sim_program_link_base).strip()
    for field in (
        "sim_compile_preset",
        "sim_compile_opt_level",
        "sim_compile_march",
        "sim_compile_mabi",
        "sim_coremark_iterations",
        "sim_coremark_total_data_size",
        "sim_coremark_max_cycles",
    ):
        if values[field]:
            updates[field] = str(values[field]).strip()

    for field in ("sim_cpp_sources", "sim_images", "sim_program_sources"):
        resolved = _resolve_param_paths(values[field])
        if resolved:
            updates[field] = resolved

    extra_cflags = _clean_str_list(sim_compile_extra_cflags)
    if extra_cflags:
        updates["sim_compile_extra_cflags"] = extra_cflags

    for field in (
        "sim_cflags",
        "sim_ldflags",
        "sim_program_names",
        "core_supported_test_suites",
        "soc_supported_test_suites",
    ):
        cleaned = _clean_str_list(values[field])
        if cleaned:
            updates[field] = cleaned

    run_args = [str(x) for x in (sim_run_args or []) if str(x)]
    if run_args:
        updates["sim_run_args"] = run_args

    for field in _BOOL_PARAMETER_FIELDS:
        if field in {"cpu_supports_difftest", "soc_supports_difftest"}:
            updates[field] = _to_bool(values[field])
            continue
        if values[field]:
            updates[field] = True

    return updates


# ── private helpers ────────────────────────────────────────────────────────────

def _build_parameters(spec: CreateWorkspaceData) -> dict[str, Any]:
    params = dict(spec.parameters)
    requested_kind = params.get("frontend_design_kind", spec.frontend_design_kind)
    design_kind = normalize_frontend_design_kind(requested_kind)
    params["frontend_design_kind"] = design_kind
    params.setdefault("Design",              spec.design_name)
    params.setdefault("Top module",          "top")
    params.setdefault("Clock",               "clk")
    params.setdefault("Frequency max [MHz]", 100)
    if design_kind == CPU_CORE:
        params.update(build_parameter_overrides(**{
            field: getattr(spec, field)
            for field in _PARAMETER_OVERRIDE_FIELDS
        }))
    return params


def _default_bool_parameter(field: str) -> bool:
    if field in {"cpu_supports_difftest", "soc_supports_difftest"}:
        return True
    return False


def _prepare_origin(project_dir: Path, spec: CreateWorkspaceData, parameters: dict[str, Any]) -> None:
    design     = parameters["Design"]
    top_module = parameters["Top module"]
    origin_dir = project_dir / "origin"
    design_kind = normalize_frontend_design_kind(parameters.get("frontend_design_kind"))

    if spec.cpu_rtl_files:
        generated = write_generated_rtl_filelist(
            origin_dir / ".cpu_sources.f",
            spec.cpu_rtl_files,
        )
        parameters["cpu_filelist"] = str(generated)

    # Verilog (skip the placeholder when an explicit source set is provided)
    has_explicit_sources = bool(spec.filelist or spec.rtl_list)
    if spec.origin_verilog or not has_explicit_sources:
        _copy_or_touch(
            src=spec.origin_verilog,
            dst=origin_dir / (Path(spec.origin_verilog).name if spec.origin_verilog else f"{design}.v"),
            placeholder=f"module {top_module}(); endmodule\n",
        )

    # Generic RTL keeps absolute source paths so include relationships are not
    # broken by flattening files with the same basename into origin/.
    if design_kind == GENERIC_RTL and spec.rtl_list:
        generated = write_generated_rtl_filelist(
            origin_dir / ".rtl_sources.f",
            spec.rtl_list,
            enable_difftest=False,
            description="selected RTL files",
        )
        parameters["input_filelist"] = str(generated)
    elif design_kind == GENERIC_RTL and spec.filelist:
        parameters["input_filelist"] = str(Path(spec.filelist).expanduser().resolve())
    # Legacy / CPU rtl_list → copy files + write filelist.
    elif spec.rtl_list:
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

    # flow.json — one entry per selected frontend profile step
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
            for name, tool in flow_steps(parameters)
        ]
    })

    # parameters.json
    _write_json(params_path, parameters)

    # home.json — index file pointing to the other two
    _write_json(home_path, {
        "schema_version": WORKSPACE_SCHEMA_VERSION,
        "parameters": str(params_path),
        "flow":       str(flow_path),
        "layout":     "",
        "metrics":    {},
        "monitor": {
            "step": [], "memory": [], "runtime": [], "instance": [], "frequency": [],
        },
    })


def _copy_filelist_sources(origin_dir: Path, filelist_path: str) -> None:
    """Snapshot the RTL a filelist refers to into origin/ and rewrite the list.

    The rewritten ``origin/<filelist name>`` keeps the design's include
    directories and defines and lists every RTL file by absolute path. Files
    are copied flat; one whose name is already taken by a different file is
    left where it is and listed in place, so nothing is silently replaced.
    """
    parsed = parse_filelist(filelist_path)
    lines = [f"+define+{define}" for define in parsed.defines]
    lines.append(f"+incdir+{shlex.quote(str(origin_dir))}")
    lines.extend(f"+incdir+{shlex.quote(str(incdir))}" for incdir in parsed.incdirs)

    taken: dict[str, Path] = {parsed.path.name: parsed.path}
    for source in parsed.files:
        owner = taken.setdefault(source.name, source)
        listed = source
        if owner == source:
            try:
                shutil.copy2(source, origin_dir / source.name)
                listed = origin_dir / source.name
            except OSError:
                pass
        if source.suffix.lower() in RTL_SOURCE_SUFFIXES:
            lines.append(shlex.quote(str(listed)))

    (origin_dir / parsed.path.name).write_text(
        "\n".join(dict.fromkeys(lines)) + "\n", encoding="utf-8"
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
    atomic_write_json(path, payload)


def _resolve_param_path(value: Any) -> str:
    return str(Path(str(value)).expanduser().resolve())


def _resolve_param_paths(values: list[str] | None) -> list[str]:
    return [_resolve_param_path(p) for p in (values or []) if str(p).strip()]


def _clean_str_list(values: list[str] | None) -> list[str]:
    return [str(x).strip() for x in (values or []) if str(x).strip()]


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
