"""Init failure cleanup, ecc-fe.toml edits, debug output, and step usage."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from fecompiler.cli import commands as cli_commands
from fecompiler.cli import main as cli_main
from fecompiler.cli.project.config import (
    ProjectConfigError,
    _updated_config_text,
    load_project_config,
    write_parameter_override,
)
from fecompiler.engine.resource_usage import StepUsage


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    monkeypatch.delenv("ECC_FE_DEBUG", raising=False)


def _records(capsys) -> list[dict[str, object]]:
    return json.loads(capsys.readouterr().out)["records"]


# --- init failure ---------------------------------------------------------


def test_failed_project_init_says_why_and_leaves_nothing(tmp_path, capsys) -> None:
    project = tmp_path / "nested" / "project"

    exit_code = cli_main.run(
        ["init", str(project), "--cpu-filelist", str(tmp_path / "nosuch.f"), "--json"]
    )

    records = _records(capsys)
    assert exit_code == 1
    assert records[0]["kind"] == "error"
    assert records[0]["error"] == "init_failed"
    assert records[0]["project"] == str(project)
    issues = [record for record in records if record["kind"] == "init_issue"]
    assert issues and issues[0]["code"] == "cpu_filelist_not_found"
    assert issues[0]["field"] == "cpu_filelist"
    assert not (tmp_path / "nested").exists()


def test_failed_init_keeps_what_was_already_in_the_directory(tmp_path, capsys) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "notes.txt").write_text("mine", encoding="utf-8")

    assert (
        cli_main.run(
            ["init", str(project), "--cpu-filelist", str(tmp_path / "nosuch.f"), "--json"]
        )
        == 1
    )

    assert sorted(path.name for path in project.iterdir()) == ["notes.txt"]


def test_failed_init_can_be_repeated_successfully(tmp_path, capsys) -> None:
    project = tmp_path / "project"
    assert cli_main.run(["init", str(project), "--cpu-filelist", "nosuch.f", "--json"]) == 1
    capsys.readouterr()

    assert cli_main.run(["init", str(project), "--json"]) == 0
    assert _records(capsys)[0]["status"] == "created"


def test_failed_workspace_init_leaves_no_directory(tmp_path, capsys) -> None:
    workspace = tmp_path / "ws"

    exit_code = cli_main.run(
        ["init", "--workspace", str(workspace), "--cpu-filelist", "nosuch.f", "--json"]
    )

    assert exit_code == 1
    assert _records(capsys)[0]["error"] == "init_failed"
    assert not workspace.exists()


# --- ecc-fe.toml ----------------------------------------------------------


def test_project_config_does_not_record_machine_specific_filelists(tmp_path, capsys) -> None:
    project = tmp_path / "project"
    assert cli_main.run(["init", str(project), "--json"]) == 0

    text = (project / "ecc-fe.toml").read_text(encoding="utf-8")
    config = tomllib.loads(text)
    assert text.startswith("# ECC-FE project configuration.")
    assert config["frontend"]["frontend_core_id"] == "picorv32"
    assert "cpu_filelist" not in config["frontend"]
    assert "soc_filelist" not in config["frontend"]
    assert "/latest/" not in text


_DOCUMENT = '''schema_version = 1

[design]
name = "p"
doc = """
[params]
"trap" = 1
"""

[params]
# tuned for speed
"sim.compile_opt_level" = "-O2"  # was -O3
"sim.compile_extra_cflags" = [
  "-g",
  "-funroll-loops",
]

[flow]
run = "default"
'''


def test_params_edit_keeps_comments_and_untouched_entries() -> None:
    updated = _updated_config_text(
        _DOCUMENT,
        {
            "sim.compile_opt_level": "-O2",
            "sim.compile_extra_cflags": ["-g", "-funroll-loops"],
            "design.frequency_mhz": 200.0,
        },
        Path("ecc-fe.toml"),
    )

    assert "# tuned for speed" in updated
    assert '"sim.compile_opt_level" = "-O2"  # was -O3' in updated
    assert '  "-funroll-loops",' in updated
    parsed = tomllib.loads(updated)
    assert parsed["params"]["design.frequency_mhz"] == 200.0
    assert parsed["design"] == tomllib.loads(_DOCUMENT)["design"]
    assert parsed["flow"] == {"run": "default"}


def test_params_edit_replaces_and_removes_whole_entries() -> None:
    updated = _updated_config_text(
        _DOCUMENT, {"sim.compile_opt_level": "-O3"}, Path("ecc-fe.toml")
    )

    parsed = tomllib.loads(updated)
    assert parsed["params"] == {"sim.compile_opt_level": "-O3"}
    assert "-funroll-loops" not in updated
    assert "# tuned for speed" in updated
    assert parsed["design"]["doc"] == '[params]\n"trap" = 1\n'


def test_params_table_is_added_when_missing() -> None:
    updated = _updated_config_text(
        "schema_version = 1\n", {"design.frequency_mhz": 50.0}, Path("ecc-fe.toml")
    )

    assert tomllib.loads(updated) == {
        "schema_version": 1,
        "params": {"design.frequency_mhz": 50.0},
    }


def test_params_edit_refuses_a_file_it_cannot_change_safely() -> None:
    # Inline table: no [params] header exists to edit, and appending one
    # would define the table twice.
    document = 'schema_version = 1\nparams = { "a" = 1 }\n'

    with pytest.raises(ProjectConfigError, match="edit the table by hand"):
        _updated_config_text(document, {"a": 2}, Path("ecc-fe.toml"))


def test_param_set_leaves_the_file_alone_when_the_edit_is_refused(tmp_path, capsys) -> None:
    project = tmp_path / "project"
    assert cli_main.run(["init", str(project), "--json"]) == 0
    capsys.readouterr()
    path = project / "ecc-fe.toml"
    text = path.read_text(encoding="utf-8").replace(
        "[params]\n", 'params = { "design.frequency_mhz" = 120.0 }\n'
    )
    # Move the inline table above the first header so the file stays valid.
    text = text.replace('params = { "design.frequency_mhz" = 120.0 }\n', "")
    text = text.replace(
        "schema_version = 1\n",
        'schema_version = 1\nparams = { "design.frequency_mhz" = 120.0 }\n',
    )
    path.write_text(text, encoding="utf-8")
    config = load_project_config(str(project))
    assert config is not None and config.overrides == {"design.frequency_mhz": 120.0}

    with pytest.raises(ProjectConfigError):
        write_parameter_override(config, "design.frequency_mhz", 150.0)

    assert path.read_text(encoding="utf-8") == text


# --- debug ----------------------------------------------------------------


def _explode(directory):
    raise RuntimeError("boom")


def test_internal_error_names_the_exception_and_how_to_get_a_traceback(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli_commands, "load_existing_workspace", _explode)

    assert cli_main.run(["status", "--workspace", str(tmp_path), "--json"]) == 1

    captured = capsys.readouterr()
    record = json.loads(captured.out)["records"][0]
    assert record["error"] == "internal_error"
    assert record["exception"] == "RuntimeError"
    assert "ECC_FE_DEBUG=1" in record["hint"]
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("how", ["flag", "env"])
def test_debug_prints_the_traceback_to_stderr(tmp_path, monkeypatch, capsys, how) -> None:
    monkeypatch.setattr(cli_commands, "load_existing_workspace", _explode)
    argv = ["status", "--workspace", str(tmp_path), "--json"]
    if how == "flag":
        argv = ["--debug", *argv]
    else:
        monkeypatch.setenv("ECC_FE_DEBUG", "1")

    assert cli_main.run(argv) == 1

    captured = capsys.readouterr()
    assert "Traceback (most recent call last)" in captured.err
    assert "RuntimeError: boom" in captured.err
    # stdout is still one clean document.
    assert json.loads(captured.out)["records"][0]["error"] == "internal_error"
    monkeypatch.delenv("ECC_FE_DEBUG", raising=False)


# --- step usage -----------------------------------------------------------


def test_step_usage_reports_elapsed_seconds() -> None:
    usage = StepUsage(interval=0.01)
    seconds, peak = usage.stop()

    assert 0 <= seconds < 5
    assert peak >= 0
    assert usage.stop()[1] == peak


@pytest.mark.skipif(not Path("/proc/self/statm").is_file(), reason="needs /proc")
def test_step_usage_counts_the_memory_of_child_processes() -> None:
    baseline = StepUsage(interval=0.02).stop()[1]
    usage = StepUsage(interval=0.02)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import time; block = bytearray(120 * 1024 * 1024); time.sleep(0.4)",
        ],
        check=True,
    )
    _, peak = usage.stop()

    assert baseline > 0
    assert peak >= baseline + 100


def test_status_reports_exact_runtime_and_peak_memory(tmp_path, capsys) -> None:
    workspace = tmp_path / "ws"
    assert cli_main.run(["init", "--workspace", str(workspace), "--json"]) == 0
    capsys.readouterr()
    flow_path = workspace / "home" / "flow.json"
    flow = json.loads(flow_path.read_text(encoding="utf-8"))
    flow["steps"][0].update(
        {"state": "Success", "runtime": "00:00:00", "runtime_s": 0.412, "peak memory (mb)": 64.5}
    )
    flow_path.write_text(json.dumps(flow), encoding="utf-8")

    assert cli_main.run(["status", "--workspace", str(workspace), "--json"]) == 0

    step = _records(capsys)[1]
    assert step["runtime_s"] == 0.412
    assert step["peak_memory_mb"] == 64.5
