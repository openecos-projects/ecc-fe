"""Parse a Verilog/SystemVerilog filelist (``.f`` file).

One parser for every reader that needs to know what a filelist refers to. It
understands:

* one or more entries per line, with shell-style quoting and ``#`` comments;
* whole-line comments starting with ``#``, ``//``, or a backtick;
* nested filelists: ``-f other.f``, ``-F other.f``, ``-fother.f``, ``-Fother.f``;
* ``+incdir+a+b`` and ``+define+A+B=1``;
* RTL files, recognised by suffix: compilation units (``.v``, ``.sv``) and
  headers (``.vh``, ``.svh``).

Relative paths are resolved against the directory of the filelist that names
them, and paths under ``thirdparty/`` follow managed resources. Every other
option is ignored. Parsing never raises for a missing file: anything that
cannot be found is reported in :attr:`Filelist.missing`.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from fecompiler.resources import resolve_thirdparty_path

RTL_SOURCE_SUFFIXES = (".v", ".sv")
RTL_HEADER_SUFFIXES = (".vh", ".svh")
RTL_SUFFIXES = RTL_SOURCE_SUFFIXES + RTL_HEADER_SUFFIXES

_NESTED_OPTIONS = ("-f", "-F")
_INCDIR = "+incdir+"
_DEFINE = "+define+"


@dataclass(frozen=True, slots=True)
class Filelist:
    """Everything one filelist, including the filelists it nests, refers to."""

    path: Path
    # Every filelist that was read, the root first.
    filelists: tuple[Path, ...]
    # Existing RTL files, headers included, in the order they are listed.
    files: tuple[Path, ...]
    # Listed RTL files and nested filelists that do not exist.
    missing: tuple[Path, ...]
    incdirs: tuple[Path, ...]
    defines: tuple[str, ...]

    @property
    def sources(self) -> tuple[Path, ...]:
        """The compilation units among :attr:`files` (headers excluded)."""
        return tuple(
            path for path in self.files if path.suffix.lower() in RTL_SOURCE_SUFFIXES
        )


def parse_filelist(path: str | Path) -> Filelist:
    """Parse *path* and every filelist it nests."""
    root = _resolve(Path.cwd(), str(path))
    collected = _Collected()
    _visit(root, collected)
    return Filelist(
        path=root,
        filelists=tuple(collected.filelists),
        files=tuple(collected.files),
        missing=tuple(collected.missing),
        incdirs=tuple(collected.incdirs),
        defines=tuple(collected.defines),
    )


def filelist_tokens(raw_line: str) -> list[str]:
    """Split one filelist line into entries; a comment line yields nothing."""
    line = raw_line.strip()
    if not line or line.startswith(("#", "//", "`")):
        return []
    try:
        return shlex.split(line, comments=True, posix=True)
    except ValueError:  # unbalanced quote: fall back to whitespace
        return line.split()


def is_rtl_path_token(token: str) -> bool:
    """Report whether a filelist entry names an RTL file rather than an option."""
    if token.startswith(("+", "-", "$")):
        return False
    return Path(token).suffix.lower() in RTL_SUFFIXES


class _Collected:
    def __init__(self) -> None:
        self.visited: set[Path] = set()
        self.filelists: list[Path] = []
        self.files: list[Path] = []
        self.missing: list[Path] = []
        self.incdirs: list[Path] = []
        self.defines: list[str] = []


def _visit(filelist: Path, collected: _Collected) -> None:
    if filelist in collected.visited:
        return
    collected.visited.add(filelist)
    try:
        lines = filelist.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        collected.missing.append(filelist)
        return
    collected.filelists.append(filelist)

    base = filelist.parent
    for raw_line in lines:
        tokens = filelist_tokens(raw_line)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            index += 1
            nested = ""
            if token in _NESTED_OPTIONS:
                if index < len(tokens):
                    nested = tokens[index]
                    index += 1
            elif token.startswith(_NESTED_OPTIONS) and len(token) > 2:
                nested = token[2:]
            if nested:
                nested_path = _resolve(base, nested)
                if nested_path.is_file():
                    _visit(nested_path, collected)
                else:
                    collected.missing.append(nested_path)
                continue

            if token.startswith(_INCDIR):
                collected.incdirs.extend(
                    _resolve(base, item)
                    for item in token.removeprefix(_INCDIR).split("+")
                    if item.strip()
                )
                continue
            if token.startswith(_DEFINE):
                collected.defines.extend(
                    item.strip()
                    for item in token.removeprefix(_DEFINE).split("+")
                    if item.strip()
                )
                continue

            if not is_rtl_path_token(token):
                continue
            rtl_path = _resolve(base, token)
            if rtl_path.is_file():
                collected.files.append(rtl_path)
            else:
                collected.missing.append(rtl_path)


def _resolve(base: Path, value: str) -> Path:
    path = Path(value.strip().strip("\"'")).expanduser()
    if not path.is_absolute():
        path = base / path
    return resolve_thirdparty_path(path).resolve()


__all__ = [
    "RTL_HEADER_SUFFIXES",
    "RTL_SOURCE_SUFFIXES",
    "RTL_SUFFIXES",
    "Filelist",
    "filelist_tokens",
    "is_rtl_path_token",
    "parse_filelist",
]
