from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fecompiler.application.workspace_service import workspace_application
from fecompiler.cli import commands as cli_commands
from fecompiler.cli import main as cli_main
from fecompiler.data.step import StateEnum
from fecompiler.engine.flow import EngineFlow


def _json_call(capsys, *args: str) -> tuple[int, dict[str, object]]:
    exit_code = cli_main.run([*args, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)


@pytest.fixture
def project(monkeypatch, tmp_path, capsys) -> Path:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    directory = tmp_path / "project"
    exit_code, _ = _json_call(capsys, "init", str(directory))
    assert exit_code == 0
    return directory


@pytest.fixture
def calls(monkeypatch) -> list[tuple[str, dict[str, object]]]:
    recorded: list[tuple[str, dict[str, object]]] = []

    def fake_execute(command, payload, *, base_dir):
        recorded.append((command, payload))
        return SimpleNamespace(response="success", data={}, message=[])

    monkeypatch.setattr(
        cli_commands.workspace_application, "execute_payload", fake_execute
    )
    return recorded


def _run(capsys, project: Path, *args: str) -> tuple[int, dict[str, object]]:
    exit_code, payload = _json_call(capsys, "run", "--project", str(project), *args)
    return exit_code, payload["records"][0]


def test_new_run_can_stop_after_a_step(project, calls, capsys) -> None:
    exit_code, record = _run(capsys, project, "--to", "lint")

    run_dir = project / "runs" / "default"
    assert exit_code == 0
    assert record["to_step"] == "lint"
    assert calls == [
        ("run-flow", {"directory": str(run_dir), "rerun": False, "to_step": "lint"})
    ]
    assert (run_dir / "home" / "flow.json").is_file()


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--resume"], ("run-flow", {"rerun": False})),
        (["--resume", "--to", "elab"], ("run-flow", {"rerun": False, "to_step": "elab"})),
        (["--from", "review"], ("run-flow", {"rerun": False, "from_step": "review"})),
        (
            ["--from", "review", "--to", "lint"],
            ("run-flow", {"rerun": False, "from_step": "review", "to_step": "lint"}),
        ),
        (["--only", "lint"], ("run-step", {"rerun": False, "step": "lint"})),
        (["--only", "lint", "--force"], ("run-step", {"rerun": True, "step": "lint"})),
        (["--step", "sim", "--rerun"], ("run-step", {"rerun": True, "step": "sim"})),
        (["--rerun"], ("run-flow", {"rerun": True})),
    ],
)
def test_selectors_continue_an_existing_project_run_in_place(
    project, calls, capsys, arguments, expected
) -> None:
    assert _run(capsys, project)[0] == 0
    run_dir = project / "runs" / "default"
    marker = run_dir / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")
    calls.clear()

    exit_code, record = _run(capsys, project, *arguments)

    command, payload = expected
    assert exit_code == 0
    assert calls == [(command, {"directory": str(run_dir), **payload})]
    assert record["run"] == "default"
    assert record["workspace"] == str(run_dir)
    assert marker.is_file()


def test_selectors_address_the_named_run(project, calls, capsys) -> None:
    assert _run(capsys, project, "--run-id", "exp")[0] == 0
    calls.clear()

    exit_code, record = _run(capsys, project, "--run-id", "exp", "--only", "lint")

    assert exit_code == 0
    assert calls == [
        (
            "run-step",
            {"directory": str(project / "runs" / "exp"), "rerun": False, "step": "lint"},
        )
    ]
    assert record["log_cmd"] == f"ecc-fe log lint --project {project} --run-id exp"


def test_continuing_a_run_keeps_its_parameters(project, calls, capsys) -> None:
    assert _run(capsys, project)[0] == 0
    parameters_path = project / "runs" / "default" / "home" / "parameters.json"
    before = parameters_path.read_text(encoding="utf-8")
    exit_code, _ = _json_call(
        capsys, "param", "set", "design.frequency_mhz", "175", "--project", str(project)
    )
    assert exit_code == 0

    assert _run(capsys, project, "--resume")[0] == 0

    assert parameters_path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("arguments", [["--resume"], ["--from", "lint"], ["--only", "lint"]])
