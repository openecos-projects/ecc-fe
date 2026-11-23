"""Simulation settings of a workspace: SoC runtime defaults, test suites,
CoreMark options, and difftest capability."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fecompiler.allflow.profile import CPU_CORE, normalize_frontend_design_kind
from fecompiler.application.result import WorkspaceCliError
from fecompiler.application.values import (
    _first_text,
    _normalize_bool,
    _normalize_str_list,
    _path_is_relative_to,
    _positive_int,
    _resolve_optional_path,
)
from fecompiler.resources import resolve_difftest_reference_model
from fecompiler.soc import soc_runtime_options
from fecompiler.utility.atomic_write import atomic_write_json
from fecompiler.utility.json import json_read_strict


DEFAULT_FRONTEND_SMOKE_TEST_CASES = ["add"]
DEFAULT_FRONTEND_COREMARK_CASES = ["coremark"]
DEFAULT_COREMARK_COMPILE_PRESET = "balanced"
DEFAULT_COREMARK_OPT_LEVEL = "-O2"
DEFAULT_COREMARK_MARCH = "rv32im_zicsr"
DEFAULT_COREMARK_MABI = "ilp32"
DEFAULT_COREMARK_ITERATIONS = 1
DEFAULT_COREMARK_TOTAL_DATA_SIZE = 2000
DEFAULT_COREMARK_MAX_CYCLES = 200000000
DEFAULT_COREMARK_HAS_FLOAT = True
DIFFTEST_SOURCE_NAME = "difftest.cpp"
DIFFTEST_STUB_SOURCE_NAME = "difftest_stub.cpp"
_DIFFTEST_CUSTOM_CPU_IDS = {"custom-filelist", "standard-cpu-filelist"}
_DIFFTEST_UNSUPPORTED_BUILTIN_CPU_IDS = {
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

def _apply_default_soc_runtime_options(data: dict[str, Any]) -> bool:
    defaults = _default_soc_runtime_options(data)
    if not defaults:
        return False

    changed = False
    scalar_fields = (
        "soc_variant",
        "soc_wrapper_id",
        "soc_wrapper_contract",
        "top_module",
        "sim_soc_root",
        "soc_filelist",
        "testbench",
        "sim_build_test_script",
        "sim_programs_dir",
        "sim_tests_dir",
        "soc_supports_difftest",
    )
    for field in scalar_fields:
        value = defaults.get(field, "")
        if value:
            if str(data.get(field, "")).strip() and not _should_replace_soc_runtime_value(data, defaults, field):
                continue
            if data.get(field) == value:
                continue
            data[field] = value
            changed = True

    for field in ("sim_cpp_sources", "sim_cflags", "sim_ldflags"):
        values = _normalize_str_list(defaults.get(field, []))
        if values and (
            not _normalize_str_list(data.get(field, []))
            or _should_replace_soc_runtime_list(data, defaults, field)
        ):
            if _normalize_str_list(data.get(field, [])) == values:
                continue
            data[field] = values
            changed = True

    adapted_sources = _adapt_sim_cpp_sources_for_cpu(data, _normalize_str_list(data.get("sim_cpp_sources", [])))
    if adapted_sources != _normalize_str_list(data.get("sim_cpp_sources", [])):
        data["sim_cpp_sources"] = adapted_sources
        changed = True

    return changed


def _repair_workspace_sim_defaults(workspace: dict[str, Any]) -> bool:
    if normalize_frontend_design_kind(workspace.get("frontend_design_kind")) != CPU_CORE:
        return False
    defaults = _default_soc_runtime_options(workspace)
    if not defaults:
        return False

    updates: dict[str, Any] = {}
    scalar_fields = (
        "soc_variant",
        "soc_wrapper_id",
        "soc_wrapper_contract",
        "top_module",
        "sim_soc_root",
        "soc_filelist",
        "testbench",
        "sim_build_test_script",
        "sim_programs_dir",
        "sim_tests_dir",
        "soc_supports_difftest",
    )
    for field in scalar_fields:
        value = defaults.get(field, "")
        if value:
            if str(workspace.get(field, "")).strip() and not _should_replace_soc_runtime_value(workspace, defaults, field):
                continue
            if workspace.get(field) == value:
                continue
            updates[field] = value

    for field in ("sim_cflags", "sim_ldflags"):
        values = _normalize_str_list(defaults.get(field, []))
        if values and (
            not _normalize_str_list(workspace.get(field, []))
            or _should_replace_soc_runtime_list(workspace, defaults, field)
        ):
            if _normalize_str_list(workspace.get(field, [])) == values:
                continue
            updates[field] = values

    existing_sources = _normalize_str_list(workspace.get("sim_cpp_sources", []))
    default_sources = _normalize_str_list(defaults.get("sim_cpp_sources", []))
    source_base = existing_sources or default_sources
    adapted_sources = _adapt_sim_cpp_sources_for_cpu(workspace, source_base)
    if (
        default_sources
        and _should_replace_soc_runtime_list(workspace, defaults, "sim_cpp_sources")
    ):
        adapted_sources = _adapt_sim_cpp_sources_for_cpu(workspace, default_sources)
    if adapted_sources and adapted_sources != existing_sources:
        updates["sim_cpp_sources"] = adapted_sources

    if not updates:
        return False
    _update_workspace_parameters(workspace, updates)
    return True


def _default_soc_runtime_options(data: dict[str, Any]) -> dict[str, Any]:
    if not _should_apply_soc_runtime_options(data):
        return {}

    defaults = soc_runtime_options(data)
    if defaults:
        return _with_cpu_runtime_options(data, defaults)

    # Backward-compatible fallback for workspaces created before soc_wrapper_id
    # was introduced: infer the wrapper from an explicit SoC root/filelist.
    root = _explicit_soc_root(data)
    if root is None:
        return {}
    inferred = {
        **data,
        "sim_soc_root": str(root),
        "soc_harness_id": _soc_wrapper_id_from_root(root),
    }
    return _with_cpu_runtime_options(data, soc_runtime_options(inferred))


def _with_cpu_runtime_options(data: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    if not defaults:
        return {}
    out = dict(defaults)
    out["sim_cpp_sources"] = _adapt_sim_cpp_sources_for_cpu(data, _normalize_str_list(out.get("sim_cpp_sources", [])))
    return out


def _should_replace_soc_runtime_value(data: dict[str, Any], defaults: dict[str, Any], field: str) -> bool:
    current = str(data.get(field, "")).strip()
    expected = str(defaults.get(field, "")).strip()
    if not current or not expected or current == expected:
        return False
    if field in {"soc_wrapper_id", "soc_wrapper_contract", "soc_variant", "top_module", "soc_supports_difftest"}:
        return True
    return _is_builtin_soc_runtime_path(current) and _is_builtin_soc_runtime_path(expected)


def _should_replace_soc_runtime_list(data: dict[str, Any], defaults: dict[str, Any], field: str) -> bool:
    current = _normalize_str_list(data.get(field, []))
    expected = _normalize_str_list(defaults.get(field, []))
    if not current or not expected or current == expected:
        return False
    if field in {"sim_cflags", "sim_ldflags"}:
        return _list_uses_builtin_soc_runtime_path(current) and _list_uses_builtin_soc_runtime_path(expected)
    return all(_is_builtin_soc_runtime_path(item) for item in current) and all(
        _is_builtin_soc_runtime_path(item) for item in expected
    )


def _list_uses_builtin_soc_runtime_path(values: list[str]) -> bool:
    return any(_is_builtin_soc_runtime_path(_strip_cflag_path(value)) for value in values)


def _strip_cflag_path(value: str) -> str:
    text = str(value).strip()
    if text.startswith("-I") and len(text) > 2:
        return text[2:]
    return text


def _is_builtin_soc_runtime_path(value: str) -> bool:
    text = _strip_cflag_path(value)
    if not text:
        return False
    try:
        path = Path(text).expanduser().resolve()
        return any(_path_is_relative_to(path, root) for root in _builtin_soc_runtime_roots())
    except (OSError, ValueError):
        return False


def _builtin_soc_runtime_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.getenv("ECOS_FE_COMPILER_ROOT", "").strip()
    if env_root:
        roots.append(Path(env_root).expanduser().resolve() / "fecompiler" / "thirdparty")
    roots.append(Path(__file__).resolve().parents[2] / "fecompiler" / "thirdparty")
    return roots


def _adapt_sim_cpp_sources_for_cpu(data: dict[str, Any], sources: list[str]) -> list[str]:
    if _supports_difftest(data):
        return _replace_difftest_source(sources, DIFFTEST_STUB_SOURCE_NAME, DIFFTEST_SOURCE_NAME)
    return _replace_difftest_source(sources, DIFFTEST_SOURCE_NAME, DIFFTEST_STUB_SOURCE_NAME)


def _replace_difftest_source(sources: list[str], old_name: str, new_name: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for source in sources:
        path = Path(source)
        candidate = str(path.with_name(new_name)) if path.name == old_name else source
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def _should_apply_soc_runtime_options(data: dict[str, Any]) -> bool:
    return any(
        str(data.get(field, "")).strip()
        for field in ("cpu_filelist", "soc_filelist", "sim_soc_root", "soc_variant", "soc_harness_id", "soc_wrapper_id")
    )


def _explicit_soc_root(data: dict[str, Any]) -> Path | None:
    for field in ("sim_soc_root", "soc_filelist"):
        value = str(data.get(field, "")).strip()
        if not value:
            continue
        path = Path(value).expanduser().resolve()
        if field == "soc_filelist":
            path = path.parent
        if path.exists():
            return path
    return None


def _soc_wrapper_id_from_root(root: Path) -> str:
    name = root.name
    return "ysyx-am-soc"


def _normalize_workspace_soc_id(value: Any) -> str:
    text = str(value or "").strip()
    alias_map = {
        "": "",
        "SoC": "ysyx-am-soc",
        "SoC2": "ysyx-am-soc",
        "SoC3": "ysyx-am-soc",
        "soc1": "ysyx-am-soc",
        "soc2": "ysyx-am-soc",
        "soc3": "ysyx-am-soc",
        "ysyx-am-soc-alt": "ysyx-am-soc",
        "ysyx-am-soc-extended": "ysyx-am-soc",
    }
    return alias_map.get(text, text)


def _workspace_soc_id(workspace: dict[str, Any]) -> str:
    return _normalize_workspace_soc_id(workspace.get("soc_wrapper_id") or workspace.get("soc_harness_id") or "")


def _apply_sim_test_suite(
    workspace: dict[str, Any],
    suite: Any,
    cpu_test_mode: Any = "selected",
    cpu_test_cases: Any = None,
) -> None:
    suite_name = str(suite or "").strip()
    if not suite_name or suite_name == "default":
        return

    if suite_name in {"cpu_tests", "cpu-tests", "smoke"}:
        _validate_workspace_test_suite_supported(workspace, "cpu-tests")
        mode = str(cpu_test_mode or "selected").strip().lower()
        cases = _normalize_str_list(cpu_test_cases)
        if mode not in {"all", "selected"}:
            raise WorkspaceCliError("run_step", "failed", f"unknown CPU Tests mode: {mode}")
        if mode == "selected" and not cases:
            cases = _default_cpu_test_cases(workspace)
        benchmark_cases = [name for name in cases if name in DEFAULT_FRONTEND_COREMARK_CASES]
        if benchmark_cases:
            raise WorkspaceCliError(
                "run_step",
                "failed",
                f"use CoreMark suite for benchmark case: {', '.join(benchmark_cases)}",
                data={"test_suite_id": "coremark", "cases": benchmark_cases},
            )
        if mode == "selected":
            _validate_cpu_test_cases(workspace, cases)
        updates = {
            "sim_all_tests": False,
            "sim_images": [],
            "sim_build_all_programs": mode == "all",
            "sim_program_names": [] if mode == "all" else cases,
            "sim_run_args": _default_cpu_tests_run_args(workspace),
        }
    elif suite_name == "coremark":
        _validate_workspace_test_suite_supported(workspace, "coremark")
        _validate_cpu_test_cases(workspace, DEFAULT_FRONTEND_COREMARK_CASES)
        updates = {
            "sim_all_tests": False,
            "sim_images": [],
            "sim_build_all_programs": False,
            "sim_program_names": list(DEFAULT_FRONTEND_COREMARK_CASES),
            "sim_run_args": _default_coremark_run_args(workspace),
            **_default_coremark_compile_settings(workspace),
        }
    else:
        raise WorkspaceCliError("run_step", "failed", f"unknown frontend sim test suite: {suite_name}")

    _update_workspace_parameters(workspace, updates)


def _default_coremark_compile_settings(workspace: dict[str, Any] | None = None) -> dict[str, Any]:
    source = workspace or {}
    return {
        "sim_compile_preset": _first_text(source.get("sim_compile_preset"), DEFAULT_COREMARK_COMPILE_PRESET),
        "sim_compile_opt_level": _first_text(source.get("sim_compile_opt_level"), DEFAULT_COREMARK_OPT_LEVEL),
        "sim_compile_march": _first_text(source.get("sim_compile_march"), DEFAULT_COREMARK_MARCH),
        "sim_compile_mabi": _first_text(source.get("sim_compile_mabi"), DEFAULT_COREMARK_MABI),
        "sim_compile_extra_cflags": _normalize_str_list(source.get("sim_compile_extra_cflags", [])),
        "sim_coremark_iterations": _positive_int(source.get("sim_coremark_iterations"), DEFAULT_COREMARK_ITERATIONS),
        "sim_coremark_total_data_size": _positive_int(
            source.get("sim_coremark_total_data_size"),
            DEFAULT_COREMARK_TOTAL_DATA_SIZE,
        ),
        "sim_coremark_has_float": _normalize_bool(
            source.get("sim_coremark_has_float", DEFAULT_COREMARK_HAS_FLOAT),
        ),
        "sim_coremark_max_cycles": _positive_int(
            source.get("sim_coremark_max_cycles"),
            DEFAULT_COREMARK_MAX_CYCLES,
        ),
        "sim_coremark_use_difftest": _normalize_bool(source.get("sim_coremark_use_difftest", False)),
    }


def _apply_coremark_compile_options(workspace: dict[str, Any], args: argparse.Namespace) -> None:
    updates = _default_coremark_compile_settings(workspace)
    for attr, field in (
        ("sim_compile_preset", "sim_compile_preset"),
        ("sim_compile_opt_level", "sim_compile_opt_level"),
        ("sim_compile_march", "sim_compile_march"),
        ("sim_compile_mabi", "sim_compile_mabi"),
    ):
        value = str(getattr(args, attr, "") or "").strip()
        if value:
            updates[field] = value

    extra = _normalize_str_list(getattr(args, "sim_compile_extra_cflag", []))
    if extra:
        updates["sim_compile_extra_cflags"] = extra

    iterations = getattr(args, "sim_coremark_iterations", None)
    if iterations is not None:
        updates["sim_coremark_iterations"] = _positive_int(iterations, DEFAULT_COREMARK_ITERATIONS)

    data_size = getattr(args, "sim_coremark_total_data_size", None)
    if data_size is not None:
        updates["sim_coremark_total_data_size"] = _positive_int(data_size, DEFAULT_COREMARK_TOTAL_DATA_SIZE)

    max_cycles = getattr(args, "sim_coremark_max_cycles", None)
    if max_cycles is not None:
        updates["sim_coremark_max_cycles"] = _positive_int(max_cycles, DEFAULT_COREMARK_MAX_CYCLES)

    has_float = str(getattr(args, "sim_coremark_has_float", "") or "").strip()
    if has_float:
        updates["sim_coremark_has_float"] = _normalize_bool(has_float)

    _update_workspace_parameters(workspace, updates)


def _apply_workspace_create_test_suite_defaults(workspace: dict[str, Any], test_suite_id: Any) -> None:
    suite = str(test_suite_id or "").strip()
    if not suite or suite == "default":
        return
    if _normalize_str_list(workspace.get("sim_program_names", [])) or _normalize_str_list(workspace.get("sim_images", [])):
        return
    if suite in {"smoke", "cpu-tests", "cpu_tests"}:
        _apply_default_sim_smoke_suite(workspace)
        return
    if suite == "coremark":
        _apply_sim_test_suite(workspace, "coremark")


def _apply_default_sim_smoke_suite(workspace: dict[str, Any]) -> None:
    _validate_workspace_test_suite_supported(workspace, "cpu-tests")
    cases = _default_cpu_test_cases(workspace)
    if not cases:
        return
    _validate_cpu_test_cases(workspace, cases)
    _update_workspace_parameters(
        workspace,
        {
            "sim_all_tests": False,
            "sim_images": [],
            "sim_build_all_programs": False,
            "sim_program_names": cases,
            "sim_run_args": _default_cpu_tests_run_args(workspace),
        },
    )


def _default_cpu_test_cases(workspace: dict[str, Any]) -> list[str]:
    preferred = list(DEFAULT_FRONTEND_SMOKE_TEST_CASES)
    programs_dir = _resolve_optional_path(workspace.get("sim_programs_dir", ""))
    if not programs_dir:
        return preferred

    path = Path(programs_dir)
    if not path.is_dir():
        return preferred

    available = [
        source.stem
        for source in sorted(path.glob("*.c"))
        if source.stem not in DEFAULT_FRONTEND_COREMARK_CASES
    ]
    if not available:
        return []

    selected = [name for name in preferred if name in available]
    for name in available:
        if len(selected) >= len(DEFAULT_FRONTEND_SMOKE_TEST_CASES):
            break
        if name not in selected:
            selected.append(name)
    return selected[: len(DEFAULT_FRONTEND_SMOKE_TEST_CASES)]


def _validate_cpu_test_cases(workspace: dict[str, Any], cases: list[str]) -> None:
    invalid_names = [
        name for name in cases
        if not name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name)
    ]
    if invalid_names:
        raise WorkspaceCliError("run_step", "failed", f"invalid CPU test case name: {', '.join(invalid_names)}")

    programs_dir = _resolve_optional_path(workspace.get("sim_programs_dir", ""))
    if not programs_dir:
        return

    missing = [
        name for name in cases
        if not (Path(programs_dir) / f"{name}.c").is_file()
    ]
    if missing:
        raise WorkspaceCliError("run_step", "failed", f"CPU test case not found: {', '.join(missing)}")


def _default_cpu_tests_run_args(workspace: dict[str, Any]) -> list[str]:
    if not _supports_difftest(workspace):
        return ["--max-cycles", "50000000"]
    soc_root = _workspace_soc_root(workspace)
    if not soc_root:
        return ["--max-cycles", "50000000"]
    ref_so = resolve_difftest_reference_model(soc_root)
    return [
        "--max-cycles",
        "50000000",
        "--diff",
        "--ref",
        str(ref_so),
        "--diff-image-offset",
        "0x100",
        "--diff-reset-vector",
        "0x80000000",
    ]


def _default_coremark_run_args(workspace: dict[str, Any]) -> list[str]:
    max_cycles = str(_positive_int(workspace.get("sim_coremark_max_cycles"), DEFAULT_COREMARK_MAX_CYCLES))
    args = ["--max-cycles", max_cycles]
    if not _normalize_bool(workspace.get("sim_coremark_use_difftest", False)):
        return args
    if not _supports_difftest(workspace):
        return args
    soc_root = _workspace_soc_root(workspace)
    if not soc_root:
        return args
    ref_so = resolve_difftest_reference_model(soc_root)
    return [
        *args,
        "--diff",
        "--ref",
        str(ref_so),
        "--diff-image-offset",
        "0x100",
        "--diff-reset-vector",
        "0x80000000",
    ]


def _validate_workspace_test_suite_supported(workspace: dict[str, Any], suite_id: str) -> None:
    known, supported = _workspace_test_suite_contract(workspace)
    if known and suite_id not in supported:
        raise WorkspaceCliError(
            "run_step",
            "failed",
            f"{_workspace_core_label(workspace)} on {_workspace_soc_label(workspace)} does not support {suite_id} in the current ECOS adapter.",
            data={
                "core_id": str(workspace.get("cpu_wrapper_id", "")).strip(),
                "soc_harness_id": str(workspace.get("soc_wrapper_id", "")).strip(),
                "test_suite_id": suite_id,
                "supported_test_suites": supported,
            },
        )


def _workspace_supported_test_suites(workspace: dict[str, Any]) -> list[str]:
    return _workspace_test_suite_contract(workspace)[1]


def _workspace_test_suite_contract(workspace: dict[str, Any]) -> tuple[bool, list[str]]:
    core_supported = _expand_frontend_supported_test_suites(
        _normalize_str_list(workspace.get("core_supported_test_suites", [])),
    )
    soc_supported = _expand_frontend_supported_test_suites(
        _normalize_str_list(workspace.get("soc_supported_test_suites", [])),
    )
    has_core_contract = "core_supported_test_suites" in workspace
    has_soc_contract = "soc_supported_test_suites" in workspace
    if core_supported and soc_supported:
        return True, [suite for suite in core_supported if suite in soc_supported]
    if has_core_contract or has_soc_contract:
        if has_core_contract and not core_supported:
            return True, []
        if has_soc_contract and not soc_supported:
            return True, []
        soc_known, soc_fallback = _fallback_soc_test_suite_contract(workspace)
        core_known, core_fallback = _fallback_core_test_suite_contract(workspace)
        if core_supported:
            return True, [suite for suite in core_supported if not soc_known or suite in soc_fallback]
        if soc_supported:
            return True, [suite for suite in soc_supported if not core_known or suite in core_fallback]

    core_known, core_fallback = _fallback_core_test_suite_contract(workspace)
    soc_known, soc_fallback = _fallback_soc_test_suite_contract(workspace)
    if core_fallback and soc_fallback:
        return True, [suite for suite in core_fallback if suite in soc_fallback]
    if core_fallback:
        return core_known, core_fallback
    if soc_fallback:
        if core_known:
            return True, []
        return soc_known, soc_fallback
    return core_known or soc_known, []


def _expand_frontend_supported_test_suites(suites: list[str]) -> list[str]:
    return list(suites)


def _fallback_core_test_suite_contract(workspace: dict[str, Any]) -> tuple[bool, list[str]]:
    core_id = str(workspace.get("cpu_wrapper_id") or workspace.get("frontend_core_id") or "").strip()
    if core_id == "darkriscv":
        return True, []
    return bool(core_id in {"picorv32", "scr1", "ibex", "cv32e40p", "cva6", "serv", "femtorv32", "vexriscv", "custom-filelist", "standard-cpu-filelist", ""}), _fallback_core_supported_test_suites(workspace)


def _fallback_soc_test_suite_contract(workspace: dict[str, Any]) -> tuple[bool, list[str]]:
    soc_id = _workspace_soc_id(workspace)
    return soc_id == "ysyx-am-soc", _fallback_soc_supported_test_suites(workspace)


def _fallback_core_supported_test_suites(workspace: dict[str, Any]) -> list[str]:
    core_id = str(workspace.get("cpu_wrapper_id") or workspace.get("frontend_core_id") or "").strip()
    if core_id in {"picorv32", "scr1", "ibex", "cv32e40p", "serv", "femtorv32", "vexriscv"}:
        return ["cpu-tests", "smoke", "coremark"]
    if core_id == "cva6":
        return ["cpu-tests", "smoke"]
    if core_id in {"custom-filelist", ""}:
        return ["smoke", "cpu-tests", "coremark"]
    if core_id == "standard-cpu-filelist":
        return ["smoke", "cpu-tests", "coremark"]
    return []


def _fallback_soc_supported_test_suites(workspace: dict[str, Any]) -> list[str]:
    soc_id = _workspace_soc_id(workspace)
    if soc_id == "ysyx-am-soc":
        return ["smoke", "cpu-tests", "coremark"]
    return []


def _cpu_supports_difftest(workspace: dict[str, Any]) -> bool:
    core_id = str(workspace.get("cpu_wrapper_id") or workspace.get("frontend_core_id") or "").strip()
    raw = workspace.get("cpu_supports_difftest")
    if core_id in _DIFFTEST_UNSUPPORTED_BUILTIN_CPU_IDS:
        return False
    if core_id in _DIFFTEST_CUSTOM_CPU_IDS:
        return _normalize_bool(raw) if raw is not None else False
    if raw is not None:
        return _normalize_bool(raw)
    return True


def _soc_supports_difftest(workspace: dict[str, Any]) -> bool:
    raw = workspace.get("soc_supports_difftest")
    if raw is not None:
        return _normalize_bool(raw)
    return True


def _supports_difftest(workspace: dict[str, Any]) -> bool:
    return _cpu_supports_difftest(workspace) and _soc_supports_difftest(workspace)


def _workspace_core_label(workspace: dict[str, Any]) -> str:
    core_id = str(workspace.get("cpu_wrapper_id") or workspace.get("frontend_core_id") or "selected CPU").strip()
    if core_id == "picorv32":
        return "PicoRV32"
    return core_id or "selected CPU"


def _workspace_soc_label(workspace: dict[str, Any]) -> str:
    soc_id = _workspace_soc_id(workspace) or "selected SoC"
    if soc_id.startswith("ysyx-am-soc"):
        return "YSYX AM SoC"
    return soc_id or "selected SoC"


def _workspace_soc_root(workspace: dict[str, Any]) -> Path | None:
    explicit = _resolve_optional_path(workspace.get("sim_soc_root", ""))
    if explicit and Path(explicit).exists():
        return Path(explicit)
    soc_filelist = _resolve_optional_path(workspace.get("soc_filelist", ""))
    if soc_filelist and Path(soc_filelist).exists():
        return Path(soc_filelist).parent
    return None


def _update_workspace_parameters(workspace: dict[str, Any], updates: dict[str, Any]) -> None:
    params_path = str(workspace.get("parameters_path", "")).strip()
    if not params_path:
        workspace.update(updates)
        return
    parameters = json_read_strict(params_path)
    parameters.update(updates)
    atomic_write_json(params_path, parameters)
    workspace.update(updates)


def _configure_sim_run(workspace: dict[str, Any], args: argparse.Namespace | None) -> bool:
    """Select the test suite of the next sim run; return True to force a rerun.

    A named suite in *args* is applied and always re-executed. Otherwise the
    default smoke suite is selected and an existing result may be reused.
    """
    suite_name = str(getattr(args, "sim_test_suite", "") or "").strip()
    if suite_name and suite_name.lower() != "default":
        if suite_name.lower() == "coremark":
            _apply_coremark_compile_options(workspace, args)
        _apply_sim_test_suite(
            workspace,
            suite_name,
            args.sim_cpu_test_mode,
            args.sim_cpu_test_case,
        )
        return True
    _apply_default_sim_smoke_suite(workspace)
    return False


# Steps whose workspace settings are chosen just before they run.
_STEP_RUN_SETUP: dict[str, Callable[[dict[str, Any], argparse.Namespace | None], bool]] = {
    "sim": _configure_sim_run,
}


def _prepare_step_run(
    workspace: dict[str, Any], step_name: str, args: argparse.Namespace | None = None
) -> bool:
    """Apply the pre-run setup of *step_name*; return True to force a rerun."""
    setup = _STEP_RUN_SETUP.get(step_name)
    return setup(workspace, args) if setup is not None else False
