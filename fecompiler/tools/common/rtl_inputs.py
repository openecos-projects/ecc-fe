"""Shared RTL / include / define input helpers for tool runners."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.utility.json import json_read


def prepared_inputs(workspace: dict[str, Any]) -> dict[str, Any]:
    """Load normalized prepare artifact if available."""
    manifest = str(workspace.get("prepared_manifest", "")).strip()
    if manifest and Path(manifest).exists():
        data = json_read(manifest)
        if isinstance(data, dict) and data.get("rtl_files"):
            return data
    return {}


def rtl_files(workspace: dict[str, Any]) -> list[str]:
    """Collect RTL files (prefer prepare manifest, then filelist / origin)."""
    prepared = prepared_inputs(workspace)
    if prepared:
        return [str(p) for p in prepared.get("rtl_files", [])]

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


def slang_incdir_args(workspace: dict[str, Any]) -> list[str]:
    args: list[str] = []
    for inc in incdirs(workspace):
        args.extend(["-I", inc])
    return args


def slang_define_args(workspace: dict[str, Any]) -> list[str]:
    args: list[str] = []
    for define in defines(workspace):
        args.extend(["-D", define])
    return args


def verilator_incdir_args(workspace: dict[str, Any]) -> list[str]:
    return [f"+incdir+{inc}" for inc in incdirs(workspace)]


def verilator_define_args(workspace: dict[str, Any]) -> list[str]:
    return [f"+define+{define}" for define in defines(workspace)]

