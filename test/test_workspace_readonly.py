from __future__ import annotations

import json
from pathlib import Path

from fecompiler.application.workspace_service import workspace_application
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow

_RECORDED = {"steps": [{"name": "collect inputs", "state": "Success"}]}


def _workspace(tmp_path: Path) -> Path:
    directory = tmp_path / "workspace"
    create_workspace(
        CreateWorkspaceData(
            directory=str(directory),
            parameters={"Design": "demo", "Top module": "chip_top"},
        ),
    )
    return directory


def _with_recorded_stages(directory: Path) -> list[Path]:
    """Create the step directories and record stages as a finished run would."""
    engine = EngineFlow(load_workspace(str(directory)))
    engine.create_step_workspaces()
    subflows = [
        Path(step["directory"]) / "subflow.json" for step in engine.workspace_steps
    ]
    for subflow in subflows:
        subflow.write_text(json.dumps(_RECORDED), encoding="utf-8")
    return subflows


def _tree(directory: Path) -> list[str]:
    return sorted(str(path.relative_to(directory)) for path in directory.rglob("*"))


def _call(command: str, directory: Path, **payload: object):
    return workspace_application.execute_payload(
        command, {"directory": str(directory), **payload}
    )


def test_loading_and_querying_keep_the_recorded_stages(tmp_path) -> None:
    directory = _workspace(tmp_path)
    subflows = _with_recorded_stages(directory)

    assert _call("load", directory).response == "success"
    assert _call("get-info", directory, step="prepare", id="subflow").response == (
        "success"
    )
    assert _call("refresh-config", directory).response == "success"

    for subflow in subflows:
        assert json.loads(subflow.read_text(encoding="utf-8")) == _RECORDED


def test_resetting_the_flow_discards_the_recorded_stages(tmp_path) -> None:
    directory = _workspace(tmp_path)
    subflows = _with_recorded_stages(directory)

    assert _call("reset-flow", directory).response == "success"

    for subflow in subflows:
        assert json.loads(subflow.read_text(encoding="utf-8")) == {"steps": []}


def test_invalidating_a_step_discards_only_its_recorded_stages(tmp_path) -> None:
    directory = _workspace(tmp_path)
    subflows = _with_recorded_stages(directory)
    engine = EngineFlow(load_workspace(str(directory)))
    first, second = engine.flow["steps"][:2]
    second["state"] = "Success"

    assert engine._mark_step_stale(second, "upstream changed", first["name"]) is True

    assert json.loads(subflows[0].read_text(encoding="utf-8")) == _RECORDED
    assert json.loads(subflows[1].read_text(encoding="utf-8")) == {"steps": []}


def test_get_info_creates_nothing_in_the_workspace(tmp_path) -> None:
    directory = _workspace(tmp_path)
    before = _tree(directory)

    _call("get-info", directory, step="prepare", id="subflow")
    _call("get-home", directory)

    assert _tree(directory) == before


def test_a_directory_that_is_not_a_workspace_is_refused_untouched(tmp_path) -> None:
    directory = tmp_path / "sources"
    directory.mkdir()
    (directory / "readme.txt").write_text("not a workspace\n", encoding="utf-8")

    results = [
        _call(command, directory, step="prepare", id="subflow")
        for command in ("load", "get-info", "get-home", "run-flow", "reset-flow")
    ]

    assert [result.response for result in results] == ["failed"] * 5
    assert load_workspace(str(directory)) is None
    assert _tree(directory) == ["readme.txt"]
