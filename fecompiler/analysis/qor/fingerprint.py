"""Identify what a result was produced from, so two results can be compared.

Compile and link arguments, C++ sources and headers, and prepared inputs are
normalised to content identities: the same inputs give the same fingerprint
wherever they are on disk."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from collections.abc import Callable
from pathlib import Path
from subprocess import SubprocessError
from subprocess import run as run_subprocess
from typing import Any

from fecompiler.analysis.qor.common import (
    _read_record,
    _records,
    _string_list,
)
from fecompiler.tools.verilator.sim_settings import effective_sim_cflags, sim_cpp_sources

_HDL_INCLUDE_SUFFIXES = frozenset({".h", ".inc", ".orig", ".sv", ".svh", ".v", ".vh"})


_CPP_INCLUDE_RE = re.compile(
    r'^\s*#\s*include\s*(?:"(?P<quoted>[^"]+)"|<(?P<system>[^>]+)>)',
    re.MULTILINE,
)


_LINKER_PATH_OPTIONS = {
    "--dynamic-list": "file",
    "--just-symbols": "file",
    "--output": "output",
    "--retain-symbols-file": "file",
    "--script": "file",
    "--version-script": "file",
    "-Map": "output",
    "-T": "file",
    "-o": "output",
    "-rpath": "search_directory",
    "-rpath-link": "search_directory",
}


_MAKE_LINK_DRIVER_MARKER = "__ECC_FE_LINK_DRIVER__="


_GLOBAL_STATIC_LINK_ARGUMENTS = frozenset({"--static", "-static"})


_POSITIONAL_STATIC_LINK_ARGUMENTS = frozenset(
    {"--static", "-Bstatic", "-dn", "-non_shared", "-static"}
)


_POSITIONAL_DYNAMIC_LINK_ARGUMENTS = frozenset(
    {"-Bdynamic", "-call_shared", "-dy"}
)


def _normalized_run_arguments(value: Any, base_directory: Any = None) -> list[Any]:
    return _normalize_path_arguments(
        _string_list(value),
        {
            "--image": "file",
            "--ref": "file",
            "--wave": "output",
        },
        base_directory,
    )


def _normalized_compile_flags(value: Any, base_directory: Any = None) -> list[Any]:
    return _normalize_compile_arguments(
        _string_list(value), base_directory, response_files=set()
    )


def _normalize_compile_arguments(
    arguments: list[str],
    base_directory: Any = None,
    *,
    response_files: set[Path],
) -> list[Any]:
    def normalize_response(argument: str) -> dict[str, Any] | None:
        if not argument.startswith("@") or len(argument) == 1:
            return None
        return _response_file_identity(
            argument[1:],
            base_directory,
            response_files,
            lambda nested, visited: _normalize_compile_arguments(
                nested,
                base_directory,
                response_files=visited,
            ),
        )

    return _normalize_path_arguments(
        arguments,
        {
            "--sysroot": "directory",
            "-B": "directory",
            "-I": "search_directory",
            "-L": "search_directory",
            "-include": "file",
            "-iquote": "search_directory",
            "-isystem": "search_directory",
        },
        base_directory,
        embedded_normalizer=normalize_response,
    )


def _normalized_shell_compile_flags(
    value: Any, base_directory: Any = None
) -> list[Any]:
    return _normalize_compile_arguments(
        _shell_flag_tokens(value), base_directory, response_files=set()
    )


def _normalized_shell_link_flags(
    value: Any, base_directory: Any = None
) -> list[Any]:
    arguments = _shell_flag_tokens(value)
    link_driver = _verilator_link_driver(base_directory)
    link_state: dict[str, Any] = {
        "static": _link_has_global_static(arguments, base_directory),
        "stack": [],
    }
    return _normalize_link_arguments(
        arguments,
        _link_search_directories(arguments, base_directory),
        base_directory,
        link_state=link_state,
        response_files=set(),
        library_probe_command=[*link_driver, *arguments],
    )


def _shell_flag_tokens(value: Any) -> list[str]:
    tokens: list[str] = []
    for flag in _string_list(value):
        try:
            tokens.extend(shlex.split(flag))
        except ValueError:
            tokens.append(flag)
    return tokens


def _normalized_wl_flag(
    argument: str,
    library_directories: list[Path],
    link_state: dict[str, Any],
    base_directory: Any = None,
    response_files: set[Path] | None = None,
    library_probe_command: list[str] | None = None,
) -> dict[str, Any] | None:
    if not argument.startswith("-Wl,"):
        return None
    return {
        "option": "-Wl",
        "arguments": _normalize_link_arguments(
            argument[len("-Wl,") :].split(","),
            library_directories,
            base_directory,
            allow_wl=False,
            link_state=link_state,
            response_files=response_files,
            library_probe_command=library_probe_command,
        ),
    }


def _normalize_link_arguments(
    arguments: list[str],
    library_directories: list[Path],
    base_directory: Any = None,
    *,
    allow_wl: bool = True,
    link_state: dict[str, Any] | None = None,
    response_files: set[Path] | None = None,
    library_probe_command: list[str] | None = None,
) -> list[Any]:
    if link_state is None:
        link_state = {"static": False, "stack": []}
    if response_files is None:
        response_files = set()
    normalized: list[Any] = []
    index = 0
    path_options = sorted(_LINKER_PATH_OPTIONS, key=len, reverse=True)
    while index < len(arguments):
        argument = arguments[index]
        if allow_wl:
            wl_flag = _normalized_wl_flag(
                argument,
                library_directories,
                link_state,
                base_directory,
                response_files,
                library_probe_command,
            )
            if wl_flag is not None:
                normalized.append(wl_flag)
                index += 1
                continue
        if argument in _POSITIONAL_STATIC_LINK_ARGUMENTS:
            if not allow_wl or argument not in _GLOBAL_STATIC_LINK_ARGUMENTS:
                link_state["static"] = True
            normalized.append(argument)
            index += 1
            continue
        if argument in _POSITIONAL_DYNAMIC_LINK_ARGUMENTS:
            link_state["static"] = False
            normalized.append(argument)
            index += 1
            continue
        if argument == "--push-state":
            link_state["stack"].append(link_state["static"])
            normalized.append(argument)
            index += 1
            continue
        if argument == "--pop-state":
            if link_state["stack"]:
                link_state["static"] = link_state["stack"].pop()
            normalized.append(argument)
            index += 1
            continue
        if (
            argument in {"--sysroot", "-L", *_LINKER_PATH_OPTIONS}
            and index + 1 < len(arguments)
        ):
            kind = (
                "directory"
                if argument == "--sysroot"
                else "search_directory"
                if argument == "-L"
                else _LINKER_PATH_OPTIONS[argument]
            )
            normalized.append(
                {
                    "option": argument,
                    "value": _path_argument_identity(
                        arguments[index + 1], kind, base_directory
                    ),
                }
            )
            index += 2
            continue
        if argument == "-l" and index + 1 < len(arguments):
            normalized.append(
                _library_argument_identity(
                    arguments[index + 1],
                    library_directories,
                    static=link_state["static"],
                    base_directory=base_directory,
                    compiler_command=library_probe_command,
                )
            )
            index += 2
            continue
        if argument.startswith("-l") and argument != "-l":
            normalized.append(
                _library_argument_identity(
                    argument[len("-l") :],
                    library_directories,
                    static=link_state["static"],
                    base_directory=base_directory,
                    compiler_command=library_probe_command,
                )
            )
            index += 1
            continue

        attached = next(
            (
                option
                for option in ("--sysroot", "-L", *path_options)
                if argument.startswith(f"{option}=")
                or (
                    option.startswith("-")
                    and not option.startswith("--")
                    and argument.startswith(option)
                    and len(argument) > len(option)
                )
            ),
            None,
        )
        if attached is not None:
            kind = (
                "directory"
                if attached == "--sysroot"
                else "search_directory"
                if attached == "-L"
                else _LINKER_PATH_OPTIONS[attached]
            )
            normalized.append(
                {
                    "option": attached,
                    "value": _path_argument_identity(
                        argument[len(attached) :].removeprefix("="),
                        kind,
                        base_directory,
                    ),
                }
            )
        elif argument.startswith("@") and len(argument) > 1:
            normalized.append(
                _response_file_identity(
                    argument[1:],
                    base_directory,
                    response_files,
                    lambda nested, visited: _normalize_link_arguments(
                        nested,
                        library_directories,
                        base_directory,
                        allow_wl=allow_wl,
                        link_state=link_state,
                        response_files=visited,
                        library_probe_command=library_probe_command,
                    ),
                )
            )
        elif not argument.startswith("-") and _resolved_path(
            argument, base_directory
        ).is_file():
            normalized.append(_path_argument_identity(argument, "file", base_directory))
        else:
            normalized.append(argument)
        index += 1
    return normalized


def _link_search_directories(
    arguments: list[str], base_directory: Any = None
) -> list[Path]:
    directories: list[Path] = []
    seen: set[Path] = set()

    def add(raw_value: str) -> None:
        directory = _resolved_path(raw_value, base_directory)
        if directory.is_dir() and directory not in seen:
            seen.add(directory)
            directories.append(directory)

    def visit(tokens: list[str], response_files: set[Path]) -> None:
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.startswith("-Wl,"):
                visit(token[len("-Wl,") :].split(","), response_files)
                index += 1
            elif token.startswith("@") and len(token) > 1:
                response_path = _resolved_path(token[1:], base_directory)
                if response_path not in response_files:
                    response_tokens = _read_response_file(response_path)
                    if response_tokens is not None:
                        visit(response_tokens, {*response_files, response_path})
                index += 1
            elif token == "-L" and index + 1 < len(tokens):
                add(tokens[index + 1])
                index += 2
            elif token.startswith("-L") and token != "-L":
                add(token[len("-L") :].removeprefix("="))
                index += 1
            else:
                index += 1

    visit(arguments, set())
    for value in os.environ.get("LIBRARY_PATH", "").split(os.pathsep):
        if value.strip():
            add(value)
    return directories


def _library_argument_identity(
    name: str,
    library_directories: list[Path],
    *,
    static: bool,
    base_directory: Any = None,
    compiler_command: list[str] | None = None,
) -> dict[str, Any]:
    filenames = (
        [name[1:]]
        if name.startswith(":")
        else [f"lib{name}.a"]
        if static
        else [f"lib{name}.so", f"lib{name}.a"]
    )
    library = next(
        (
            candidate
            for directory in library_directories
            for filename in filenames
            if (candidate := directory / filename).is_file()
        ),
        None,
    )
    if library is None:
        library = _compiler_library_file(
            filenames,
            base_directory,
            compiler_command=compiler_command,
        )
    return {
        "option": "-l",
        "name": name,
        "mode": "static" if static else "dynamic-preferred",
        "value": (
            {"kind": "file", "sha256": _file_sha256(library)}
            if library is not None
            else {"kind": "unresolved"}
        ),
    }


def _compiler_library_file(
    filenames: list[str],
    base_directory: Any = None,
    *,
    compiler_command: list[str] | None = None,
) -> Path | None:
    compiler = compiler_command or ["c++"]
    if not compiler:
        return None

    working_directory = _resolved_path(".", base_directory)
    cwd = str(working_directory) if working_directory.is_dir() else None
    for filename in filenames:
        try:
            result = run_subprocess(
                [*compiler, f"-print-file-name={filename}"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, SubprocessError):
            return None
        resolved = result.stdout.strip()
        if result.returncode != 0 or not resolved or resolved == filename:
            continue
        candidate = _resolved_path(resolved, base_directory)
        if candidate.is_file():
            return candidate
    return None


def _verilator_link_driver(base_directory: Any = None) -> list[str]:
    build_directory = _resolved_path(".", base_directory)
    if not build_directory.is_dir():
        return ["c++"]
    makefile = next(
        (
            path
            for path in sorted(build_directory.glob("V*.mk"))
            if not path.name.endswith("_classes.mk")
        ),
        None,
    )
    if makefile is None:
        return ["c++"]
    try:
        result = run_subprocess(
            [
                "make",
                "--no-print-directory",
                "-f",
                makefile.name,
                "-f",
                "-",
                "-n",
            ],
            cwd=str(build_directory),
            input=f"$(info {_MAKE_LINK_DRIVER_MARKER}$(LINK))\n",
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, SubprocessError):
        return ["c++"]
    match = re.search(
        rf"^{re.escape(_MAKE_LINK_DRIVER_MARKER)}(?P<command>\S.*)$",
        result.stdout,
        re.MULTILINE,
    )
    if match is None:
        return ["c++"]
    try:
        command = shlex.split(match.group("command"))
    except ValueError:
        return ["c++"]
    return command or ["c++"]


def _link_has_global_static(
    arguments: list[str],
    base_directory: Any = None,
    response_files: set[Path] | None = None,
) -> bool:
    visited = response_files or set()
    for argument in arguments:
        if argument in _GLOBAL_STATIC_LINK_ARGUMENTS:
            return True
        if argument.startswith("@") and len(argument) > 1:
            response_path = _resolved_path(argument[1:], base_directory)
            if response_path in visited:
                continue
            response_arguments = _read_response_file(response_path)
            if response_arguments is not None and _link_has_global_static(
                response_arguments,
                base_directory,
                {*visited, response_path},
            ):
                return True
    return False


def _response_file_identity(
    value: str,
    base_directory: Any,
    response_files: set[Path],
    normalize_arguments: Callable[[list[str], set[Path]], list[Any]],
) -> dict[str, Any]:
    path = _resolved_path(value, base_directory)
    identity: dict[str, Any] = {
        "option": "@",
        "value": _path_argument_identity(value, "file", base_directory),
    }
    if not path.is_file():
        return identity
    if path in response_files:
        identity["cycle"] = True
        return identity
    arguments = _read_response_file(path)
    if arguments is not None:
        identity["arguments"] = normalize_arguments(
            arguments, {*response_files, path}
        )
    return identity


def _read_response_file(path: Path) -> list[str] | None:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        return shlex.split(content, comments=False, posix=True)
    except ValueError:
        return None


def _simulation_harness_sources(
    workspace: dict[str, Any],
    compile_flags: Any = None,
    build_directory: Any = None,
    dependency_map: dict[Path, set[Path]] | None = None,
) -> list[dict[str, Any]]:
    include_directories, _ = _cpp_compile_inputs(
        effective_sim_cflags(workspace) if compile_flags is None else compile_flags,
        build_directory,
    )
    sources: list[dict[str, Any]] = []
    for source in sim_cpp_sources(workspace):
        source_path = _resolved_path(source)
        compiler_dependencies = (
            dependency_map.get(source_path) if dependency_map is not None else None
        )
        sources.append(
            {
                "name": Path(source).name,
                "sha256": _file_sha256(source_path),
                "local_headers": (
                    _compiler_dependency_identities(
                        source_path,
                        compiler_dependencies,
                        include_directories,
                        build_directory,
                    )
                    if compiler_dependencies is not None
                    else _cpp_local_header_identities(
                        source_path,
                        include_directories,
                    )
                ),
            }
        )
    return sources


def _simulation_forced_header_identities(
    compile_flags: Any,
    build_directory: Any = None,
    dependency_map: dict[Path, set[Path]] | None = None,
) -> list[dict[str, Any]]:
    include_directories, forced_includes = _cpp_compile_inputs(
        compile_flags, build_directory
    )
    compiler_dependencies = (
        set().union(*dependency_map.values()) if dependency_map else None
    )
    identities: list[dict[str, Any]] = []
    for include_name in forced_includes:
        portable_name = (
            Path(include_name).name if Path(include_name).is_absolute() else include_name
        )
        header = _resolve_cpp_include(
            include_name,
            None,
            include_directories,
            quoted=True,
            base_directory=build_directory,
        )
        if header is None:
            identities.append({"include": portable_name, "kind": "missing"})
            continue
        identities.append(
            {
                "include": portable_name,
                "sha256": _file_sha256(header),
                "local_headers": _cpp_local_header_identities(
                    header,
                    include_directories,
                    active_dependencies=compiler_dependencies,
                ),
            }
        )
    return identities


def _cpp_compile_inputs(
    compile_flags: Any,
    base_directory: Any = None,
) -> tuple[dict[str, list[Path]], list[str]]:
    tokens = _shell_flag_tokens(compile_flags)
    directories = {"quote": [], "user": [], "system": []}
    seen = {"quote": set(), "user": set(), "system": set()}
    forced_includes: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-include" and index + 1 < len(tokens):
            forced_includes.append(tokens[index + 1])
            index += 2
            continue
        if token.startswith("-include") and token != "-include":
            forced_includes.append(token[len("-include") :].removeprefix("="))
            index += 1
            continue
        value = ""
        option = ""
        if token in {"-I", "-iquote", "-isystem"} and index + 1 < len(tokens):
            option = token
            value = tokens[index + 1]
            index += 2
        else:
            option = next(
                (
                    prefix
                    for prefix in ("-isystem", "-iquote", "-I")
                    if token.startswith(prefix) and token != prefix
                ),
                "",
            )
            if option:
                value = token[len(option) :].removeprefix("=")
            index += 1
        if not value:
            continue
        kind = {"-iquote": "quote", "-I": "user", "-isystem": "system"}[option]
        directory = _resolved_path(value, base_directory)
        if directory in seen[kind] or not directory.is_dir():
            continue
        seen[kind].add(directory)
        directories[kind].append(directory)
    return directories, forced_includes


def _cpp_local_header_identities(
    source: Any,
    include_directories: dict[str, list[Path]],
    *,
    active_dependencies: set[Path] | None = None,
) -> list[dict[str, str]]:
    source_path = Path(str(source or "")).expanduser().resolve()
    headers: list[dict[str, str]] = []
    visited: set[Path] = {source_path}

    def visit(path: Path) -> None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for match in _CPP_INCLUDE_RE.finditer(content):
            quoted_include = match.group("quoted")
            include_name = quoted_include or match.group("system")
            header = _resolve_cpp_include(
                include_name,
                path.parent,
                include_directories,
                quoted=bool(quoted_include),
            )
            if header is None:
                continue
            canonical = header.resolve()
            if canonical in visited or (
                active_dependencies is not None
                and canonical not in active_dependencies
            ):
                continue
            visited.add(canonical)
            headers.append(
                {
                    "include": include_name,
                    "sha256": _file_sha256(canonical),
                }
            )
            visit(canonical)

    if source_path.is_file():
        visit(source_path)
    headers.sort(key=lambda item: (item["include"], item["sha256"]))
    return headers


def _resolve_cpp_include(
    include_name: str,
    source_directory: Path | None,
    include_directories: dict[str, list[Path]],
    *,
    quoted: bool,
    base_directory: Any = None,
) -> Path | None:
    include_path = Path(include_name).expanduser()
    if include_path.is_absolute():
        return include_path.resolve() if include_path.is_file() else None
    candidates: list[Path] = []
    if quoted and source_directory is not None:
        candidates.append(source_directory / include_path)
    if quoted:
        candidates.extend(
            directory / include_path for directory in include_directories["quote"]
        )
    candidates.extend(
        directory / include_path for directory in include_directories["user"]
    )
    candidates.extend(
        directory / include_path for directory in include_directories["system"]
    )
    if quoted and source_directory is None:
        candidates.insert(0, _resolved_path(include_name, base_directory))
    return next(
        (candidate.resolve() for candidate in candidates if candidate.is_file()), None
    )


def _simulation_dependency_map(
    workspace: dict[str, Any], build_directory: Any
) -> dict[Path, set[Path]]:
    root = _resolved_path(build_directory)
    source_paths = {_resolved_path(source) for source in sim_cpp_sources(workspace)}
    dependencies_by_source: dict[Path, set[Path]] = {}
    if not root.is_dir():
        return dependencies_by_source
    for depfile in sorted(root.glob("*.d")):
        dependencies = _read_make_dependencies(depfile, root)
        for source in source_paths & dependencies:
            dependencies_by_source.setdefault(source, set()).update(dependencies)
    return dependencies_by_source


def _read_make_dependencies(path: Path, base_directory: Path) -> set[Path]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        tokens = shlex.split(content.replace("\\\n", " "), comments=False)
    except (OSError, ValueError):
        return set()
    separator = next(
        (index for index, token in enumerate(tokens) if token.endswith(":")), None
    )
    if separator is None:
        return set()
    return {
        _resolved_path(token, base_directory)
        for token in tokens[separator + 1 :]
        if token and token != "\\"
    }


def _compiler_dependency_identities(
    source: Path,
    dependencies: set[Path],
    include_directories: dict[str, list[Path]],
    build_directory: Any,
) -> list[dict[str, str]]:
    build_root = _resolved_path(build_directory)
    roots = sorted(
        {
            source.parent,
            *include_directories["quote"],
            *include_directories["user"],
            *include_directories["system"],
        },
        key=lambda path: len(path.parts),
        reverse=True,
    )
    identities: set[tuple[str, str]] = set()
    for dependency in dependencies:
        canonical = dependency.resolve()
        if canonical == source or canonical.is_relative_to(build_root):
            continue
        try:
            if not canonical.is_file():
                continue
        except OSError:
            continue
        portable_name = canonical.name
        for root in roots:
            try:
                portable_name = canonical.relative_to(root).as_posix()
                break
            except ValueError:
                continue
        identities.add((portable_name, _file_sha256(canonical)))
    return [
        {"include": name, "sha256": sha256}
        for name, sha256 in sorted(identities)
    ]


def _normalize_path_arguments(
    arguments: list[str],
    path_options: dict[str, str],
    base_directory: Any = None,
    *,
    identify_bare_files: bool = False,
    embedded_normalizer: Callable[[str], Any | None] | None = None,
) -> list[Any]:
    normalized: list[Any] = []
    index = 0
    attached_options = sorted(path_options, key=len, reverse=True)
    while index < len(arguments):
        argument = arguments[index]
        embedded = embedded_normalizer(argument) if embedded_normalizer else None
        if embedded is not None:
            normalized.append(embedded)
            index += 1
            continue
        if argument in path_options and index + 1 < len(arguments):
            normalized.append(
                {
                    "option": argument,
                    "value": _path_argument_identity(
                        arguments[index + 1], path_options[argument], base_directory
                    ),
                }
            )
            index += 2
            continue

        attached = next(
            (
                option
                for option in attached_options
                if (
                    argument.startswith(f"{option}=")
                    or (
                        option.startswith("-")
                        and not option.startswith("--")
                        and argument.startswith(option)
                        and len(argument) > len(option)
                    )
                )
            ),
            None,
        )
        if attached is not None:
            raw_value = argument[len(attached) :].removeprefix("=")
            normalized.append(
                {
                    "option": attached,
                    "value": _path_argument_identity(
                        raw_value, path_options[attached], base_directory
                    ),
                }
            )
        elif identify_bare_files and not argument.startswith("-"):
            path = Path(argument).expanduser()
            if not path.is_absolute() and str(base_directory or "").strip():
                path = Path(str(base_directory)).expanduser() / path
            normalized.append(
                _path_argument_identity(argument, "file", base_directory)
                if path.is_file()
                else argument
            )
        else:
            normalized.append(argument)
        index += 1
    return normalized


def _path_argument_identity(
    value: str, kind: str, base_directory: Any = None
) -> dict[str, Any]:
    if kind == "output":
        return {"kind": "output"}
    path = _resolved_path(value, base_directory)
    if kind == "file" and path.is_file():
        return {"kind": "file", "sha256": _file_sha256(path)}
    if kind == "search_directory" and path.is_dir():
        return {"kind": "directory"}
    if kind == "directory" and path.is_dir():
        return {"kind": "directory", "contents": _directory_identity(path)}
    return {"kind": "missing", "name": path.name}


def _resolved_path(value: Any, base_directory: Any = None) -> Path:
    path = Path(str(value or "")).expanduser()
    if path.is_absolute():
        return path.resolve()
    configured_base = str(base_directory or "").strip()
    if not configured_base:
        configured_base = os.getenv("BUILD_WORKSPACE_DIRECTORY", "").strip()
    root = Path(configured_base).expanduser() if configured_base else Path.cwd()
    return (root / path).resolve()


def _directory_identity(value: Any) -> list[dict[str, str]]:
    root = Path(str(value or "")).expanduser().resolve()
    files: list[dict[str, str]] = []
    visited_directories: set[tuple[int, int]] = set()
    for directory, child_directories, filenames in os.walk(root, followlinks=True):
        current = Path(directory)
        try:
            directory_stat = current.stat()
        except OSError:
            child_directories.clear()
            continue
        directory_key = (directory_stat.st_dev, directory_stat.st_ino)
        if directory_key in visited_directories:
            child_directories.clear()
            continue
        visited_directories.add(directory_key)
        child_directories.sort()
        for filename in sorted(filenames):
            path = current / filename
            try:
                if path.is_file():
                    files.append(
                        {
                            "path": path.relative_to(root).as_posix(),
                            "sha256": _file_sha256(path),
                        }
                    )
            except (OSError, ValueError):
                continue
    return files


def _comparison_fingerprint(
    step: Any, workspace: dict[str, Any], comparison: dict[str, Any]
) -> str:
    payload = {
        "step": str(step.name),
        "tool": str(step.tool),
        "comparison": comparison,
        "design": str(workspace.get("design", "")),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _prepared_input_fingerprint(
    step: Any,
    comparison_inputs: dict[str, Any] | None = None,
) -> str:
    payload = comparison_inputs
    if payload is None:
        manifest = (
            Path(step.directory).parent
            / "prepare_fe"
            / "output"
            / "prepared_inputs.json"
        )
        payload = _read_record(manifest)
    if payload is None:
        return ""
    sources = _records(payload.get("rtl_sources"))
    if not sources:
        sources = [{"path": path} for path in _string_list(payload.get("rtl_files"))]
    identity = {
        "rtl": [
            {
                "name": Path(str(item.get("path") or "")).name,
                "ownership": str(item.get("ownership") or ""),
                "source": str(item.get("source") or ""),
                "sha256": _file_sha256(item.get("path")),
            }
            for item in sources
        ],
        "include_dirs": [
            _include_directory_identity(path)
            for path in _prepared_include_directories(payload)
        ],
        "defines": _string_list(payload.get("defines")),
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _file_sha256(value: Any) -> str:
    path = Path(str(value or "")).expanduser()
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _include_directory_identity(value: Any) -> dict[str, Any]:
    root = Path(str(value or "")).expanduser().resolve()
    if not root.is_dir():
        return {"status": "missing", "headers": []}

    headers: list[dict[str, str]] = []
    visited_directories: set[tuple[int, int]] = set()
    walk_errors: list[OSError] = []
    for directory, child_directories, filenames in os.walk(
        root,
        followlinks=True,
        onerror=walk_errors.append,
    ):
        current = Path(directory)
        try:
            directory_stat = current.stat()
        except OSError as exc:
            walk_errors.append(exc)
            child_directories.clear()
            continue
        directory_identity = (directory_stat.st_dev, directory_stat.st_ino)
        if directory_identity in visited_directories:
            child_directories.clear()
            continue
        visited_directories.add(directory_identity)
        child_directories.sort()

        for filename in sorted(filenames):
            path = current / filename
            if path.suffix and path.suffix.lower() not in _HDL_INCLUDE_SUFFIXES:
                continue
            try:
                if not path.is_file():
                    continue
                relative_path = path.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            headers.append(
                {"path": relative_path, "sha256": _file_sha256(path)}
            )
    headers.sort(key=lambda item: (item["path"], item["sha256"]))
    return {
        "status": "unreadable" if walk_errors else "available",
        "headers": headers,
    }


def _prepared_include_directories(payload: dict[str, Any]) -> list[str]:
    directories = _string_list(payload.get("incdirs"))
    directories.extend(
        str(Path(path).expanduser().resolve().parent)
        for path in _string_list(payload.get("rtl_files"))
        if path.strip()
    )
    seen: set[Path] = set()
    ordered: list[str] = []
    for value in directories:
        text = value.strip()
        if not text:
            continue
        canonical = Path(text).expanduser().resolve()
        if canonical in seen:
            continue
        seen.add(canonical)
        ordered.append(text)
    return ordered
