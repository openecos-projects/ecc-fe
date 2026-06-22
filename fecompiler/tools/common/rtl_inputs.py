"""Shared RTL / include / define input helpers for tool runners."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.utility.json import json_read


_SLANG_ELAB_DEFAULT_DEFINES = ("SYNTHESIS",)
_VERILATOR_LINT_DEFAULT_DEFINES = ("SYNTHESIS",)
_FINGERPRINT_PATH_FIELDS = (
    "cpu_filelist",
    "cpu_adapter_filelist",
    "soc_filelist",
    "filelist",
    "origin_verilog",
)
_FINGERPRINT_TEXT_FIELDS = (
    "cpu_wrapper_top",
    "soc_wrapper_id",
    "soc_harness_id",
    "soc_wrapper_top",
    "top_module",
    "cpu_socket_contract",
)


def workspace_input_fingerprint(workspace: dict[str, Any]) -> dict[str, str]:
    fingerprint: dict[str, str] = {}
    for field in _FINGERPRINT_PATH_FIELDS:
        fingerprint[field] = _normalized_path_text(workspace.get(field, ""))
    for field in _FINGERPRINT_TEXT_FIELDS:
        fingerprint[field] = str(workspace.get(field, "") or "").strip()
    return fingerprint


def prepared_inputs_current(workspace: dict[str, Any], data: dict[str, Any] | None = None) -> bool:
    if data is None:
        manifest = str(workspace.get("prepared_manifest", "")).strip()
        if not manifest or not Path(manifest).exists():
            return not _uses_explicit_frontend_inputs(workspace)
        loaded = json_read(manifest)
        data = loaded if isinstance(loaded, dict) else {}

    actual = data.get("source_fingerprint")
    if not isinstance(actual, dict):
        return not _uses_explicit_frontend_inputs(workspace)
    return {str(key): str(value) for key, value in actual.items()} == workspace_input_fingerprint(workspace)


def prepared_inputs(workspace: dict[str, Any]) -> dict[str, Any]:
    """Load normalized prepare artifact if available."""
    manifest = str(workspace.get("prepared_manifest", "")).strip()
    if manifest and Path(manifest).exists():
        data = json_read(manifest)
        if isinstance(data, dict) and data.get("rtl_files") and prepared_inputs_current(workspace, data):
            return data
    return {}


def rtl_files(workspace: dict[str, Any]) -> list[str]:
    """Collect RTL files (prefer prepare manifest, then filelist / origin)."""
    prepared = prepared_inputs(workspace)
    if prepared:
        return [str(p) for p in prepared.get("rtl_files", [])]

    if _uses_explicit_frontend_inputs(workspace):
        return []

    filelist = workspace.get("input_filelist", "")
    if filelist and Path(filelist).exists():
        return [
            line.strip()
            for line in Path(filelist).read_text(encoding="utf-8").splitlines()
            if line.strip()
            and not line.strip().startswith(("#", "//"))
            and (line.strip().endswith(".v") or line.strip().endswith(".sv"))
        ]

    verilog = workspace.get("origin_verilog", "")
    if verilog and Path(verilog).exists():
        return [verilog]
    return []


def incdirs(workspace: dict[str, Any]) -> list[str]:
    """Return ordered include directories from manifest and RTL parent dirs."""
    prepared = prepared_inputs(workspace)
    seen: set[str] = set()
    ordered: list[str] = []

    for inc in prepared.get("incdirs", []) if prepared else []:
        text = str(inc).strip()
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)

    for rtl in rtl_files(workspace):
        parent = str(Path(rtl).expanduser().resolve().parent)
        if parent and parent not in seen:
            seen.add(parent)
            ordered.append(parent)
    return ordered


def defines(workspace: dict[str, Any]) -> list[str]:
    """Return ordered defines from prepare manifest."""
    prepared = prepared_inputs(workspace)
    return [str(define) for define in prepared.get("defines", [])] if prepared else []


def _merge_defines(*groups: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in groups:
        for define in group:
            text = str(define).strip()
            if text and text not in seen:
                seen.add(text)
                ordered.append(text)
    return ordered


def slang_defines(workspace: dict[str, Any]) -> list[str]:
    """Return ordered defines for Slang elaboration checks.

    Slang elab is a pre-sim semantic gate, so we default it to synthesis-like
    assertion behavior to avoid third-party assertion-only hierarchy breakage.
    """
    return _merge_defines(_SLANG_ELAB_DEFAULT_DEFINES, defines(workspace))


def verilator_lint_defines(workspace: dict[str, Any]) -> list[str]:
    """Return ordered defines for Verilator lint.

    Lint is a pre-synthesis quality gate, so keep simulation-only debug blocks
    behind SYNTHESIS while preserving user / catalog defines.
    """
    return _merge_defines(_VERILATOR_LINT_DEFAULT_DEFINES, defines(workspace))


def slang_incdir_args(workspace: dict[str, Any]) -> list[str]:
    args: list[str] = []
    for inc in incdirs(workspace):
        args.extend(["-I", inc])
    return args


def slang_define_args(workspace: dict[str, Any]) -> list[str]:
    args: list[str] = []
    for define in slang_defines(workspace):
        args.extend(["-D", define])
    return args


def verilator_incdir_args(workspace: dict[str, Any]) -> list[str]:
    return [f"+incdir+{inc}" for inc in incdirs(workspace)]


def verilator_define_args(workspace: dict[str, Any]) -> list[str]:
    return [f"+define+{define}" for define in defines(workspace)]


def verilator_lint_define_args(workspace: dict[str, Any]) -> list[str]:
    return [f"+define+{define}" for define in verilator_lint_defines(workspace)]


def _uses_explicit_frontend_inputs(workspace: dict[str, Any]) -> bool:
    return any(
        str(workspace.get(field, "") or "").strip()
        for field in ("cpu_filelist", "cpu_adapter_filelist", "soc_filelist")
    )


def _normalized_path_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve())
    except OSError:
        return text
