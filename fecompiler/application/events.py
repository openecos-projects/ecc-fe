"""Progress events emitted while a workspace operation runs."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from fecompiler.data.step import StateEnum


WorkspaceEventSink = Callable[[dict[str, Any]], None]
_workspace_event_sink: ContextVar[WorkspaceEventSink | None] = ContextVar(
    "workspace_event_sink",
    default=None,
)
_runtime_operation_marker: ContextVar[dict[str, Any] | None] = ContextVar(
    "runtime_operation_marker",
    default=None,
)


@contextmanager
def workspace_event_sink(sink: WorkspaceEventSink | None) -> Iterator[None]:
    token = _workspace_event_sink.set(sink)
    try:
        yield
    finally:
        _workspace_event_sink.reset(token)


@contextmanager
def runtime_operation_marker(marker: dict[str, Any] | None) -> Iterator[None]:
    token = _runtime_operation_marker.set(dict(marker) if marker else None)
    try:
        yield
    finally:
        _runtime_operation_marker.reset(token)


class _FrontendSubflowObserver:
    def __init__(self, *, cmd: str, directory: str, json_output: bool) -> None:
        self.cmd = cmd
        self.directory = directory
        self.json_output = json_output

    @property
    def runtime_operation(self) -> dict[str, Any] | None:
        marker = _runtime_operation_marker.get()
        return dict(marker) if marker else None

    def on_subflow_stage(self, workspace_step: Any, subflow_step: dict[str, Any]) -> None:
        name = str(subflow_step.get("name", ""))
        _emit_event(
            self.cmd,
            "subflow.stage",
            {
                "directory": self.directory,
                "peak_memory_mb": subflow_step.get("peak memory (mb)", 0),
                "runtime": str(subflow_step.get("runtime", "")),
                "state": str(subflow_step.get("state", StateEnum.Unstart.value)),
                "step": str(workspace_step.name),
                "subflow_path": str(workspace_step.subflow.get("path", "")),
                "subflow_step": name,
                "tool": str(workspace_step.tool),
            },
            [f"frontend subflow {workspace_step.name}/{name} updated"],
            json_output=self.json_output,
        )


def _emit_event(
    cmd: str,
    phase: str,
    data: dict[str, Any] | None = None,
    message: list[str] | None = None,
    *,
    json_output: bool,
) -> None:
    payload = {
        "type": "event",
        "phase": phase,
        "cmd": cmd,
        "data": data or {},
        "message": message or [],
    }
    sink = _workspace_event_sink.get()
    if sink is not None:
        sink(payload)
        return
    if not json_output:
        return
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
