"""Filelist parsing utilities — mirrors chipcompiler/utility/filelist.py."""

from __future__ import annotations

import os
from typing import List


UNSUPPORTED_OPTIONS = {
    '-f': 'Recursive filelist files',
    '-v': 'Library files',
    '-y': 'Library search directories',
}


def parse_filelist(filelist_path: str) -> List[str]:
    """Parse a filelist file and extract all file paths.

    Args:
        filelist_path: Path to the filelist file

    Returns:
        List of file paths (relative or absolute) found in the filelist

    Raises:
        FileNotFoundError: If filelist file doesn't exist
        IOError: If filelist file can't be read
    """
    if not os.path.exists(filelist_path):
        raise FileNotFoundError(f"Filelist not found: {filelist_path}")

    file_paths = []
    with open(filelist_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            path = _parse_line(line, line_num)
            if path:
                file_paths.append(path)
    return file_paths


def _parse_line(line: str, line_num: int) -> str | None:
    """Parse a single line from the filelist.

    Returns the file path if the line contains one, or None if the line should
    be skipped.  Raises ValueError for unsupported options (-f, -v, -y).
    """
    line = line.strip()

    if not line or line.startswith(('#', '//', '`')):
        return None

    if line.startswith('+'):
        return None

    if line.startswith('-'):
        option = line.split()[0]
        if option in UNSUPPORTED_OPTIONS:
            descriptions = '\n'.join(
                f"  {opt}: {desc}" for opt, desc in UNSUPPORTED_OPTIONS.items()
            )
            raise ValueError(
                f"Unsupported filelist option at line {line_num}: '{option}'\n"
                f"The parser does not support:\n"
                f"{descriptions}\n"
                f"Please expand these manually or use a filelist with only direct file paths."
            )
        return None

    line = _remove_inline_comment(line)
    return line.strip('"\'') or None


def _remove_inline_comment(line: str) -> str:
    """Remove inline comments from a line."""
    for marker in ('#', '//'):
        if marker in line:
            line = line[:line.index(marker)].strip()
    return line


def resolve_path(path: str, base_dir: str) -> str:
    """Resolve a file path (relative or absolute) based on a base directory.

    Args:
        path: File path (can be relative or absolute)
        base_dir: Base directory to resolve relative paths against

    Returns:
        Absolute path to the file
    """
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(base_dir, path))


def validate_filelist(filelist_path: str) -> tuple[List[str], List[str]]:
    """Validate a filelist by checking if all referenced files exist.

    Args:
        filelist_path: Path to the filelist file

    Returns:
        Tuple of (existing_files, missing_files) where each is a list of paths
    """
    filelist_dir = os.path.dirname(os.path.abspath(filelist_path))
    file_paths = parse_filelist(filelist_path)

    existing_files: List[str] = []
    missing_files: List[str] = []
    for file_path in file_paths:
        abs_path = resolve_path(file_path, filelist_dir)
        target = existing_files if os.path.exists(abs_path) else missing_files
        target.append(file_path)
    return existing_files, missing_files


def get_filelist_info(filelist_path: str) -> dict:
    """Get detailed information about a filelist and its referenced files.

    Returns a dict with keys: filelist, base_dir, total_files,
    existing_files, missing_files, file_sizes.
    """
    abs_filelist = os.path.abspath(filelist_path)
    filelist_dir = os.path.dirname(abs_filelist)
    existing_files, missing_files = validate_filelist(filelist_path)
    file_sizes = _compute_file_sizes(existing_files, filelist_dir)
    return {
        'filelist': abs_filelist,
        'base_dir': filelist_dir,
        'total_files': len(existing_files) + len(missing_files),
        'existing_files': existing_files,
        'missing_files': missing_files,
        'file_sizes': file_sizes,
    }


def _compute_file_sizes(file_paths: List[str], base_dir: str) -> dict:
    """Compute file sizes for a list of file paths."""
    file_sizes: dict = {}
    for file_path in file_paths:
        abs_path = resolve_path(file_path, base_dir)
        try:
            file_sizes[file_path] = os.path.getsize(abs_path)
        except OSError:
            file_sizes[file_path] = 0
    return file_sizes


def parse_incdir_directives(filelist_path: str) -> List[str]:
    """Parse +incdir directives from a filelist file.

    Args:
        filelist_path: Path to the filelist file

    Returns:
        List of include directory paths from +incdir directives

    Raises:
        FileNotFoundError: If filelist file doesn't exist
    """
    if not os.path.exists(filelist_path):
        raise FileNotFoundError(f"Filelist not found: {filelist_path}")

    incdir_paths: List[str] = []
    with open(filelist_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(('#', '//', '`')):
                continue
            if line.startswith('+incdir+'):
                path = _extract_incdir_path(line)
                if path:
                    incdir_paths.append(path)
    return incdir_paths


def _extract_incdir_path(line: str) -> str:
    """Extract path from +incdir+ directive, handling comments and quotes."""
    path = line.removeprefix('+incdir+')
    path = _remove_inline_comment(path).strip()
    return path.strip('"\'') or ""


__all__ = [
    "parse_filelist",
    "resolve_path",
    "validate_filelist",
    "get_filelist_info",
    "parse_incdir_directives",
]
