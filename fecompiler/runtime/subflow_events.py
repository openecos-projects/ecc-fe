from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


_subflow_observer: ContextVar[Any | None] = ContextVar(
    "frontend_subflow_observer",
    default=None,
)


@contextmanager
def subflow_observer(observer: Any | None) -> Iterator[None]:
    """Expose a runtime-only observer while one flow step is executing."""
    token = _subflow_observer.set(observer)
    try:
        yield
    finally:
        _subflow_observer.reset(token)


def publish_subflow_stage(
    workspace_step: Any,
    subflow_step: dict[str, Any],
) -> None:
    """Forward a saved subflow transition to the optional runtime observer."""
    observer = _subflow_observer.get()
    callback = getattr(observer, "on_subflow_stage", None)
    if not callable(callback):
        return
    try:
        callback(workspace_step, dict(subflow_step))
    except Exception:
        # Runtime notification delivery must not change the tool result.
        return
