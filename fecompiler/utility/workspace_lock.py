"""Cross-process exclusion for commands that mutate one frontend workspace.

The lock is an advisory ``flock`` on a hidden sibling file,
``<parent>/.<workspace-name>.lock``. Keeping it beside the workspace rather
than inside lets the same lock cover creating, replacing, and deleting the
workspace directory itself. The kernel releases it when the owner exits, so a
killed process never leaves a stale lock behind.
"""

from __future__ import annotations

import errno
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - platforms without flock
    fcntl = None  # type: ignore[assignment]

_UNWRITABLE = {errno.EACCES, errno.EPERM, errno.EROFS}


class WorkspaceBusyError(RuntimeError):
    """Another live command already holds the workspace lock."""

    def __init__(self, directory: Path, owner: dict[str, Any]) -> None:
        self.directory = directory
        self.owner = owner
        pid = owner.get("pid")
        holder = f"pid {pid}" if pid else "another command"
        super().__init__(f"frontend workspace is busy ({holder}): {directory}")


@dataclass(slots=True)
class _Held:
    thread_id: int
    depth: int
    descriptor: int | None


_guard = threading.Lock()
_held: dict[str, _Held] = {}


def workspace_lock_path(directory: str | Path) -> Path:
    path = _resolved(directory)
    return path.parent / f".{path.name}.lock"


@contextmanager
def workspace_lock(directory: str | Path) -> Iterator[None]:
    """Hold the exclusive lock of *directory* for the duration of the block.

    Never waits: raises :class:`WorkspaceBusyError` when another process, or
    another thread of this process, owns the workspace. Nested use on the same
    thread is a no-op, so a command can take the lock and then call services
    that take it again.
    """
    path = _resolved(directory)
    key = str(path)
    thread_id = threading.get_ident()
    with _guard:
        held = _held.get(key)
        if held is not None:
            if held.thread_id != thread_id:
                raise WorkspaceBusyError(path, {"pid": os.getpid()})
            held.depth += 1
        else:
            _held[key] = _Held(thread_id, 1, _acquire(path))
    try:
        yield
    finally:
        with _guard:
            held = _held[key]
            held.depth -= 1
            if held.depth == 0:
                del _held[key]
                _release(held.descriptor)


def workspace_lock_owner(directory: str | Path) -> dict[str, Any] | None:
    """Return the owner record of a different command holding *directory*.

    ``None`` means no other command owns the workspace. This is a read-only
    probe for inspection commands: it never creates the lock file and never
    keeps the lock.
    """
    path = _resolved(directory)
    with _guard:
        held = _held.get(str(path))
    if held is not None:
        if held.thread_id == threading.get_ident():
            return None
        return {"pid": os.getpid()}
    if fcntl is None:
        return None
    for candidate in _lock_candidates(path):
        try:
            descriptor = os.open(candidate, os.O_RDONLY)
        except OSError:
            continue
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EAGAIN, errno.EACCES}:
                return _read_owner(descriptor)
            raise
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    return None


def workspace_is_busy(directory: str | Path) -> bool:
    """Report whether a different command currently owns *directory*."""
    return workspace_lock_owner(directory) is not None


def discard_workspace_lock(directory: str | Path) -> bool:
    """Remove the lock file of a workspace that does not exist.

    Returns ``False`` and leaves the file alone when the workspace exists or
    another command holds the lock. Safe to call while holding the lock.
    """
    path = _resolved(directory)
    if path.exists():
        return False
    try:
        with workspace_lock(path):
            workspace_lock_path(path).unlink(missing_ok=True)
    except (WorkspaceBusyError, OSError):
        return False
    return True


def _resolved(directory: str | Path) -> Path:
    return Path(directory).expanduser().resolve()


def _acquire(path: Path) -> int | None:
    if fcntl is None:
        return None
    descriptor = _open_lock_file(path)
    if descriptor is None:
        return None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        owner = _read_owner(descriptor)
        os.close(descriptor)
        if error.errno in {errno.EAGAIN, errno.EACCES}:
            raise WorkspaceBusyError(path, owner) from None
        raise
    _write_owner(descriptor)
    return descriptor


def _open_lock_file(path: Path) -> int | None:
    """Open the sibling lock file, or one inside the workspace as a fallback.

    A workspace whose parent directory does not exist cannot exist either, and
    one in a read-only location that has no directory yet cannot be created,
    so both cases have nothing to protect and run unlocked.
    """
    if not path.parent.is_dir():
        return None
    candidates = _lock_candidates(path)
    if not path.is_dir():
        candidates = candidates[:1]
    for candidate in candidates:
        try:
            return os.open(candidate, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as error:
            if error.errno not in _UNWRITABLE:
                raise
    return None


def _lock_candidates(path: Path) -> list[Path]:
    return [workspace_lock_path(path), path / ".ecc-fe.lock"]


def _write_owner(descriptor: int) -> None:
    payload = json.dumps({"pid": os.getpid(), "acquired_at": time.time()})
    os.ftruncate(descriptor, 0)
    os.pwrite(descriptor, payload.encode("utf-8"), 0)


def _read_owner(descriptor: int) -> dict[str, Any]:
    try:
        owner = json.loads(os.pread(descriptor, 4096, 0).decode("utf-8"))
    except (OSError, ValueError):
        return {}
    return owner if isinstance(owner, dict) else {}


def _release(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        os.ftruncate(descriptor, 0)
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


__all__ = [
    "WorkspaceBusyError",
    "discard_workspace_lock",
    "workspace_is_busy",
    "workspace_lock",
    "workspace_lock_owner",
    "workspace_lock_path",
]
