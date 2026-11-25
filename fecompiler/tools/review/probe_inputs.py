"""Collect what the structural precheck reads: sources, defines, the top module.

Includes the lightweight scans of the sources themselves (module
declarations, instantiations, DPI imports) that complete and check the list.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fecompiler.allflow.profile import GENERIC_RTL, normalize_frontend_design_kind
from fecompiler.tools.common.filelist import read_complete_filelist
from fecompiler.tools.review.inputs import prepared_review_inputs

_AUTODISCOVER_SOURCE_LIMIT = 32


_AUTODISCOVER_EXTENSIONS = (".sv", ".v")


_SV_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_$]*"


_MODULE_DECL_RE = re.compile(rf"\bmodule\s+({_SV_IDENTIFIER})\b")


_ENDMODULE_RE = re.compile(r"\bendmodule\b")


_DPI_IMPORT_RE = re.compile(r'\bimport\s+"DPI-C"')


_SIMPLE_INSTANCE_RE = re.compile(rf"(?m)(?:^|[;\n])\s*({_SV_IDENTIFIER})\s+({_SV_IDENTIFIER})\s*\(")


_PARAM_INSTANCE_RE = re.compile(rf"(?ms)(?:^|[;\n])\s*({_SV_IDENTIFIER})\s*#\s*\(.*?\)\s*({_SV_IDENTIFIER})\s*\(")


_LINE_COMMENT_RE = re.compile(r"//.*?$", re.MULTILINE)


_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _cpu_probe_inputs(workspace: dict[str, Any]) -> dict[str, Any]:
    prepared = prepared_review_inputs(workspace)
    if prepared is not None:
        rtl_files = [
            str(path)
            for path in _filter_review_rtl_files(Path(item) for item in prepared["rtl_files"])
        ]
        completed = _complete_missing_module_sources({
            "rtl_files": rtl_files,
            "incdirs": _unique_paths([Path(item) for item in prepared["incdirs"]]),
            "defines": list(prepared["defines"]),
            "auto_discovered_rtl_files": [],
        })
        completed["filelist"] = str(
            workspace.get("cpu_filelist") or workspace.get("input_filelist") or ""
        ).strip()
        return completed

    cpu_filelist = str(workspace.get("cpu_filelist", "")).strip()
    if cpu_filelist:
        parsed = _parse_filelist(cpu_filelist)
        parsed["filelist"] = str(Path(cpu_filelist).expanduser().resolve())
        return parsed

    if not str(workspace.get("soc_filelist", "")).strip():
        input_filelist = str(workspace.get("input_filelist", "")).strip()
        if input_filelist:
            parsed = _parse_filelist(input_filelist)
            parsed["filelist"] = str(Path(input_filelist).expanduser().resolve())
            return parsed

        origin_verilog = str(workspace.get("origin_verilog", "")).strip()
        if origin_verilog and Path(origin_verilog).exists():
            path = Path(origin_verilog).expanduser().resolve()
            return {
                "filelist": "",
                "rtl_files": [str(path)],
                "incdirs": [str(path.parent)],
                "defines": [],
            }

    return {"filelist": "", "rtl_files": [], "incdirs": [], "defines": []}


def _parse_filelist(filelist: str) -> dict[str, Any]:
    try:
        parsed = read_complete_filelist(filelist)
    except Exception:
        return {"rtl_files": [], "incdirs": [], "defines": []}

    rtl_files = [str(path) for path in _filter_review_rtl_files(parsed.sources)]
    incdirs = _unique_paths([
        *parsed.incdirs,
        # A listed header is included, not compiled; its directory has to be
        # searchable for the `include that names it.
        *(path.parent for path in parsed.headers),
        *(Path(path).parent for path in rtl_files),
        # Library directories (-y) are searched for modules the listed
        # sources instantiate but do not define.
        *parsed.libdirs,
    ])
    return _complete_missing_module_sources({
        "rtl_files": rtl_files,
        "incdirs": incdirs,
        "defines": list(parsed.defines),
        "auto_discovered_rtl_files": [],
    })


def _filter_review_rtl_files(paths: Any) -> list[Path]:
    return [path for path in paths if not _is_review_excluded_rtl(path)]


def _is_review_excluded_rtl(path: Path) -> bool:
    """Skip simulation-only sources that should not participate in synthesis precheck."""
    name = path.name
    if name in {"instr_tracer.sv", "instr_tracer_if.sv"} and "/thirdparty/cva6/" in str(path):
        return True
    return False


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


def _complete_missing_module_sources(inputs: dict[str, Any]) -> dict[str, Any]:
    """Add local same-name sources for modules omitted by compact filelists.

    Some RTL generators and simulators accept a filelist that names the main
    sources and relies on the source/include directories to locate same-name
    helper modules.  Yosys read_slang does not reliably do that, so Review fills
    in only nearby CPU-local `ModuleName.sv` / `ModuleName.v` files.
    """
    completed = dict(inputs)
    rtl_files = [str(path) for path in completed.get("rtl_files", [])]
    seen = set(rtl_files)
    discovered: list[str] = []

    for _ in range(4):
        declared = {item["name"] for item in _module_declarations_from_inputs({"rtl_files": rtl_files})}
        missing = [
            name
            for name in sorted(_instantiated_module_candidates_from_inputs({"rtl_files": rtl_files}) - declared)
            if _should_autodiscover_module(name)
        ]
        added = False
        for module_name in missing:
            path = _find_local_module_source(module_name, rtl_files, completed.get("incdirs", []))
            if not path or str(path) in seen:
                continue
            text = str(path)
            rtl_files.append(text)
            seen.add(text)
            discovered.append(text)
            added = True
            if len(discovered) >= _AUTODISCOVER_SOURCE_LIMIT:
                break
        if not added or len(discovered) >= _AUTODISCOVER_SOURCE_LIMIT:
            break

    completed["rtl_files"] = rtl_files
    completed["incdirs"] = _unique_paths([
        *(Path(path) for path in completed.get("incdirs", [])),
        *(Path(path).parent for path in rtl_files),
    ])
    completed["auto_discovered_rtl_files"] = discovered
    return completed


def _instantiated_module_candidates_from_inputs(inputs: dict[str, Any]) -> set[str]:
    candidates: set[str] = set()
    for raw_path in inputs.get("rtl_files", []):
        text = _strip_sv_comments(_read_rtl_for_scan(Path(str(raw_path))))
        if not text:
            continue
        for pattern in (_SIMPLE_INSTANCE_RE, _PARAM_INSTANCE_RE):
            for match in pattern.finditer(text):
                module_name = match.group(1)
                instance_name = match.group(2)
                if _is_sv_keyword(module_name) or _is_sv_keyword(instance_name):
                    continue
                candidates.add(module_name)
    return candidates


def _should_autodiscover_module(module_name: str) -> bool:
    return bool(module_name) and not _is_sv_keyword(module_name) and not _is_sv_primitive(module_name)


def _find_local_module_source(module_name: str, rtl_files: list[str], incdirs: list[str]) -> Path | None:
    roots = _unique_paths([
        *(Path(path).parent for path in rtl_files),
        *(Path(path) for path in incdirs),
    ])
    for root_text in roots:
        root = Path(root_text)
        for suffix in _AUTODISCOVER_EXTENSIONS:
            candidate = root / f"{module_name}{suffix}"
            if _is_review_excluded_rtl(candidate):
                continue
            if candidate.is_file() and _file_declares_module(candidate, module_name):
                return candidate.resolve()
    return None


def _file_declares_module(path: Path, module_name: str) -> bool:
    text = _strip_sv_comments(_read_rtl_for_scan(path))
    return any(match.group(1) == module_name for match in _MODULE_DECL_RE.finditer(text))


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
    inputs = inputs or {}
    if normalize_frontend_design_kind(workspace.get("frontend_design_kind")) == GENERIC_RTL:
        return str(workspace.get("top_module", "")).strip()
    for field in ("cpu_wrapper_top", "cpu_top_module"):
        text = str(workspace.get(field, "")).strip()
        if text and _top_is_declared_or_unscanned(text, inputs):
            return text
    top = str(workspace.get("top_module", "")).strip()
    if top and top != "ecos_sim_top" and _top_is_declared_or_unscanned(top, inputs):
        return top
    return _infer_top_from_inputs(inputs)


def _top_is_declared_or_unscanned(top: str, inputs: dict[str, Any]) -> bool:
    rtl_files = inputs.get("rtl_files", [])
    if not rtl_files:
        return True
    return _module_declared_in_inputs(top, inputs)


def _module_declared_in_inputs(top: str, inputs: dict[str, Any]) -> bool:
    if not top:
        return False
    return any(item["name"] == top for item in _module_declarations_from_inputs(inputs))


def _infer_top_from_inputs(inputs: dict[str, Any]) -> str:
    declarations = _module_declarations_from_inputs(inputs)
    if not declarations:
        return ""
    declared = [item["name"] for item in declarations]
    instantiated = _instantiated_modules_from_inputs(inputs, declared)
    candidates = [item for item in declarations if item["name"] not in instantiated]
    if not candidates:
        candidates = declarations
    return max(candidates, key=_top_candidate_score)["name"]


def _module_declarations_from_inputs(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    declarations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path in inputs.get("rtl_files", []):
        path = Path(str(raw_path))
        text = _read_rtl_for_scan(path)
        if not text:
            continue
        text = _strip_sv_comments(text)
        for match in _MODULE_DECL_RE.finditer(text):
            name = match.group(1)
            if name in seen:
                continue
            seen.add(name)
            declarations.append({
                "name": name,
                "path": str(path),
                "order": len(declarations),
            })
    return declarations


def _instantiated_modules_from_inputs(inputs: dict[str, Any], declared: list[str]) -> set[str]:
    declared_set = set(declared)
    instantiated: set[str] = set()
    for raw_path in inputs.get("rtl_files", []):
        text = _strip_sv_comments(_read_rtl_for_scan(Path(str(raw_path))))
        if not text:
            continue
        for pattern in (_SIMPLE_INSTANCE_RE, _PARAM_INSTANCE_RE):
            for match in pattern.finditer(text):
                module_name = match.group(1)
                instance_name = match.group(2)
                if module_name in declared_set and not _is_sv_keyword(instance_name):
                    instantiated.add(module_name)
    return instantiated


def _top_candidate_score(candidate: dict[str, Any]) -> tuple[int, int]:
    name = str(candidate.get("name", ""))
    lower = name.lower()
    score = 0
    if lower.endswith("top"):
        score += 60
    if "top" in lower:
        score += 30
    if "cpu" in lower:
        score += 20
    if "core" in lower:
        score += 10
    return score, int(candidate.get("order", 0))


def _read_rtl_for_scan(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _strip_sv_comments(text: str) -> str:
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", text))


def _is_sv_keyword(text: str) -> bool:
    return text in {
        "always",
        "assign",
        "begin",
        "case",
        "else",
        "end",
        "for",
        "foreach",
        "forever",
        "function",
        "generate",
        "if",
        "initial",
        "module",
        "package",
        "program",
        "task",
        "while",
    }


def _is_sv_primitive(text: str) -> bool:
    return text in {
        "and",
        "buf",
        "bufif0",
        "bufif1",
        "cmos",
        "nand",
        "nmos",
        "nor",
        "not",
        "notif0",
        "notif1",
        "or",
        "pmos",
        "pulldown",
        "pullup",
        "rcmos",
        "rnmos",
        "rpmos",
        "rtran",
        "rtranif0",
        "rtranif1",
        "tran",
        "tranif0",
        "tranif1",
        "xnor",
        "xor",
    }
