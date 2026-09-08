from __future__ import annotations

import json
import shlex
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from fecompiler.application import workspace_service
from fecompiler.application.workspace_service import workspace_application
from fecompiler.cli import commands as cli_commands
from fecompiler.cli import main as cli_main
from fecompiler.data.step import StateEnum
from fecompiler.engine.flow import EngineFlow


def _isolate_xdg(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)


def _json_call(capsys, *args: str) -> tuple[int, dict[str, object]]:
    exit_code = cli_main.run([*args, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)


def _init_project(monkeypatch, tmp_path: Path, capsys) -> Path:
    _isolate_xdg(monkeypatch, tmp_path)
    project = tmp_path / "project"
    exit_code, payload = _json_call(capsys, "init", str(project))
    assert exit_code == 0
    assert payload["records"][0]["status"] == "created"
    return project


def _init_workspace(monkeypatch, tmp_path: Path, capsys) -> Path:
    _isolate_xdg(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    exit_code, payload = _json_call(
        capsys, "init", "--workspace", str(workspace)
    )
    assert exit_code == 0
    assert payload["records"][0]["status"] == "success"
    return workspace


def test_named_init_creates_project_layout_and_default_run_target(
    monkeypatch, tmp_path, capsys
) -> None:
    project = _init_project(monkeypatch, tmp_path, capsys)

    with (project / "ecc-fe.toml").open("rb") as stream:
        config = tomllib.load(stream)
    assert config["flow"] == {"run": "default"}
    assert (project / "runs").is_dir()
    assert (project / ".ecc-fe" / "template" / "home" / "flow.json").is_file()

    exit_code, payload = _json_call(capsys, "status", "--project", str(project))
    assert exit_code == 1
    record = payload["records"][0]
    assert record["run"] == "default"
    assert record["workspace"] == str(project / "runs" / "default")
    assert record["remediation_cmd"] == f"ecc-fe run --project {project}"

    config_path = project / "ecc-fe.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'run = "default"', 'run = "configured"'
        ),
        encoding="utf-8",
    )
    exit_code, configured = _json_call(
        capsys, "status", "--project", str(project)
    )
    assert exit_code == 1
    assert configured["records"][0]["run"] == "configured"
    assert configured["records"][0]["workspace"] == str(
        project / "runs" / "configured"
    )

    exit_code, explicit_default = _json_call(
        capsys, "status", "--project", str(project), "--run-id", ""
    )
    assert exit_code == 1
    assert explicit_default["records"][0]["workspace"] == str(
        project / "runs" / "default"
    )
    assert explicit_default["records"][0]["remediation_cmd"].endswith(
        "--run-id ''"
    )


def test_project_run_isolated_overrides_run_selection_and_overwrite(
    monkeypatch, tmp_path, capsys
) -> None:
    project = _init_project(monkeypatch, tmp_path, capsys)
    exit_code, _ = _json_call(
        capsys,
        "param",
        "set",
        "design.frequency_mhz",
        "150",
        "--project",
        str(project),
    )
    assert exit_code == 0

    calls: list[tuple[str, dict[str, object]]] = []

    def fake_execute(command, payload, *, base_dir):
        calls.append((command, payload))
        return SimpleNamespace(response="success", data={}, message=[])

    monkeypatch.setattr(
        cli_commands.workspace_application, "execute_payload", fake_execute
    )
    exit_code, payload = _json_call(
        capsys,
        "run",
        "--project",
        str(project),
        "--run-id",
        "experiment",
        "--set",
        "design.frequency_mhz=175",
    )
    assert exit_code == 0
    record = payload["records"][0]
    run_dir = project / "runs" / "experiment"
    assert record["run"] == "experiment"
    assert record["parameter_overrides"] == {"design.frequency_mhz": 175.0}
    assert calls == [("run-flow", {"directory": str(run_dir), "rerun": False})]
    parameters = json.loads(
        (run_dir / "home" / "parameters.json").read_text(encoding="utf-8")
    )
    assert parameters["Frequency max [MHz]"] == 175.0
    assert json.loads(
        (run_dir / "home" / "cli-param-overrides.json").read_text(
            encoding="utf-8"
        )
    ) == {"design.frequency_mhz": 175.0}
    with (project / "ecc-fe.toml").open("rb") as stream:
        config = tomllib.load(stream)
    assert config["params"] == {"design.frequency_mhz": 150.0}

    exit_code, resolved = _json_call(
        capsys,
        "config",
        "--resolved",
        "--project",
        str(project),
        "--run-id",
        "experiment",
    )
    assert exit_code == 0
    config_record = resolved["records"][0]
    assert config_record["run_overrides"] == {"design.frequency_mhz": 175.0}
    frequency = next(
        record
        for record in resolved["records"]
        if record.get("param") == "design.frequency_mhz"
    )
    assert frequency["value"] == 175.0
    assert frequency["source"] == "cli"

    exit_code, status = _json_call(
        capsys,
        "status",
        "--project",
        str(project),
        "--run-id",
        "experiment",
    )
    assert exit_code == 0
    assert status["records"][0]["run"] == "experiment"

    exit_code, existing = _json_call(
        capsys,
        "run",
        "--project",
        str(project),
        "--run-id",
        "experiment",
    )
    assert exit_code == 1
    assert existing["records"][0]["error"] == "run_exists"

    calls.clear()
    exit_code, _ = _json_call(
        capsys,
        "run",
        "--project",
        str(project),
        "--run-id",
        "experiment",
        "--overwrite",
    )
    assert exit_code == 0
    assert calls == [("run-flow", {"directory": str(run_dir), "rerun": False})]


