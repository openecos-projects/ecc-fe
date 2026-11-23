"""Normalize and validate a workspace creation request."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fecompiler.application.result import WorkspaceCliError
from fecompiler.application.values import (
    _first_text,
    _normalize_bool,
    _normalize_str_list,
    _optional_text,
    _resolve_path,
)
from fecompiler.tools.common.rtl_inputs import write_generated_rtl_filelist
from fecompiler.tools.common.sv_module import is_simple_sv_identifier


_PATH_FIELDS = {
    "directory",
    "origin_verilog",
    "filelist",
    "cpu_filelist",
    "soc_filelist",
    "testbench",
    "sim_tests_dir",
    "sim_programs_dir",
    "sim_tests_out_dir",
    "sim_soc_root",
    "sim_build_test_script",
}
_PATH_LIST_FIELDS = {
    "cpu_rtl_files",
    "rtl_list",
    "sim_cpp_sources",
    "sim_images",
    "sim_program_sources",
}
_CPU_RTL_SUFFIXES = {".v", ".sv", ".vh", ".svh"}
_RTL_COMPILATION_UNIT_SUFFIXES = {".v", ".sv"}


def _create_request_from_args(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    base_dir = Path.cwd()
    request: dict[str, Any] = {}

    if args.input_json:
        request, base_dir = _load_input_json(args.input_json)

    direct: dict[str, Any] = {}
    for arg_name, field in (
        ("directory", "directory"),
        ("design_kind", "frontend_design_kind"),
        ("origin_verilog", "origin_verilog"),
        ("filelist", "filelist"),
        ("cpu_filelist", "cpu_filelist"),
        ("cpu_top_module", "cpu_top_module"),
        ("soc_filelist", "soc_filelist"),
        ("testbench", "testbench"),
        ("sim_tests_dir", "sim_tests_dir"),
        ("sim_programs_dir", "sim_programs_dir"),
        ("sim_tests_out_dir", "sim_tests_out_dir"),
        ("sim_soc_root", "sim_soc_root"),
        ("sim_build_test_script", "sim_build_test_script"),
        ("sim_compile_preset", "sim_compile_preset"),
        ("sim_compile_opt_level", "sim_compile_opt_level"),
        ("sim_compile_march", "sim_compile_march"),
        ("sim_compile_mabi", "sim_compile_mabi"),
        ("sim_coremark_iterations", "sim_coremark_iterations"),
        ("sim_coremark_total_data_size", "sim_coremark_total_data_size"),
        ("sim_coremark_max_cycles", "sim_coremark_max_cycles"),
        ("core_id", "core_id"),
        ("soc_variant", "soc_variant"),
        ("soc_harness_id", "soc_harness_id"),
    ):
        value = getattr(args, arg_name, "")
        if value:
            direct[field] = value

    list_fields = {
        "cpu_rtl_files": getattr(args, "cpu_rtl", []),
        "sim_cpp_sources": getattr(args, "sim_cpp", []),
        "sim_cflags": getattr(args, "sim_cflag", []),
        "sim_ldflags": getattr(args, "sim_ldflag", []),
        "sim_run_args": getattr(args, "sim_arg", []),
        "sim_images": getattr(args, "sim_image", []),
        "sim_program_names": getattr(args, "sim_program", []),
        "sim_program_sources": getattr(args, "sim_program_source", []),
        "sim_compile_extra_cflags": getattr(args, "sim_compile_extra_cflag", []),
        "rtl_list": getattr(args, "rtl", []),
    }
    for field, values in list_fields.items():
        cleaned = _normalize_str_list(values)
        if cleaned:
            direct[field] = cleaned

    if getattr(args, "sim_all_tests", False):
        direct["sim_all_tests"] = True
    if getattr(args, "sim_build_all_programs", False):
        direct["sim_build_all_programs"] = True

    parameters = dict(request.get("parameters", {}) if isinstance(request.get("parameters"), dict) else {})
    if args.design:
        parameters["Design"] = args.design
    if args.top:
        parameters["Top module"] = args.top
    if args.clock:
        parameters["Clock"] = args.clock
    if args.freq is not None:
        parameters["Frequency max [MHz]"] = args.freq
    if parameters:
        direct["parameters"] = parameters

    request.update(direct)
    return request, base_dir


def _catalog_config_from_args(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    base_dir = Path.cwd()
    request: dict[str, Any] = {}
    if args.input_json:
        request, base_dir = _load_input_json(args.input_json)

    for field in (
        "core_id",
        "soc_harness_id",
        "toolchain_id",
        "test_suite_id",
        "cpu_filelist",
        "cpu_top_module",
    ):
        value = str(getattr(args, field, "") or "").strip()
        if value:
            request[field] = value
    cpu_rtl_files = _normalize_str_list(getattr(args, "cpu_rtl", []))
    if cpu_rtl_files:
        request["cpu_rtl_files"] = cpu_rtl_files
    return request, base_dir


def _load_input_json(path_text: str) -> tuple[dict[str, Any], Path]:
    if path_text == "-":
        return json.load(sys.stdin), Path.cwd()
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise WorkspaceCliError("create_workspace", "failed", "input JSON must contain an object")
    return data, path.resolve().parent


def _normalize_create_request(request: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    normalized = dict(request)
    for field in _PATH_FIELDS:
        value = normalized.get(field, "")
        if value:
            normalized[field] = _resolve_path(value, base_dir)
    for field in _PATH_LIST_FIELDS:
        normalized[field] = [
            _resolve_path(item, base_dir)
            for item in _normalize_str_list(normalized.get(field, []))
        ]
    return normalized


def _normalize_catalog_config_paths(request: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    normalized = dict(request)
    value = _optional_text(normalized.get("cpu_filelist", ""))
    if value:
        normalized["cpu_filelist"] = _resolve_path(value, base_dir)
    else:
        normalized.pop("cpu_filelist", None)
    normalized["cpu_rtl_files"] = [
        _resolve_path(item, base_dir)
        for item in _normalize_str_list(normalized.get("cpu_rtl_files", []))
    ]
    return normalized


@contextmanager
def _materialized_cpu_filelist(
    request: dict[str, Any],
    command: str,
) -> Iterator[tuple[dict[str, Any], list[str]]]:
    cpu_rtl_files = _validated_cpu_rtl_files(request, command)
    if not cpu_rtl_files:
        yield request, []
        return

    with TemporaryDirectory(prefix="ecc-fe-cpu-filelist-") as temp_dir:
        filelist = write_generated_rtl_filelist(
            Path(temp_dir) / "filelist.cpu.f",
            cpu_rtl_files,
        )
        materialized = dict(request)
        materialized["cpu_filelist"] = str(filelist)
        yield materialized, cpu_rtl_files


def _validated_cpu_rtl_files(request: dict[str, Any], command: str) -> list[str]:
    raw_files = _normalize_str_list(request.get("cpu_rtl_files", []))
    if not raw_files:
        return []
    if _optional_text(request.get("cpu_filelist")):
        raise WorkspaceCliError(
            command,
            "failed",
            "choose either cpu_filelist or cpu_rtl_files, not both",
        )

    files: list[str] = []
    seen: set[Path] = set()
    invalid: list[str] = []
    for value in raw_files:
        path = Path(value).expanduser().resolve()
        if path.suffix.lower() not in _CPU_RTL_SUFFIXES or not path.is_file():
            invalid.append(str(path))
            continue
        if path in seen:
            continue
        seen.add(path)
        files.append(str(path))

    if invalid:
        raise WorkspaceCliError(
            command,
            "failed",
            "CPU RTL selection contains missing or unsupported files: " + "; ".join(invalid[:8]),
            data={"invalid_cpu_rtl_files": invalid},
        )
    if not files:
        raise WorkspaceCliError(command, "failed", "at least one CPU RTL source is required")
    return files


def _frontend_catalog_config_from_create_request(request: dict[str, Any]) -> dict[str, Any]:
    parameters = request.get("parameters", {})
    params = parameters if isinstance(parameters, dict) else {}
    return {
        "core_id": _first_text(request.get("core_id"), params.get("frontend_core_id"), params.get("core_id")),
        "soc_harness_id": (
            _first_text(
                request.get("soc_harness_id"),
                params.get("soc_harness_id"),
                request.get("soc_variant"),
                params.get("soc_variant"),
            )
        ),
        "toolchain_id": _first_text(request.get("toolchain_id"), params.get("toolchain_id")),
        "test_suite_id": (
            _first_text(
                request.get("test_suite_id"),
                params.get("test_suite_id"),
                request.get("sim_test_suite"),
                params.get("sim_test_suite"),
            )
        ),
        "cpu_filelist": _first_text(request.get("cpu_filelist"), params.get("cpu_filelist")),
        "cpu_top_module": _first_text(
            request.get("cpu_top_module"),
            params.get("cpu_top_module"),
        ),
    }


def _apply_catalog_defaults(request: dict[str, Any], normalized: dict[str, Any]) -> None:
    request["core_id"] = normalized.get("core_id", "")
    request["soc_harness_id"] = normalized.get("soc_harness_id", "")
    request["toolchain_id"] = normalized.get("toolchain_id", "")
    request["test_suite_id"] = normalized.get("test_suite_id", "")
    if normalized.get("soc_wrapper_top"):
        request["top_module"] = normalized["soc_wrapper_top"]
    if normalized.get("cpu_filelist"):
        request["cpu_filelist"] = normalized["cpu_filelist"]
    if normalized.get("required_cpu_reset_vector"):
        request["cpu_reset_vector"] = normalized["required_cpu_reset_vector"]
    if normalized.get("soc_cpu_reset_vector"):
        request["soc_cpu_reset_vector"] = normalized["soc_cpu_reset_vector"]
    if normalized.get("soc_default_program_link_base"):
        request["soc_default_program_link_base"] = normalized["soc_default_program_link_base"]
    if normalized.get("soc_bootloader_payload_link_base"):
        request["soc_bootloader_payload_link_base"] = normalized["soc_bootloader_payload_link_base"]
    request["cpu_supports_difftest"] = bool(normalized.get("cpu_supports_difftest", True))
    request["soc_supports_difftest"] = bool(normalized.get("soc_supports_difftest", True))
    request["core_supported_test_suites"] = normalized.get("core_supported_test_suites", [])
    request["soc_supported_test_suites"] = normalized.get("soc_supported_test_suites", [])
    if normalized.get("soc_variant"):
        request["soc_variant"] = normalized["soc_variant"]
    if normalized.get("core_sim_program_link_base"):
        request["sim_program_link_base"] = normalized["core_sim_program_link_base"]
    _apply_core_sim_defaults_to(request, normalized)


def _apply_core_sim_defaults_to(target: dict[str, Any], normalized: dict[str, Any]) -> None:
    for source, field in (
        ("core_sim_compile_preset", "sim_compile_preset"),
        ("core_sim_compile_opt_level", "sim_compile_opt_level"),
        ("core_sim_compile_march", "sim_compile_march"),
        ("core_sim_compile_mabi", "sim_compile_mabi"),
        ("core_sim_coremark_iterations", "sim_coremark_iterations"),
        ("core_sim_coremark_total_data_size", "sim_coremark_total_data_size"),
        ("core_sim_coremark_max_cycles", "sim_coremark_max_cycles"),
    ):
        value = normalized.get(source)
        if str(value or "").strip():
            target[field] = str(value).strip()

    extra = _normalize_str_list(normalized.get("core_sim_compile_extra_cflags", []))
    if extra:
        target["sim_compile_extra_cflags"] = extra

    for source, field in (
        ("core_sim_coremark_has_float", "sim_coremark_has_float"),
        ("core_sim_coremark_use_difftest", "sim_coremark_use_difftest"),
    ):
        value = normalized.get(source)
        if value != "":
            target[field] = _normalize_bool(value)


def _normalize_parameters(raw: Any, *, default_clock: str = "clk") -> dict[str, Any]:
    parameters = dict(raw) if isinstance(raw, dict) else {}
    aliases = {
        "design": "Design",
        "top_module": "Top module",
        "clock": "Clock",
        "frequency_max": "Frequency max [MHz]",
    }
    for source, target in aliases.items():
        if source in parameters and target not in parameters:
            parameters[target] = parameters[source]
    parameters.setdefault("Design", "New_Chip_Design")
    parameters.setdefault("Top module", "top")
    parameters.setdefault("Clock", default_clock)
    parameters.setdefault("Frequency max [MHz]", 100)
    return parameters


def _validate_generic_create_request(normalized: dict[str, Any]) -> None:
    parameters = normalized.get("parameters", {})
    params = parameters if isinstance(parameters, dict) else {}
    top_module = _first_text(
        normalized.get("top_module"),
        params.get("Top module"),
        params.get("top_module"),
    )
    if not top_module:
        raise WorkspaceCliError(
            "create_workspace",
            "failed",
            "generic RTL workspace requires a top_module",
        )
    if not is_simple_sv_identifier(top_module):
        raise WorkspaceCliError(
            "create_workspace",
            "failed",
            f"invalid SystemVerilog top module identifier: {top_module}",
        )
    normalized["top_module"] = top_module

    source_fields = [
        field
        for field in ("filelist", "origin_verilog", "rtl_list")
        if normalized.get(field)
    ]
    if not source_fields:
        raise WorkspaceCliError(
            "create_workspace",
            "failed",
            "generic RTL workspace requires filelist, origin_verilog, or rtl_list",
        )
    if len(source_fields) > 1:
        raise WorkspaceCliError(
            "create_workspace",
            "failed",
            "choose exactly one generic RTL input: filelist, origin_verilog, or rtl_list",
        )

    rtl_files = _normalize_str_list(normalized.get("rtl_list", []))
    if normalized.get("origin_verilog"):
        rtl_files.append(str(normalized["origin_verilog"]))
    if normalized.get("filelist"):
        from fecompiler.tools.prepare.runner import PrepareStep

        try:
            parsed = PrepareStep._parse_sv_filelist(str(normalized["filelist"]))
        except (OSError, ValueError) as exc:
            raise WorkspaceCliError(
                "create_workspace",
                "failed",
                f"invalid generic RTL filelist: {exc}",
            ) from exc
        rtl_files.extend(str(path) for path in parsed["rtl_files"])
    invalid = [
        path
        for path in rtl_files
        if Path(path).suffix.lower() not in _CPU_RTL_SUFFIXES
    ]
    if invalid:
        raise WorkspaceCliError(
            "create_workspace",
            "failed",
            "generic RTL selection contains unsupported files: " + "; ".join(invalid[:8]),
            data={"invalid_rtl_files": invalid},
        )
    if not any(
        Path(path).suffix.lower() in _RTL_COMPILATION_UNIT_SUFFIXES
        for path in rtl_files
    ):
        raise WorkspaceCliError(
            "create_workspace",
            "failed",
            "generic RTL selection requires at least one .v or .sv source file",
            data={"invalid_rtl_files": rtl_files},
        )

    cpu_only_fields = (
        "core_id",
        "frontend_core_id",
        "cpu_filelist",
        "cpu_rtl_files",
        "cpu_top_module",
        "cpu_wrapper_id",
        "cpu_wrapper_contract",
        "cpu_socket_contract",
        "cpu_wrapper_top",
        "cpu_reset_vector",
        "cpu_supports_difftest",
        "required_cpu_top_module",
        "required_cpu_top_ports",
        "required_cpu_top_port_contract",
        "soc_filelist",
        "soc_harness_id",
        "soc_variant",
        "soc_wrapper_id",
        "soc_wrapper_contract",
        "soc_wrapper_top",
        "soc_cpu_reset_vector",
        "soc_default_program_link_base",
        "soc_bootloader_payload_link_base",
        "soc_supports_difftest",
        "core_supported_test_suites",
        "soc_supported_test_suites",
        "test_suite_id",
        "toolchain_id",
        "testbench",
        "sim_cpp_sources",
        "sim_cflags",
        "sim_ldflags",
        "sim_run_args",
        "sim_images",
        "sim_all_tests",
        "sim_build_all_programs",
        "sim_program_names",
        "sim_program_sources",
        "sim_tests_dir",
        "sim_tests_out_dir",
        "sim_programs_dir",
        "sim_soc_root",
        "sim_build_test_script",
        "sim_program_link_base",
        "sim_compile_preset",
        "sim_compile_opt_level",
        "sim_compile_march",
        "sim_compile_mabi",
        "sim_compile_extra_cflags",
        "sim_coremark_iterations",
        "sim_coremark_total_data_size",
        "sim_coremark_max_cycles",
        "sim_coremark_has_float",
        "sim_coremark_use_difftest",
    )
    unexpected = [
        field
        for field in cpu_only_fields
        if normalized.get(field) or params.get(field)
    ]
    if unexpected:
        raise WorkspaceCliError(
            "create_workspace",
            "failed",
            "generic RTL workspace does not accept CPU or simulation fields: "
            + ", ".join(unexpected),
            data={"unsupported_fields": unexpected},
        )


def _validate_create_request_paths(normalized: dict[str, Any]) -> None:
    missing: list[str] = []
    file_fields = (
        "cpu_filelist",
        "soc_filelist",
        "filelist",
        "origin_verilog",
        "testbench",
        "sim_build_test_script",
    )
    directory_fields = (
        "sim_soc_root",
        "sim_programs_dir",
        "sim_tests_dir",
    )

    for field in file_fields:
        value = str(normalized.get(field, "")).strip()
        if value and not Path(value).is_file():
            missing.append(f"{field}: {value}")
    for field in directory_fields:
        value = str(normalized.get(field, "")).strip()
        if value and not Path(value).is_dir():
            missing.append(f"{field}: {value}")

    list_missing: list[str] = []
    for field in _PATH_LIST_FIELDS:
        for value in _normalize_str_list(normalized.get(field, [])):
            if not Path(value).exists():
                list_missing.append(f"{field}: {value}")

    if missing or list_missing:
        details = missing + list_missing
        raise WorkspaceCliError(
            "create_workspace",
            "failed",
            "frontend workspace input path not found: " + "; ".join(details[:8]),
            data={
                "missing_paths": details,
            },
        )
