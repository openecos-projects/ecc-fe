"""Runtime resource discovery helpers for installable ECC-FE bundles."""

from __future__ import annotations

import os
from pathlib import Path


def frontend_repo_root() -> Path:
    """Return the installed ECC-FE runtime root."""
    env_root = os.getenv("ECOS_FE_COMPILER_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


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
    return _dedupe_candidates([
        frontend_repo_root() / "fecompiler" / kind,
        *resource_roots(),
    ])


def soc_manifest_roots() -> list[Path]:
    """Return roots that may contain SoC runtime manifests."""
    return _dedupe_candidates([
        frontend_repo_root() / "fecompiler" / "thirdparty",
        *resource_roots(),
    ])


def builtin_soc_runtime_roots() -> list[Path]:
    """Return roots considered managed SoC runtime roots."""
    return soc_manifest_roots()


def thirdparty_roots() -> list[Path]:
    """Return roots that may contain ECC-FE thirdparty resources."""
    candidates: list[Path] = []
    for root in resource_roots():
        candidates.extend([
            root / "thirdparty",
            root / "fecompiler" / "thirdparty",
            root,
        ])
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

    rel = Path(*parts[index + 1:])
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

    fallback_root = soc_root if soc_root is not None else frontend_repo_root() / "fecompiler" / "thirdparty" / "SoC"
    return (fallback_root / "tools" / "riscv32-spike-so").expanduser().resolve()


def resolve_rtthread_am_root(soc_root: Path | None = None) -> Path:
    """Return the preferred RT-Thread AM BSP root."""
    env_root = os.getenv("RTTHREAD_AM_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()

    candidates: list[Path] = []
    if soc_root is not None:
        candidates.append(soc_root.parent / "rt-thread-am")
    candidates.extend(root / "rt-thread-am" for root in thirdparty_roots())

    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if (resolved / "bsp" / "abstract-machine").is_dir():
            return resolved
    return (frontend_repo_root() / "fecompiler" / "thirdparty" / "rt-thread-am").resolve()


def resolve_rtthread_prepare_helper(soc_root: Path | None = None) -> Path:
    """Return the preferred RT-Thread fallback preparation helper path."""
    candidates: list[Path] = []
    if soc_root is not None:
        candidates.append(soc_root.parent / "rtthread_prepare.py")
    candidates.extend(root / "rtthread_prepare.py" for root in thirdparty_roots())

    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved.is_file():
            return resolved
    return (frontend_repo_root() / "fecompiler" / "thirdparty" / "rtthread_prepare.py").resolve()


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