def test_project_overwrite_refuses_non_workspace_directory(
    monkeypatch, tmp_path, capsys
) -> None:
    project = _init_project(monkeypatch, tmp_path, capsys)
    target = project / "runs" / "notes"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")

    exit_code, payload = _json_call(
        capsys,
        "run",
        "--project",
        str(project),
        "--run-id",
        "notes",
        "--overwrite",
    )

    assert exit_code == 1
    assert payload["records"][0]["error"] == "overwrite_refused"
    assert marker.read_text(encoding="utf-8") == "keep\n"


def test_project_overwrite_refuses_symlinked_workspace(
    monkeypatch, tmp_path, capsys
) -> None:
    project = _init_project(monkeypatch, tmp_path, capsys)
    external = _init_workspace(monkeypatch, tmp_path / "external-root", capsys)
    target = project / "runs" / "redirected"
    target.symlink_to(external, target_is_directory=True)

    exit_code, payload = _json_call(
        capsys,
        "run",
        "--project",
        str(project),
        "--run-id",
        "redirected",
        "--overwrite",
    )

    assert exit_code == 1
    assert payload["records"][0]["error"] == "overwrite_refused"
    assert target.is_symlink()
    assert (external / "home" / "flow.json").is_file()


def test_project_overwrite_rejects_ancestor_and_template_paths(
    monkeypatch, tmp_path, capsys
) -> None:
    parent_workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    project = _init_project(monkeypatch, parent_workspace, capsys)

    for run_id in ("../", ".ecc-fe/", ".ecc-fe/template/home/child"):
        exit_code, payload = _json_call(
            capsys,
            "run",
            "--project",
            str(project),
            "--run-id",
            run_id,
            "--overwrite",
        )

        assert exit_code == 1
        assert payload["records"][0]["error"] == "invalid_run_id"
        assert (parent_workspace / "home" / "flow.json").is_file()
        assert (project / "ecc-fe.toml").is_file()

    assert not (project / ".ecc-fe" / "template" / "home" / "child").exists()


def test_project_run_does_not_delete_concurrent_winner(
    monkeypatch, tmp_path, capsys
) -> None:
    project = _init_project(monkeypatch, tmp_path, capsys)
    target = project / "runs" / "race"

    def lose_creation_race(source, destination):
        Path(destination).mkdir(parents=True)
        (Path(destination) / "winner.txt").write_text("winner\n", encoding="utf-8")
        raise FileExistsError(destination)

    monkeypatch.setattr(
        cli_commands, "clone_workspace_template", lose_creation_race
    )
    exit_code, payload = _json_call(
        capsys,
        "run",
        "--project",
        str(project),
        "--run-id",
        "race",
    )

    assert exit_code == 1
    assert payload["records"][0]["error"] == "run_exists"
    assert (target / "winner.txt").read_text(encoding="utf-8") == "winner\n"


