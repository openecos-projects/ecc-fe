"""Runtime resource discovery helpers for installable ECC-FE bundles."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = 3

_TOOL_HEALTH_MARKERS: dict[str, tuple[tuple[str, str], ...]] = {
    "slang": (("bin/slang", "executable"),),
    "verilator": (
        ("bin/verilator", "executable"),
        ("bin/verilator_bin", "executable"),
        ("share/verilator/include/verilated.cpp", "file"),
    ),
    "yosys": (("bin/yosys", "executable"),),
    "riscv-toolchain": (
        ("bin/riscv64-unknown-elf-gcc", "executable"),
        ("bin/riscv64-unknown-elf-ld", "executable"),
        ("bin/riscv64-unknown-elf-objdump", "executable"),
        ("bin/riscv64-unknown-elf-objcopy", "executable"),
    ),
    "ecc-fe": (("bin/ecc-fe", "executable"), ("fecompiler", "directory")),
    "ecc-fe-soc-ysyx-am": (
        ("manifest.json", "file"),
        ("catalog.json", "file"),
        ("filelist.soc.f", "file"),
        ("driver/main.cpp", "file"),
    ),
    "ecc-fe-cpu-rtl": (
        ("thirdparty/README", "file"),
        ("thirdparty/cv32e40p", "directory"),
        ("thirdparty/cva6", "directory"),
        ("thirdparty/darkriscv", "directory"),
        ("thirdparty/ibex", "directory"),
        ("thirdparty/learn-fpga", "directory"),
        ("thirdparty/picorv32", "directory"),
        ("thirdparty/scr1", "directory"),
        ("thirdparty/serv", "directory"),
        ("thirdparty/vexriscv", "directory"),
    ),
    "ecc-fe-difftest-ref": (("tools/riscv32-spike-so", "file"),),
    "ecc-fe-examples": (
        ("examples/ysyx_00000000/filelist.cpu.f", "file"),
        ("examples/ysyx_00000000/rtl/ysyx_00000000.sv", "file"),
        ("examples/ysyx_00000000/rtl/ysyx_00000000_difftest.sv", "file"),
    ),
    "surfer": (
        ("index.html", "file"),
        ("integration.js", "file"),
        ("surfer.js", "file"),
        ("surfer_bg.wasm", "file"),
    ),
}

_RUNTIME_ALIASES: dict[str, tuple[str, ...]] = {
    "ecc-fe": ("ecc-fe", "fecompiler"),
    "slang": ("slang",),
    "verilator": ("verilator",),
    "yosys": ("yosys",),
    "riscv-toolchain": ("riscv64-unknown-elf-gcc",),
}


def frontend_repo_root() -> Path:
    """Return the installed ECC-FE runtime root."""
    env_root = os.getenv("ECOS_FE_COMPILER_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()
    return _default_frontend_repo_root()


def _default_frontend_repo_root() -> Path:
    if getattr(sys, "frozen", False):
        installed = Path(sys.executable).resolve().parent.parent
        if (installed / "fecompiler").is_dir():
            return installed
    return Path(__file__).resolve().parents[1]


def xdg_data_home(environ: MutableMapping[str, str] | None = None) -> Path:
    env = environ if environ is not None else os.environ
    value = env.get("XDG_DATA_HOME", "").strip()
    return Path(value).expanduser() if value else Path.home() / ".local" / "share"


def xdg_state_home(environ: MutableMapping[str, str] | None = None) -> Path:
    env = environ if environ is not None else os.environ
    value = env.get("XDG_STATE_HOME", "").strip()
    return Path(value).expanduser() if value else Path.home() / ".local" / "state"


def xdg_cache_home(environ: MutableMapping[str, str] | None = None) -> Path:
    env = environ if environ is not None else os.environ
    value = env.get("XDG_CACHE_HOME", "").strip()
    return Path(value).expanduser() if value else Path.home() / ".cache"


def managed_tools_dir(environ: MutableMapping[str, str] | None = None) -> Path:
    return xdg_data_home(environ) / "ecos-studio" / "tools"


def resource_manifest_path(environ: MutableMapping[str, str] | None = None) -> Path:
    return xdg_state_home(environ) / "ecos-studio" / "resources" / "manifest.json"


def empty_resource_manifest(
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, Any]:
    data_home = xdg_data_home(environ) / "ecos-studio"
    resources_dir = resource_manifest_path(environ).parent
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "resources_dir": str(resources_dir),
        "tools_dir": str(data_home / "tools"),
        "pdks_dir": str(data_home / "pdks"),
        "mpcs_dir": str(data_home / "mpcs"),
        "required_resources": [],
        "installed": {},
        "pdk_references": [],
    }


def read_resource_manifest(
    path: Path | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    manifest_path = path or resource_manifest_path(environ)
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(
            value.get("installed", {}), dict
        ):
            raise ValueError(  # noqa: TRY004 - callers handle all manifest validation uniformly.
                "manifest root and installed field must be objects"
            )
        return value
    except FileNotFoundError:
        return empty_resource_manifest(environ)
    except (OSError, ValueError, json.JSONDecodeError):
        if strict:
            raise
        return empty_resource_manifest(environ)


def required_tool_health_markers(name: str) -> tuple[tuple[str, str], ...]:
    normalized = name.strip().lower()
    if normalized.startswith("ecc-fe-cpu-") and normalized != "ecc-fe-cpu-rtl":
        return (("thirdparty", "directory"),)
    if normalized.startswith("ecc-fe-test-"):
        return (("tests", "directory"),)
    return _TOOL_HEALTH_MARKERS.get(normalized, ())


def tool_entry_health(entry: dict[str, Any]) -> dict[str, object]:
    root = Path(str(entry.get("path", ""))).expanduser()
    name = str(entry.get("name", "")).strip().lower()
    markers = required_tool_health_markers(name)
    missing: list[str] = []
    if not root.is_dir():
        return {
            "status": "missing",
            "path_exists": False,
            "required_markers": [path for path, _ in markers],
            "missing_markers": [path for path, _ in markers],
        }
    for relative, kind in markers:
        candidate = root / relative
        present = candidate.is_dir() if kind == "directory" else candidate.is_file()
        if present and kind == "executable":
            present = os.access(candidate, os.X_OK)
        if not present:
            missing.append(relative)
    return {
        "status": "invalid" if missing else "ok",
        "path_exists": True,
        "required_markers": [path for path, _ in markers],
        "missing_markers": missing,
    }


def installed_tool_entries(
    manifest: dict[str, Any] | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    source = manifest or read_resource_manifest(environ=environ)
    installed = source.get("installed", {})
    if not isinstance(installed, dict):
        return {}
    tools: dict[str, dict[str, Any]] = {}
    for resource_id, raw_entry in installed.items():
        if not isinstance(raw_entry, dict) or raw_entry.get("type") != "tool":
            continue
        name = str(raw_entry.get("name") or str(resource_id).removeprefix("tool:"))
        tools[name] = raw_entry
    return tools


def activate_managed_resources(
    environ: MutableMapping[str, str] | None = None,
    *,
    manifest_path: Path | None = None,
) -> dict[str, str]:
    """Add healthy desktop/CLI-managed resources to an ECC-FE process environment."""
    env = environ if environ is not None else os.environ
    before = dict(env)
    env.setdefault("ECOS_FE_COMPILER_ROOT", str(_default_frontend_repo_root()))
    manifest = read_resource_manifest(manifest_path, environ=env)
    resource_paths: list[str] = []
    path_dirs: list[str] = []
    runtime_tools: dict[str, tuple[Path, Path]] = {}

    for name, entry in installed_tool_entries(manifest).items():
        if (
            entry.get("active", True) is False
            or tool_entry_health(entry)["status"] != "ok"
        ):
            continue
        root = Path(str(entry.get("path", ""))).expanduser().resolve()
        normalized = name.strip().lower()
        if normalized.startswith("ecc-fe-") and normalized != "ecc-fe":
            resource_paths.append(str(root))
            if normalized.startswith("ecc-fe-soc-"):
                env.setdefault("ECOS_FE_SOC_ROOT", str(root))
            continue
        if normalized == "surfer":
            env.setdefault("ECOS_SURFER_ASSETS_PATH", str(root))
            continue
        for capability, aliases in _RUNTIME_ALIASES.items():
            executable = _resolve_runtime_executable(entry, root, aliases)
            if executable is not None:
                runtime_tools.setdefault(capability, (executable, root))
                path_dirs.append(str(executable.parent))

    if "slang" in runtime_tools:
        env.setdefault("ECOS_SLANG", str(runtime_tools["slang"][0]))
    if "ecc-fe" in runtime_tools:
        env.setdefault("ECOS_FE_CLI", str(runtime_tools["ecc-fe"][0]))
    if "verilator" in runtime_tools:
        executable, root = runtime_tools["verilator"]
        env.setdefault("ECOS_VERILATOR", str(executable))
        env.setdefault("VERILATOR_ROOT", str(root / "share" / "verilator"))
    if "yosys" in runtime_tools:
        root = runtime_tools["yosys"][1]
        env.setdefault("CHIPCOMPILER_OSS_CAD_DIR", str(root))
        env.setdefault("ECOS_ELECTRON_OSS_CAD_DIR", str(root))
    if "riscv-toolchain" in runtime_tools:
        executable, root = runtime_tools["riscv-toolchain"]
        env.setdefault("RISCV", str(root))
        env.setdefault("RISCV_TOOLCHAIN", str(root))
        suffix = "gcc"
        if executable.name.endswith(suffix):
            env.setdefault("RISCV_PREFIX", str(executable)[: -len(suffix)])

    if resource_paths:
        env["ECOS_FE_RESOURCE_ROOTS"] = _merge_path_values(
            env.get("ECOS_FE_RESOURCE_ROOTS", ""), resource_paths
        )
    if path_dirs:
        env["PATH"] = _merge_path_values(env.get("PATH", ""), path_dirs, prepend=True)
    return {key: value for key, value in env.items() if before.get(key) != value}


def current_registry_platform() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    return f"{system}-{machine}"


def resource_roots() -> list[Path]:
    """Return external ECC-FE resource roots in priority order."""
    roots: list[Path] = []
    seen: set[Path] = set()

    for value in _split_path_env(os.getenv("ECOS_FE_RESOURCE_ROOTS", "")):
        _append_root(roots, seen, Path(value).expanduser())

    soc_root = os.getenv("ECOS_FE_SOC_ROOT", "").strip()
    if soc_root:
        _append_root(roots, seen, Path(soc_root).expanduser())

    return roots


def catalog_manifest_roots(kind: str) -> list[Path]:
    """Return roots that may contain catalog manifests for a catalog kind."""
    return _dedupe_candidates(
        [
            frontend_repo_root() / "fecompiler" / kind,
            *resource_roots(),
        ]
    )


def soc_manifest_roots() -> list[Path]:
    """Return roots that may contain SoC runtime manifests."""
    return _dedupe_candidates(
        [
            frontend_repo_root() / "fecompiler" / "thirdparty",
            *resource_roots(),
        ]
    )


def builtin_soc_runtime_roots() -> list[Path]:
    """Return roots considered managed SoC runtime roots."""
    return soc_manifest_roots()


def thirdparty_roots() -> list[Path]:
    """Return roots that may contain ECC-FE thirdparty resources."""
    candidates: list[Path] = []
    for root in resource_roots():
        candidates.extend(
            [
                root / "thirdparty",
                root / "fecompiler" / "thirdparty",
                root,
            ]
        )
    candidates.append(frontend_repo_root() / "fecompiler" / "thirdparty")
    return _dedupe_candidates(candidates)


def resolve_thirdparty_path(path: Path) -> Path:
    """Resolve a path that may have moved into an external thirdparty bundle."""
    resolved = path.expanduser().resolve()
    if resolved.exists():
        return resolved

    parts = resolved.parts
    try:
        index = parts.index("thirdparty")
    except ValueError:
        return resolved

    rel = Path(*parts[index + 1 :])
    if not rel.parts:
        return resolved

    for root in thirdparty_roots():
        candidate = (root / rel).resolve()
        if candidate.exists():
            return candidate
    return resolved


def resolve_difftest_reference_model(soc_root: Path | None = None) -> Path:
    """Return the preferred difftest reference model path."""
    if soc_root is not None:
        candidate = (soc_root / "tools" / "riscv32-spike-so").expanduser().resolve()
        if candidate.exists():
            return candidate

    for root in resource_roots():
        for rel in (
            Path("tools") / "riscv32-spike-so",
            Path("difftest") / "riscv32-spike-so",
            Path("thirdparty") / "SoC" / "tools" / "riscv32-spike-so",
            Path("fecompiler") / "thirdparty" / "SoC" / "tools" / "riscv32-spike-so",
        ):
            candidate = (root / rel).resolve()
            if candidate.exists():
                return candidate

    fallback_root = (
        soc_root
        if soc_root is not None
        else frontend_repo_root() / "fecompiler" / "thirdparty" / "SoC"
    )
    return (fallback_root / "tools" / "riscv32-spike-so").expanduser().resolve()


def _split_path_env(value: str) -> list[str]:
    if not value:
        return []
    return [item for item in value.split(os.pathsep) if item.strip()]


def _dedupe_candidates(roots: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        _append_root(out, seen, root, require_exists=False)
    return out


def _append_root(
    roots: list[Path],
    seen: set[Path],
    root: Path,
    *,
    require_exists: bool = True,
) -> None:
    resolved = root.expanduser().resolve()
    if require_exists and not resolved.exists():
        return
    if resolved in seen:
        return
    seen.add(resolved)
    roots.append(resolved)


def _resolve_runtime_executable(
    entry: dict[str, Any],
    root: Path,
    aliases: tuple[str, ...],
) -> Path | None:
    candidates: list[Path] = []
    detected = entry.get("detected_executables", [])
    if not isinstance(detected, list):
        detected = []
    for raw in [entry.get("executable", ""), *detected]:
        text = str(raw).strip()
        if text:
            candidates.append(Path(text).expanduser())
    candidates.extend(root / "bin" / alias for alias in aliases)
    candidates.extend(root / alias for alias in aliases)
    for alias in aliases:
        found = shutil.which(alias, path=str(root / "bin"))
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        resolved = candidate if candidate.is_absolute() else root / candidate
        if (
            resolved.name in aliases
            and resolved.is_file()
            and os.access(resolved, os.X_OK)
        ):
            return resolved.resolve()
    return None


def _merge_path_values(
    current: str,
    additions: list[str],
    *,
    prepend: bool = False,
) -> str:
    existing = _split_path_env(current)
    values = [*additions, *existing] if prepend else [*existing, *additions]
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(Path(value).expanduser())
        if text in seen:
            continue
        seen.add(text)
        merged.append(text)
    return os.pathsep.join(merged)
