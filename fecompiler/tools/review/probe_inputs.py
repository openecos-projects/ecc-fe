"""Collect what the structural precheck reads: sources, defines, the top module.

The sources are the ones elaboration reads: prepare's result, or the filelist
read by the same parser when there is no current prepare result. Library
directories stay library directories, searched for modules the sources do
not define. Nothing is added by guessing from file names.

Includes the lightweight scan of the sources for DPI imports, whose modules
are read as black boxes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fecompiler.allflow.profile import user_top_module
from fecompiler.tools.common.filelist import read_complete_filelist
from fecompiler.tools.review.inputs import prepared_review_inputs

_SV_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_$]*"


_MODULE_DECL_RE = re.compile(rf"\bmodule\s+({_SV_IDENTIFIER})\b")


_ENDMODULE_RE = re.compile(r"\bendmodule\b")


_DPI_IMPORT_RE = re.compile(r'\bimport\s+"DPI-C"')


_LINE_COMMENT_RE = re.compile(r"//.*?$", re.MULTILINE)


_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _cpu_probe_inputs(workspace: dict[str, Any]) -> dict[str, Any]:
    prepared = prepared_review_inputs(workspace)
    if prepared is not None:
        inputs = {**prepared, "rtl_files": _without_skipped(prepared["rtl_files"], workspace)}
        inputs["filelist"] = str(
            workspace.get("cpu_filelist") or workspace.get("input_filelist") or ""
        ).strip()
        return inputs

    cpu_filelist = str(workspace.get("cpu_filelist", "")).strip()
    if cpu_filelist:
        parsed = _parse_filelist(cpu_filelist, workspace)
        parsed["filelist"] = str(Path(cpu_filelist).expanduser().resolve())
        return parsed

    if not str(workspace.get("soc_filelist", "")).strip():
        input_filelist = str(workspace.get("input_filelist", "")).strip()
        if input_filelist:
            parsed = _parse_filelist(input_filelist, workspace)
            parsed["filelist"] = str(Path(input_filelist).expanduser().resolve())
            return parsed

        origin_verilog = str(workspace.get("origin_verilog", "")).strip()
        if origin_verilog and Path(origin_verilog).exists():
            path = Path(origin_verilog).expanduser().resolve()
            return _inputs(rtl_files=[str(path)], incdirs=[str(path.parent)])

    return _inputs()


def _inputs(**fields: Any) -> dict[str, Any]:
    return {
        "filelist": "",
        "rtl_files": [],
        "incdirs": [],
        "defines": [],
        "libdirs": [],
        "libexts": [],
        **fields,
    }


def _parse_filelist(filelist: str, workspace: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = read_complete_filelist(filelist)
    except Exception:
        return _inputs()

    rtl_files = _without_skipped([str(path) for path in parsed.sources], workspace)
    incdirs = _unique_paths([
        *parsed.incdirs,
        # A listed header is included, not compiled; its directory has to be
        # searchable for the `include that names it.
        *(path.parent for path in parsed.headers),
        *(Path(path).parent for path in rtl_files),
    ])
    return _inputs(
        rtl_files=rtl_files,
        incdirs=incdirs,
        defines=list(parsed.defines),
        libdirs=_unique_paths(list(parsed.libdirs)),
        libexts=[str(item) for item in parsed.libexts],
    )


def _without_skipped(paths: list[str], workspace: dict[str, Any]) -> list[str]:
    """Leave out the sources the catalog says Yosys cannot read.

    A catalog core can name simulation-only files (``review_skip_files``);
    elaboration and lint still read them.
    """
    from fecompiler.catalog import core_review_skip_files

    skipped = set(core_review_skip_files(workspace.get("frontend_core_id")))
    return [str(path) for path in paths if Path(str(path)).name not in skipped]


def _dpi_blackbox_modules(inputs: dict[str, Any], *, exclude: set[str]) -> list[str]:
    """Find non-top modules whose implementation depends on DPI-C simulation code."""
    modules: list[str] = []
    seen: set[str] = set()
    for raw_path in inputs.get("rtl_files", []):
        text = _strip_sv_comments(_read_rtl_for_scan(Path(str(raw_path))))
        if not text or not _DPI_IMPORT_RE.search(text):
            continue
        for match in _MODULE_DECL_RE.finditer(text):
            end_match = _ENDMODULE_RE.search(text, match.end())
            body_end = end_match.start() if end_match else len(text)
            if not _DPI_IMPORT_RE.search(text, match.end(), body_end):
                continue
            name = match.group(1)
            if name in exclude or name in seen:
                continue
            seen.add(name)
            modules.append(name)
    return modules


def _unique_paths(paths: list[Path]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        text = str(path.expanduser().resolve())
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _review_defines(inputs: dict[str, Any]) -> list[str]:
    defines = ["SYNTHESIS", "YOSYS"]
    seen = {item.split("=", maxsplit=1)[0] for item in defines}
    for define in inputs.get("defines", []):
        text = str(define).strip()
        key = text.split("=", maxsplit=1)[0]
        if not text or key in seen:
            continue
        seen.add(key)
        defines.append(text)
    return defines


def _cpu_top(workspace: dict[str, Any], inputs: dict[str, Any] | None = None) -> str:
    """The top module the precheck elaborates: the user's design top.

    It is the module prepare checks, never one guessed from the sources; when
    the sources do not define it, Yosys reports that.
    """
    return user_top_module(workspace)


def _read_rtl_for_scan(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _strip_sv_comments(text: str) -> str:
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", text))