def test_workspace_run_maps_ecc_selectors_and_legacy_aliases(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_execute(command, payload, *, base_dir):
        calls.append((command, payload))
        return SimpleNamespace(response="success", data={}, message=[])

    monkeypatch.setattr(
        cli_commands.workspace_application, "execute_payload", fake_execute
    )

    exit_code, _ = _json_call(
        capsys, "run", "--workspace", str(workspace), "--from", "review"
    )
    assert exit_code == 0
    assert calls[-1] == (
        "run-flow",
        {"directory": str(workspace), "rerun": False, "from_step": "review"},
    )

    exit_code, _ = _json_call(
        capsys,
        "run",
        "--workspace",
        str(workspace),
        "--only",
        "lint",
        "--force",
    )
    assert exit_code == 0
    assert calls[-1] == (
        "run-step",
        {"directory": str(workspace), "rerun": True, "step": "lint"},
    )

    exit_code, _ = _json_call(
        capsys,
        "run",
        "--workspace",
        str(workspace),
        "--step",
        "sim",
        "--rerun",
    )
    assert exit_code == 0
    assert calls[-1] == (
        "run-step",
        {"directory": str(workspace), "rerun": True, "step": "sim"},
    )

    exit_code, conflict = _json_call(
        capsys,
        "run",
        "--workspace",
        str(workspace),
        "--resume",
        "--only",
        "lint",
    )
    assert exit_code == 2
    assert conflict["records"][0]["error"] == "selector_conflict"


@pytest.mark.parametrize(
    "command",
    (
        ("run",),
        ("check",),
        ("doctor",),
        ("status",),
        ("log",),
        ("config",),
        ("report", "qor"),
        ("report", "files"),
    ),
)
def test_workspace_rejects_run_selection(
    command, monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)
    exit_code, payload = _json_call(
        capsys,
        *command,
        "--workspace",
        str(tmp_path / "workspace"),
        "--run-id",
        "ignored",
    )

    assert exit_code == 2
    assert payload["records"] == [
        {"kind": "error", "error": "project_workspace_conflict"}
    ]


def test_project_workspace_conflict_is_a_usage_error(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)
    exit_code, payload = _json_call(
        capsys,
        "status",
        "--workspace",
        str(tmp_path / "workspace"),
        "--project",
        str(tmp_path / "project"),
    )

    assert exit_code == 2
    assert payload["records"][0]["error"] == "project_workspace_conflict"


def test_check_alias_and_shared_command_contract(monkeypatch, tmp_path, capsys) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    monkeypatch.setattr(cli_commands, "_find_executable", lambda candidates: "/bin/true")
    monkeypatch.setattr(cli_commands, "_tool_version", lambda executable: "test")

    exit_code, payload = _json_call(
        capsys, "check", "--workspace", str(workspace)
    )
    assert exit_code == 0
    assert payload["records"][-1]["kind"] == "check_summary"
    assert any(
        record.get("kind") == "check" and record.get("check") == "workspace"
        for record in payload["records"]
    )

    assert cli_main.run(["run", "--help"]) == 0
    help_text = capsys.readouterr().out
    for option in (
        "--project",
        "--run-id",
        "--overwrite",
        "--resume",
        "--from",
        "--only",
        "--force",
        "--set",
    ):
        assert option in help_text

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    exit_code, missing = _json_call(capsys, "check")
    assert exit_code == 1
    assert missing["records"][-2]["error"] == "project_not_found"
    assert missing["records"][-1]["kind"] == "check_summary"


def test_check_preserves_unknown_step_error(monkeypatch, tmp_path, capsys) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)

    exit_code, payload = _json_call(
        capsys, "check", "--workspace", str(workspace), "--step", "unknown"
    )

    assert exit_code == 2
    assert len(payload["records"]) == 1
    assert payload["records"][0]["error"] == "Unknown flow step: unknown"


def test_missing_project_remediation_uses_positional_init(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)
    project = tmp_path / "missing project"
    expected = f"ecc-fe init {shlex.quote(str(project))}"

    _, doctor_payload = _json_call(
        capsys, "doctor", "--project", str(project)
    )
    workspace_check = next(
        record
        for record in doctor_payload["records"]
        if record.get("check") == "workspace"
    )
    assert workspace_check["remediation_cmd"] == expected

    _, param_payload = _json_call(
        capsys, "param", "list", "--project", str(project)
    )
    assert param_payload["records"][0]["remediation_cmd"] == expected


def test_project_param_show_ignores_selected_run_local_override(
    monkeypatch, tmp_path, capsys
) -> None:
    project = _init_project(monkeypatch, tmp_path, capsys)
    monkeypatch.setattr(
        cli_commands.workspace_application,
        "execute_payload",
        lambda command, payload, *, base_dir: SimpleNamespace(
            response="success", data={}, message=[]
        ),
    )

    exit_code, _ = _json_call(
        capsys,
        "run",
        "--project",
        str(project),
        "--run-id",
        "experiment",
        "--set",
        "design.frequency_mhz=175",
    )
    assert exit_code == 0
    config_path = project / "ecc-fe.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'run = "default"', 'run = "experiment"'
        ),
        encoding="utf-8",
    )

    exit_code, payload = _json_call(
        capsys,
        "param",
        "show",
        "design.frequency_mhz",
        "--project",
        str(project),
    )

    assert exit_code == 0
    assert payload["records"][0]["value"] == 100.0
    assert payload["records"][0]["source"] == "default"


def test_application_run_from_executes_selected_suffix(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        workspace_service,
        "_refresh_prepare_if_stale",
        lambda *args, **kwargs: True,
    )

    def fake_run_step(self, step_name, rerun=False, *, observer=None):
        calls.append((step_name, rerun))
        return StateEnum.Success

    monkeypatch.setattr(EngineFlow, "run_step", fake_run_step)
    result = workspace_application.execute_payload(
        "run-flow",
        {"directory": str(workspace), "from_step": "lint"},
        base_dir=tmp_path,
    )

    assert result.response == "success"
    assert result.data["from_step"] == "lint"
    assert calls == [("lint", True), ("sim", False)]
