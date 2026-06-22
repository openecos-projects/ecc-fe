"""CPU-only Yosys precheck for RTL Review.

This analyzer intentionally stops before backend implementation.  It uses Yosys
only as an RTL structure checker for Review Center: read CPU RTL, run hierarchy,
process lowering, check, and stat, then summarize risks.  It does not run tech
mapping, ABC, PDK-aware implementation, STA, or netlist handoff.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fecompiler.tools.prepare.runner import PrepareStep

_STRUCTURAL_STAT = "yosys_precheck_stat.json"
_STRUCTURAL_NETLIST = "yosys_precheck_netlist.json"
_STRUCTURAL_LOG = "yosys_precheck.log"
_STRUCTURAL_REPORT = "yosys_precheck.json"
_STRUCTURAL_SCRIPT = "yosys_precheck.ys"
_TIMEOUT_SECONDS = 45
_HIGH_FANOUT_THRESHOLD = 64
_HIGH_FANIN_THRESHOLD = 32
_DEEP_COMB_DEPTH_THRESHOLD = 16
_AUTODISCOVER_SOURCE_LIMIT = 32
_AUTODISCOVER_EXTENSIONS = (".sv", ".v")
_SV_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_$]*"
_MODULE_DECL_RE = re.compile(rf"\bmodule\s+({_SV_IDENTIFIER})\b")
_SIMPLE_INSTANCE_RE = re.compile(rf"(?m)(?:^|[;\n])\s*({_SV_IDENTIFIER})\s+({_SV_IDENTIFIER})\s*\(")
_PARAM_INSTANCE_RE = re.compile(rf"(?ms)(?:^|[;\n])\s*({_SV_IDENTIFIER})\s*#\s*\(.*?\)\s*({_SV_IDENTIFIER})\s*\(")
_LINE_COMMENT_RE = re.compile(r"//.*?$", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def run_structural_probe(workspace: dict[str, Any], step: Any) -> dict[str, Any]:
    """Run a bounded Yosys precheck for CPU RTL only."""
    report_dir = Path(step.report["dir"])
    script_dir = Path(step.script["dir"])
    report_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "report": str(report_dir / _STRUCTURAL_REPORT),
        "log": str(report_dir / _STRUCTURAL_LOG),
        "stat": str(report_dir / _STRUCTURAL_STAT),
        "netlist": str(report_dir / _STRUCTURAL_NETLIST),
        "script": str(script_dir / _STRUCTURAL_SCRIPT),
    }

    inputs = _cpu_probe_inputs(workspace)
    top_module = _cpu_top(workspace, inputs)
    if not inputs["rtl_files"]:
        return _write_probe(paths, {
            "status": "skipped",
            "mode": "cpu_only_yosys_precheck",
            "scope": "cpu",
            "tool": "yosys",
            "title": "Yosys Precheck",
            "reason": "no CPU RTL files found",
            "top_module": top_module,
            "inputs": inputs,
            "metrics": {},
            "module_risks": [],
            "quality": _quality("skipped"),
            "diagnostics": [],
            "issues": [],
            "artifacts": paths,
        })

    yosys = _resolve_yosys()
    if not yosys:
        return _write_probe(paths, {
            "status": "unavailable",
            "mode": "cpu_only_yosys_precheck",
            "scope": "cpu",
            "tool": "yosys",
            "title": "Yosys Precheck",
            "reason": _yosys_not_found_reason(),
            "resolution": _yosys_resolution_report(),
            "top_module": top_module,
            "inputs": inputs,
            "metrics": {},
            "module_risks": [],
            "quality": _quality("unavailable"),
            "diagnostics": [],
            "issues": [],
            "artifacts": paths,
        })

    script_path = Path(paths["script"])
    log_path = Path(paths["log"])
    stat_path = Path(paths["stat"])
    netlist_path = Path(paths["netlist"])
    frontend = _select_yosys_frontend(yosys, inputs)
    script_path.write_text(
        _build_yosys_script(inputs, workspace, stat_path, netlist_path, frontend=frontend),
        encoding="utf-8",
    )

    try:
        command = [yosys, "-s", str(script_path)]
        result = subprocess.run(
            command,
            cwd=str(script_dir),
            capture_output=True,
            text=True,
            env=_yosys_runtime_env(yosys),
            timeout=_TIMEOUT_SECONDS,
        )
        output = (result.stdout or "") + (result.stderr or "")
        log_path.write_text(output, encoding="utf-8")
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + (
            (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        )
        log_path.write_text(output + f"\n[rtl-review] Yosys precheck timed out after {_TIMEOUT_SECONDS}s\n", encoding="utf-8")
        return _write_probe(paths, {
            "status": "timeout",
            "mode": "cpu_only_yosys_precheck",
            "scope": "cpu",
            "tool": "yosys",
            "frontend": frontend,
            "title": "Yosys Precheck",
            "reason": "Yosys precheck timed out",
            "top_module": top_module,
            "inputs": inputs,
            "metrics": {},
            "module_risks": [],
            "quality": _quality("timeout"),
            "diagnostics": _diagnostics_from_log(output),
            "issues": [_issue(
                "warning",
                "tooling",
                "Yosys precheck timed out",
                "The CPU RTL Yosys precheck did not finish within the bounded review timeout.",
                recommendation="Run elaboration/lint for detailed diagnostics, then retry Review after reducing unsupported constructs.",
            )],
            "artifacts": paths,
        })
    except OSError as exc:
        log_path.write_text(f"[rtl-review] failed to launch yosys: {exc}\n", encoding="utf-8")
        return _write_probe(paths, {
            "status": "failed",
            "mode": "cpu_only_yosys_precheck",
            "scope": "cpu",
            "tool": "yosys",
            "frontend": frontend,
            "title": "Yosys Precheck",
            "reason": str(exc),
            "top_module": top_module,
            "inputs": inputs,
            "metrics": {},
            "module_risks": [],
            "quality": _quality("failed"),
            "diagnostics": [],
            "issues": [_issue(
                "warning",
                "tooling",
                "Yosys precheck could not start",
                "Yosys was found but could not be launched by the Review step.",
                recommendation="Check Yosys permissions and runtime dependencies.",
            )],
            "artifacts": paths,
        })

    stat = _read_json(stat_path)
    diagnostics = _diagnostics_from_log(output)
    netlist = _read_json(netlist_path)
    metrics = _metrics_from_stat(stat, output)
    metrics.update(_structural_metrics_from_netlist(netlist))
    module_risks = _module_risks_from_stat(stat, metrics)
    quality = _quality_from_run(result.returncode, diagnostics, metrics)
    issues = _issues_from_probe(output, diagnostics, metrics, result.returncode)
    status = "success" if result.returncode == 0 else "failed"
    reason = "" if status == "success" else _yosys_failure_reason(diagnostics, output, result.returncode)

    return _write_probe(paths, {
        "status": status,
        "mode": "cpu_only_yosys_precheck",
        "scope": "cpu",
        "tool": "yosys",
        "frontend": frontend,
        "title": "Yosys Precheck",
        "reason": reason,
        "returncode": int(result.returncode),
        "command": command,
        "top_module": top_module,
        "inputs": inputs,
        "diagnostics": diagnostics,
        "metrics": metrics,
        "module_risks": module_risks,
        "quality": quality,
        "issues": issues,
        "artifacts": paths,
    })


def _cpu_probe_inputs(workspace: dict[str, Any]) -> dict[str, Any]:
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
        parsed = PrepareStep._parse_sv_filelist(filelist)
    except Exception:
        return {"rtl_files": [], "incdirs": [], "defines": []}

    rtl_files = [
        str(path)
        for path in _filter_review_rtl_files(
            Path(path).expanduser().resolve() for path in parsed.get("rtl_files", [])
        )
    ]
    incdirs = _unique_paths([
        *(Path(path).expanduser().resolve() for path in parsed.get("incdirs", [])),
        *(Path(path).parent for path in rtl_files),
    ])
    return _complete_missing_module_sources({
        "rtl_files": rtl_files,
        "incdirs": incdirs,
        "defines": [str(define) for define in parsed.get("defines", [])],
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


def _resolve_yosys() -> str:
    for candidate in _yosys_candidate_paths():
        if candidate and Path(candidate).exists():
            return candidate
    return shutil.which("yosys") or ""


def _yosys_candidate_paths() -> list[str]:
    candidates: list[str] = []
    executable_name = "yosys.exe" if os.name == "nt" else "yosys"

    for env_name in ("YOSYS", "ECOS_YOSYS", "ECOS_OSS_CAD_BIN"):
        value = os.getenv(env_name, "").strip()
        if value:
            candidates.append(value)

    for env_name in ("CHIPCOMPILER_OSS_CAD_DIR", "ECOS_ELECTRON_OSS_CAD_DIR", "ECOS_OSS_CAD_DIR"):
        value = os.getenv(env_name, "").strip()
        if value:
            candidates.append(str(Path(value) / "bin" / executable_name))

    candidates.extend(str(path) for path in _bazel_oss_cad_yosys_candidates(executable_name))
    return _unique_strings(candidates)


def _bazel_oss_cad_yosys_candidates(executable_name: str) -> list[Path]:
    """Find oss-cad-suite yosys from Bazel's local external repository cache.

    ECOS Studio's GUI process may not inherit PATH entries created during
    Bazel packaging, but `make gui` often leaves oss-cad-suite under the Bazel
    output base.  This fallback is read-only and only uses an executable if it
    already exists locally.
    """
    roots: list[Path] = []
    env_output_base = os.getenv("BAZEL_OUTPUT_BASE", "").strip()
    if env_output_base:
        roots.append(Path(env_output_base))

    home = Path.home()
    roots.extend([
        home / ".cache" / "bazel" / f"_bazel_{os.getenv('USER', '')}",
        home / ".cache" / "bazel",
    ])

    candidates: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for pattern in (
            f"*/external/*oss_cad_suite*/oss-cad-suite/bin/{executable_name}",
            f"*/external/*oss-cad-suite*/oss-cad-suite/bin/{executable_name}",
            f"*/external/*oss_cad_suite*/bin/{executable_name}",
            f"*/external/*oss-cad-suite*/bin/{executable_name}",
        ):
            for path in root.glob(pattern):
                key = str(path)
                if key in seen or not path.is_file():
                    continue
                seen.add(key)
                candidates.append(path)
                if len(candidates) >= 8:
                    return candidates
    return candidates


def _yosys_runtime_env(yosys: str) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)

    yosys_path = Path(yosys)
    root = yosys_path.parent.parent if yosys_path.exists() and yosys_path.parent.name == "bin" else _yosys_root_from_env()
    if root is None:
        return env

    bin_dir = str(root / "bin")
    current_path = env.get("PATH", "")
    if bin_dir not in current_path.split(os.pathsep):
        env["PATH"] = f"{bin_dir}{os.pathsep}{current_path}".rstrip(os.pathsep)
    env.setdefault("CHIPCOMPILER_OSS_CAD_DIR", str(root))
    env.setdefault("ECOS_ELECTRON_OSS_CAD_DIR", str(root))

    share_dir = root / "share" / "yosys"
    if (share_dir / "plugins").exists():
        env.setdefault("YOSYS_PLUGINPATH", str(share_dir / "plugins"))
    if (share_dir / "techlibs").exists():
        env.setdefault("YOSYS_DATDIR", str(share_dir))
    return env


def _yosys_supports_slang(yosys: str) -> bool:
    root = _yosys_root_from_executable(yosys) or _yosys_root_from_env()
    if root is not None and _yosys_slang_plugin_exists(root):
        return True
    for raw in os.getenv("YOSYS_PLUGINPATH", "").split(os.pathsep):
        if raw and _plugin_dir_has_slang(Path(raw)):
            return True
    return False


def _yosys_root_from_executable(yosys: str) -> Path | None:
    yosys_path = Path(yosys)
    if yosys_path.exists() and yosys_path.parent.name == "bin":
        return yosys_path.parent.parent
    return None


def _yosys_slang_plugin_exists(root: Path) -> bool:
    plugin_dir = root / "share" / "yosys" / "plugins"
    return _plugin_dir_has_slang(plugin_dir)


def _plugin_dir_has_slang(plugin_dir: Path) -> bool:
    if not plugin_dir.exists():
        return False
    plugin_names = ("slang.so", "slang.dll", "slang.dylib")
    return any((plugin_dir / name).exists() for name in plugin_names)


def _yosys_root_from_env() -> Path | None:
    for env_name in ("CHIPCOMPILER_OSS_CAD_DIR", "ECOS_ELECTRON_OSS_CAD_DIR", "ECOS_OSS_CAD_DIR"):
        value = os.getenv(env_name, "").strip()
        if value:
            return Path(value)
    return None


def _yosys_not_found_reason() -> str:
    candidates = _yosys_resolution_report()
    checked = [item["path"] for item in candidates if item.get("path")]
    if checked:
        return "yosys executable not found; checked " + ", ".join(checked[:6])
    return "yosys executable not found; set YOSYS, ECOS_YOSYS, CHIPCOMPILER_OSS_CAD_DIR, use Bazel-provided oss-cad-suite, or install yosys in PATH"


def _yosys_resolution_report() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sources: list[tuple[str, str]] = []
    for env_name in ("YOSYS", "ECOS_YOSYS", "ECOS_OSS_CAD_BIN"):
        value = os.getenv(env_name, "").strip()
        if value:
            sources.append((env_name, value))
    executable_name = "yosys.exe" if os.name == "nt" else "yosys"
    for env_name in ("CHIPCOMPILER_OSS_CAD_DIR", "ECOS_ELECTRON_OSS_CAD_DIR", "ECOS_OSS_CAD_DIR"):
        value = os.getenv(env_name, "").strip()
        if value:
            sources.append((env_name, str(Path(value) / "bin" / executable_name)))
    path_candidate = shutil.which("yosys")
    if path_candidate:
        sources.append(("PATH", path_candidate))
    for candidate in _bazel_oss_cad_yosys_candidates(executable_name):
        sources.append(("BAZEL_OSS_CAD_SUITE", str(candidate)))

    for source, path in sources:
        rows.append({
            "source": source,
            "path": path,
            "exists": Path(path).exists(),
        })
    return rows


def _build_yosys_script(
    inputs: dict[str, Any],
    workspace: dict[str, Any],
    stat_path: Path,
    netlist_path: Path,
    *,
    frontend: str = "read_verilog",
) -> str:
    top = _cpu_top(workspace, inputs)
    read_lines = (
        _build_read_slang_script(inputs, top)
        if frontend == "read_slang"
        else [_build_read_verilog_command(inputs)]
    )

    hierarchy = f"hierarchy -top {_quote_yosys(top, quote=False)} -check" if top else "hierarchy -auto-top -check"
    return "\n".join([
        "# Auto-generated by ECOS frontend RTL Review.",
        "# CPU-only Yosys precheck. This script stops before implementation and netlist handoff.",
        *read_lines,
        hierarchy,
        "proc",
        "opt_expr",
        "opt_clean",
        "check",
        f"tee -o {_quote_yosys(str(stat_path))} stat -json",
        f"write_json {_quote_yosys(str(netlist_path))}",
        "",
    ])


def _select_yosys_frontend(yosys: str, inputs: dict[str, Any]) -> str:
    if not _yosys_supports_slang(yosys):
        return "read_verilog"
    return "read_slang" if _has_systemverilog_sources(inputs) else "read_verilog"


def _has_systemverilog_sources(inputs: dict[str, Any]) -> bool:
    for path in inputs.get("rtl_files", []):
        if str(path).lower().endswith((".sv", ".svh")):
            return True
    return False


def _build_read_verilog_command(inputs: dict[str, Any]) -> str:
    read_args: list[str] = ["read_verilog", "-sv", "-defer"]
    for incdir in inputs.get("incdirs", []):
        read_args.append(f"-I{_quote_yosys(str(incdir), quote=False)}")
    for define in _review_defines(inputs):
        read_args.append(f"-D{_quote_yosys(str(define), quote=False)}")
    read_args.extend(_quote_yosys(str(path)) for path in inputs.get("rtl_files", []))
    return " ".join(read_args)


def _build_read_slang_script(inputs: dict[str, Any], top: str) -> list[str]:
    read_args: list[str] = [
        "read_slang",
        "--compat-mode",
        "--allow-use-before-declare",
        "--ignore-timing",
        "--ignore-assertions",
        "-Wduplicate-definition",
    ]
    if top:
        read_args.extend(["--top", _quote_yosys(top, quote=False)])
    for incdir in inputs.get("incdirs", []):
        read_args.append(f"-I{_quote_yosys(str(incdir), quote=False)}")
    for define in _review_defines(inputs):
        read_args.append(f"+define+{_quote_yosys(str(define), quote=False)}")
    read_args.extend(_quote_yosys(str(path), quote=False) for path in inputs.get("rtl_files", []))
    return [
        "plugin -i slang",
        " ".join(read_args),
    ]


def _quote_yosys(value: str, *, quote: bool = True) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"' if quote else escaped


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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _metrics_from_stat(stat: dict[str, Any], log: str) -> dict[str, Any]:
    design = stat.get("design", {}) if isinstance(stat.get("design"), dict) else {}
    modules = stat.get("modules", {}) if isinstance(stat.get("modules"), dict) else {}
    module_values = [item for item in modules.values() if isinstance(item, dict)]

    cell_types: dict[str, int] = {}
    for module in module_values:
        for key, value in _extract_cell_types(module).items():
            cell_types[key] = cell_types.get(key, 0) + value

    num_cells = _int_or_zero(design.get("num_cells"))
    if not num_cells:
        num_cells = sum(_int_or_zero(module.get("num_cells")) for module in module_values)
    if not num_cells and cell_types:
        num_cells = sum(cell_types.values())

    num_wires = _int_or_zero(design.get("num_wires"))
    if not num_wires:
        num_wires = sum(_int_or_zero(module.get("num_wires")) for module in module_values)

    num_ports = _int_or_zero(design.get("num_port_bits"))
    if not num_ports:
        num_ports = sum(_int_or_zero(module.get("num_port_bits")) for module in module_values)

    num_processes = sum(_int_or_zero(module.get("num_processes")) for module in module_values)
    memory_cells = sum(count for name, count in cell_types.items() if "$mem" in name.lower() or "mem" in name.lower())
    mux_cells = sum(count for name, count in cell_types.items() if "$mux" in name.lower() or "$pmux" in name.lower())
    arithmetic_cells = sum(
        count
        for name, count in cell_types.items()
        if any(op in name.lower() for op in ("$add", "$sub", "$mul", "$div", "$mod", "$alu"))
    )

    return {
        "source": "yosys_stat",
        "modules": len(modules),
        "module_names": sorted(str(name).lstrip("\\") for name in modules.keys())[:80],
        "cells": num_cells,
        "wires": num_wires,
        "port_bits": num_ports,
        "processes": num_processes,
        "memory_cells": memory_cells,
        "mux_cells": mux_cells,
        "arithmetic_cells": arithmetic_cells,
        "cell_types": dict(sorted(cell_types.items())[:80]),
        "log_warnings": len(re.findall(r"\bwarning\b", log, flags=re.I)),
        "log_errors": len(re.findall(r"\berror\b", log, flags=re.I)),
    }


def _structural_metrics_from_netlist(netlist: dict[str, Any]) -> dict[str, Any]:
    modules = netlist.get("modules", {}) if isinstance(netlist.get("modules"), dict) else {}
    summaries: list[dict[str, Any]] = []
    high_fanout_nets: list[dict[str, Any]] = []
    high_fanin_cells: list[dict[str, Any]] = []
    deep_comb_paths: list[dict[str, Any]] = []
    cycles: list[str] = []

    for raw_name, raw_module in modules.items():
        if not isinstance(raw_module, dict):
            continue
        module_name = str(raw_name).lstrip("\\")
        summary = _module_graph_summary(module_name, raw_module)
        summaries.append(summary)
        high_fanout_nets.extend(summary.get("high_fanout_nets", []))
        high_fanin_cells.extend(summary.get("high_fanin_cells", []))
        if _int_or_zero(summary.get("max_comb_depth")) >= _DEEP_COMB_DEPTH_THRESHOLD:
            deep_comb_paths.append({
                "module": module_name,
                "depth": summary.get("max_comb_depth", 0),
                "endpoint": summary.get("deepest_endpoint", ""),
                "source": summary.get("deepest_source", ""),
                "line": summary.get("deepest_line", 0),
                "column": summary.get("deepest_column", 0),
            })
        if summary.get("comb_cycle"):
            cycles.append(module_name)

    summaries.sort(key=lambda item: (
        _int_or_zero(item.get("max_comb_depth")),
        _int_or_zero(item.get("max_fanout")),
        _int_or_zero(item.get("cell_count")),
    ), reverse=True)
    high_fanout_nets.sort(key=lambda item: _int_or_zero(item.get("fanout")), reverse=True)
    high_fanin_cells.sort(key=lambda item: _int_or_zero(item.get("fanin")), reverse=True)
    deep_comb_paths.sort(key=lambda item: _int_or_zero(item.get("depth")), reverse=True)

    return {
        "netlist_source": "yosys_write_json",
        "max_fanout": _max_int(high_fanout_nets, "fanout") or _max_int(summaries, "max_fanout"),
        "max_fanin": _max_int(high_fanin_cells, "fanin") or _max_int(summaries, "max_fanin"),
        "max_comb_depth": _max_int(deep_comb_paths, "depth") or _max_int(summaries, "max_comb_depth"),
        "high_fanout_nets": high_fanout_nets[:20],
        "high_fanin_cells": high_fanin_cells[:20],
        "deep_comb_paths": deep_comb_paths[:20],
        "comb_cycle_modules": cycles[:20],
        "module_structure": summaries[:40],
    }


def _module_graph_summary(module_name: str, module: dict[str, Any]) -> dict[str, Any]:
    cells = module.get("cells", {}) if isinstance(module.get("cells"), dict) else {}
    ports = module.get("ports", {}) if isinstance(module.get("ports"), dict) else {}
    netnames = module.get("netnames", {}) if isinstance(module.get("netnames"), dict) else {}
    drivers: dict[int, list[str]] = {}
    consumers: dict[int, list[tuple[str, str]]] = {}
    cell_inputs: dict[str, set[int]] = {}
    cell_outputs: dict[str, set[int]] = {}
    cell_locations: dict[str, dict[str, Any]] = {}
    bit_locations: dict[int, dict[str, Any]] = {}
    comb_cells: set[str] = set()
    seq_cells: set[str] = set()

    for raw_net in netnames.values():
        if not isinstance(raw_net, dict):
            continue
        location = _source_location_from_attrs(raw_net.get("attributes"))
        if not location.get("source"):
            continue
        for bit in _connection_bits(raw_net.get("bits")):
            bit_locations.setdefault(bit, location)

    for port_name, raw_port in ports.items():
        if not isinstance(raw_port, dict):
            continue
        direction = str(raw_port.get("direction", ""))
        bits = _connection_bits(raw_port.get("bits"))
        if direction == "input":
            for bit in bits:
                drivers.setdefault(bit, []).append(f"port:{port_name}")
        elif direction == "output":
            for bit in bits:
                consumers.setdefault(bit, []).append((f"port:{port_name}", "out"))

    for raw_cell_name, raw_cell in cells.items():
        if not isinstance(raw_cell, dict):
            continue
        cell_name = str(raw_cell_name).lstrip("\\")
        cell_type = str(raw_cell.get("type", ""))
        location = _source_location_from_attrs(raw_cell.get("attributes"))
        if location.get("source"):
            cell_locations[cell_name] = location
        connections = raw_cell.get("connections", {}) if isinstance(raw_cell.get("connections"), dict) else {}
        directions = raw_cell.get("port_directions", {}) if isinstance(raw_cell.get("port_directions"), dict) else {}
        inputs: set[int] = set()
        outputs: set[int] = set()

        for port, raw_bits in connections.items():
            direction = str(directions.get(port, "")).lower()
            if not direction:
                direction = _infer_port_direction(cell_type, str(port))
            bits = _connection_bits(raw_bits)
            if direction == "output":
                outputs.update(bits)
                for bit in bits:
                    drivers.setdefault(bit, []).append(cell_name)
            elif direction == "input":
                inputs.update(bits)
                for bit in bits:
                    consumers.setdefault(bit, []).append((cell_name, str(port)))
            elif direction == "inout":
                inputs.update(bits)
                outputs.update(bits)
                for bit in bits:
                    consumers.setdefault(bit, []).append((cell_name, str(port)))
                    drivers.setdefault(bit, []).append(cell_name)

        cell_inputs[cell_name] = inputs
        cell_outputs[cell_name] = outputs
        if _is_sequential_cell(cell_type):
            seq_cells.add(cell_name)
        else:
            comb_cells.add(cell_name)

    max_fanout = 0
    high_fanout: list[dict[str, Any]] = []
    for bit, sinks in consumers.items():
        fanout = len([sink for sink, _ in sinks if not sink.startswith("port:")])
        if fanout > max_fanout:
            max_fanout = fanout
        if fanout >= _HIGH_FANOUT_THRESHOLD:
            location = (
                bit_locations.get(bit)
                or _first_cell_location(drivers.get(bit, []), cell_locations)
                or _first_cell_location([sink for sink, _ in sinks], cell_locations)
            )
            high_fanout.append({
                "module": module_name,
                "net": _bit_name(module, bit),
                "bit": bit,
                "fanout": fanout,
                "drivers": drivers.get(bit, [])[:6],
                **location,
            })

    max_fanin = 0
    high_fanin: list[dict[str, Any]] = []
    for cell_name, bits in cell_inputs.items():
        fanin = len(bits)
        if fanin > max_fanin:
            max_fanin = fanin
        if fanin >= _HIGH_FANIN_THRESHOLD:
            location = cell_locations.get(cell_name, {})
            high_fanin.append({
                "module": module_name,
                "cell": cell_name,
                "fanin": fanin,
                **location,
            })

    graph: dict[str, set[str]] = {cell: set() for cell in comb_cells}
    indegree: dict[str, int] = {cell: 0 for cell in comb_cells}
    for source in comb_cells:
        for bit in cell_outputs.get(source, set()):
            for sink, _ in consumers.get(bit, []):
                if sink == source or sink not in comb_cells:
                    continue
                if sink not in graph[source]:
                    graph[source].add(sink)
                    indegree[sink] += 1

    queue = [cell for cell, degree in indegree.items() if degree == 0]
    depth = {cell: 1 for cell in comb_cells}
    visited = 0
    deepest_endpoint = ""
    while queue:
        cell = queue.pop(0)
        visited += 1
        if depth.get(cell, 0) >= depth.get(deepest_endpoint, 0):
            deepest_endpoint = cell
        for sink in graph.get(cell, set()):
            depth[sink] = max(depth.get(sink, 1), depth.get(cell, 1) + 1)
            indegree[sink] -= 1
            if indegree[sink] == 0:
                queue.append(sink)

    has_cycle = bool(comb_cells) and visited < len(comb_cells)
    deepest_location = cell_locations.get(deepest_endpoint, {})
    return {
        "module": module_name,
        "cell_count": len(cells),
        "comb_cells": len(comb_cells),
        "sequential_cells": len(seq_cells),
        "max_fanout": max_fanout,
        "max_fanin": max_fanin,
        "max_comb_depth": max(depth.values()) if depth else 0,
        "deepest_endpoint": deepest_endpoint,
        "deepest_source": deepest_location.get("source", ""),
        "deepest_line": deepest_location.get("line", 0),
        "deepest_column": deepest_location.get("column", 0),
        "comb_cycle": has_cycle,
        "high_fanout_nets": sorted(high_fanout, key=lambda item: _int_or_zero(item.get("fanout")), reverse=True)[:8],
        "high_fanin_cells": sorted(high_fanin, key=lambda item: _int_or_zero(item.get("fanin")), reverse=True)[:8],
    }


def _first_cell_location(names: list[str], locations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for name in names:
        if name.startswith("port:"):
            continue
        location = locations.get(name)
        if location:
            return location
    return {}


def _source_location_from_attrs(attrs: Any) -> dict[str, Any]:
    if not isinstance(attrs, dict):
        return {}
    raw = str(attrs.get("src", "")).strip()
    if not raw:
        return {}
    first = raw.split("|", maxsplit=1)[0]
    match = re.search(
        r"(?P<source>.*?\.(?:sv|svh|v|vh)):(?P<line>\d+)(?:\.(?P<column>\d+))?",
        first,
    )
    if not match:
        return {}
    return {
        "source": match.group("source"),
        "line": _int_or_zero(match.group("line")),
        "column": _int_or_zero(match.groupdict().get("column")) or 1,
    }


def _connection_bits(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    bits: list[int] = []
    for item in value:
        if isinstance(item, int):
            bits.append(item)
    return bits


def _infer_port_direction(cell_type: str, port: str) -> str:
    cell = cell_type.lower()
    name = port.upper().lstrip("\\")
    if _is_sequential_cell(cell):
        return "output" if name in {"Q"} else "input"
    if name in {"Y", "Q", "O", "OUT", "C"}:
        return "output"
    return "input"


def _is_sequential_cell(cell_type: str) -> bool:
    text = cell_type.lower()
    return any(token in text for token in ("$dff", "$adff", "$sdff", "$dlatch", "$mem", "dff", "latch"))


def _bit_name(module: dict[str, Any], bit: int) -> str:
    netnames = module.get("netnames", {}) if isinstance(module.get("netnames"), dict) else {}
    for raw_name, raw_net in netnames.items():
        if not isinstance(raw_net, dict):
            continue
        bits = _connection_bits(raw_net.get("bits"))
        if bit in bits:
            return str(raw_name).lstrip("\\")
    return f"bit[{bit}]"


def _max_int(items: list[dict[str, Any]], key: str) -> int:
    return max((_int_or_zero(item.get(key)) for item in items), default=0)


def _module_risks_from_stat(stat: dict[str, Any], metrics: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    modules = stat.get("modules", {}) if isinstance(stat.get("modules"), dict) else {}
    structure_by_module = {
        str(item.get("module", "")): item
        for item in (metrics or {}).get("module_structure", [])
        if isinstance(item, dict)
    }
    risks: list[dict[str, Any]] = []
    for raw_name, raw_module in modules.items():
        if not isinstance(raw_module, dict):
            continue
        module_name = str(raw_name).lstrip("\\")
        structure = structure_by_module.get(module_name, {})
        cell_types = _extract_cell_types(raw_module)
        cells = _int_or_zero(raw_module.get("num_cells")) or sum(cell_types.values())
        wires = _int_or_zero(raw_module.get("num_wires"))
        ports = _int_or_zero(raw_module.get("num_port_bits"))
        processes = _int_or_zero(raw_module.get("num_processes"))
        mux_cells = sum(count for name, count in cell_types.items() if "$mux" in name.lower() or "$pmux" in name.lower())
        memory_cells = sum(count for name, count in cell_types.items() if "$mem" in name.lower() or "mem" in name.lower())
        arithmetic_cells = sum(
            count
            for name, count in cell_types.items()
            if any(op in name.lower() for op in ("$add", "$sub", "$mul", "$div", "$mod", "$alu"))
        )
        score = cells + wires // 2 + mux_cells * 3 + arithmetic_cells * 4 + memory_cells * 5 + processes * 12
        score += _int_or_zero(structure.get("max_comb_depth")) * 50
        score += _int_or_zero(structure.get("max_fanout")) * 8
        score += _int_or_zero(structure.get("max_fanin")) * 6
        reasons = _module_risk_reasons(
            cells=cells,
            wires=wires,
            processes=processes,
            mux_cells=mux_cells,
            arithmetic_cells=arithmetic_cells,
            memory_cells=memory_cells,
            max_fanout=_int_or_zero(structure.get("max_fanout")),
            max_fanin=_int_or_zero(structure.get("max_fanin")),
            max_comb_depth=_int_or_zero(structure.get("max_comb_depth")),
            comb_cycle=bool(structure.get("comb_cycle")),
        )
        if not reasons and score < 100:
            continue
        risks.append({
            "module": module_name,
            "score": score,
            "risk": _module_risk_bucket(score, reasons),
            "cells": cells,
            "wires": wires,
            "ports": ports,
            "processes": processes,
            "mux_cells": mux_cells,
            "arithmetic_cells": arithmetic_cells,
            "memory_cells": memory_cells,
            "max_fanout": _int_or_zero(structure.get("max_fanout")),
            "max_fanin": _int_or_zero(structure.get("max_fanin")),
            "max_comb_depth": _int_or_zero(structure.get("max_comb_depth")),
            "reasons": reasons,
            "top_cell_types": [
                {"type": name, "count": count}
                for name, count in sorted(cell_types.items(), key=lambda item: item[1], reverse=True)[:8]
            ],
        })
    return sorted(risks, key=lambda item: int(item.get("score", 0)), reverse=True)[:12]


def _module_risk_reasons(
    *,
    cells: int,
    wires: int,
    processes: int,
    mux_cells: int,
    arithmetic_cells: int,
    memory_cells: int,
    max_fanout: int = 0,
    max_fanin: int = 0,
    max_comb_depth: int = 0,
    comb_cycle: bool = False,
) -> list[str]:
    reasons: list[str] = []
    if cells >= 2000:
        reasons.append("large cell population")
    if wires >= 4000:
        reasons.append("large wire population")
    if mux_cells >= 200:
        reasons.append("mux-heavy control/data selection")
    if arithmetic_cells >= 40:
        reasons.append("arithmetic-heavy datapath")
    if memory_cells:
        reasons.append("inferred memory candidate")
    if processes >= 20:
        reasons.append("many lowered procedural blocks")
    if max_fanout >= _HIGH_FANOUT_THRESHOLD:
        reasons.append("high fanout net candidate")
    if max_fanin >= _HIGH_FANIN_THRESHOLD:
        reasons.append("wide fanin cell candidate")
    if max_comb_depth >= _DEEP_COMB_DEPTH_THRESHOLD:
        reasons.append("deep combinational path candidate")
    if comb_cycle:
        reasons.append("combinational cycle candidate")
    return reasons


def _module_risk_bucket(score: int, reasons: list[str]) -> str:
    if score >= 8000 or len(reasons) >= 3:
        return "high"
    if score >= 2500 or len(reasons) >= 2:
        return "medium"
    return "low"


def _extract_cell_types(module: dict[str, Any]) -> dict[str, int]:
    cells = module.get("num_cells_by_type")
    if isinstance(cells, dict):
        return {str(key): _int_or_zero(value) for key, value in cells.items()}
    cells = module.get("cells")
    if isinstance(cells, dict):
        out: dict[str, int] = {}
        for value in cells.values():
            if isinstance(value, dict):
                cell_type = str(value.get("type", ""))
                if cell_type:
                    out[cell_type] = out.get(cell_type, 0) + 1
        return out
    return {}


def _diagnostics_from_log(log: str) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for raw in log.splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        severity = ""
        if "error:" in lower or lower.startswith("error") or "syntax error" in lower:
            severity = "error"
        elif "warning:" in lower or lower.startswith("warning"):
            severity = "warning"
        if not severity:
            continue
        location = _diagnostic_location(line)
        diagnostics.append({
            "severity": severity,
            "message": line,
            "category": _diagnostic_category(line),
            "source": location.get("source", ""),
            "line": location.get("line", 0),
            "column": location.get("column", 0),
        })
        if len(diagnostics) >= 80:
            break
    return diagnostics


def _diagnostic_location(message: str) -> dict[str, Any]:
    patterns = (
        r"(?P<source>/[^:\s]+?\.(?:sv|svh|v|vh)):(?P<line>\d+):(?P<column>\d+)",
        r"(?P<source>/[^:\s]+?\.(?:sv|svh|v|vh)):(?P<line>\d+)",
        r"(?P<source>[^:\s]+?\.(?:sv|svh|v|vh)):(?P<line>\d+):(?P<column>\d+)",
        r"(?P<source>[^:\s]+?\.(?:sv|svh|v|vh)):(?P<line>\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if not match:
            continue
        source = str(match.group("source"))
        return {
            "source": source,
            "line": _int_or_zero(match.group("line")),
            "column": _int_or_zero(match.groupdict().get("column")) or 1,
        }
    return {"source": "", "line": 0, "column": 0}


def _diagnostic_category(message: str) -> str:
    text = message.lower()
    if _is_yosys_tool_limit_message(text):
        return "tool-limit"
    if "syntax" in text or "parse" in text:
        return "syntax"
    if "module" in text and ("not found" in text or "missing" in text):
        return "hierarchy"
    if "latch" in text:
        return "combinational"
    if "driver" in text or "driven" in text:
        return "structural"
    if "loop" in text:
        return "combinational"
    return "tooling"


def _is_yosys_tool_limit_message(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            "feature unimplemented",
            "failed condition",
            "slang_frontend.cc",
            "yosys-slang-plugin",
            "unsupported",
            "not supported",
        )
    )


def _quality_from_run(
    returncode: int,
    diagnostics: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    errors = sum(1 for item in diagnostics if item.get("severity") == "error")
    warnings = sum(1 for item in diagnostics if item.get("severity") == "warning")
    blocking_errors = sum(1 for item in diagnostics if _diagnostic_blocks_review(item))
    complexity_score = (
        _int_or_zero(metrics.get("cells"))
        + _int_or_zero(metrics.get("mux_cells")) * 2
        + _int_or_zero(metrics.get("arithmetic_cells")) * 3
        + _int_or_zero(metrics.get("memory_cells")) * 4
    )
    if returncode != 0 and not blocking_errors:
        gate = "warnings"
    elif blocking_errors:
        gate = "failed"
    elif warnings:
        gate = "warnings"
    else:
        gate = "clean"
    return {
        "gate": gate,
        "frontend_parse": "pass" if not blocking_errors else "fail",
        "hierarchy": "pass" if not blocking_errors else "fail",
        "structural_check": "warnings" if warnings or errors else "pass",
        "diagnostic_errors": errors,
        "diagnostic_warnings": warnings,
        "complexity": _complexity_bucket(complexity_score),
        "complexity_score": complexity_score,
    }


def _diagnostic_blocks_review(diagnostic: dict[str, Any]) -> bool:
    if str(diagnostic.get("severity", "")).lower() != "error":
        return False
    if str(diagnostic.get("category", "")).lower() == "tool-limit":
        return False
    return True


def _only_tool_limit_errors(diagnostics: list[dict[str, Any]]) -> bool:
    errors = [item for item in diagnostics if str(item.get("severity", "")).lower() == "error"]
    return bool(errors) and all(str(item.get("category", "")).lower() == "tool-limit" for item in errors)


def _quality(status: str) -> dict[str, Any]:
    return {
        "gate": status,
        "frontend_parse": status,
        "hierarchy": status,
        "structural_check": status,
        "diagnostic_errors": 0,
        "diagnostic_warnings": 0,
        "complexity": "unknown",
        "complexity_score": 0,
    }


def _complexity_bucket(score: int) -> str:
    if score >= 20000:
        return "very_high"
    if score >= 8000:
        return "high"
    if score >= 2500:
        return "medium"
    return "low"


def _yosys_failure_reason(diagnostics: list[dict[str, Any]], log: str, returncode: int) -> str:
    first_error = next((item for item in diagnostics if item.get("severity") == "error"), None)
    first_warning = next((item for item in diagnostics if item.get("severity") == "warning"), None)
    diagnostic = first_error or first_warning
    if diagnostic:
        source = str(diagnostic.get("source") or "")
        line = _int_or_zero(diagnostic.get("line"))
        prefix = f"{source}:{line}: " if source and line else ""
        return prefix + str(diagnostic.get("message") or "Yosys reported a diagnostic").strip()

    for raw in log.splitlines():
        line = raw.strip()
        if line:
            return f"Yosys exited with code {returncode}: {line[:180]}"
    return f"Yosys exited with code {returncode}"


def _issues_from_probe(
    log: str,
    diagnostics: list[dict[str, Any]],
    metrics: dict[str, Any],
    returncode: int,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    text = log.lower()

    if returncode != 0:
        blocking_error = any(_diagnostic_blocks_review(item) for item in diagnostics)
        tool_limited = _only_tool_limit_errors(diagnostics)
        first_location = _first_diagnostic_location(diagnostics)
        issues.append(_issue(
            "error" if blocking_error else "warning",
            "tooling" if tool_limited else "structural",
            "Yosys precheck hit a tool frontend limitation" if tool_limited else "Yosys precheck failed before completion",
            (
                "Yosys could not complete the optional structural probe because the selected frontend does not support one RTL construct."
                if tool_limited
                else "Yosys could not complete the CPU RTL precheck."
            ),
            path=first_location.get("source", ""),
            line=first_location.get("line", 0),
            column=first_location.get("column", 0),
            evidence={"returncode": returncode},
            recommendation=(
                "Use Elab/Lint as the source-level gate and treat Yosys structural metrics as unavailable for this run."
                if tool_limited
                else "Check the Yosys precheck log, then run Elab/Lint for source-level diagnostics."
            ),
        ))

    diagnostic_categories = {str(item.get("category", "")) for item in diagnostics}
    if "syntax" in diagnostic_categories:
        first_location = _first_diagnostic_location(diagnostics, category="syntax")
        issues.append(_issue(
            "error",
            "syntax",
            "Yosys reported RTL syntax/front-end errors",
            "The CPU RTL cannot pass the Yosys Verilog frontend in its current form.",
            path=first_location.get("source", ""),
            line=first_location.get("line", 0),
            column=first_location.get("column", 0),
            evidence={"diagnostic_errors": sum(1 for item in diagnostics if item.get("severity") == "error")},
            recommendation="Open the Yosys precheck log, fix the first syntax or unsupported construct error, then rerun Review.",
        ))

    if "hierarchy" in diagnostic_categories:
        issues.append(_issue(
            "error",
            "hierarchy",
            "Yosys could not resolve the CPU top hierarchy",
            "A missing or mismatched top module prevents downstream quality checks from being reliable.",
            recommendation="Confirm the selected CPU wrapper/top exists in the CPU filelist.",
        ))

    if "logic loop" in text or "combinational loop" in text:
        issues.append(_issue(
            "error",
            "combinational",
            "Combinational loop reported by Yosys precheck",
            "A combinational feedback loop can break simulation convergence and timing analysis.",
            recommendation="Break the loop with a register or correct the ready/valid/control dependency.",
        ))

    if "multiple conflicting drivers" in text or "multiple drivers" in text:
        issues.append(_issue(
            "error",
            "structural",
            "Multiple drivers reported by Yosys precheck",
            "Multiple drivers on the same net can create X propagation or non-synthesizable logic.",
            recommendation="Ensure each signal has exactly one procedural or continuous driver.",
        ))

    if "no driver" in text or "undriven" in text:
        issues.append(_issue(
            "warning",
            "structural",
            "Undriven signal reported by Yosys precheck",
            "Undriven nets can become constants, X sources, or backend optimization surprises.",
            recommendation="Tie off unused inputs explicitly and drive every architecturally visible net.",
        ))

    if "latch inferred" in text or "inferring latch" in text:
        issues.append(_issue(
            "warning",
            "combinational",
            "Latch inference reported by Yosys precheck",
            "Unintended latches make timing, reset, and implementation mapping harder to control.",
            recommendation="Assign defaults in combinational blocks and cover all control branches.",
        ))

    if _int_or_zero(metrics.get("mux_cells")) >= 200:
        issues.append(_issue(
            "info",
            "timing",
            "Large mux population candidate",
            "A high mux count often points to deep decode, bypass, or CSR selection cones.",
            evidence={"mux_cells": metrics.get("mux_cells")},
            recommendation="Inspect the largest select cones and consider staging or one-hot structure.",
        ))

    if _int_or_zero(metrics.get("memory_cells")) > 0:
        issues.append(_issue(
            "info",
            "memory",
            "Inferred memory candidate",
            "Inferred memories should be checked against the target memory template and inference style.",
            evidence={"memory_cells": metrics.get("memory_cells")},
            recommendation="Confirm memory templates, read/write latency, and reset behavior match the target platform.",
        ))

    if _int_or_zero(metrics.get("arithmetic_cells")) >= 40:
        issues.append(_issue(
            "info",
            "timing",
            "Arithmetic-heavy structure candidate",
            "Large arithmetic populations can dominate frequency and implementation resource usage.",
            evidence={"arithmetic_cells": metrics.get("arithmetic_cells")},
            recommendation="Check multiplier/divider implementation choices and pipeline long arithmetic paths.",
        ))

    if _int_or_zero(metrics.get("max_fanout")) >= _HIGH_FANOUT_THRESHOLD:
        worst = _first_metric_item(metrics, "high_fanout_nets")
        issues.append(_issue(
            "warning",
            "fanout",
            "High fanout net candidate",
            "A CPU net drives many structural consumers after Yosys lowering, which can hurt buffering, placement, and routing.",
            path=str(worst.get("source", "")),
            line=_int_or_zero(worst.get("line")),
            column=_int_or_zero(worst.get("column")),
            evidence={
                "module": worst.get("module", ""),
                "net": worst.get("net", ""),
                "fanout": worst.get("fanout", metrics.get("max_fanout")),
            },
            recommendation="Inspect this control/reset/enable style signal and consider register replication or localized decode.",
        ))

    if _int_or_zero(metrics.get("max_fanin")) >= _HIGH_FANIN_THRESHOLD:
        worst = _first_metric_item(metrics, "high_fanin_cells")
        issues.append(_issue(
            "warning",
            "fanin",
            "Wide fanin cell candidate",
            "A lowered logic cell depends on many input bits, which often corresponds to wide compares, decode cones, or priority logic.",
            path=str(worst.get("source", "")),
            line=_int_or_zero(worst.get("line")),
            column=_int_or_zero(worst.get("column")),
            evidence={
                "module": worst.get("module", ""),
                "cell": worst.get("cell", ""),
                "fanin": worst.get("fanin", metrics.get("max_fanin")),
            },
            recommendation="Review the decode/compare expression and consider predecode, staging, or splitting the logic cone.",
        ))

    if _int_or_zero(metrics.get("max_comb_depth")) >= _DEEP_COMB_DEPTH_THRESHOLD:
        worst = _first_metric_item(metrics, "deep_comb_paths")
        issues.append(_issue(
            "warning",
            "timing",
            "Deep combinational path candidate",
            "The Yosys structural graph found a long combinational cell chain before a sequential boundary.",
            path=str(worst.get("source", "")),
            line=_int_or_zero(worst.get("line")),
            column=_int_or_zero(worst.get("column")),
            evidence={
                "module": worst.get("module", ""),
                "endpoint": worst.get("endpoint", ""),
                "depth": worst.get("depth", metrics.get("max_comb_depth")),
            },
            recommendation="Pipeline or rebalance the indicated module, especially decode, bypass, CSR, branch, or memory address logic.",
        ))

    if metrics.get("comb_cycle_modules"):
        issues.append(_issue(
            "error",
            "combinational",
            "Combinational cycle candidate in structural graph",
            "The Yosys netlist graph could not be fully topologically ordered.",
            evidence={"modules": metrics.get("comb_cycle_modules")},
            recommendation="Check ready/valid feedback and combinational dependencies in the reported modules.",
        ))

    return issues


def _first_metric_item(metrics: dict[str, Any], key: str) -> dict[str, Any]:
    value = metrics.get(key)
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _first_diagnostic_location(
    diagnostics: list[dict[str, Any]],
    *,
    category: str = "",
) -> dict[str, Any]:
    for diagnostic in diagnostics:
        if category and diagnostic.get("category") != category:
            continue
        if diagnostic.get("source"):
            return {
                "source": diagnostic.get("source", ""),
                "line": diagnostic.get("line", 0),
                "column": diagnostic.get("column", 0),
            }
    return {"source": "", "line": 0, "column": 0}


def _write_probe(paths: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    report_path = Path(paths["report"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if not Path(paths["log"]).exists():
        Path(paths["log"]).write_text(_format_probe_log(payload), encoding="utf-8")
    return payload


def _format_probe_log(payload: dict[str, Any]) -> str:
    lines = [
        "[rtl-review] Yosys precheck",
        f"status={payload.get('status', '')}",
        f"reason={payload.get('reason', '')}",
    ]
    return "\n".join(lines) + "\n"


def _issue(
    severity: str,
    category: str,
    title: str,
    detail: str,
    *,
    path: str = "",
    line: int = 0,
    column: int = 0,
    evidence: dict[str, Any] | None = None,
    recommendation: str = "",
) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "title": title,
        "detail": detail,
        "source": path,
        "line": line,
        "column": column,
        "evidence": evidence or {},
        "recommendation": recommendation,
    }


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
