"""`status` names the steps the next run will re-execute."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fecompiler.cli import main as cli_main
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow, stale_steps
from fecompiler.engine.provenance import build_step_provenance


@pytest.fixture
def workspace_dir(monkeypatch, tmp_path) -> Path:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    source = tmp_path / "chip_top.v"
    source.write_text("module chip_top; endmodule\n", encoding="utf-8")
    directory = tmp_path / "workspace"
    create_workspace(
        CreateWorkspaceData(
            directory=str(directory),
            parameters={"Design": "demo", "Top module": "chip_top"},
            origin_verilog=str(source),
        ),
    )
    return directory


def _flow_path(directory: Path) -> Path:
    return directory / "home" / "flow.json"


def _mark_succeeded(directory: Path, count: int) -> None:
    """Record the first *count* steps as succeeded with their current provenance."""
    workspace = load_workspace(str(directory))
    flow = json.loads(_flow_path(directory).read_text(encoding="utf-8"))
    upstream = None
    for step in flow["steps"][:count]:
        provenance = build_step_provenance(workspace, step["name"], step["tool"], upstream)
        provenance["output_fingerprint"] = "recorded"
        step["state"] = "Success"
        step["provenance"] = provenance
        upstream = provenance
    _flow_path(directory).write_text(json.dumps(flow), encoding="utf-8")


def _status(capsys, directory: Path) -> tuple[int, list[dict[str, object]]]:
    exit_code = cli_main.run(["status", "--workspace", str(directory), "--json"])
    return exit_code, json.loads(capsys.readouterr().out)["records"]


def _edit_rtl(directory: Path) -> None:
    rtl = directory / "origin" / "chip_top.v"
    rtl.write_text(rtl.read_text(encoding="utf-8") + "// edited\n", encoding="utf-8")


def test_up_to_date_results_are_not_stale(workspace_dir, capsys) -> None:
    _mark_succeeded(workspace_dir, 4)

    exit_code, records = _status(capsys, workspace_dir)

    assert exit_code == 0
    assert "stale_steps" not in records[0]
    assert "resume_cmd" not in records[0]
    assert all("stale" not in record for record in records[1:])


def test_changed_rtl_marks_every_finished_step_stale_without_touching_the_run(
    workspace_dir, capsys
) -> None:
    _mark_succeeded(workspace_dir, 4)
    _edit_rtl(workspace_dir)
    stored = _flow_path(workspace_dir).read_text(encoding="utf-8")

    exit_code, records = _status(capsys, workspace_dir)

    assert exit_code == 0
    assert records[0]["stale_steps"] == ["prepare", "review", "elab", "lint"]
    assert records[0]["resume_cmd"] == f"ecc-fe run --resume --workspace {workspace_dir}"
    steps = {record["step"]: record for record in records[1:]}
    assert steps["prepare"]["state"] == "success"
    assert steps["prepare"]["stale"] is True
    assert steps["prepare"]["stale_reason"] == "step inputs changed"
    assert steps["lint"]["stale_reason"] == "upstream step prepare is stale"
    assert "stale" not in steps["sim"]
    assert _flow_path(workspace_dir).read_text(encoding="utf-8") == stored


def test_changed_parameter_marks_the_first_affected_step(workspace_dir, capsys) -> None:
    _mark_succeeded(workspace_dir, 4)
    parameters_path = workspace_dir / "home" / "parameters.json"
    parameters = json.loads(parameters_path.read_text(encoding="utf-8"))
    parameters["lint_profile"] = "strict"
    parameters_path.write_text(json.dumps(parameters), encoding="utf-8")

    _, records = _status(capsys, workspace_dir)

    assert records[0]["stale_steps"] == ["lint"]
    lint = next(record for record in records if record.get("step") == "lint")
    assert lint["stale_reason"] == "step configuration changed"


def test_steps_already_reset_as_stale_keep_their_reason(workspace_dir, capsys) -> None:
    _mark_succeeded(workspace_dir, 4)
    _edit_rtl(workspace_dir)
    engine = EngineFlow(workspace=load_workspace(str(workspace_dir)))
    assert engine.refresh_stale_states() is True

    _, records = _status(capsys, workspace_dir)

    steps = {record["step"]: record for record in records[1:]}
    assert steps["prepare"]["state"] == "unstart"
    assert steps["prepare"]["stale"] is True
    assert steps["prepare"]["stale_reason"] == "step inputs changed"
    assert records[0]["stale_steps"] == ["prepare", "review", "elab", "lint"]


def test_engine_and_status_agree_on_what_is_stale(workspace_dir) -> None:
    _mark_succeeded(workspace_dir, 3)
    _edit_rtl(workspace_dir)
    workspace = load_workspace(str(workspace_dir))
    flow = json.loads(_flow_path(workspace_dir).read_text(encoding="utf-8"))

    previewed = [
        (step["name"], reason, origin)
        for step, reason, origin in stale_steps(workspace, flow["steps"])
    ]

    assert previewed == [
        ("prepare", "step inputs changed", "prepare"),
        ("review", "upstream step prepare is stale", "prepare"),
        ("elab", "upstream step prepare is stale", "prepare"),
        ("lint", "upstream step prepare is stale", "prepare"),
        ("sim", "upstream step prepare is stale", "prepare"),
    ]
    engine = EngineFlow(workspace=workspace)
    engine.refresh_stale_states()
    assert [step["state"] for step in engine.flow["steps"]] == ["Unstart"] * 5
