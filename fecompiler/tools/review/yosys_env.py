"""Find the Yosys to run for the structural precheck, and what it can do.

Where the executable is looked for, the environment it needs, and whether it
has the slang frontend, which decides how SystemVerilog sources are read.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from fecompiler.tools.review.probe_common import (
    _unique_strings,
)


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


def _select_yosys_frontend(yosys: str, inputs: dict[str, Any]) -> str:
    if not _yosys_supports_slang(yosys):
        return "read_verilog"
    return "read_slang" if _has_systemverilog_sources(inputs) else "read_verilog"


def _has_systemverilog_sources(inputs: dict[str, Any]) -> bool:
    for path in inputs.get("rtl_files", []):
        if str(path).lower().endswith((".sv", ".svh")):
            return True
    return False
