"""Lint settings: profile, rules, waivers, baseline gate, tool arguments."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fecompiler.cli import commands as cli_commands
from fecompiler.cli import main as cli_main
from fecompiler.cli.project.params import lookup_parameter, parse_cli_value, validate_value
from fecompiler.engine.provenance import build_step_provenance
from fecompiler.tools.common.filelist import parse_filelist
from fecompiler.tools.common.rtl_inputs import slang_library_args, verilator_library_args
from fecompiler.tools.common.tool_config import (
    config_base_dir,
    config_list,
    config_paths,
    design_parameter_overrides,
)
from fecompiler.tools.verilator.lint import (
    apply_lint_baseline,
    build_lint_summary,
    lint_baseline_entries,
    lint_options,
    parse_verilator_diagnostics,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)


def _workspace(tmp_path: Path, **parameters) -> dict:
    directory = tmp_path / "ws"
    (directory / "home").mkdir(parents=True, exist_ok=True)
    path = directory / "home" / "parameters.json"
    path.write_text(json.dumps(parameters), encoding="utf-8")
    return {"directory": str(directory), "parameters_path": str(path)}


def _warning(code: str, source: str, message: str, *, line: int = 1, actionable: bool = True) -> dict:
    return {
        "severity": "warning", "code": code, "source": source, "line": line,
        "message": message, "actionable": actionable,
    }


# --- settings ---------------------------------------------------------------


def test_default_lint_options_match_the_previous_command(tmp_path) -> None:
    assert lint_options(_workspace(tmp_path)) == {
        "warning_args": ["-Wall"],
        "parameter_args": [],
        "extra_args": [],
        "waiver_files": [],
    }


def test_lint_options_from_settings(tmp_path) -> None:
    workspace = _workspace(
        tmp_path,
        lint_profile="basic",
        lint_rules_off=["UNUSEDSIGNAL", "WIDTHTRUNC"],
        lint_extra_args=["--timescale", "1ns/1ps"],
        design_parameters=["WIDTH=16", 'MODE="fast"'],
        lint_waivers=["waive.vlt"],
    )
    waiver = tmp_path / "ws" / "waive.vlt"
    waiver.write_text("`verilator_config\n", encoding="utf-8")

    options = lint_options(workspace)

    assert options["warning_args"] == ["-Wno-UNUSEDSIGNAL", "-Wno-WIDTHTRUNC"]
    assert options["parameter_args"] == ["-GWIDTH=16", '-GMODE="fast"']
    assert options["extra_args"] == ["--timescale", "1ns/1ps"]
    assert options["waiver_files"] == [str(waiver)]


@pytest.mark.parametrize(
    ("parameters", "text"),
    [
        ({"lint_profile": "pedantic"}, "lint.profile must be one of"),
        ({"lint_rules_off": ["-Wno-fatal"]}, "lint.rules_off entries must be"),
        ({"lint_waivers": ["gone.vlt"]}, "lint.waiver_files not found"),
        ({"design_parameters": ["WIDTH"]}, "design.parameters entries must look like"),
    ],
)
def test_unusable_lint_settings_are_named(tmp_path, parameters, text) -> None:
    with pytest.raises(ValueError, match=text):
        lint_options(_workspace(tmp_path, **parameters))


def test_setting_paths_resolve_against_the_project_for_a_run(tmp_path) -> None:
    project = tmp_path / "project"
    run = project / "runs" / "default"
    (run / "home").mkdir(parents=True)
    (project / "ecc-fe.toml").write_text("schema_version = 1\n", encoding="utf-8")
    parameters = run / "home" / "parameters.json"
    parameters.write_text(json.dumps({"lint_baseline": "lint-baseline.json"}))
    workspace = {"directory": str(run), "parameters_path": str(parameters)}

    assert config_base_dir(workspace) == project
    assert config_paths(workspace, "lint_baseline") == [project / "lint-baseline.json"]
    assert config_paths(workspace, "lint_waivers") == []


def test_list_settings_accept_json_text(tmp_path) -> None:
    workspace = _workspace(tmp_path, lint_rules_off='["A", "B"]', design_parameters=[" W=1 ", ""])

    assert config_list(workspace, "lint_rules_off") == ["A", "B"]
    assert design_parameter_overrides(workspace) == ["W=1"]


def test_empty_string_is_valid_only_for_a_setting_that_can_be_unset() -> None:
    baseline = lookup_parameter("lint.baseline")
    preset = lookup_parameter("sim.compile_preset")
    assert baseline is not None and preset is not None

    validate_value("", baseline)
    assert parse_cli_value("lint-baseline.json", baseline) == "lint-baseline.json"
    with pytest.raises(ValueError):
        validate_value("", preset)
    with pytest.raises(ValueError):
        parse_cli_value("pedantic", lookup_parameter("lint.profile"))


# --- diagnostics ------------------------------------------------------------


def test_verilator_closing_line_is_not_counted_as_an_error() -> None:
    log = (
        "%Error: /rtl/top.sv:6:36: Can't find definition of variable: 'zzz'\n"
        "%Error: Exiting due to 1 error(s)\n"
        "%Error: Command Failed /usr/bin/verilator_bin\n"
        "%Error: Cannot find file containing module: 'missing'\n"
    )

    diagnostics = parse_verilator_diagnostics(log)

    assert [item["message"] for item in diagnostics] == [
        "Can't find definition of variable: 'zzz'",
        "Cannot find file containing module: 'missing'",
    ]


# --- baseline ---------------------------------------------------------------


def _baseline(tmp_path: Path, diagnostics: list[dict]) -> dict:
    path = tmp_path / "ws" / "lint-baseline.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "entries": lint_baseline_entries(diagnostics)}),
        encoding="utf-8",
    )
    return _workspace(tmp_path, lint_baseline="lint-baseline.json")


def test_baseline_entries_count_repeats_and_ignore_position() -> None:
    entries = lint_baseline_entries(
        [
            _warning("WIDTHTRUNC", "/a/rtl/top.sv", "m", line=3),
            _warning("WIDTHTRUNC", "/b/elsewhere/top.sv", "m", line=90),
            {"severity": "error", "code": "E", "source": "x.sv", "message": "boom"},
        ]
    )

    assert entries == [{"code": "WIDTHTRUNC", "file": "top.sv", "message": "m", "count": 2}]


def test_no_baseline_means_no_gate(tmp_path) -> None:
    diagnostics = [_warning("W", "top.sv", "m")]

    result = apply_lint_baseline(_workspace(tmp_path), diagnostics)

    assert result["gate"] == "off"
    assert "baseline" not in diagnostics[0]


def test_known_warnings_pass_even_when_lines_move(tmp_path) -> None:
    workspace = _baseline(tmp_path, [_warning("W", "top.sv", "m", line=3)])
    diagnostics = [_warning("W", "/other/checkout/top.sv", "m", line=40)]

    result = apply_lint_baseline(workspace, diagnostics)

    assert (result["gate"], result["known"], result["new"]) == ("passed", 1, 0)
    assert diagnostics[0]["baseline"] == "known"


def test_new_warning_in_own_code_fails_the_gate(tmp_path) -> None:
    workspace = _baseline(tmp_path, [_warning("W", "top.sv", "m")])
    diagnostics = [
        _warning("W", "top.sv", "m"),
        _warning("W", "top.sv", "m"),  # one more of the same than the baseline has
        _warning("UNUSED", "top.sv", "other"),
    ]

    result = apply_lint_baseline(workspace, diagnostics)

    assert result["gate"] == "failed"
    assert (result["known"], result["new"], result["new_actionable"]) == (1, 2, 2)
    assert [item["baseline"] for item in diagnostics] == ["known", "new", "new"]


def test_new_warning_in_code_the_user_does_not_own_does_not_fail_the_gate(tmp_path) -> None:
    workspace = _baseline(tmp_path, [])

    result = apply_lint_baseline(
        workspace, [_warning("W", "soc.v", "m", actionable=False)]
    )

    assert (result["gate"], result["new"], result["new_actionable"]) == ("passed", 1, 0)


def test_fixed_warnings_are_counted(tmp_path) -> None:
    workspace = _baseline(tmp_path, [_warning("W", "top.sv", "m"), _warning("X", "top.sv", "n")])

    result = apply_lint_baseline(workspace, [_warning("W", "top.sv", "m")])

    assert (result["gate"], result["fixed"]) == ("passed", 1)


@pytest.mark.parametrize("content", ["not json", "[]", '{"schema_version": 9, "entries": []}'])
def test_unreadable_baseline_fails_the_gate(tmp_path, content) -> None:
    workspace = _workspace(tmp_path, lint_baseline="lint-baseline.json")
    (tmp_path / "ws" / "lint-baseline.json").write_text(content, encoding="utf-8")

    result = apply_lint_baseline(workspace, [])

    assert result["gate"] == "failed"
    assert "cannot read lint baseline" in result["error"]


def test_lint_summary_fails_when_the_gate_fails(tmp_path) -> None:
    workspace = _baseline(tmp_path, [])
    workspace["frontend_design_kind"] = "generic_rtl"
    source = tmp_path / "ws" / "top.sv"
    source.write_text("module top; endmodule\n", encoding="utf-8")
    workspace["input_filelist"] = str(tmp_path / "ws" / "design.f")
    log = f"%Warning-UNUSEDSIGNAL: {source}:1:1: Signal is not used: 'x'\n"

    summary = build_lint_summary(
        workspace,
        {"returncode": 0, "rtl_files": [str(source)], "top_module": "top", "command": []},
        log,
        summary_path=tmp_path / "lint_summary.json",
    )

    assert summary["status"] == "fail"
    assert summary["summary"]["errors"] == 0
    assert summary["summary"]["baseline_gate"] == "failed"
    assert summary["summary"]["new_diagnostics"] == 1
    assert summary["settings"]["profile"] == "all"


# --- provenance -------------------------------------------------------------


def test_lint_result_depends_on_the_content_of_setting_files(tmp_path) -> None:
    workspace = _workspace(tmp_path, lint_waivers=["waive.vlt"])
    waiver = tmp_path / "ws" / "waive.vlt"
    waiver.write_text("a\n", encoding="utf-8")
    source = {"fixed": "x"}

    first = build_step_provenance(workspace, "lint", "verilator", None, source=source)
    waiver.write_text("b\n", encoding="utf-8")
    second = build_step_provenance(workspace, "lint", "verilator", None, source=source)
    elab = build_step_provenance(workspace, "elab", "slang", None, source=source)

    assert first["config_fingerprint"] != second["config_fingerprint"]
    assert "lint_waivers:content" in second["configuration"]
    assert "lint_waivers" not in elab["configuration"]


def test_workspace_without_lint_settings_keeps_its_configuration(tmp_path) -> None:
    provenance = build_step_provenance(
        _workspace(tmp_path, top_module="top"), "lint", "verilator", None, source={}
    )

    assert provenance["configuration"] == {"top_module": "top"}


# --- library directories ----------------------------------------------------


def test_library_directories_reach_both_tools(tmp_path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "top.sv").write_text("module top; endmodule\n", encoding="utf-8")
    filelist = tmp_path / "design.f"
    filelist.write_text("top.sv\n-y lib\n+libext+.v+.sv\n", encoding="utf-8")
    workspace = {"frontend_design_kind": "generic_rtl", "input_filelist": str(filelist)}

    parsed = parse_filelist(filelist)

    assert parsed.unsupported == ()
    assert verilator_library_args(workspace) == ["-y", str(tmp_path / "lib"), "+libext+.v+.sv"]
    assert slang_library_args(workspace) == ["-y", str(tmp_path / "lib"), "-Y", ".v", "-Y", ".sv"]


# --- commands ---------------------------------------------------------------


def test_report_lint_baseline_writes_a_file_the_gate_accepts(tmp_path, capsys) -> None:
    project = tmp_path / "project"
    (tmp_path / "top.sv").write_text("module top; endmodule\n", encoding="utf-8")
    assert cli_main.run(
        ["init", str(project), "--kind", "rtl", "--top", "top", "--rtl", str(tmp_path / "top.sv"), "--json"]
    ) == 0
    assert cli_main.run(["run", "--project", str(project), "--to", "prepare", "--json"]) in (0, 1)
    capsys.readouterr()
    run = project / "runs" / "default"
    report = run / "lint_verilator" / "report"
    report.mkdir(parents=True, exist_ok=True)
    diagnostics = [_warning("W", "/x/top.sv", "m"), _warning("W", "/x/top.sv", "m")]
    (report / "lint_summary.json").write_text(
        json.dumps({"top_module": "top", "diagnostics": diagnostics}), encoding="utf-8"
    )
    destination = project / "lint-baseline.json"

    exit_code = cli_main.run(
        ["report", "lint-baseline", "--project", str(project), "-o", str(destination), "--json"]
    )

    record = json.loads(capsys.readouterr().out)["records"][0]
    assert exit_code == 0
    assert (record["warnings"], record["entries"]) == (2, 1)
    assert record["set_cmd"] == "ecc-fe param set lint.baseline lint-baseline.json"
    workspace = {
        "directory": str(run),
        "parameters_path": str(run / "home" / "parameters.json"),
    }
    parameters = json.loads((run / "home" / "parameters.json").read_text())
    parameters["lint_baseline"] = "lint-baseline.json"
    (run / "home" / "parameters.json").write_text(json.dumps(parameters))
    assert apply_lint_baseline(workspace, diagnostics)["gate"] == "passed"


def test_report_lint_baseline_needs_a_lint_result(tmp_path, capsys) -> None:
    workspace = tmp_path / "ws"
    assert cli_main.run(["init", "--workspace", str(workspace), "--json"]) == 0
    capsys.readouterr()

    assert cli_main.run(["report", "lint-baseline", "--workspace", str(workspace), "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["records"][0]["error"] == "lint_result_not_found"


@pytest.mark.parametrize(
    ("version", "status"),
    [("Verilator 5.050 2026-07-01", "pass"), ("Verilator 4.228 2022-10-01", "fail")],
)
def test_doctor_checks_the_minimum_verilator_version(
    tmp_path, monkeypatch, capsys, version, status
) -> None:
    monkeypatch.setattr(cli_commands, "_find_executable", lambda candidates: "/usr/bin/tool")
    monkeypatch.setattr(cli_commands, "_tool_version", lambda path: version)

    cli_main.run(["doctor", "--step", "lint", "--workspace", str(tmp_path), "--json"])

    records = json.loads(capsys.readouterr().out)["records"]
    verilator = next(record for record in records if record.get("check") == "verilator")
    assert verilator["status"] == status
    assert verilator["minimum_version"] == "5.0"
    assert verilator.get("error") == (None if status == "pass" else "tool_too_old")
