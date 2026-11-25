"""Simulating an RTL design with the testbench its project names."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from fecompiler.allflow.profile import flow_steps, parameter_groups_outside
from fecompiler.analysis.qor import write_step_qor
from fecompiler.cli import main as cli_main
from fecompiler.tools.common.process import ToolResult
from fecompiler.tools.fe.builder import build_step, build_step_space
from fecompiler.tools.verilator import testbench
from fecompiler.tools.verilator.sim import SimStep
from fecompiler.tools.verilator.testbench import judge_run

_COUNTER = "module counter(input clk, output logic [3:0] q);\n  always_ff @(posedge clk) q <= q + 1;\nendmodule\n"
_TESTBENCH = "module tb_counter;\n  logic clk = 0;\n  logic [3:0] q;\n  counter dut (.*);\n  initial $finish;\nendmodule\n"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)


@pytest.fixture
def sources(tmp_path) -> Path:
    root = tmp_path / "src"
    (root / "tb").mkdir(parents=True)
    (root / "counter.sv").write_text(_COUNTER, encoding="utf-8")
    (root / "tb" / "tb_counter.sv").write_text(_TESTBENCH, encoding="utf-8")
    return root


def _call(capsys, *args: str) -> tuple[int, list[dict[str, object]]]:
    exit_code = cli_main.run([*args, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)["records"]


def _init(capsys, directory: Path, sources: Path, *extra: str):
    return _call(
        capsys,
        "init",
        str(directory),
        "--kind",
        "rtl",
        "--top",
        "counter",
        "--rtl",
        str(sources / "counter.sv"),
        *extra,
    )


@pytest.fixture
def project(tmp_path, sources, capsys) -> Path:
    directory = tmp_path / "project"
    exit_code, records = _init(
        capsys,
        directory,
        sources,
        "--testbench",
        str(sources / "tb" / "tb_counter.sv"),
        "--testbench-top",
        "tb_counter",
    )
    assert exit_code == 0, records
    return directory


def _flow_steps(workspace: Path) -> list[str]:
    flow = json.loads((workspace / "home" / "flow.json").read_text(encoding="utf-8"))
    return [step["name"] for step in flow["steps"]]


# --- which designs have a sim step ---------------------------------------


def test_a_testbench_adds_the_sim_step_to_an_rtl_design() -> None:
    rtl = {"frontend_design_kind": "generic_rtl"}

    assert [name for name, _ in flow_steps(rtl)] == ["prepare", "review", "elab", "lint"]
    assert [name for name, _ in flow_steps({**rtl, "testbench_top": "tb"})] == [
        "prepare",
        "review",
        "elab",
        "lint",
        "sim",
    ]
    # A CPU is simulated on its harness whatever the testbench setting says.
    assert flow_steps({"testbench_top": "tb"}) == flow_steps({})


def test_parameter_groups_follow_the_design() -> None:
    rtl = {"frontend_design_kind": "generic_rtl"}

    assert parameter_groups_outside(rtl) == {"sim"}
    assert parameter_groups_outside({**rtl, "testbench_top": "tb"}) == {"sim"}
    assert parameter_groups_outside({}) == {"testbench"}


# --- init -----------------------------------------------------------------


def test_init_with_a_testbench_creates_a_five_step_project(project, sources) -> None:
    template = project / ".ecc-fe" / "template"
    parameters = json.loads((template / "home" / "parameters.json").read_text())
    config = tomllib.loads((project / "ecc-fe.toml").read_text(encoding="utf-8"))

    assert _flow_steps(template) == ["prepare", "review", "elab", "lint", "sim"]
    assert parameters["testbench_top"] == "tb_counter"
    # Referenced where it is, like the design sources.
    assert parameters["testbench_files"] == [str(sources / "tb" / "tb_counter.sv")]
    assert config["defaults"]["testbench.top"] == "tb_counter"
    assert not any(key.startswith("sim.") for key in config["defaults"])


def test_init_without_a_testbench_has_no_sim_step(tmp_path, sources, capsys) -> None:
    exit_code, records = _init(capsys, tmp_path / "plain", sources)

    assert exit_code == 0, records
    assert _flow_steps(tmp_path / "plain" / ".ecc-fe" / "template") == [
        "prepare",
        "review",
        "elab",
        "lint",
    ]


@pytest.mark.parametrize(
    ("options", "code"),
    [
        (("--testbench-top", "tb_counter"), "init_input_required"),
        (("--testbench", "TB"), "init_input_required"),
    ],
)
def test_init_needs_both_testbench_options(tmp_path, sources, capsys, options, code) -> None:
    options = tuple(
        str(sources / "tb" / "tb_counter.sv") if item == "TB" else item for item in options
    )

    exit_code, records = _init(capsys, tmp_path / "project", sources, *options)

    assert (exit_code, records[0]["error"]) == (2, code)
    assert not (tmp_path / "project").exists()


@pytest.mark.parametrize(
    ("file", "top", "issue"),
    [
        ("tb/missing.sv", "tb_counter", "testbench_file_not_found"),
        ("tb/tb_counter.sv", "tb_other", "testbench_top_not_found"),
    ],
)
def test_init_checks_the_testbench(tmp_path, sources, capsys, file, top, issue) -> None:
    exit_code, records = _init(
        capsys,
        tmp_path / "project",
        sources,
        "--testbench",
        str(sources / file),
        "--testbench-top",
        top,
    )

    assert exit_code == 1
    assert records[0]["error"] == "init_failed"
    assert [record["code"] for record in records[1:]] == [issue]
    assert not (tmp_path / "project").exists()


def test_a_cpu_project_takes_no_testbench(tmp_path, sources, capsys) -> None:
    exit_code, records = _call(
        capsys,
        "init",
        str(tmp_path / "cpu"),
        "--kind",
        "cpu",
        "--testbench",
        str(sources / "tb" / "tb_counter.sv"),
        "--testbench-top",
        "tb_counter",
    )

    assert (exit_code, records[0]["error"]) == (2, "init_option_conflict")


# --- parameters -----------------------------------------------------------


def test_rtl_project_lists_testbench_parameters_not_cpu_ones(project, capsys) -> None:
    exit_code, records = _call(capsys, "param", "list", "--all", "--project", str(project))

    groups = {record["group"] for record in records if record.get("kind") == "param"}
    assert exit_code == 0
    assert "testbench" in groups
    assert "sim" not in groups


def test_cpu_project_refuses_testbench_parameters(tmp_path, capsys) -> None:
    cpu = tmp_path / "cpu"
    assert _call(capsys, "init", str(cpu))[0] == 0

    listed = _call(capsys, "param", "list", "--all", "--project", str(cpu))[1]
    exit_code, records = _call(
        capsys, "param", "set", "testbench.top", "tb", "--project", str(cpu)
    )

    assert "testbench" not in {record.get("group") for record in listed}
    assert (exit_code, records[0]["error"]) == (2, "parameter_not_applicable")
    assert "SoC harness" in records[0]["reason"]


def test_rtl_project_refuses_cpu_program_parameters(project, capsys) -> None:
    exit_code, records = _call(
        capsys, "param", "set", "sim.compile_preset", "speed", "--project", str(project)
    )

    assert (exit_code, records[0]["error"]) == (2, "parameter_not_applicable")
    assert "testbench.*" in records[0]["reason"]


def test_naming_a_testbench_later_adds_the_sim_step(tmp_path, sources, capsys) -> None:
    workspace = tmp_path / "ws"
    exit_code, records = _call(
        capsys,
        "init",
        "--workspace",
        str(workspace),
        "--kind",
        "rtl",
        "--top",
        "counter",
        "--rtl",
        str(sources / "counter.sv"),
    )
    assert exit_code == 0, records
    assert _flow_steps(workspace) == ["prepare", "review", "elab", "lint"]

    for name, value in (
        ("testbench.files", json.dumps([str(sources / "tb" / "tb_counter.sv")])),
        ("testbench.top", "tb_counter"),
    ):
        exit_code, records = _call(
            capsys, "param", "set", name, value, "--workspace", str(workspace)
        )
        assert exit_code == 0, records
    _, status = _call(capsys, "status", "--workspace", str(workspace))

    assert [record["step"] for record in status if "step" in record] == [
        "prepare",
        "review",
        "elab",
        "lint",
        "sim",
    ]


def test_doctor_does_not_ask_an_rtl_design_for_a_riscv_toolchain(project, capsys) -> None:
    _, records = _call(capsys, "doctor", "--project", str(project))

    checks = {record.get("check") for record in records}
    assert {"verilator", "cxx", "make"} <= checks
    assert "riscv" not in checks


# --- the verdict ----------------------------------------------------------


@pytest.mark.parametrize(
    ("returncode", "output", "timed_out", "patterns", "expected"),
    [
        (0, "anything", False, (None, None), None),
        (1, "TEST PASSED", False, ("PASSED", None), "exit_status"),
        (0, "TEST PASSED", True, ("PASSED", None), "timeout"),
        (0, "TEST PASSED", False, ("PASSED", "FAILED"), None),
        (0, "ok\nTEST FAILED: q=3\n", False, (None, "FAILED"), "fail_pattern"),
        (0, "TEST PASSED\nTEST FAILED\n", False, ("PASSED", "FAILED"), "fail_pattern"),
        (0, "finished", False, ("PASSED", None), "pass_pattern_missing"),
        (0, "a\nsummary: 0 errors\n", False, (r"^summary: 0 errors$", None), None),
    ],
)
def test_judge_run(returncode, output, timed_out, patterns, expected) -> None:
    pass_pattern, fail_pattern = (
        re.compile(text, re.MULTILINE) if text else None for text in patterns
    )

    failure = judge_run(
        returncode,
        output,
        timed_out=timed_out,
        pass_pattern=pass_pattern,
        fail_pattern=fail_pattern,
    )

    assert (failure or {}).get("kind") == expected


def test_a_matched_failure_quotes_the_line() -> None:
    failure = judge_run(
        0,
        "start\nTEST FAILED: q=3 expected=5\nend\n",
        timed_out=False,
        pass_pattern=None,
        fail_pattern=re.compile("FAILED"),
    )

    assert failure == {
        "kind": "fail_pattern",
        "message": "the output matches testbench.fail_pattern: TEST FAILED: q=3 expected=5",
    }


def _workspace(tmp_path: Path, sources: Path, **parameters) -> dict:
    directory = tmp_path / "ws"
    (directory / "home").mkdir(parents=True, exist_ok=True)
    path = directory / "home" / "parameters.json"
    stored = {
        "frontend_design_kind": "generic_rtl",
        "testbench_top": "tb_counter",
        "testbench_files": [str(sources / "tb" / "tb_counter.sv")],
        **parameters,
    }
    path.write_text(json.dumps(stored), encoding="utf-8")
    return {"directory": str(directory), "design": "counter", "parameters_path": str(path)}


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        ({"testbench_top": "tb counter"}, "testbench.top must be a module name"),
        ({"testbench_files": []}, "testbench.files is empty"),
        ({"testbench_files": ["nowhere.sv"]}, "testbench.files not found"),
        ({"testbench_pass_pattern": "(unclosed"}, "testbench.pass_pattern is not a valid"),
        ({"testbench_fail_pattern": "[z-a]"}, "testbench.fail_pattern is not a valid"),
    ],
)
def test_unusable_settings_are_named(tmp_path, sources, parameters, message) -> None:
    with pytest.raises(testbench.TestbenchSettingsError, match=re.escape(message)):
        testbench.testbench_settings(_workspace(tmp_path, sources, **parameters))


def test_relative_testbench_files_are_resolved(tmp_path, sources) -> None:
    workspace = _workspace(tmp_path, sources, testbench_files=["tb.sv"])
    (Path(workspace["directory"]) / "tb.sv").write_text(_TESTBENCH, encoding="utf-8")

    assert testbench.testbench_settings(workspace)["files"] == [
        str(Path(workspace["directory"]) / "tb.sv")
    ]


# --- the step -------------------------------------------------------------


def _fake_tools(monkeypatch, *, build: int = 0, run: tuple[int, str] = (0, "TEST PASSED\n")):
    """Stand in for Verilator and the program it builds; return the commands run."""
    commands: list[list[str]] = []

    def run_tool(command, **_options):
        commands.append([str(item) for item in command])
        if "--binary" in command:
            if build == 0:
                program = Path(command[command.index("-o") + 1])
                program.write_text("#!/bin/sh\n", encoding="utf-8")
            return ToolResult(build, "%Error: tb.sv:3: syntax error\n" if build else "", False)
        return ToolResult(run[0], run[1], False)

    monkeypatch.setattr(testbench, "run_tool", run_tool)
    monkeypatch.setattr(testbench, "rtl_files", lambda _workspace: ["/design/counter.sv"])
    for name in ("verilator_incdir_args", "verilator_library_args", "verilator_define_args"):
        monkeypatch.setattr(testbench, name, lambda _workspace: [])
    return commands


def _sim_step(workspace: dict):
    step = build_step(workspace=workspace, step_name="sim", tool="verilator")
    build_step_space(step)
    return step


def _report(step) -> dict:
    return json.loads((Path(step.report["dir"]) / "cases.json").read_text(encoding="utf-8"))


def test_step_builds_the_design_with_its_testbench_and_runs_it(
    tmp_path, sources, monkeypatch
) -> None:
    workspace = _workspace(tmp_path, sources, testbench_plusargs=["+seed=3"])
    commands = _fake_tools(monkeypatch)
    step = _sim_step(workspace)

    SimStep().run(step, workspace)

    build, run = commands
    testbench_file = str(sources / "tb" / "tb_counter.sv")
    assert build[build.index("--top-module") + 1] == "tb_counter"
    # The design first, then the testbench, whose own directory is searched for includes.
    assert build[-2:] == ["/design/counter.sv", testbench_file]
    assert f"+incdir+{sources / 'tb'}" in build
    assert run[1:] == ["+seed=3"]
    assert _report(step) == {
        "schema_version": 1,
        "kind": "testbench",
        "suite": "testbench",
        "cases": [
            {
                "name": "tb_counter",
                "ok": True,
                "returncode": 0,
                "log": str(Path(step.report["dir"]) / "log.txt"),
                "metrics": None,
                "failure": None,
                "design_parameters": {"applied": [], "not_applied": []},
                "judged_by": ["exit_status"],
            }
        ],
    }
    assert SimStep().check_result(step) is True
    # Passing without a pattern is said where the log is read.
    assert "passed on its exit status alone" in (
        Path(step.report["dir"]) / "log.txt"
    ).read_text(encoding="utf-8")


def test_step_fails_on_a_matching_fail_pattern(tmp_path, sources, monkeypatch) -> None:
    workspace = _workspace(tmp_path, sources, testbench_fail_pattern="FAILED")
    _fake_tools(monkeypatch, run=(0, "TEST FAILED q=3\n"))
    step = _sim_step(workspace)

    SimStep().run(step, workspace)

    case = _report(step)["cases"][0]
    assert (case["ok"], case["failure"]["kind"]) == (False, "fail_pattern")
    assert case["judged_by"] == ["exit_status", "fail_pattern"]
    assert SimStep().check_result(step) is False
    assert (Path(step.report["dir"]) / "log.txt").read_text(encoding="utf-8").endswith(
        "ecc-fe: simulation failed: the output matches testbench.fail_pattern: "
        "TEST FAILED q=3\n"
    )


def test_step_reports_a_testbench_that_does_not_build(tmp_path, sources, monkeypatch) -> None:
    workspace = _workspace(tmp_path, sources)
    commands = _fake_tools(monkeypatch, build=1)
    step = _sim_step(workspace)

    SimStep().run(step, workspace)

    assert len(commands) == 1
    assert _report(step)["cases"][0]["failure"] == {
        "kind": "compile",
        "message": "%Error: tb.sv:3: syntax error",
    }
    assert SimStep().check_result(step) is False


def test_step_reports_unusable_settings_without_running_anything(
    tmp_path, sources, monkeypatch
) -> None:
    workspace = _workspace(tmp_path, sources, testbench_files=["nowhere.sv"])
    commands = _fake_tools(monkeypatch)
    step = _sim_step(workspace)

    SimStep().run(step, workspace)

    assert commands == []
    assert _report(step)["cases"][0]["failure"]["kind"] == "settings"
    assert SimStep().check_result(step) is False


# --- quality --------------------------------------------------------------


@pytest.mark.parametrize(("output", "state"), [("TEST PASSED\n", "pass"), ("TEST FAILED\n", "failed")])
def test_testbench_result_is_gated_and_not_scored(
    tmp_path, sources, monkeypatch, output, state
) -> None:
    workspace = _workspace(tmp_path, sources, testbench_fail_pattern="FAILED")
    _fake_tools(monkeypatch, run=(0, output))
    step = _sim_step(workspace)
    testbench.TestbenchSimStep().run(step, workspace)

    write_step_qor(step, workspace, state == "pass")

    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    gates = {gate["id"]: gate["state"] for gate in summary["gates"]}
    assert gates["all_required_cases_pass"] == state
    assert gates["simulation_cases_present"] == "pass"
    assert "score" not in summary
