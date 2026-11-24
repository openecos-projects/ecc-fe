"""Report the progress of a running flow while `ecc-fe run` waits for it.

The application layer emits an event when a step starts, when it finishes,
and when one of its stages changes state. :class:`RunProgress` turns those
into ``progress`` records and writes them as they happen:

* ``--jsonl`` and ``--plain``: one record per line on stdout, ahead of the
  final ``run`` record, so a consumer can follow a run without polling;
* text and ``--json``: one short line per step on stderr, which keeps stdout
  a single document for ``--json`` and a clean summary for text.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from typing import Any, TextIO

from fecompiler.cli.core.types import OutputMode
from fecompiler.cli.rendering.render import _plain_record
from fecompiler.utility.program import program_name

_STEP_STARTED = "started"
_STEP_FINISHED = {"completed", "failed"}
_STAGE = "subflow.stage"


class RunProgress:
    """An application event sink that writes progress for one output mode."""

    def __init__(
        self,
        mode: OutputMode,
        *,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._mode = mode
        self._stdout = stdout
        self._stderr = stderr
        self._clock = clock
        self._started: dict[str, float] = {}

    def __call__(self, event: dict[str, Any]) -> None:
        record = self._record(event)
        if record is None:
            return
        if self._mode == OutputMode.JSONL:
            self._write(self._stdout or sys.stdout, json.dumps(record, ensure_ascii=False))
        elif self._mode == OutputMode.PLAIN:
            self._write(self._stdout or sys.stdout, _plain_record(record))
        elif record["event"] != "stage":
            self._write(self._stderr or sys.stderr, _text_line(record))

    def _record(self, event: dict[str, Any]) -> dict[str, object] | None:
        phase = str(event.get("phase", ""))
        data = event.get("data")
        if not isinstance(data, dict):
            return None
        step = str(data.get("step", "")).strip()
        if not step:
            return None
        if phase == _STEP_STARTED:
            self._started[step] = self._clock()
            record: dict[str, object] = {
                "kind": "progress",
                "event": "step_started",
                "step": step,
            }
            if data.get("tool"):
                record["tool"] = str(data["tool"])
            return record
        if phase in _STEP_FINISHED:
            record = {
                "kind": "progress",
                "event": "step_finished",
                "step": step,
                "state": str(data.get("state", "")).lower(),
            }
            started = self._started.pop(step, None)
            if started is not None:
                record["elapsed_s"] = round(self._clock() - started, 2)
            if data.get("log_file"):
                record["log"] = str(data["log_file"])
            return record
        if phase == _STAGE:
            state = str(data.get("state", "")).lower()
            stage = str(data.get("subflow_step", "")).strip()
            # Every stage is announced as "unstart" when a step is reset.
            if not stage or state in {"", "unstart"}:
                return None
            return {
                "kind": "progress",
                "event": "stage",
                "step": step,
                "stage": stage,
                "state": state,
            }
        return None

    @staticmethod
    def _write(stream: TextIO, line: str) -> None:
        print(line, file=stream, flush=True)


def _text_line(record: dict[str, object]) -> str:
    step = record["step"]
    if record["event"] == "step_started":
        return f"{program_name()}: {step} started"
    elapsed = record.get("elapsed_s")
    suffix = f" ({elapsed:.1f}s)" if isinstance(elapsed, float) else ""
    return f"{program_name()}: {step} {record.get('state', '')}{suffix}"


__all__ = ["RunProgress"]
