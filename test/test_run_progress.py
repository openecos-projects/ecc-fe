from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path

import pytest

from fecompiler.application.events import _emit_event, workspace_event_sink
from fecompiler.application.workspace_service import workspace_application
from fecompiler.cli import commands as cli_commands
from fecompiler.cli import main as cli_main
from fecompiler.cli.core.types import OutputMode
from fecompiler.cli.rendering.progress import RunProgress
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace
from fecompiler.engine.flow import EngineFlow
from fecompiler.utility.workspace_lock import workspace_lock

_EVENTS = [
    {"phase": "started", "data": {"step": "lint", "tool": "verilator"}},
    {"phase": "subflow.stage", "data": {"step": "lint", "subflow_step": "lint", "state": "Unstart"}},
    {"phase": "subflow.stage", "data": {"step": "lint", "subflow_step": "lint", "state": "Success"}},
    {
        "phase": "completed",
        "data": {"step": "lint", "state": "Success", "log_file": "/ws/lint/log.txt"},
    },
    {"phase": "started", "data": {"step": "sim"}},
    {"phase": "failed", "data": {"step": "sim", "state": "Incomplete"}},
    {"phase": "other", "data": {"step": "sim"}},
    {"phase": "started", "data": {}},
]


def _progress(mode: OutputMode) -> tuple[str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    ticks = iter(range(100))
    sink = RunProgress(mode, stdout=stdout, stderr=stderr, clock=lambda: float(next(ticks)))
    for event in _EVENTS:
        sink(event)
    return stdout.getvalue(), stderr.getvalue()


def test_jsonl_streams_step_and_stage_records_on_stdout() -> None:
    stdout, stderr = _progress(OutputMode.JSONL)

    assert stderr == ""
    assert [json.loads(line) for line in stdout.splitlines()] == [
        {"kind": "progress", "event": "step_started", "step": "lint", "tool": "verilator"},
        {"kind": "progress", "event": "stage", "step": "lint", "stage": "lint", "state": "success"},
        {
            "kind": "progress",
            "event": "step_finished",
            "step": "lint",
            "state": "success",
            "elapsed_s": 1.0,
            "log": "/ws/lint/log.txt",
        },
        {"kind": "progress", "event": "step_started", "step": "sim"},
        {
            "kind": "progress",
            "event": "step_finished",
            "step": "sim",
            "state": "incomplete",
            "elapsed_s": 1.0,
        },
    ]


def test_plain_streams_key_value_records_on_stdout() -> None:
    stdout, stderr = _progress(OutputMode.PLAIN)

    assert stderr == ""
    assert stdout.splitlines()[0] == "kind=progress event=step_started step=lint tool=verilator"
    assert stdout.splitlines()[2].startswith("kind=progress event=step_finished step=lint")


@pytest.mark.parametrize("mode", [OutputMode.TEXT, OutputMode.JSON])
def test_text_and_json_keep_stdout_clean_and_report_steps_on_stderr(mode) -> None:
    stdout, stderr = _progress(mode)

    assert stdout == ""
    assert stderr.splitlines() == [
        "ecc-fe: lint started",
        "ecc-fe: lint success (1.0s)",
        "ecc-fe: sim started",
        "ecc-fe: sim incomplete (1.0s)",
    ]


def test_an_installed_sink_survives_a_payload_call_without_one(tmp_path) -> None:
    received: list[dict[str, object]] = []

    with workspace_event_sink(received.append):
        workspace_application.execute_payload("load", {"directory": str(tmp_path)})
        _emit_event("run_step", "started", {"step": "lint"}, json_output=False)

    assert [event["phase"] for event in received] == ["started"]


def _workspace(tmp_path: Path) -> Path:
    directory = tmp_path / "workspace"
    create_workspace(
        CreateWorkspaceData(
            directory=str(directory),
            parameters={"Design": "demo", "Top module": "chip_top"},
        ),
    )
    return directory


def test_run_reports_each_step_before_the_final_record(
    tmp_path, monkeypatch, capsys
) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    directory = _workspace(tmp_path)
    monkeypatch.setattr(
        EngineFlow,
        "run_step",
        lambda self, step_name, rerun=False, *, observer=None: StateEnum.Success,
    )

    exit_code = cli_main.run(
        ["run", "--workspace", str(directory), "--from", "elab", "--to", "lint", "--jsonl"]
    )

    captured = capsys.readouterr()
    records = [json.loads(line) for line in captured.out.splitlines()]
    assert exit_code == 0
    assert [
        (record["event"], record["step"])
        for record in records
        if record["kind"] == "progress" and record["event"] != "stage"
    ] == [
        ("step_started", "elab"),
        ("step_finished", "elab"),
        ("step_started", "lint"),
        ("step_finished", "lint"),
    ]
    assert records[-1]["kind"] == "run"
    assert captured.err == ""


def test_follow_prints_the_tail_of_a_finished_log_and_returns(tmp_path, capsys) -> None:
    directory = _workspace(tmp_path)
    log = directory / "lint_verilator" / "report" / "log.txt"
    log.parent.mkdir(parents=True)
    log.write_text("one\ntwo\nthree\n", encoding="utf-8")

    exit_code = cli_main.run(
        ["log", "lint", "--workspace", str(directory), "--follow", "--lines", "2"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "two\nthree\n"


def test_follow_prints_what_a_running_step_appends(tmp_path, capsys) -> None:
    directory = _workspace(tmp_path)
    report = directory / "sim_verilator" / "report" / "log.txt"
    live = directory / "sim_verilator" / "log" / "log.txt"
    report.parent.mkdir(parents=True)
    live.parent.mkdir(parents=True)
    report.write_text("previous summary\n", encoding="utf-8")
    release = threading.Event()

    def running_step() -> None:
        with workspace_lock(directory):
            live.write_text("compiling\n", encoding="utf-8")
            time.sleep(0.5)
            with live.open("a", encoding="utf-8") as stream:
                stream.write("linking\n")
            time.sleep(0.5)
            report.write_text("new summary\n", encoding="utf-8")
            release.set()

    writer = threading.Thread(target=running_step)
    writer.start()
    time.sleep(0.2)
    try:
        cli_commands._follow_logs([report, live], 5, str(directory), poll_seconds=0.05)
    finally:
        writer.join()

    assert release.is_set()
    output = capsys.readouterr().out
    assert "linking\n" in output
    assert f"==> {report} <==\nnew summary\n" in output
    assert output.index("linking") < output.index("new summary")


def test_follow_refuses_record_output_modes(tmp_path, capsys) -> None:
    directory = _workspace(tmp_path)

    exit_code = cli_main.run(
        ["log", "lint", "--workspace", str(directory), "--follow", "--json"]
    )

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out)["records"][0]["error"] == (
        "follow_requires_text_output"
    )
