from __future__ import annotations

import json
from pathlib import Path

import pytest

from fecompiler.application.workspace_service import workspace_application
from fecompiler.cli import main as cli_main
from fecompiler.data.workspace import (
    WORKSPACE_SCHEMA_VERSION,
    CreateWorkspaceData,
    UnsupportedWorkspaceSchemaError,
    create_workspace,
    load_workspace,
    workspace_schema_version,
)
from fecompiler.runtime.recovery import recover_interrupted_operations


def _workspace(tmp_path: Path) -> Path:
    directory = tmp_path / "workspace"
    create_workspace(
        CreateWorkspaceData(
            directory=str(directory),
            parameters={"Design": "demo", "Top module": "chip_top"},
        ),
    )
    return directory


def _set_schema(directory: Path, value: object) -> str:
    home_path = directory / "home" / "home.json"
    home = json.loads(home_path.read_text(encoding="utf-8"))
    if value is None:
        home.pop("schema_version", None)
    else:
        home["schema_version"] = value
    home_path.write_text(json.dumps(home), encoding="utf-8")
    return _state(directory)


def _state(directory: Path) -> str:
    return "".join(
        (directory / "home" / name).read_text(encoding="utf-8")
        for name in ("home.json", "flow.json", "parameters.json")
    )


def _isolate(monkeypatch, tmp_path: Path) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))


def test_new_workspace_records_the_current_schema_version(tmp_path) -> None:
    directory = _workspace(tmp_path)

    home = json.loads((directory / "home" / "home.json").read_text(encoding="utf-8"))
    assert home["schema_version"] == WORKSPACE_SCHEMA_VERSION == 1
    assert workspace_schema_version(directory) == 1
    assert "schema_version" not in json.loads(
        (directory / "home" / "parameters.json").read_text(encoding="utf-8")
    )
    assert set(json.loads((directory / "home" / "flow.json").read_text())) == {"steps"}


def test_workspace_without_the_field_is_version_one_and_is_not_rewritten(tmp_path) -> None:
    directory = _workspace(tmp_path)
    before = _set_schema(directory, None)

    assert workspace_schema_version(directory) == 1
    assert load_workspace(str(directory)) is not None
    result = workspace_application.execute_payload("load", {"directory": str(directory)})

    assert result.response == "success"
    assert _state(directory) == before


def test_missing_home_json_is_treated_as_version_one(tmp_path) -> None:
    directory = _workspace(tmp_path)
    (directory / "home" / "home.json").unlink()

    assert workspace_schema_version(directory) == 1
    assert load_workspace(str(directory)) is not None


@pytest.mark.parametrize("value", [2, 99, 0, -1, "2", 1.5, True, [1]])
def test_unknown_schema_versions_are_refused(tmp_path, value) -> None:
    directory = _workspace(tmp_path)
    _set_schema(directory, value)

    with pytest.raises(UnsupportedWorkspaceSchemaError) as caught:
        load_workspace(str(directory))

    assert caught.value.found == value
    assert caught.value.supported == 1
    assert "update ecc-fe" in str(caught.value)


@pytest.mark.parametrize(
    "command", ["load", "run-flow", "run-step", "reset-flow", "refresh-config", "get-home"]
)
def test_application_refuses_a_newer_workspace_without_touching_it(tmp_path, command) -> None:
    directory = _workspace(tmp_path)
    before = _set_schema(directory, 2)
    payload = {"directory": str(directory)}
    if command == "run-step":
        payload["step"] = "prepare"

    result = workspace_application.execute_payload(command, payload)

    assert result.response == "failed"
    assert result.data == {
        "directory": str(directory),
        "error": "unsupported_workspace_schema",
        "schema_version": 2,
        "supported_schema_version": 1,
    }
    assert _state(directory) == before
    assert sorted(path.name for path in directory.iterdir()) == ["home", "log", "origin"]


def test_create_does_not_downgrade_a_newer_workspace(tmp_path) -> None:
    directory = _workspace(tmp_path)
    before = _set_schema(directory, 2)

    result = workspace_application.execute_payload(
        "create",
        {
            "directory": str(directory),
            "core_id": "picorv32",
            "parameters": {"Design": "other", "Top module": "top"},
        },
    )

    assert result.response == "failed"
    assert result.data["error"] == "unsupported_workspace_schema"
    assert _state(directory) == before


def test_recovery_leaves_a_newer_workspace_alone(tmp_path) -> None:
    directory = _workspace(tmp_path)
    flow_path = directory / "home" / "flow.json"
    flow = json.loads(flow_path.read_text(encoding="utf-8"))
    flow["steps"][0]["state"] = "Ongoing"
    flow_path.write_text(json.dumps(flow), encoding="utf-8")
    before = _set_schema(directory, 2)

    assert recover_interrupted_operations(directory, active_operation_ids=set()) == {
        "recovered": []
    }
    assert _state(directory) == before


@pytest.mark.parametrize("command", [["status"], ["run"], ["log"], ["config"], ["report", "qor"]])
def test_cli_reports_a_newer_workspace(tmp_path, monkeypatch, capsys, command) -> None:
    _isolate(monkeypatch, tmp_path)
    directory = _workspace(tmp_path)
    before = _set_schema(directory, 2)

    exit_code = cli_main.run([*command, "--workspace", str(directory), "--json"])

    output = capsys.readouterr().out
    assert exit_code != 0
    assert "schema version 2 is not supported" in output
    assert "update ecc-fe" in output
    assert _state(directory) == before


def test_project_runs_inherit_the_schema_version_from_the_template(
    tmp_path, monkeypatch, capsys
) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    project = tmp_path / "project"
    assert cli_main.run(["init", str(project), "--json"]) == 0
    capsys.readouterr()

    template_home = project / ".ecc-fe" / "template" / "home" / "home.json"
    assert json.loads(template_home.read_text(encoding="utf-8"))["schema_version"] == 1


def test_overwrite_does_not_replace_a_run_from_a_newer_ecc_fe(
    tmp_path, monkeypatch, capsys
) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    project = tmp_path / "project"
    assert cli_main.run(["init", str(project), "--json"]) == 0
    run_dir = project / "runs" / "default"
    create_workspace(
        CreateWorkspaceData(
            directory=str(run_dir),
            parameters={"Design": "demo", "Top module": "chip_top"},
        ),
    )
    before = _set_schema(run_dir, 2)
    capsys.readouterr()

    exit_code = cli_main.run(
        ["run", "--project", str(project), "--overwrite", "--json"]
    )

    record = json.loads(capsys.readouterr().out)["records"][0]
    assert exit_code == 1
    assert record["error"] == "overwrite_refused"
    assert "schema version 2 is not supported" in record["reason"]
    assert _state(run_dir) == before
    assert [
        path.name for path in run_dir.parent.iterdir() if path.is_dir()
    ] == ["default"]
