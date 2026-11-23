"""Crash-safe file replacement for workspace and project state."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: str | Path, content: str) -> None:
    """Replace *path* with *content* so readers never observe a partial file.

    The content is written to a temporary sibling, flushed to disk, and renamed
    over the destination. A process killed at any point leaves either the
    previous file or the complete new one.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: str | Path, value: Any, *, indent: int = 2) -> None:
    """Serialize *value* first, then atomically replace *path* with it."""
    atomic_write_text(
        path,
        json.dumps(value, indent=indent, ensure_ascii=False) + "\n",
    )


__all__ = ["atomic_write_json", "atomic_write_text"]
