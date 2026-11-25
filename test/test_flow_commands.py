"""`flow list` and `flow show` describe the flow from the step registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fecompiler.allflow.steps import step_definitions, step_names
from fecompiler.cli import main as cli_main

_RTL = "module adder(input clk, input [3:0] a, output reg [3:0] q);\n  always @(posedge clk) q <= a;\nendmodule\n"
_TB = "module tb;\n  initial $finish;\nendmodule\n"


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    monkeypatch.delenv("ECC_FE_PROGRAM", raising=False)


def _call(capsys, *args: str) -> tuple[int, list[dict[str, object]]]:
    exit_code = cli_main.run([*args, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)["records"]


def _ok(capsys, *args: str) -> list[dict[str, object]]:
    exit_code, records = _call(capsys, *args)
    assert exit_code == 0, records
    return records


@pytest.fixture
def cpu_project(monkeypatch, tmp_path, capsys) -> Path:
    _isolate(monkeypatch, tmp_path)
    project = tmp_path / "cpu"
    _ok(capsys, "init", str(project))
    return project


def _rtl_project(monkeypatch, tmp_path, capsys, *, testbench: bool) -> Path:
    _isolate(monkeypatch, tmp_path)
    source = tmp_path / "adder.v"
    source.write_text(_RTL, encoding="utf-8")
    arguments = ["init", str(tmp_path / "rtl"), "--kind", "rtl", "--top", "adder", "--rtl", str(source)]
    if testbench:
        bench = tmp_path / "tb.v"
        bench.write_text(_TB, encoding="utf-8")
        arguments += ["--testbench", str(bench), "--testbench-top", "tb"]
    _ok(capsys, *arguments)
    return tmp_path / "rtl"


def test_flow_list_of_a_cpu_project_has_every_step_in_order(cpu_project, capsys):
    summary, *steps = _ok(capsys, "flow", "list", "--project", str(cpu_project))

    assert summary["kind"] == "flow"
    assert summary["design_kind"] == "cpu_core"
    assert summary["describes"] == "next_run"
    assert summary["project"] == str(cpu_project)
    assert summary["steps"] == ["prepare", "review", "elab", "lint", "sim"]
    assert "cpu_tests" in summary["capabilities"]
    assert [(step["index"], step["step"], step["tool"], step["directory"], step["in_flow"]) for step in steps] == [
        (0, "prepare", "fe", "prepare_fe", True),
        (1, "review", "fe", "review_fe", True),
        (2, "elab", "slang", "elab_slang", True),
        (3, "lint", "verilator", "lint_verilator", True),
        (4, "sim", "verilator", "sim_verilator", True),
    ]
    assert all("reason" not in step for step in steps)


def test_flow_list_names_every_registered_step(cpu_project, capsys):
    _, *steps = _ok(capsys, "flow", "list", "--project", str(cpu_project))

    assert tuple(step["step"] for step in steps) == step_names()


def test_an_rtl_design_without_a_testbench_has_no_sim_step(monkeypatch, tmp_path, capsys):
    project = _rtl_project(monkeypatch, tmp_path, capsys, testbench=False)

    summary, *steps = _ok(capsys, "flow", "list", "--project", str(project))

    assert summary["design_kind"] == "generic_rtl"
    assert summary["steps"] == ["prepare", "review", "elab", "lint"]
    sim = steps[-1]
    assert (sim["step"], sim["in_flow"], sim["index"]) == ("sim", False, None)
    assert "testbench.top" in sim["reason"]


def test_an_rtl_design_with_a_testbench_is_simulated(monkeypatch, tmp_path, capsys):
    project = _rtl_project(monkeypatch, tmp_path, capsys, testbench=True)

    summary, *steps = _ok(capsys, "flow", "list", "--project", str(project))

    assert summary["steps"] == ["prepare", "review", "elab", "lint", "sim"]
    assert summary["capabilities"] == ["qor", "simulation", "source"]
    assert steps[-1]["in_flow"] is True


def test_flow_list_of_a_direct_workspace(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    workspace = tmp_path / "ws"
    _ok(capsys, "init", "--workspace", str(workspace))

    summary, *_ = _ok(capsys, "flow", "list", "--workspace", str(workspace))

    assert summary["describes"] == "workspace"
    assert summary["workspace"] == str(workspace)
    assert summary["project"] is None


def test_flow_list_of_a_run_that_does_not_exist_is_an_error(cpu_project, capsys):
    exit_code, records = _call(
        capsys, "flow", "list", "--project", str(cpu_project), "--run-id", "missing"
    )

    assert exit_code == 1
    assert records[0]["error"] == "run_not_found"


def test_flow_list_outside_a_project_is_an_error(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "empty").mkdir()

    exit_code, records = _call(capsys, "flow", "list", "--project", str(tmp_path / "empty"))

    assert exit_code == 1
    assert records[0]["error"] == "workspace_not_found"


def test_flow_list_text_is_one_line_per_step(cpu_project, capsys):
    assert cli_main.run(["flow", "list", "--project", str(cpu_project)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "[flow.list]"
    assert lines[-5:] == [
        "  1  prepare   fe          prepare_fe",
        "  2  review    fe          review_fe",
        "  3  elab      slang       elab_slang",
        "  4  lint      verilator   lint_verilator",
        "  5  sim       verilator   sim_verilator",
    ]


def test_flow_list_text_says_why_a_step_is_left_out(monkeypatch, tmp_path, capsys):
    project = _rtl_project(monkeypatch, tmp_path, capsys, testbench=False)
    assert cli_main.run(["flow", "list", "--project", str(project)]) == 0

    assert capsys.readouterr().out.splitlines()[-1] == (
        "  -  sim       not in this flow: an RTL design is simulated when it names "
        "a testbench (testbench.top)"
    )


def test_flow_show_describes_the_lint_step(cpu_project, capsys):
    (record,) = _ok(capsys, "flow", "show", "lint", "--project", str(cpu_project))

    assert record["kind"] == "flow_step"
    assert (record["step"], record["tool"], record["directory"]) == ("lint", "verilator", "lint_verilator")
    assert (record["index"], record["after"], record["before"]) == (3, "elab", "sim")
    assert record["inputs_from"] == "prepare"
    assert record["programs"] == ["verilator"]
    assert record["environment_checks"] == ["verilator"]
    assert record["qor_report"] == "lint_verilator/report/lint_summary.json"
    assert "lint.profile" in record["parameters"]
    assert "design.parameters" in record["parameters"]
    stale = record["stale_when"]
    assert stale["parameters"] == [
        "design.parameters",
        "lint.profile",
        "lint.rules_off",
        "lint.extra_args",
        "lint.baseline",
        "lint.waiver_files",
    ]
    assert stale["design_settings"] == ["top_module"]
    assert stale["sources"] == [
        "fecompiler/tools/verilator/lint.py",
        "fecompiler/tools/verilator/command.py",
    ]
    assert record["run_cmd"] == f"ecc-fe run --only lint --project {cpu_project}"
    assert record["params_cmd"] == f"ecc-fe param list --step lint --project {cpu_project}"
    assert record["doctor_cmd"] == f"ecc-fe doctor --step lint --project {cpu_project}"


def test_the_first_and_last_steps_have_one_neighbour(cpu_project, capsys):
    (first,) = _ok(capsys, "flow", "show", "prepare", "--project", str(cpu_project))
    (last,) = _ok(capsys, "flow", "show", "sim", "--project", str(cpu_project))

    assert (first["after"], first["before"], first["inputs_from"]) == (None, "review", None)
    assert (last["after"], last["before"]) == ("lint", None)


def test_flow_show_separates_settings_that_do_not_make_a_result_stale(cpu_project, capsys):
    (record,) = _ok(capsys, "flow", "show", "sim", "--project", str(cpu_project))

    stale = record["stale_when"]
    assert stale["parameters_not_tracked"] == [
        "sim.build_jobs",
        "sim.case_jobs",
        "sim.history_limit",
    ]
    assert not set(stale["parameters_not_tracked"]) & set(stale["parameters"])
    assert "sim.dump_waveform" in stale["parameters"]
    assert "design.frequency_mhz" in stale["parameters"]
    # A CPU is simulated on its SoC harness; the testbench settings are not its own.
    assert not [name for name in stale["parameters"] if name.startswith("testbench.")]


def test_flow_show_lists_only_the_parameters_of_this_design(monkeypatch, tmp_path, capsys):
    project = _rtl_project(monkeypatch, tmp_path, capsys, testbench=True)

    (record,) = _ok(capsys, "flow", "show", "sim", "--project", str(project))

    assert record["in_flow"] is True
    assert "testbench.top" in record["parameters"]
    assert not [name for name in record["parameters"] if name.startswith("sim.")]
    stale = record["stale_when"]
    assert "testbench.pass_pattern" in stale["parameters"]
    assert not [
        name
        for name in (*stale["parameters"], *stale["parameters_not_tracked"])
        if name.startswith("sim.")
    ]


def test_flow_show_of_a_step_outside_the_flow_gives_no_commands(monkeypatch, tmp_path, capsys):
    project = _rtl_project(monkeypatch, tmp_path, capsys, testbench=False)

    (record,) = _ok(capsys, "flow", "show", "sim", "--project", str(project))

    assert record["in_flow"] is False
    assert "testbench.top" in record["reason"]
    assert (record["index"], record["after"], record["before"]) == (None, None, None)
    assert not {"run_cmd", "params_cmd", "doctor_cmd"} & set(record)


def test_flow_show_rejects_an_unknown_step(cpu_project, capsys):
    exit_code, records = _call(capsys, "flow", "show", "route", "--project", str(cpu_project))

    assert exit_code == 2
    assert records[0]["error"] == "unknown_step"
    assert records[0]["expected"] == list(step_names())


def test_flow_show_commands_carry_the_run_and_the_program_name(cpu_project, capsys, monkeypatch):
    monkeypatch.setenv("ECC_FE_PROGRAM", "ecc fe")
    exit_code, records = _call(
        capsys, "flow", "show", "elab", "--project", str(cpu_project), "--run-id", "r1"
    )

    # The run does not exist yet, so there is nothing to describe.
    assert exit_code == 1
    assert records[0]["error"] == "run_not_found"

    (record,) = _ok(capsys, "flow", "show", "elab", "--project", str(cpu_project))
    assert record["run_cmd"] == f"ecc fe run --only elab --project {cpu_project}"


@pytest.mark.parametrize("definition", step_definitions(), ids=lambda item: item.name)
def test_every_registered_step_can_be_shown(cpu_project, capsys, definition):
    (record,) = _ok(capsys, "flow", "show", definition.name, "--project", str(cpu_project))

    assert record["handler"] == definition.handler
    assert record["stale_when"]["programs"] == list(definition.executables)
    for parameter in record["parameters"]:
        assert definition.name in parameter or parameter.startswith(("design.", "testbench."))
