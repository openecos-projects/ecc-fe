"""The output contract every command shares: error codes, streams, exit codes."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from fecompiler.cli import commands as cli_commands
from fecompiler.cli import main as cli_main
from fecompiler.cli.core.records import (
    UNCLASSIFIED_ERROR,
    error_record,
    failure_record,
)
from fecompiler.cli.core.types import CommandResult, OutputMode
from fecompiler.cli.rendering.render import render_result
from fecompiler.data.step import StateEnum
from fecompiler.engine.flow import EngineFlow

_CODE = re.compile(r"[a-z][a-z0-9_]*")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)


@pytest.fixture
def workspace(tmp_path, capsys) -> Path:
    directory = tmp_path / "workspace"
    assert cli_main.run(["init", "--workspace", str(directory), "--json"]) == 0
    capsys.readouterr()
    return directory


@pytest.fixture
def project(tmp_path, capsys) -> Path:
    directory = tmp_path / "project"
    assert cli_main.run(["init", str(directory), "--json"]) == 0
    capsys.readouterr()
    return directory


def _fake_engine(monkeypatch, state: StateEnum = StateEnum.Success) -> None:
    monkeypatch.setattr(
        EngineFlow,
        "run_step",
        lambda self, step_name, rerun=False, *, observer=None: state,
    )


def test_error_is_always_a_code() -> None:
    assert error_record("run_not_found", run="a") == {
        "kind": "error",
        "error": "run_not_found",
        "run": "a",
    }
    sentence = error_record("Something went wrong: /tmp/x")
    assert sentence["error"] == UNCLASSIFIED_ERROR
    assert sentence["message"] == "Something went wrong: /tmp/x"
    failure = failure_record("workspace_unreadable", ValueError("bad json"), workspace="w")
    assert failure == {
        "kind": "error",
        "error": "workspace_unreadable",
        "message": "bad json",
        "workspace": "w",
    }


@pytest.mark.parametrize(
    ("argv", "code", "exit_code"),
    [
        (["status"], "workspace_not_found", 1),
        (["log"], "workspace_not_found", 1),
        (["config"], "workspace_not_found", 1),
        (["report", "qor"], "workspace_not_found", 1),
        (["run"], "workspace_not_found", 1),
        (["param", "show", "no.such.parameter"], "unknown_parameter", 2),
        (["param", "set", "no.such.parameter", "1"], "unknown_parameter", 2),
        (["param", "unset", "no.such.parameter"], "unknown_parameter", 2),
        (["doctor", "--step", "nope"], "unknown_step", 2),
        (["run", "--only", "nope"], "unknown_step", 2),
        (["resource", "env", "--shell", "tcsh"], "unsupported_shell", 2),
        (["resource", "install"], "invalid_resource_selection", 2),
    ],
)
def test_errors_carry_a_code_and_the_documented_exit_status(
    tmp_path, capsys, argv, code, exit_code
) -> None:
    missing = tmp_path / "missing"
    target = [] if argv[0] == "resource" else ["--workspace", str(missing)]

    assert cli_main.run([*argv, *target, "--json"]) == exit_code

    captured = capsys.readouterr()
    record = json.loads(captured.out)["records"][0]
    assert record["kind"] == "error"
    assert record["error"] == code
    assert _CODE.fullmatch(record["error"])
    assert captured.err == ""


@pytest.mark.parametrize("command", ["log", "config"])
def test_unknown_step_is_a_usage_error(workspace, capsys, command) -> None:
    assert (
        cli_main.run([command, "nope", "--workspace", str(workspace), "--json"]) == 2
    )

    record = json.loads(capsys.readouterr().out)["records"][0]
    assert record["error"] == "unknown_step"
    assert record["step"] == "nope"
    assert "sim" in record["expected"]


def test_text_errors_go_to_stderr(tmp_path, capsys) -> None:
    missing = tmp_path / "missing"

    assert cli_main.run(["status", "--workspace", str(missing)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert lines[0] == "ecc-fe: error: workspace_not_found"
    assert f"  workspace: {missing}" in lines
    assert any(line.startswith("  remediation_cmd: ecc-fe init") for line in lines)


def test_report_qor_reports_a_missing_workspace_in_text_mode(tmp_path, capsys) -> None:
    assert cli_main.run(["report", "qor", "--workspace", str(tmp_path / "missing")]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines()[0] == "ecc-fe: error: workspace_not_found"


@pytest.mark.parametrize("flag", ["--plain", "--jsonl"])
def test_structured_errors_stay_on_stdout(tmp_path, capsys, flag) -> None:
    assert cli_main.run(["status", "--workspace", str(tmp_path / "missing"), flag]) == 1

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "workspace_not_found" in captured.out


def test_text_keeps_field_names_so_commands_do_not_shadow_values() -> None:
    out = io.StringIO()
    result = CommandResult.ok(
        [{"kind": "run", "status": "success", "status_cmd": "ecc-fe status"}]
    )

    render_result("run", result, OutputMode.TEXT, file=out)

    lines = out.getvalue().splitlines()
    assert "  status: success" in lines
    assert "  status_cmd: ecc-fe status" in lines
    assert sum(line.startswith("  status:") for line in lines) == 1


@pytest.mark.parametrize("mode", [OutputMode.TEXT, OutputMode.PLAIN])
def test_booleans_are_spelled_as_in_json(mode) -> None:
    out = io.StringIO()
    result = CommandResult.ok([{"kind": "workspace", "running": False, "stale": True}])

    render_result("status", result, mode, file=out)

    text = out.getvalue()
    assert "False" not in text and "True" not in text
    assert "false" in text and "true" in text


def test_mixed_text_result_splits_records_and_errors() -> None:
    out, err = io.StringIO(), io.StringIO()
    result = CommandResult.err(
        [{"kind": "qor", "status": "partial"}, error_record("invalid_report", step="sim")]
    )

    render_result("report.files", result, OutputMode.TEXT, file=out, error_file=err)

    assert out.getvalue().splitlines() == ["[report.files]", "  kind: qor", "  status: partial"]
    assert err.getvalue().splitlines() == ["ecc-fe: error: invalid_report", "  step: sim"]


def test_status_exits_zero_when_a_step_failed(workspace, capsys) -> None:
    flow_path = workspace / "home" / "flow.json"
    flow = json.loads(flow_path.read_text(encoding="utf-8"))
    flow["steps"][0]["state"] = StateEnum.Incomplete.value
    flow_path.write_text(json.dumps(flow), encoding="utf-8")

    assert cli_main.run(["status", "--workspace", str(workspace), "--json"]) == 0

    records = json.loads(capsys.readouterr().out)["records"]
    assert records[1]["state"] == "incomplete"
    assert records[0]["state"] == "failed"
    assert records[0]["failed_step"] == records[1]["step"]


def test_status_summarises_a_flow_that_has_not_run(workspace, capsys) -> None:
    assert cli_main.run(["status", "--workspace", str(workspace), "--json"]) == 0

    summary = json.loads(capsys.readouterr().out)["records"][0]
    assert summary["state"] == "pending"
    assert summary["failed_step"] is None


def test_run_emits_step_records_and_no_data_blob(project, monkeypatch, capsys) -> None:
    _fake_engine(monkeypatch)

    assert cli_main.run(["run", "--project", str(project), "--to", "elab", "--plain"]) == 0

    lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if not line.startswith("kind=progress")
    ]
    assert [line.split()[0] for line in lines] == [
        "kind=step",
        "kind=step",
        "kind=step",
        "kind=run",
    ]
    assert lines[0].startswith("kind=step step=prepare tool=fe state=success log=")
    assert all("data=" not in line and "{" not in line.split(" messages=")[0] for line in lines)
    assert " status=success " in lines[-1]
    assert " status_cmd=" in lines[-1]


def test_failed_run_exits_one_with_the_run_record_last(project, monkeypatch, capsys) -> None:
    _fake_engine(monkeypatch, StateEnum.Incomplete)

    assert cli_main.run(["run", "--project", str(project), "--json"]) == 1

    records = json.loads(capsys.readouterr().out)["records"]
    assert records[-1]["kind"] == "run"
    assert records[-1]["status"] != "success"
    assert "data" not in records[-1]


def test_missing_log_points_at_a_command_that_exists(workspace, capsys) -> None:
    assert cli_main.run(["log", "sim", "--workspace", str(workspace), "--json"]) == 1

    record = json.loads(capsys.readouterr().out)["records"][0]
    assert record["error"] == "log_not_found"
    assert record["remediation_cmd"] == f"ecc-fe run --only sim --workspace {workspace}"


def test_unexpected_exception_is_reported_as_internal_error(
    workspace, monkeypatch, capsys
) -> None:
    def explode(directory):
        raise RuntimeError("boom at /tmp/x")

    monkeypatch.setattr(cli_commands, "load_existing_workspace", explode)

    assert cli_main.run(["status", "--workspace", str(workspace), "--json"]) == 1

    record = json.loads(capsys.readouterr().out)["records"][0]
    assert record["error"] == "internal_error"
    assert record["message"] == "boom at /tmp/x"
    assert record["command"] == "status"


def test_every_command_has_help_text(capsys) -> None:
    import typer

    from fecompiler.cli.app import app

    root = typer.main.get_command(app)
    missing: list[str] = []

    def walk(command, path: tuple[str, ...]) -> None:
        for name, child in getattr(command, "commands", {}).items():
            if not (child.help or "").strip():
                missing.append(" ".join((*path, name)))
            walk(child, (*path, name))

    walk(root, ())
    assert missing == []
