from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fecompiler.cli import commands as cli_commands
from fecompiler.cli import main as cli_main
from fecompiler.cli.project.layout import publish_workspace_replacement


def _json_call(capsys, *args: str) -> tuple[int, dict[str, object]]:
    exit_code = cli_main.run([*args, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)


@pytest.fixture
def project_run(monkeypatch, tmp_path, capsys) -> Path:
    """A project whose ``experiment`` run exists and holds a result file."""
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    project = tmp_path / "project"
    exit_code, _ = _json_call(capsys, "init", str(project))
    assert exit_code == 0
    monkeypatch.setattr(
        cli_commands.workspace_application,
        "execute_payload",
        lambda command, payload, *, base_dir: SimpleNamespace(
            response="success", data={}, message=[]
        ),
    )
    exit_code, _ = _json_call(
        capsys, "run", "--project", str(project), "--run-id", "experiment"
    )
    assert exit_code == 0
    run_dir = project / "runs" / "experiment"
    (run_dir / "result.txt").write_text("previous result\n", encoding="utf-8")
    return run_dir


def _overwrite(capsys, run_dir: Path, *extra: str) -> tuple[int, dict[str, object]]:
    return _json_call(
        capsys,
        "run",
        "--project",
        str(run_dir.parents[1]),
        "--run-id",
        run_dir.name,
        "--overwrite",
        *extra,
    )


def _run_entries(run_dir: Path) -> list[str]:
    return sorted(
        path.name for path in run_dir.parent.iterdir() if path.suffix != ".lock"
    )


def test_overwrite_replaces_the_run_and_records_its_own_location(
    project_run, capsys
) -> None:
    exit_code, payload = _overwrite(
        capsys, project_run, "--set", "design.frequency_mhz=175"
    )

    assert exit_code == 0
    assert payload["records"][0]["workspace"] == str(project_run)
    assert not (project_run / "result.txt").exists()
    assert _run_entries(project_run) == ["experiment"]
    home = json.loads((project_run / "home" / "home.json").read_text(encoding="utf-8"))
    assert home["parameters"] == str(project_run / "home" / "parameters.json")
    parameters = json.loads(
        (project_run / "home" / "parameters.json").read_text(encoding="utf-8")
    )
    assert parameters["Frequency max [MHz]"] == 175.0
    assert json.loads(
        (project_run / "home" / "cli-param-overrides.json").read_text(encoding="utf-8")
    ) == {"design.frequency_mhz": 175.0}


def test_failed_overwrite_keeps_the_previous_run(
    project_run, capsys, monkeypatch
) -> None:
    def reject_overrides(directory, **kwargs):
        raise ValueError("override cannot be applied")

    monkeypatch.setattr(cli_commands, "apply_project_overrides", reject_overrides)

    exit_code, payload = _overwrite(capsys, project_run)

    assert exit_code == 1
    assert payload["records"][0]["error"] == "workspace_failed"
    assert payload["records"][0]["reason"] == "override cannot be applied"
    assert (project_run / "result.txt").read_text(encoding="utf-8") == (
        "previous result\n"
    )
    assert _run_entries(project_run) == ["experiment"]


def test_overwrite_with_a_broken_template_keeps_the_previous_run(
    project_run, capsys
) -> None:
    template_home = project_run.parents[1] / ".ecc-fe" / "template" / "home"
    (template_home / "flow.json").unlink()

    exit_code, payload = _overwrite(capsys, project_run)

    assert exit_code == 1
    assert payload["records"][0]["error"] == "workspace_failed"
    assert (project_run / "result.txt").is_file()
    assert _run_entries(project_run) == ["experiment"]


def test_overwrite_removes_directories_left_by_an_interrupted_overwrite(
    project_run, capsys
) -> None:
    runs = project_run.parent
    leftovers = [
        runs / ".experiment.staging-0123abcd",
        runs / ".experiment.retired-89abcdef",
    ]
    for leftover in leftovers:
        leftover.mkdir()
    unrelated = runs / ".experiment.staging-notes"
    unrelated.mkdir()

    exit_code, _ = _overwrite(capsys, project_run)

    assert exit_code == 0
    assert _run_entries(project_run) == [".experiment.staging-notes", "experiment"]


def test_publish_restores_the_previous_run_when_the_swap_fails(tmp_path) -> None:
    run_dir = tmp_path / "experiment"
    run_dir.mkdir()
    (run_dir / "result.txt").write_text("previous result\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        publish_workspace_replacement(str(tmp_path / "missing-staging"), str(run_dir))

    assert sorted(path.name for path in tmp_path.iterdir()) == ["experiment"]
    assert (run_dir / "result.txt").read_text(encoding="utf-8") == "previous result\n"
