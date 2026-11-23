"""A project run reports the parameters it was created with."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fecompiler.cli import commands as cli_commands
from fecompiler.cli import main as cli_main


def _json_call(capsys, *args: str) -> tuple[int, list[dict[str, object]]]:
    exit_code = cli_main.run([*args, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)["records"]


def _param(records: list[dict[str, object]], name: str) -> dict[str, object]:
    return next(record for record in records if record.get("param") == name)


@pytest.fixture
def project(monkeypatch, tmp_path, capsys) -> Path:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    directory = tmp_path / "project"
    assert _json_call(capsys, "init", str(directory))[0] == 0
    # Runs are created for real; only executing the flow is skipped.
    monkeypatch.setattr(
        cli_commands.workspace_application,
        "execute_payload",
        lambda command, payload, *, base_dir: SimpleNamespace(
            response="success", data={}, message=[]
        ),
    )
    # first: project default 150, one run-local override
    assert _json_call(
        capsys, "param", "set", "design.frequency_mhz", "150", "--project", str(directory)
    )[0] == 0
    assert _json_call(
        capsys,
        "run",
        "--project",
        str(directory),
        "--run-id",
        "first",
        "--set",
        "sim.compile_preset=speed",
    )[0] == 0
    # afterwards the project moves on
    assert _json_call(
        capsys, "param", "set", "design.frequency_mhz", "200", "--project", str(directory)
    )[0] == 0
    assert _json_call(
        capsys, "param", "set", "sim.compile_preset", "size", "--project", str(directory)
    )[0] == 0
    return directory


def test_config_shows_what_the_run_was_created_with(project, capsys) -> None:
    exit_code, records = _json_call(
        capsys, "config", "--resolved", "--project", str(project), "--run-id", "first"
    )

    assert exit_code == 0
    frequency = _param(records, "design.frequency_mhz")
    preset = _param(records, "sim.compile_preset")
    assert (frequency["value"], frequency["source"]) == (150.0, "run")
    assert (preset["value"], preset["source"]) == ("speed", "cli")
    assert records[0]["source"] == str(
        project / "runs" / "first" / "home" / "parameters.json"
    )
    overrides = records[0]["overrides"]
    assert overrides["design.frequency_mhz"] == 150.0
    assert overrides["sim.compile_preset"] == "speed"
    assert records[0]["run_overrides"] == {"sim.compile_preset": "speed"}


def test_param_commands_without_run_id_describe_the_next_run(project, capsys) -> None:
    exit_code, records = _json_call(
        capsys, "param", "list", "--project", str(project)
    )

    assert exit_code == 0
    assert _param(records, "design.frequency_mhz")["value"] == 200.0
    assert _param(records, "design.frequency_mhz")["source"] == "ecc-fe.toml"
    assert _param(records, "sim.compile_preset")["value"] == "size"


def test_param_commands_with_run_id_describe_that_run(project, capsys) -> None:
    target = ("--project", str(project), "--run-id", "first")

    exit_code, listed = _json_call(capsys, "param", "list", *target)
    assert exit_code == 0
    assert _param(listed, "design.frequency_mhz")["value"] == 150.0
    assert _param(listed, "design.frequency_mhz")["source"] == "run"
    assert _param(listed, "sim.compile_preset")["source"] == "cli"

    exit_code, shown = _json_call(capsys, "param", "show", "sim.compile_preset", *target)
    assert exit_code == 0
    assert (shown[0]["value"], shown[0]["source"]) == ("speed", "cli")

    exit_code, diff = _json_call(capsys, "param", "diff", *target)
    assert exit_code == 0
    assert {(item["param"], item["value"], item["source"]) for item in diff} == {
        ("design.frequency_mhz", 150.0, "run"),
        ("sim.compile_preset", "speed", "cli"),
    }


def test_two_runs_report_their_own_values(project, capsys) -> None:
    assert _json_call(
        capsys, "run", "--project", str(project), "--run-id", "second"
    )[0] == 0

    def frequency(run_id: str) -> object:
        _, records = _json_call(
            capsys,
            "param",
            "show",
            "design.frequency_mhz",
            "--project",
            str(project),
            "--run-id",
            run_id,
        )
        return records[0]["value"]

    assert frequency("first") == 150.0
    assert frequency("second") == 200.0


def test_param_commands_report_a_missing_run(project, capsys) -> None:
    exit_code, records = _json_call(
        capsys, "param", "list", "--project", str(project), "--run-id", "absent"
    )

    assert exit_code == 1
    assert records[0]["error"] == "run_not_found"
    assert records[0]["run"] == "absent"
    assert records[0]["create_cmd"] == (
        f"ecc-fe run --project {project} --run-id absent"
    )
