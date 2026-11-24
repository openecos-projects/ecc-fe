"""Decide whether a test program has to be built again.

A program is built by the SoC's ``build_test.sh`` from its own source and the
runtime every program shares. After a successful build a record is written
beside the image: what was asked for, the settings the script takes from the
environment, the compiler it would pick, and a hash of every file it can
read. While all of that is unchanged and the image is still the one that was
built, the program is up to date.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 1
# Settings build_test.sh reads from its environment.
_ENVIRONMENT_PREFIXES = ("ECOS_SIM_", "ECOS_COREMARK_", "SOC_")
_ENVIRONMENT_NAMES = ("RISCV_PREFIX", "HEXDUMP_BIN")
# Compilers build_test.sh looks for, in its order.
_COMPILERS = (
    "riscv64-unknown-elf-gcc",
    "riscv32-unknown-elf-gcc",
    "riscv64-none-elf-gcc",
    "riscv-none-elf-gcc",
    "riscv64-unknown-linux-gnu-gcc",
    "riscv64-linux-gnu-gcc",
)
# Directories below the SoC's tests/ that programs are built from.
_SHARED_DIRECTORIES = ("common", "include", "benchmarks")


def shared_inputs(build_script: Path, env: Mapping[str, str]) -> dict[str, Any] | None:
    """Return what every program built by *build_script* depends on.

    ``None`` when the script is not laid out like a SoC's (no ``tests/common``
    beside it): what such a script reads is unknown, so nothing built by it
    is ever taken as up to date.
    """
    tests = build_script.resolve().parent.parent / "tests"
    if not (tests / "common").is_dir():
        return None
    files = [build_script.resolve()]
    for name in _SHARED_DIRECTORIES:
        files.extend(path for path in sorted((tests / name).rglob("*")) if path.is_file())
    return {
        "environment": {
            name: value
            for name, value in sorted(env.items())
            if name.startswith(_ENVIRONMENT_PREFIXES) or name in _ENVIRONMENT_NAMES
        },
        "compiler": _compiler(env),
        "files": {str(path): _sha256(path) for path in files},
    }


def record_path(image: Path) -> Path:
    return image.with_name(image.name + ".build.json")


def is_current(
    image: Path, command: Sequence[str], source: Path, shared: dict[str, Any] | None
) -> bool:
    if shared is None or not image.is_file():
        return False
    try:
        recorded = json.loads(record_path(image).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return recorded == _record(image, command, source, shared)


def record(image: Path, command: Sequence[str], source: Path, shared: dict[str, Any] | None) -> None:
    """Write the record of an image that was just built."""
    if shared is None:
        record_path(image).unlink(missing_ok=True)
        return
    record_path(image).write_text(
        json.dumps(_record(image, command, source, shared), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _record(
    image: Path, command: Sequence[str], source: Path, shared: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "command": [str(item) for item in command],
        "source": {str(source): _sha256(source)},
        "image_sha256": _sha256(image),
        **shared,
    }


def _compiler(env: Mapping[str, str]) -> dict[str, Any]:
    """Identify the compiler the script would use: where it is and which file."""
    prefix = env.get("RISCV_PREFIX", "")
    names = (f"{prefix}gcc",) if prefix else _COMPILERS
    for name in names:
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            try:
                stat = Path(found).resolve().stat()
            except OSError:
                break
            return {"path": found, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return {}


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


__all__ = ["is_current", "record", "record_path", "shared_inputs"]
