"""Adapters that let tests stand in for external tools."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fecompiler.tools.common.process import ToolResult


def as_run_tool(fake: Callable[..., Any]) -> Callable[..., ToolResult]:
    """Wrap a ``subprocess.run``-shaped fake as a replacement for ``run_tool``.

    The fake receives the command plus whichever of ``capture_output``,
    ``text``, ``cwd``, ``env`` and ``timeout`` it declares, and returns an
    object with ``returncode``, ``stdout`` and ``stderr``.
    """
    accepted = inspect.signature(fake).parameters

    def run_tool(cmd, *, log_path=None, cwd=None, env=None, timeout=None, session_dir=None):
        offered = {
            "capture_output": True,
            "text": True,
            "cwd": cwd,
            "env": env,
            "timeout": timeout,
        }
        completed = fake(
            cmd, **{name: value for name, value in offered.items() if name in accepted}
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        if log_path is not None:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(log_path).write_text(output, encoding="utf-8")
        return ToolResult(returncode=int(completed.returncode), output=output)

    return run_tool
