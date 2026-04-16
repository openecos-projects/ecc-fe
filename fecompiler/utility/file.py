"""File system helpers — mirrors chipcompiler/utility/file.py."""

from __future__ import annotations

import os


def chmod_folder(folder: str, mode: int = 0o777) -> None:
    """Recursively chmod all files and directories inside *folder*."""

    def _try_chmod(path: str) -> None:
        try:
            os.chmod(path, mode)
        except Exception:
            pass

    for root, dirs, files in os.walk(folder):
        _try_chmod(root)
        for file in files:
            _try_chmod(os.path.join(root, file))
        for d in dirs:
            _try_chmod(os.path.join(root, d))


def find_files(directory: str, key: str) -> list[str]:
    """Return all files inside *directory* whose name ends with *key*."""
    result_files: list[str] = []
    for root, _dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(key):
                result_files.append(os.path.join(root, file))
    return result_files


__all__ = ["chmod_folder", "find_files"]
