"""Parse a Verilog/SystemVerilog filelist (``.f`` file).

One parser for every reader that needs to know what a filelist refers to. It
understands:

* one or more entries per line, with shell-style quoting;
* comments: a line starting with ``#``, ``//``, or a backtick, and anything
  after ``#`` or ``//`` on a line;
* nested filelists: ``-f other.f``, ``-F other.f``, ``-fother.f``, ``-Fother.f``;
* include directories: ``+incdir+a+b``, ``-I dir``, ``-Idir``, ``-incdir dir``;
* defines: ``+define+A+B=1``, ``-D A``, ``-DA=1``;
* ``-v file``: a library file, read as one more RTL file;
* ``-y dir`` and ``+libext+.v+.sv``: library directories and the suffixes
  searched in them, passed on to the tools;
* RTL files, recognised by suffix: compilation units (``.v``, ``.sv``) and
  headers (``.vh``, ``.svh``);
* environment variables in paths: ``$VAR``, ``${VAR}``, ``$(VAR)``.

Relative paths are resolved against the directory of the filelist that names
them, and paths under ``thirdparty/`` follow managed resources.

Nothing is dropped silently. An entry the parser does not act on (a tool
option, a file with another suffix) is listed in
:attr:`Filelist.unsupported`, a path that uses an undefined variable or does
not exist in :attr:`Filelist.missing`, and an include directory that does not
exist in :attr:`Filelist.missing_incdirs` (it stays in ``incdirs`` too).

:func:`parse_filelist` never raises for a missing file: anything that cannot
be found is reported in :attr:`Filelist.missing`. :func:`read_complete_filelist`
is the strict form used when the design is about to be compiled.
"""

from __future__ import annotations

import os
import re
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
# Options that take their value as the next entry.
_INCDIR_OPTIONS = ("-I", "-incdir")
_DEFINE_OPTION = "-D"
_LIBRARY_FILE_OPTION = "-v"
_LIBDIR_OPTION = "-y"
_LIBEXT = "+libext+"
_OPTIONS_WITH_VALUE = ("-top", "--top", "--top-module", "-o", "-timescale")
_PAREN_VARIABLE = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)")


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
    # Library directories (``-y``) and the suffixes searched in them.
    libdirs: tuple[Path, ...] = ()
    libexts: tuple[str, ...] = ()
    # Entries of :attr:`incdirs` and :attr:`libdirs` that do not exist, and
    # directories whose path uses an undefined variable.
    missing_incdirs: tuple[Path, ...] = ()
    # Entries that were read and not acted on, as written.
    unsupported: tuple[str, ...] = ()

    @property
    def headers(self) -> tuple[Path, ...]:
        """The headers among :attr:`files`; they are included, not compiled."""
        return tuple(
            path for path in self.files if path.suffix.lower() in RTL_HEADER_SUFFIXES
        )

    @property
    def sources(self) -> tuple[Path, ...]:
        """The compilation units among :attr:`files` (headers excluded)."""
        return tuple(
            path for path in self.files if path.suffix.lower() in RTL_SOURCE_SUFFIXES
        )


def parse_filelist(path: str | Path) -> Filelist:
    """Parse *path* and every filelist it nests."""
    root = _resolve(Path.cwd(), str(path)) or Path(str(path))
    collected = _Collected()
    _visit(root, collected)
    return Filelist(
        path=root,
        filelists=tuple(collected.filelists),
        files=tuple(collected.files),
        missing=tuple(collected.missing),
        incdirs=tuple(dict.fromkeys(collected.incdirs)),
        defines=tuple(collected.defines),
        libdirs=tuple(dict.fromkeys(collected.libdirs)),
        libexts=tuple(dict.fromkeys(collected.libexts)),
        missing_incdirs=tuple(dict.fromkeys(collected.missing_incdirs)),
        unsupported=tuple(collected.unsupported),
    )


def read_complete_filelist(path: str | Path) -> Filelist:
    """Parse *path* for compilation: every file it lists must exist.

    Raises ``FileNotFoundError`` naming what is missing. Validation code that
    wants to report problems instead uses :func:`parse_filelist`.
    """
    parsed = parse_filelist(path)
    if not parsed.filelists:
        raise FileNotFoundError(f"filelist not found: {parsed.path}")
    if parsed.missing:
        listed = ", ".join(str(item) for item in parsed.missing)
        raise FileNotFoundError(
            f"files listed in {parsed.path} were not found: {listed}"
        )
    return parsed


