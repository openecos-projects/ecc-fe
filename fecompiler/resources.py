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