def test_selectors_need_an_existing_run(project, calls, capsys, arguments) -> None:
    exit_code, record = _run(capsys, project, *arguments)

    assert exit_code == 1
    assert record["error"] == "run_not_found"
    assert record["create_cmd"] == f"ecc-fe run --project {project}"
    assert calls == []
    assert not (project / "runs" / "default").exists()


def test_existing_run_without_a_selector_points_to_resume(project, calls, capsys) -> None:
    assert _run(capsys, project)[0] == 0

    exit_code, record = _run(capsys, project, "--to", "lint")

    assert exit_code == 1
    assert record["error"] == "run_exists"
    assert record["resume"] == f"ecc-fe run --resume --project {project}"


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        (["--resume", "--overwrite"], "overwrite_selector_conflict"),
        (["--only", "lint", "--set", "design.frequency_mhz=175"], "set_requires_new_run"),
        (["--only", "lint", "--to", "sim"], "selector_conflict"),
        (["--resume", "--from", "lint"], "selector_conflict"),
        (["--force"], "force_requires_only"),
        (["--to", "place"], "unknown_step"),
        (["--from", "lint", "--to", "review"], "invalid_step_range"),
    ],
)
def test_invalid_selector_combinations_are_usage_errors(
    project, calls, capsys, arguments, error
) -> None:
    assert _run(capsys, project)[0] == 0
    calls.clear()

    exit_code, record = _run(capsys, project, *arguments)

    assert exit_code == 2
    assert record["error"] == error
    assert calls == []


def _fake_engine_runs(monkeypatch) -> list[tuple[str, bool]]:
    executed: list[tuple[str, bool]] = []

    def fake_run_step(self, step_name, rerun=False, *, observer=None):
        executed.append((step_name, rerun))
        return StateEnum.Success

    monkeypatch.setattr(EngineFlow, "run_step", fake_run_step)
    return executed


def test_flow_stops_after_the_requested_step(project, monkeypatch, capsys) -> None:
    executed = _fake_engine_runs(monkeypatch)

    exit_code, record = _run(capsys, project, "--to", "elab")

    assert exit_code == 0
    # The fake engine produces no prepared inputs, so prepare is refreshed once more.
    assert executed[0][0] == "prepare"
    assert [name for name, _ in executed if name != "prepare"] == ["review", "elab"]
    assert record["data"]["to_step"] == "elab"
    assert [report["step"] for report in record["data"]["reports"]] == [
        "prepare",
        "review",
        "elab",
    ]


def test_flow_runs_the_requested_range(project, monkeypatch, capsys) -> None:
    executed = _fake_engine_runs(monkeypatch)
    assert _run(capsys, project)[0] == 0
    executed.clear()

    exit_code, _ = _run(capsys, project, "--from", "review", "--to", "lint")

    assert exit_code == 0
    assert [(name, rerun) for name, rerun in executed if name != "prepare"] == [
        ("review", True),
        ("elab", False),
        ("lint", False),
    ]


def test_service_rejects_an_unknown_or_reversed_range(project, monkeypatch, capsys) -> None:
    _fake_engine_runs(monkeypatch)
    assert _run(capsys, project)[0] == 0
    run_dir = str(project / "runs" / "default")

    unknown = workspace_application.execute_payload(
        "run-flow", {"directory": run_dir, "to_step": "place"}
    )
    reversed_range = workspace_application.execute_payload(
        "run-flow", {"directory": run_dir, "from_step": "lint", "to_step": "review"}
    )

    assert unknown.response == "failed"
    assert unknown.data["step"] == "place"
    assert reversed_range.response == "failed"
    assert reversed_range.data["from_step"] == "lint"
    assert reversed_range.data["to_step"] == "review"
