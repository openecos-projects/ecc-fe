"""How a CPU simulation is configured: harness sources, compile and link flags,
run arguments, the SoC root and the Difftest policy; and the cycle count a
run prints, which several readers of its output need."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any

from fecompiler.tools.common.tool_config import tool_config
from fecompiler.tools.verilator.lint import (  # noqa: F401
    VerilatorLintStep,
    build_lint_summary,
    parse_verilator_diagnostics,
)
from fecompiler.utility.json import json_read

_WORKSPACE_REL_SOC_ROOT = Path("fecompiler/thirdparty/SoC")


_SIM_CYCLE_RE = re.compile(r"\b(?:after|timeout after)\s+([0-9]+)\s+cycles\b")


_DIFFTEST_SOURCE_NAME = "difftest.cpp"


_DIFFTEST_STUB_SOURCE_NAME = "difftest_stub.cpp"


_DIFFTEST_UNSUPPORTED_CPU_IDS = {
    "picorv32",
    "scr1",
    "ibex",
    "cv32e40p",
    "cva6",
    "serv",
    "femtorv32",
    "vexriscv",
    "darkriscv",
}


_DIFFTEST_CUSTOM_CPU_IDS = {"custom-filelist", "standard-cpu-filelist"}


_DIFFTEST_ARG_VALUE_OPTIONS = {"--ref", "--diff-image-offset", "--diff-reset-vector"}


def sim_cpp_sources(workspace: dict[str, Any]) -> list[str]:
    """Return C++ simulation sources (testbench first, then extras)."""
    seen: set[str] = set()
    ordered: list[str] = []

    tb = str(workspace.get("testbench", "")).strip()
    if tb:
        seen.add(tb)
        ordered.append(tb)

    for src in workspace.get("sim_cpp_sources", []) or []:
        s = str(src).strip()
        if s and s not in seen:
            seen.add(s)
            ordered.append(s)
    return _adapt_sim_cpp_sources_for_difftest(workspace, ordered)


def effective_sim_cflags(workspace: dict[str, Any]) -> list[str]:
    """Return the C++ flags passed to Verilator after runtime normalization."""
    user_flags = [
        str(flag).strip()
        for flag in workspace.get("sim_cflags", []) or []
        if str(flag).strip()
    ]
    user_flags = [_normalize_sim_cflag(flag) for flag in user_flags]
    user_flags = _ensure_soc_include_flag(workspace, user_flags)
    has_std = any(flag.startswith("-std=") for flag in user_flags)
    return ([] if has_std else ["-std=c++20"]) + user_flags


def effective_sim_ldflags(workspace: dict[str, Any]) -> list[str]:
    """Return the linker flags passed to Verilator."""
    return [
        str(flag).strip()
        for flag in workspace.get("sim_ldflags", []) or []
        if str(flag).strip()
    ]


# Kept for callers that imported the former private helper.
_sim_cpp_sources = sim_cpp_sources


def _adapt_sim_cpp_sources_for_difftest(workspace: dict[str, Any], sources: list[str]) -> list[str]:
    if _sim_difftest_supported(workspace):
        return _replace_sim_cpp_source(sources, _DIFFTEST_STUB_SOURCE_NAME, _DIFFTEST_SOURCE_NAME)
    return _replace_sim_cpp_source(sources, _DIFFTEST_SOURCE_NAME, _DIFFTEST_STUB_SOURCE_NAME)


def _replace_sim_cpp_source(sources: list[str], old_name: str, new_name: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for source in sources:
        path = Path(source)
        candidate = str(path.with_name(new_name)) if path.name == old_name else source
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def _sim_cflags_args(workspace: dict[str, Any]) -> list[str]:
    flags = effective_sim_cflags(workspace)
    if not flags:
        return []
    return ["-CFLAGS", " ".join(flags)]


def _ensure_soc_include_flag(workspace: dict[str, Any], flags: list[str]) -> list[str]:
    soc_root = _workspace_soc_root(workspace)
    if soc_root is None:
        return flags

    include = f"-I{soc_root}"
    if any(flag == include or flag == f"-I{soc_root}/" for flag in flags):
        return flags
    return [*flags, include]


def _sim_ldflags_args(workspace: dict[str, Any]) -> list[str]:
    flags = effective_sim_ldflags(workspace)
    if not flags:
        return []
    return ["-LDFLAGS", " ".join(flags)]


def _sim_run_args(workspace: dict[str, Any]) -> list[str]:
    args = [str(arg) for arg in workspace.get("sim_run_args", []) or []]
    if not _sim_difftest_supported(workspace):
        return _strip_difftest_args(args)
    return args


def _sim_difftest_supported(workspace: dict[str, Any]) -> bool:
    cpu_id = _workspace_cpu_id(workspace)
    raw_cpu_support = workspace.get("cpu_supports_difftest")
    if cpu_id in _DIFFTEST_UNSUPPORTED_CPU_IDS:
        return False
    if cpu_id in _DIFFTEST_CUSTOM_CPU_IDS and raw_cpu_support is None:
        return False
    return (
        _bool_workspace_value(raw_cpu_support, True)
        and _bool_workspace_value(workspace.get("soc_supports_difftest"), True)
    )


def _strip_difftest_args(args: list[str]) -> list[str]:
    stripped: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--diff":
            continue
        if arg in _DIFFTEST_ARG_VALUE_OPTIONS:
            skip_next = True
            continue
        stripped.append(arg)
    return stripped


def _sim_difftest_enabled(workspace: dict[str, Any]) -> bool:
    return _sim_difftest_supported(workspace) and "--diff" in _sim_run_args(workspace)


def _arg_present(args: list[str], option: str) -> bool:
    return option in args or any(arg.startswith(f"{option}=") for arg in args)


def _resolve_path(path_text: str, *, base: Path | None = None) -> Path:
    p = Path(path_text).expanduser()
    if p.is_absolute():
        return p.resolve()
    if base is not None:
        return (base / p).resolve()
    return (_invocation_root() / p).resolve()


def _workspace_soc_root(workspace: dict[str, Any]) -> Path | None:
    explicit_root = str(workspace.get("sim_soc_root", "")).strip()
    if explicit_root:
        p = _resolve_path(explicit_root)
        if p.exists():
            return p

    soc_filelist = str(workspace.get("soc_filelist", "")).strip()
    if soc_filelist:
        p = _resolve_path(soc_filelist)
        if p.exists():
            return p.parent

    root = _invocation_root()
    candidate = root / _WORKSPACE_REL_SOC_ROOT
    if candidate.exists():
        return candidate.resolve()

    return None


def _strip_option_with_value(args: list[str], option: str) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == option:
            i += 2
            continue
        if arg.startswith(f"{option}="):
            i += 1
            continue
        out.append(arg)
        i += 1
    return out


def _workspace_frequency_mhz(workspace: dict[str, Any]) -> float | None:
    params_path = str(workspace.get("parameters_path", "")).strip()
    candidates: list[Any] = []
    if params_path:
        try:
            params = json_read(params_path)
            candidates.extend([
                params.get("Frequency max [MHz]"),
                params.get("frequency_max"),
                params.get("freq"),
            ])
        except Exception:
            pass
    candidates.extend([
        workspace.get("frequency_mhz"),
        workspace.get("frequency_max"),
        workspace.get("freq"),
    ])
    for value in candidates:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > 0:
            return numeric
    return None


def _sim_cycles_from_output(output: str) -> int | None:
    matches = list(_SIM_CYCLE_RE.finditer(output))
    if not matches:
        return None
    try:
        return int(matches[-1].group(1))
    except ValueError:
        return None


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _bool_workspace_value(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_string_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = shlex.split(value)
    else:
        raw = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _workspace_cpu_id(workspace: dict[str, Any]) -> str:
    return str(
        workspace.get("cpu_wrapper_id")
        or workspace.get("frontend_core_id")
        or workspace.get("core_id")
        or ""
    ).strip()


def _option_value(args: list[str], option: str) -> str:
    for i, arg in enumerate(args):
        if arg == option and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(f"{option}="):
            return arg.split("=", 1)[1]
    return ""


def _coverage_requested(workspace: dict[str, Any]) -> bool:
    return _bool_workspace_value(tool_config(workspace).get("sim_coverage"), False)


def _setting_int(workspace: dict[str, Any], key: str, default: int, *, minimum: int) -> int:
    try:
        value = int(tool_config(workspace).get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _invocation_root() -> Path:
    """Return path root for resolving user-provided relative compile flags."""
    workspace_dir = os.getenv("BUILD_WORKSPACE_DIRECTORY", "").strip()
    if workspace_dir:
        return Path(workspace_dir).expanduser().resolve()
    return Path.cwd().expanduser().resolve()


def _normalize_sim_cflag(flag: str) -> str:
    """Normalize -I / -isystem path flags so verilator make can resolve them from obj_dir."""
    tokens = shlex.split(flag)
    if not tokens:
        return flag

    root = _invocation_root()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-I" and i + 1 < len(tokens):
            p = Path(tokens[i + 1])
            out.extend(["-I", str((root / p).resolve()) if not p.is_absolute() else str(p)])
            i += 2
            continue
        if tok.startswith("-I") and tok != "-I":
            raw = tok[2:]
            p = Path(raw)
            out.append(f"-I{(root / p).resolve()}" if raw and not p.is_absolute() else tok)
            i += 1
            continue
        if tok == "-isystem" and i + 1 < len(tokens):
            p = Path(tokens[i + 1])
            out.extend(["-isystem", str((root / p).resolve()) if not p.is_absolute() else str(p)])
            i += 2
            continue
        if tok.startswith("-isystem") and tok != "-isystem":
            raw = tok[len("-isystem"):]
            p = Path(raw)
            out.append(f"-isystem{(root / p).resolve()}" if raw and not p.is_absolute() else tok)
            i += 1
            continue
        out.append(tok)
        i += 1

    return " ".join(out)
