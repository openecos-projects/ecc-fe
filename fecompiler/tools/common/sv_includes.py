"""Find the files SystemVerilog sources pull in with `` `include ``."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from fecompiler.tools.common.sv_module import strip_sv_comments

_INCLUDE = re.compile(r'`include\s+"([^"\n]+)"')
# Far more than a real design nests; stops a pathological include tree.
_MAX_FILES = 2000


def included_files(
    files: Iterable[str | Path], search_dirs: Iterable[str | Path] = ()
) -> list[Path]:
    """Return every file *files* include, directly or through another include.

    A name is looked up beside the file that includes it, then in
    *search_dirs*, as the tools do. Names that cannot be found are left out:
    the tool that compiles the sources reports them. Includes in every
    `` `ifdef `` branch are followed, so the result may name more files than
    one particular build reads, never fewer.
    """
    directories = [Path(directory) for directory in search_dirs]
    roots = [Path(path).resolve() for path in files]
    seen: set[Path] = set(roots)
    found: list[Path] = []
    pending = list(reversed(roots))
    while pending and len(found) < _MAX_FILES:
        current = pending.pop()
        try:
            text = strip_sv_comments(current.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        for name in _INCLUDE.findall(text):
            target = _resolve(name.strip(), current.parent, directories)
            if target is not None and target not in seen:
                seen.add(target)
                found.append(target)
                pending.append(target)
    return found


def _resolve(name: str, beside: Path, directories: list[Path]) -> Path | None:
    candidate = Path(name)
    if candidate.is_absolute():
        return candidate.resolve() if candidate.is_file() else None
    for directory in (beside, *directories):
        path = directory / candidate
        if path.is_file():
            return path.resolve()
    return None


__all__ = ["included_files"]
