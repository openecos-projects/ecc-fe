"""Remember what a simulation program was built from.

A built program may be reused instead of rebuilt, but only while it still
corresponds to its inputs. After a successful build a stamp is written beside
the program: the build command and a hash of every file the build read. The
files come from the dependency files Verilator and the C++ compiler leave in
the build directory, so included headers are covered as well as the sources
named on the command line.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from collections.abc import Iterable, Sequence
from pathlib import Path

_SCHEMA_VERSION = 1


def stamp_path(program: Path) -> Path:
    return program.with_name(program.name + ".build.json")


def record_build(
    program: Path, build_dir: Path, command: Sequence[str], inputs: Iterable[str]
) -> None:
    """Write the stamp of a program that was just built."""
    stamp_path(program).write_text(
        json.dumps(_stamp(build_dir, command, inputs), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def stale_reason(
    program: Path, build_dir: Path, command: Sequence[str], inputs: Iterable[str]
) -> str | None:
    """Return why *program* cannot be reused, or ``None`` when it is current."""
    if not program.is_file():
        return "the program does not exist"
    try:
        recorded = json.loads(stamp_path(program).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "it has no build record"
    if not isinstance(recorded, dict) or recorded.get("schema_version") != _SCHEMA_VERSION:
        return "its build record is not readable"
    if recorded.get("command") != [str(item) for item in command]:
        return "the build command changed"
    files = recorded.get("files")
    if not isinstance(files, dict):
        return "its build record is not readable"
    # Judge by the files the build is known to have read; the dependency
    # files in the build directory still describe exactly that build.
    current = {path: _sha256(Path(path)) for path in files}
    current.update({path: _sha256(Path(path)) for path in _explicit(inputs)})
    changed = sorted(path for path, digest in current.items() if files.get(path) != digest)
    if changed:
        more = f" and {len(changed) - 1} more" if len(changed) > 1 else ""
        return f"{changed[0]}{more} changed since it was built"
    return None


def _stamp(build_dir: Path, command: Sequence[str], inputs: Iterable[str]) -> dict[str, object]:
    paths = {*_explicit(inputs), *_dependencies(build_dir)}
    return {
        "schema_version": _SCHEMA_VERSION,
        "command": [str(item) for item in command],
        "files": {path: _sha256(Path(path)) for path in sorted(paths)},
    }


def _explicit(inputs: Iterable[str]) -> set[str]:
    return {str(Path(item).expanduser().resolve()) for item in inputs if str(item).strip()}


def _dependencies(build_dir: Path) -> set[str]:
    """Return every file outside *build_dir* that its dependency files name."""
    root = build_dir.resolve()
    found: set[str] = set()
    for dependency_file in sorted(root.glob("*.d")):
        try:
            text = dependency_file.read_text(encoding="utf-8", errors="replace")
            tokens = shlex.split(text.replace("\\\n", " "), comments=False)
        except (OSError, ValueError):
            continue
        separator = next(
            (index for index, token in enumerate(tokens) if token.endswith(":")), None
        )
        if separator is None:
            continue
        for token in tokens[separator + 1 :]:
            if not token or token == "\\":
                continue
            path = Path(token)
            # The compiler runs in the build directory, Verilator where it was started.
            candidates = [path] if path.is_absolute() else [root / path, Path.cwd() / path]
            resolved = next(
                (item.resolve() for item in candidates if item.exists()), candidates[0].resolve()
            )
            if root != resolved and root not in resolved.parents:
                found.add(str(resolved))
    return found


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


__all__ = ["record_build", "stale_reason", "stamp_path"]
