"""Wall time and peak memory of one flow step.

Peak memory is the largest resident set size, summed over this process and
every process it started, seen while the step ran. It is sampled, so a child
that lives for less than one interval can be missed. It is measured on Linux
(through ``/proc``); elsewhere it is reported as ``0``.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

_PROC = Path("/proc")
_INTERVAL_S = 0.2


class StepUsage:
    """Measure a step from construction until :meth:`stop`."""

    def __init__(self, *, interval: float = _INTERVAL_S) -> None:
        self._started = time.monotonic()
        self._interval = interval
        self._peak_bytes = 0
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        if _PROC.joinpath("self", "statm").is_file():
            self._sample()
            self._thread = threading.Thread(
                target=self._run, name="ecc-fe-step-usage", daemon=True
            )
            self._thread.start()

    def stop(self) -> tuple[float, float]:
        """Return ``(seconds, peak_memory_mb)``; safe to call more than once."""
        seconds = time.monotonic() - self._started
        if self._thread is not None:
            self._done.set()
            self._thread.join(timeout=2.0)
            self._thread = None
            self._sample()
        return round(seconds, 3), round(self._peak_bytes / (1024 * 1024), 1)

    def _run(self) -> None:
        while not self._done.wait(self._interval):
            self._sample()

    def _sample(self) -> None:
        try:
            total = process_tree_rss_bytes(os.getpid())
        except OSError:
            return
        self._peak_bytes = max(self._peak_bytes, total)


def process_tree_rss_bytes(root_pid: int) -> int:
    """Return the resident memory of *root_pid* and all of its descendants."""
    page = os.sysconf("SC_PAGE_SIZE")
    total = 0
    for pid in _process_tree(root_pid):
        try:
            fields = _PROC.joinpath(str(pid), "statm").read_text().split()
            total += int(fields[1]) * page
        except (OSError, ValueError, IndexError):
            continue  # the process ended between listing and reading
    return total


def _process_tree(root_pid: int) -> list[int]:
    children = _children_by_parent()
    tree, pending = [], [root_pid]
    while pending:
        pid = pending.pop()
        tree.append(pid)
        pending.extend(children.get(pid, ()))
    return tree


def _children_by_parent() -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for entry in os.scandir(_PROC):
        if not entry.name.isdigit():
            continue
        try:
            stat = Path(entry.path, "stat").read_text()
            # The command name may contain spaces and parentheses; the parent
            # pid is the second field after the last ')'.
            parent = int(stat[stat.rindex(")") + 2 :].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(parent, []).append(int(entry.name))
    return children
