"""A moved direct workspace, and a run that predates a step of its project."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from fecompiler.cli import main as cli_main
from fecompiler.cli.project.config import load_project_config, next_run_parameters
from fecompiler.cli.project.relocate import relocate_workspace

_RTL = "module counter(input clk, output reg [3:0] q);\n  always @(posedge clk) q <= q + 1;\nendmodule\n"
_TB = "module tb;\n  initial $finish;\nendmodule\n"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    monkeypatch.delenv("ECC_FE_PROGRAM", raising=False)


def _call(capsys, *args: str) -> tuple[int, list[dict[str, object]], str]:
    exit_code = cli_main.run([*args, "--json"])
    captured = capsys.readouterr()
    return exit_code, json.loads(captured.out)["records"], captured.err


# --- a direct workspace that was moved --------------------------------------


@pytest.fixture
def workspace(tmp_path, capsys) -> Path:
    directory = tmp_path / "ws"
    assert _call(capsys, "init", "--workspace", str(directory))[0] == 0
    return directory


def _stored_paths(workspace: Path) -> str:
    return "".join(path.read_text(encoding="utf-8") for path in (workspace / "home").glob("*.json"))


def test_a_workspace_where_it_was_created_is_left_alone(workspace) -> None:
    before = _stored_paths(workspace)

    assert relocate_workspace(str(workspace)) is None
    assert _stored_paths(workspace) == before


def test_a_moved_workspace_gets_its_own_paths_rewritten(workspace, tmp_path) -> None:
    moved = tmp_path / "elsewhere" / "renamed"
    moved.parent.mkdir()
    shutil.move(str(workspace), str(moved))
    assert str(workspace) in _stored_paths(moved)

    assert relocate_workspace(str(moved)) == {
        "from": str(workspace),
        "to": str(moved),
        "workspaces": [str(moved)],
    }

    stored = _stored_paths(moved)
    assert str(workspace) + "/" not in stored
    assert json.loads((moved / "home" / "home.json").read_text(encoding="utf-8"))["parameters"] == str(
        moved / "home" / "parameters.json"
    )
    # Once is enough.
    assert relocate_workspace(str(moved)) is None


def test_paths_outside_the_workspace_are_not_touched(workspace, tmp_path) -> None:
    outside = tmp_path / "shared" / "design.f"
    parameters_path = workspace / "home" / "parameters.json"
    parameters = json.loads(parameters_path.read_text(encoding="utf-8"))
    parameters["input_filelist"] = str(outside)
    parameters["note"] = str(workspace) + "-sibling/file.v"
    parameters_path.write_text(json.dumps(parameters), encoding="utf-8")
    moved = tmp_path / "moved"
    shutil.move(str(workspace), str(moved))

    relocate_workspace(str(moved))

    stored = json.loads((moved / "home" / "parameters.json").read_text(encoding="utf-8"))
    assert stored["input_filelist"] == str(outside)
    # A directory that merely starts with the old name is another directory.
    assert stored["note"] == str(workspace) + "-sibling/file.v"


def test_a_directory_that_is_not_a_workspace_is_ignored(tmp_path) -> None:
    (tmp_path / "plain").mkdir()

    assert relocate_workspace(str(tmp_path / "plain")) is None
    assert relocate_workspace(str(tmp_path / "missing")) is None


def test_the_next_command_repairs_a_moved_workspace_and_says_so(workspace, tmp_path, capsys) -> None:
    moved = tmp_path / "moved"
    shutil.move(str(workspace), str(moved))

    exit_code, records, stderr = _call(capsys, "status", "--workspace", str(moved))

    assert exit_code == 0
    assert records[0]["workspace"] == str(moved)
    assert stderr.strip() == f"ecc-fe: workspace moved from {workspace}; updated its paths"
    # The second command has nothing to repair.
    assert _call(capsys, "status", "--workspace", str(moved))[2] == ""


def test_a_copied_workspace_is_repaired_and_the_original_is_not_changed(workspace, tmp_path, capsys) -> None:
    before = _stored_paths(workspace)
    copy = tmp_path / "copy"
    shutil.copytree(workspace, copy)

    assert _call(capsys, "status", "--workspace", str(copy))[0] == 0

    assert _stored_paths(workspace) == before
    assert str(workspace) + "/" not in _stored_paths(copy)


# --- a run created before the project gained a step -------------------------


@pytest.fixture
def project(tmp_path, capsys) -> Path:
    source = tmp_path / "counter.v"
    source.write_text(_RTL, encoding="utf-8")
    (tmp_path / "tb.v").write_text(_TB, encoding="utf-8")
    directory = tmp_path / "project"
    assert _call(
        capsys, "init", str(directory), "--kind", "rtl", "--top", "counter", "--rtl", str(source)
    )[0] == 0
    return directory


def _name_testbench(capsys, project: Path, tmp_path: Path) -> None:
    for name, value in (
        ("testbench.files", json.dumps([str(tmp_path / "tb.v")])),
        ("testbench.top", "tb"),
    ):
        assert _call(capsys, "param", "set", name, value, "--project", str(project))[0] == 0


def _flow(capsys, project: Path, *args: str) -> list[str]:
    exit_code, records, _ = _call(capsys, "flow", "list", "--project", str(project), *args)
    assert exit_code == 0, records
    return records[0]["steps"]


def test_flow_list_of_the_next_run_follows_the_projects_settings(project, tmp_path, capsys) -> None:
    assert _flow(capsys, project) == ["prepare", "review", "elab", "lint"]

    _name_testbench(capsys, project, tmp_path)

    assert _flow(capsys, project) == ["prepare", "review", "elab", "lint", "sim"]
    exit_code, records, _ = _call(capsys, "flow", "show", "sim", "--project", str(project))
    assert exit_code == 0 and records[0]["in_flow"] is True


def test_next_run_parameters_apply_the_projects_values_over_the_template(project, tmp_path, capsys) -> None:
    _name_testbench(capsys, project, tmp_path)
    template = json.loads(
        (project / ".ecc-fe" / "template" / "home" / "parameters.json").read_text(encoding="utf-8")
    )
    before = dict(template)

    effective = next_run_parameters(template, load_project_config(str(project)))

    assert effective["testbench_top"] == "tb"
    assert template == before
    assert next_run_parameters(template, None) == template


def _make_run(project: Path, name: str = "default") -> Path:
    """A run as `run` creates it, without running any tool."""
    run = project / "runs" / name
    shutil.copytree(project / ".ecc-fe" / "template", run)
    return run


def test_a_run_made_before_the_testbench_says_a_new_run_has_the_step(project, tmp_path, capsys) -> None:
    _make_run(project)
    _name_testbench(capsys, project, tmp_path)

    exit_code, records, _ = _call(capsys, "run", "--project", str(project), "--only", "sim")

    assert exit_code == 2
    record = records[0]
    assert record["error"] == "step_not_in_flow"
    assert record["expected"] == ["prepare", "review", "elab", "lint"]
    assert record["reason"] == (
        "run default was created before the project's settings added the sim step; "
        "a new run has it"
    )
    assert record["new_run_cmd"] == f"ecc-fe run --run-id <new-run> --project {project}"


def test_a_step_no_run_of_the_project_would_have_keeps_the_plain_reason(project, capsys) -> None:
    _make_run(project)

    exit_code, records, _ = _call(capsys, "run", "--project", str(project), "--only", "sim")

    assert exit_code == 2
    assert records[0]["reason"] == "this design's flow does not include the step"
    assert "new_run_cmd" not in records[0]
