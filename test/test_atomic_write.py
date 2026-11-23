from __future__ import annotations

import json
from pathlib import Path

import pytest

from fecompiler.application import workspace_service
from fecompiler.data.workspace import save_flow
from fecompiler.utility import atomic_write
from fecompiler.utility.atomic_write import atomic_write_json, atomic_write_text
from fecompiler.utility.json import json_read_strict, json_write


def _names(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def test_atomic_write_replaces_content_and_leaves_no_temporary_file(tmp_path) -> None:
    target = tmp_path / "home" / "flow.json"

    atomic_write_json(target, {"steps": [1]})
    atomic_write_json(target, {"steps": [1, 2]})

    assert json.loads(target.read_text(encoding="utf-8")) == {"steps": [1, 2]}
    assert _names(target.parent) == ["flow.json"]


def test_unserializable_value_keeps_the_previous_file(tmp_path) -> None:
    target = tmp_path / "parameters.json"
    atomic_write_json(target, {"Design": "chip"})

    with pytest.raises(TypeError):
        atomic_write_json(target, {"Design": object()})

    assert json.loads(target.read_text(encoding="utf-8")) == {"Design": "chip"}
    assert _names(tmp_path) == ["parameters.json"]


def test_interrupted_replace_keeps_the_previous_file(tmp_path, monkeypatch) -> None:
    target = tmp_path / "flow.json"
    atomic_write_text(target, "previous")

    def interrupted(source, destination):
        raise OSError("interrupted before rename")

    monkeypatch.setattr(atomic_write.os, "replace", interrupted)
    with pytest.raises(OSError, match="interrupted before rename"):
        atomic_write_text(target, "next")

    assert target.read_text(encoding="utf-8") == "previous"
    assert _names(tmp_path) == ["flow.json"]


def test_workspace_state_writers_are_atomic(tmp_path, monkeypatch) -> None:
    flow_path = tmp_path / "home" / "flow.json"
    save_flow(flow_path, {"steps": ["prepare"]})
    monkeypatch.setattr(
        atomic_write.os,
        "replace",
        lambda source, destination: (_ for _ in ()).throw(OSError("killed")),
    )

    with pytest.raises(OSError, match="killed"):
        save_flow(flow_path, {"steps": []})
    assert json_write(flow_path, {"steps": []}) is False

    assert json.loads(flow_path.read_text(encoding="utf-8")) == {"steps": ["prepare"]}


def test_json_read_strict_distinguishes_missing_from_damaged(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    truncated = tmp_path / "truncated.json"
    truncated.write_text('{"Design": "chi', encoding="utf-8")
    not_object = tmp_path / "list.json"
    not_object.write_text("[1, 2]", encoding="utf-8")

    assert json_read_strict(missing) == {}
    with pytest.raises(ValueError, match="not valid JSON"):
        json_read_strict(truncated)
    with pytest.raises(ValueError, match="must contain a JSON object"):
        json_read_strict(not_object)


def test_parameter_update_never_replaces_a_damaged_file(tmp_path) -> None:
    parameters_path = tmp_path / "parameters.json"
    damaged = '{"Design": "chip", "Top module": "chi'
    parameters_path.write_text(damaged, encoding="utf-8")
    workspace = {"parameters_path": str(parameters_path)}

    with pytest.raises(ValueError, match="not valid JSON"):
        workspace_service._update_workspace_parameters(
            workspace, {"sim_all_tests": False}
        )

    assert parameters_path.read_text(encoding="utf-8") == damaged
    assert workspace == {"parameters_path": str(parameters_path)}


def test_parameter_update_merges_into_existing_values(tmp_path) -> None:
    parameters_path = tmp_path / "parameters.json"
    atomic_write_json(parameters_path, {"Design": "chip", "sim_all_tests": True})
    workspace = {"parameters_path": str(parameters_path)}

    workspace_service._update_workspace_parameters(workspace, {"sim_all_tests": False})

    assert json.loads(parameters_path.read_text(encoding="utf-8")) == {
        "Design": "chip",
        "sim_all_tests": False,
    }