def filelist_tokens(raw_line: str) -> list[str]:
    """Split one filelist line into entries; a comment line yields nothing."""
    line = raw_line.strip()
    if not line or line.startswith(("#", "//", "`")):
        return []
    try:
        tokens = shlex.split(line, comments=True, posix=True)
    except ValueError:  # unbalanced quote: fall back to whitespace
        tokens = line.split()
    # `a.v // note` — everything from a `//` entry on is a comment.
    for index, token in enumerate(tokens):
        if token.startswith("//"):
            return tokens[:index]
    return tokens


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
        self.missing_incdirs: list[Path] = []
        self.unsupported: list[str] = []
        self.libdirs: list[Path] = []
        self.libexts: list[str] = []

    def add_libdir(self, base: Path, value: str) -> None:
        path = _resolve(base, value)
        if path is None:
            self.missing_incdirs.append(Path(value))
            return
        self.libdirs.append(path)
        if not path.is_dir():
            self.missing_incdirs.append(path)

    def add_incdir(self, base: Path, value: str) -> None:
        path = _resolve(base, value)
        if path is None:
            self.missing_incdirs.append(Path(value))
            return
        # A directory that does not exist stays listed (tools ignore it) and
        # is reported as well.
        self.incdirs.append(path)
        if not path.is_dir():
            self.missing_incdirs.append(path)

    def add_file(self, base: Path, value: str) -> None:
        path = _resolve(base, value)
        if path is None:
            self.missing.append(Path(value))
        elif path.is_file():
            self.files.append(path)
        else:
            self.missing.append(path)


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

        def value_after(option: str) -> str | None:
            """Return the entry that follows *option*, consuming it."""
            nonlocal index
            if index < len(tokens):
                index += 1
                return tokens[index - 1]
            collected.unsupported.append(option)
            return None

        while index < len(tokens):
            token = tokens[index]
            index += 1

            if token in _NESTED_OPTIONS or (
                token.startswith(_NESTED_OPTIONS) and len(token) > 2
            ):
                nested = value_after(token) if token in _NESTED_OPTIONS else token[2:]
                if nested is None:
                    continue
                nested_path = _resolve(base, nested)
                if nested_path is None:
                    collected.missing.append(Path(nested))
                elif nested_path.is_file():
                    _visit(nested_path, collected)
                else:
                    collected.missing.append(nested_path)
            elif token.startswith(_INCDIR):
                for item in token.removeprefix(_INCDIR).split("+"):
                    if item.strip():
                        collected.add_incdir(base, item)
            elif token in _INCDIR_OPTIONS:
                value = value_after(token)
                if value is not None:
                    collected.add_incdir(base, value)
            elif token.startswith("-I") and len(token) > 2:
                collected.add_incdir(base, token[2:])
            elif token.startswith(_DEFINE):
                collected.defines.extend(
                    item.strip()
                    for item in token.removeprefix(_DEFINE).split("+")
                    if item.strip()
                )
            elif token == _DEFINE_OPTION:
                value = value_after(token)
                if value is not None:
                    collected.defines.append(value.strip())
            elif token.startswith(_DEFINE_OPTION) and len(token) > 2:
                collected.defines.append(token[2:].strip())
            elif token == _LIBRARY_FILE_OPTION:
                value = value_after(token)
                if value is not None:
                    collected.add_file(base, value)
            elif token == _LIBDIR_OPTION:
                value = value_after(token)
                if value is not None:
                    collected.add_libdir(base, value)
            elif token.startswith(_LIBEXT):
                collected.libexts.extend(
                    item.strip()
                    for item in token.removeprefix(_LIBEXT).split("+")
                    if item.strip()
                )
            elif token in _OPTIONS_WITH_VALUE:
                value = value_after(token)
                collected.unsupported.append(
                    token if value is None else f"{token} {value}"
                )
            elif token.startswith(("+", "-")):
                collected.unsupported.append(token)
            elif Path(token).suffix.lower() in RTL_SUFFIXES:
                collected.add_file(base, token)
            else:
                collected.unsupported.append(token)


def _resolve(base: Path, value: str) -> Path | None:
    """Resolve one path entry; ``None`` when it uses an undefined variable."""
    text = _PAREN_VARIABLE.sub(r"${\1}", value.strip().strip("\"'"))
    text = os.path.expandvars(text)
    if "$" in text:
        return None
    path = Path(text).expanduser()
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
    "read_complete_filelist",
]
