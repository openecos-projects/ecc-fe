"""The frontend flow carries no physical-design placeholders."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fecompiler.application.workspace_service import workspace_application
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace
from fecompiler.engine.flow import EngineFlow
from fecompiler.tools.fe import BaseStep
from fecompiler.tools.fe.builder import build_step, build_step_space


def _workspace(tmp_path: Path) -> dict[str, object]:
    workspace = create_workspace(
        CreateWorkspaceData(
            directory=str(tmp_path / "workspace"),
            parameters={"Design": "demo", "Top module": "chip_top"},
        ),
    )
    assert workspace is not None
    return workspace


def test_step_paths_name_only_frontend_artifacts(tmp_path) -> None:
    step = build_step(
        workspace={"design": "demo", "directory": str(tmp_path)},
        step_name="lint",
        tool="verilator",
    )

    assert step.output == {
        "dir": f"{tmp_path}/lint_verilator/output",
        "json": f"{tmp_path}/lint_verilator/output/demo_lint.json",
    }
    assert step.script == {"dir": f"{tmp_path}/lint_verilator/script"}
    for group in ("config", "output", "data", "report", "log", "script", "analysis"):
        for value in step[group].values():
            assert not str(value).endswith((".def", ".def.gz", ".gds", ".tcl", ".png"))
    assert step.get("input") is None
    assert step.get("feature") is None


def test_step_directories_have_no_feature_folder(tmp_path) -> None:
    step = build_step(
        workspace={"design": "demo", "directory": str(tmp_path)},
        step_name="prepare",
        tool="fe",
    )
    build_step_space(step)

    assert sorted(path.name for path in (tmp_path / "prepare_fe").iterdir()) == [
        "analysis",
        "config",
        "data",
        "log",
        "output",
        "report",
        "script",
    ]


def test_new_workspace_has_no_placeholder_def(tmp_path) -> None:
    workspace = _workspace(tmp_path)

    assert "origin_def" not in workspace
    assert list((tmp_path / "workspace" / "origin").glob("*.def*")) == []


def test_loaded_steps_do_not_chain_placeholder_inputs(tmp_path) -> None:
    engine = EngineFlow(workspace=_workspace(tmp_path))
    engine.init_default_steps()
    engine.load()

    assert [step.name for step in engine.workspace_steps] == [
        "prepare",
        "review",
        "elab",
        "lint",
        "sim",
    ]
    assert all(step.get("input") is None for step in engine.workspace_steps)


def test_standard_outputs_write_no_script_or_feature_file(tmp_path) -> None:
    class _Step(BaseStep):
        def run(self, step, workspace) -> None:
            self.write_standard_outputs(step)

        def check_result(self, step) -> bool:
            return True

    step = build_step(
        workspace={"design": "demo", "directory": str(tmp_path)},
        step_name="prepare",
        tool="fe",
    )
    build_step_space(step)
    _Step().run(step, {})

    written = sorted(
        path.relative_to(tmp_path / "prepare_fe").as_posix()
        for path in (tmp_path / "prepare_fe").rglob("*")
        if path.is_file()
    )
    assert written == [
        "analysis/prepare_metrics.json",
        "log/log.txt",
        "output/demo_prepare.json",
        "report/prepare.rpt",
    ]


def test_a_step_handler_must_say_how_its_result_is_checked() -> None:
    class _NoCheck(BaseStep):
        def run(self, step, workspace) -> None:
            return None

    with pytest.raises(TypeError, match="check_result"):
        _NoCheck()


def test_rpc_create_still_tolerates_a_def_path_from_the_desktop(tmp_path) -> None:
    result = workspace_application.execute_payload(
        "create",
        {
            "directory": str(tmp_path / "workspace"),
            "core_id": "picorv32",
            "origin_def": str(tmp_path / "ignored.def"),
            "parameters": {"Design": "demo", "Top module": "top"},
        },
    )

    assert result.response == "success", json.dumps(result.message)
    assert list((tmp_path / "workspace" / "origin").glob("*.def*")) == []
