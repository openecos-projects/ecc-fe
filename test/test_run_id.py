from __future__ import annotations

import json
from pathlib import Path

import pytest

from fecompiler.cli import main as cli_main
from fecompiler.cli.project.config import ProjectConfigError, load_project_config
from fecompiler.cli.project.layout import is_valid_run_id, resolve_run_dir

_PATH_LIKE = [
    "../escape",
    "..",
    ".",
    "nested/run",
    "/tmp/absolute",
    "~/home",
    ".hidden",
    "-option",
    "with space",
    " padded",
    "trailing\n",
    "nul\x00byte",
    "x" * 65,
]


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


@pytest.mark.parametrize("run_id", ["default", "exp-1", "v2.1_fast", "0", "x" * 64])
def test_plain_names_resolve_directly_below_runs(tmp_path, run_id) -> None:
    assert is_valid_run_id(run_id)
    assert resolve_run_dir(str(tmp_path), run_id) == (
        str(tmp_path / "runs" / run_id),
        run_id,
    )


@pytest.mark.parametrize("run_id", [None, ""])
def test_missing_run_id_selects_the_default_run(tmp_path, run_id) -> None:
    assert resolve_run_dir(str(tmp_path), run_id) == (
        str(tmp_path / "runs" / "default"),
        run_id,
    )


@pytest.mark.parametrize("run_id", _PATH_LIKE)
def test_anything_but_a_plain_name_is_rejected(tmp_path, run_id) -> None:
    assert not is_valid_run_id(run_id)
    with pytest.raises(ValueError, match="invalid run id"):
        resolve_run_dir(str(tmp_path), run_id)


@pytest.mark.parametrize("command", ["run", "status", "log", "config"])
@pytest.mark.parametrize("run_id", ["../escape", "nested/run", ".hidden"])
def test_commands_refuse_a_path_like_run_id_before_touching_disk(
    project, tmp_path, capsys, command, run_id
) -> None:
    before = sorted(str(path) for path in tmp_path.rglob("*"))

    exit_code, payload = _json_call(
        capsys, command, "--project", str(project), "--run-id", run_id
    )

    assert exit_code == 2
    assert payload["records"] == [
        {
            "kind": "error",
            "error": "invalid_run_id",
            "run": run_id,
            "reason": payload["records"][0]["reason"],
        }
    ]
    assert "plain name" in payload["records"][0]["reason"]
    assert sorted(str(path) for path in tmp_path.rglob("*")) == before


def test_absolute_run_id_cannot_create_a_run_outside_the_project(
    project, tmp_path, capsys
) -> None:
    outside = tmp_path / "outside"

    exit_code, payload = _json_call(
        capsys, "run", "--project", str(project), "--run-id", str(outside)
    )

    assert exit_code == 2
    assert payload["records"][0]["error"] == "invalid_run_id"
    assert not outside.exists()


def test_configured_flow_run_must_be_a_plain_name(project) -> None:
    config_path = project / "ecc-fe.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'run = "default"', 'run = "../escape"'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProjectConfigError, match="Unsupported flow.run"):
        load_project_config(str(project))
