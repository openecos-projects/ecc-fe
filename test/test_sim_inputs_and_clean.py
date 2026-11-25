"""Gaps closed around simulation inputs and rebuilding.

* ``design.parameters`` reach a testbench simulation through the testbench;
* headers a testbench includes are tracked and listed headers are not compiled;
* ``run --clean`` rebuilds instead of reusing earlier build outputs;
* the lint summary of an RTL design has no CPU counts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from fecompiler.cli import commands as cli_commands
from fecompiler.cli import main as cli_main
from fecompiler.engine.provenance import build_step_provenance
from fecompiler.tools.common import build_policy
from fecompiler.tools.common.process import ToolResult
from fecompiler.tools.common.sv_includes import included_files
from fecompiler.tools.common.sv_module import module_parameter_names
from fecompiler.tools.fe.builder import build_step, build_step_space
from fecompiler.tools.verilator import program_build, runner, testbench
from fecompiler.tools.verilator.sim import SimStep


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)


# --- parameters a module declares -----------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("module tb; endmodule", []),
        ("module tb #(parameter WIDTH = 8, DEPTH = 4) (); endmodule", ["WIDTH", "DEPTH"]),
        ("module tb #(parameter int WIDTH = 8, parameter logic [3:0] INIT = 4'd1); endmodule", ["WIDTH", "INIT"]),
        ("module tb #(parameter WIDTH = 8, localparam HALF = WIDTH / 2, parameter N = 1); endmodule", ["WIDTH", "N"]),
        ("module tb #(WIDTH = 8); endmodule", ["WIDTH"]),
        ("module tb;\n  parameter WIDTH = 8, DEPTH = 2;\n  localparam LOCAL = 1;\n  parameter [7:0] SEED = 8'h5a;\nendmodule", ["WIDTH", "DEPTH", "SEED"]),
        ("module tb #(parameter type T = logic, parameter string NAME = \"a,b\"); endmodule", ["T", "NAME"]),
        ("module tb;\n  // parameter HIDDEN = 1;\n  /* parameter ALSO = 2; */\nendmodule", []),
        ("module other #(parameter WIDTH = 8); endmodule\nmodule tb; endmodule", []),
        ("module tb; endmodule\nmodule other; parameter LATER = 1; endmodule", []),
        ("module tb #(parameter int unsigned MAX = fn(1, 2)); endmodule", ["MAX"]),
    ],
)
def test_module_parameter_names(text, expected) -> None:
    assert module_parameter_names(text, "tb") == expected


def test_parameters_in_an_unselected_branch_are_not_declared() -> None:
    text = "module tb;\n`ifdef WIDE\n  parameter WIDE_ONLY = 1;\n`else\n  parameter NARROW_ONLY = 1;\n`endif\nendmodule"

    assert module_parameter_names(text, "tb", defined_macros={"WIDE"}) == ["WIDE_ONLY"]
    assert module_parameter_names(text, "tb", defined_macros=set()) == ["NARROW_ONLY"]


# --- files reached by `include --------------------------------------------


def test_includes_are_followed_through_other_includes(tmp_path) -> None:
    (tmp_path / "inc").mkdir()
    (tmp_path / "tb.sv").write_text('`include "a.svh"\n`include "missing.svh"\nmodule tb; endmodule\n')
    (tmp_path / "a.svh").write_text('`include "b.svh"\n// `include "commented.svh"\n')
    (tmp_path / "inc" / "b.svh").write_text('`include "a.svh"\n')
    (tmp_path / "commented.svh").write_text("")

    assert included_files([tmp_path / "tb.sv"], [tmp_path / "inc"]) == [
        tmp_path / "a.svh",
        tmp_path / "inc" / "b.svh",
    ]


def test_a_file_beside_the_source_wins_over_a_search_directory(tmp_path) -> None:
    (tmp_path / "inc").mkdir()
    (tmp_path / "tb.sv").write_text('`include "defs.svh"\n')
    (tmp_path / "defs.svh").write_text("")
    (tmp_path / "inc" / "defs.svh").write_text("")

    assert included_files([tmp_path / "tb.sv"], [tmp_path / "inc"]) == [tmp_path / "defs.svh"]


def test_a_source_without_includes_names_nothing(tmp_path) -> None:
    (tmp_path / "tb.sv").write_text("module tb; endmodule\n")

    assert included_files([tmp_path / "tb.sv", tmp_path / "gone.sv"]) == []


# --- what makes a testbench simulation stale --------------------------------


def _workspace(tmp_path: Path, files: list[Path], **parameters) -> dict:
    directory = tmp_path / "ws"
    (directory / "home").mkdir(parents=True, exist_ok=True)
    path = directory / "home" / "parameters.json"
    path.write_text(
        json.dumps(
            {
                "frontend_design_kind": "generic_rtl",
                "testbench_top": "tb",
                "testbench_files": [str(file) for file in files],
                **parameters,
            }
        ),
        encoding="utf-8",
    )
    return {"directory": str(directory), "design": "demo", "parameters_path": str(path)}


def _signature(workspace: dict) -> str:
    return build_step_provenance(workspace, "sim", "verilator", None)["signature"]


def test_editing_a_header_the_testbench_includes_changes_the_signature(tmp_path) -> None:
    bench = tmp_path / "tb.sv"
    header = tmp_path / "checks.svh"
    bench.write_text('`include "checks.svh"\nmodule tb; endmodule\n')
    header.write_text("`define LIMIT 4\n")
    workspace = _workspace(tmp_path, [bench])
    before = _signature(workspace)

    assert _signature(workspace) == before
    header.write_text("`define LIMIT 5\n")

    assert _signature(workspace) != before


def test_a_testbench_without_includes_keeps_its_signature_inputs(tmp_path) -> None:
    bench = tmp_path / "tb.sv"
    bench.write_text("module tb; endmodule\n")
    workspace = _workspace(tmp_path, [bench])
    record = build_step_provenance(workspace, "sim", "verilator", None)

    (tmp_path / "unrelated.svh").write_text("`define X 1\n")

    assert build_step_provenance(workspace, "sim", "verilator", None)["signature"] == record["signature"]


def test_design_parameters_are_part_of_the_sim_signature(tmp_path) -> None:
    bench = tmp_path / "tb.sv"
    bench.write_text("module tb; endmodule\n")

    assert _signature(_workspace(tmp_path, [bench])) != _signature(
        _workspace(tmp_path, [bench], design_parameters=["WIDTH=16"])
    )


# --- the testbench step ---------------------------------------------------


def _fake_tools(monkeypatch) -> list[list[str]]:
    commands: list[list[str]] = []

    def run_tool(command, **_options):
        commands.append([str(item) for item in command])
        if "--binary" in command:
            Path(command[command.index("-o") + 1]).write_text("#!/bin/sh\n", encoding="utf-8")
        return ToolResult(0, "done\n", False)

    monkeypatch.setattr(testbench, "run_tool", run_tool)
    monkeypatch.setattr(testbench, "rtl_files", lambda _workspace: ["/design/dut.sv"])
    for name in ("verilator_incdir_args", "verilator_library_args", "verilator_define_args"):
        monkeypatch.setattr(testbench, name, lambda _workspace: [])
    return commands


def _run_sim(workspace: dict):
    step = build_step(workspace=workspace, step_name="sim", tool="verilator")
    build_step_space(step)
    SimStep().run(step, workspace)
    report = json.loads((Path(step.report["dir"]) / "cases.json").read_text(encoding="utf-8"))
    log = (Path(step.report["dir"]) / "log.txt").read_text(encoding="utf-8")
    return report["cases"][0], log


def test_a_design_parameter_the_testbench_declares_is_passed_to_it(tmp_path, monkeypatch) -> None:
    bench = tmp_path / "tb.sv"
    bench.write_text("module tb #(parameter WIDTH = 8);\n  dut #(.WIDTH(WIDTH)) u ();\nendmodule\n")
    workspace = _workspace(tmp_path, [bench], design_parameters=["WIDTH=16", "DEPTH=4"])
    commands = _fake_tools(monkeypatch)

    case, log = _run_sim(workspace)

    build = commands[0]
    assert "-GWIDTH=16" in build
    assert not [item for item in build if item.startswith("-GDEPTH")]
    assert case["ok"] is True
    assert case["design_parameters"] == {"applied": ["WIDTH=16"], "not_applied": ["DEPTH=4"]}
    assert "ecc-fe: design.parameters applied through the testbench: WIDTH=16" in log
    assert (
        "ecc-fe: design.parameters not applied to the simulation: DEPTH=4; "
        "module tb declares no parameter of that name to pass on"
    ) in log


def test_without_design_parameters_nothing_is_said_or_passed(tmp_path, monkeypatch) -> None:
    bench = tmp_path / "tb.sv"
    bench.write_text("module tb #(parameter WIDTH = 8); endmodule\n")
    commands = _fake_tools(monkeypatch)

    case, log = _run_sim(_workspace(tmp_path, [bench]))

    assert not [item for item in commands[0] if item.startswith("-G")]
    assert case["design_parameters"] == {"applied": [], "not_applied": []}
    assert "design.parameters" not in log


def test_a_malformed_design_parameter_is_a_settings_failure(tmp_path, monkeypatch) -> None:
    bench = tmp_path / "tb.sv"
    bench.write_text("module tb; endmodule\n")
    commands = _fake_tools(monkeypatch)

    case, _ = _run_sim(_workspace(tmp_path, [bench], design_parameters=["WIDTH 16"]))

    assert commands == []
    assert case["failure"]["kind"] == "settings"
    assert "design.parameters entries must look like NAME=value" in case["failure"]["message"]


def test_a_listed_header_is_searched_for_but_not_compiled(tmp_path, monkeypatch) -> None:
    (tmp_path / "inc").mkdir()
    bench = tmp_path / "tb.sv"
    header = tmp_path / "inc" / "checks.svh"
    bench.write_text('`include "checks.svh"\nmodule tb; endmodule\n')
    header.write_text("`define LIMIT 4\n")
    commands = _fake_tools(monkeypatch)

    case, _ = _run_sim(_workspace(tmp_path, [bench, header]))

    build = commands[0]
    assert case["ok"] is True
    assert build[-2:] == ["/design/dut.sv", str(bench)]
    assert str(header) not in build
    assert f"+incdir+{tmp_path / 'inc'}" in build


def test_only_headers_is_not_a_testbench(tmp_path) -> None:
    header = tmp_path / "checks.svh"
    header.write_text("")

    with pytest.raises(testbench.TestbenchSettingsError, match="names only headers"):
        testbench.testbench_settings(_workspace(tmp_path, [header]))


# --- run --clean ----------------------------------------------------------


def test_clean_build_is_off_unless_requested() -> None:
    assert build_policy.clean_build_requested() is False
    with build_policy.clean_build():
        assert build_policy.clean_build_requested() is True
        with build_policy.clean_build(False):
            assert build_policy.clean_build_requested() is False
        assert build_policy.clean_build_requested() is True
    assert build_policy.clean_build_requested() is False


def test_clean_build_ends_when_the_run_fails() -> None:
    with pytest.raises(RuntimeError), build_policy.clean_build():
        raise RuntimeError("step failed")

    assert build_policy.clean_build_requested() is False


@pytest.fixture
def project(tmp_path, capsys) -> Path:
    directory = tmp_path / "project"
    assert cli_main.run(["init", str(directory), "--json"]) == 0
    capsys.readouterr()
    return directory


@pytest.fixture
def calls(monkeypatch) -> list[tuple[str, dict[str, object], bool]]:
    recorded: list[tuple[str, dict[str, object], bool]] = []

    def fake_execute(command, payload, *, base_dir):
        recorded.append((command, payload, build_policy.clean_build_requested()))
        return SimpleNamespace(response="success", data={}, message=[])

    monkeypatch.setattr(cli_commands.workspace_application, "execute_payload", fake_execute)
    return recorded


def _run(capsys, project: Path, *args: str) -> tuple[int, dict[str, object]]:
    exit_code = cli_main.run(["run", "--project", str(project), *args, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)["records"][0]


def test_clean_alone_is_a_usage_error(project, calls, capsys) -> None:
    assert _run(capsys, project)[0] == 0
    calls.clear()

    exit_code, record = _run(capsys, project, "--clean")

    assert exit_code == 2
    assert record["error"] == "clean_requires_rerun"
    assert record["expected"] == ["--only <step>", "--from <step>", "--rerun"]
    assert calls == []


@pytest.mark.parametrize(
    ("arguments", "command", "rerun"),
    [
        (["--only", "sim", "--clean"], "run-step", True),
        (["--from", "sim", "--clean"], "run-flow", False),
        (["--rerun", "--clean"], "run-flow", True),
    ],
)
def test_clean_reaches_the_steps_that_are_run_again(
    project, calls, capsys, arguments, command, rerun
) -> None:
    assert _run(capsys, project)[0] == 0
    calls.clear()

    assert _run(capsys, project, *arguments)[0] == 0

    ((executed, payload, clean),) = calls
    assert (executed, payload["rerun"], clean) == (command, rerun, True)
    assert build_policy.clean_build_requested() is False


def test_without_clean_the_steps_may_reuse(project, calls, capsys) -> None:
    assert _run(capsys, project)[0] == 0
    calls.clear()

    assert _run(capsys, project, "--only", "sim", "--force")[0] == 0

    assert calls[0][2] is False


def _program_build(monkeypatch, tmp_path: Path):
    """A SoC tree whose build script is replaced by a counter of builds."""
    soc = tmp_path / "soc"
    for name, text in {
        "scripts/build_test.sh": "#!/bin/sh\n",
        "tests/common/start.S": "_start:\n",
        "tests/programs/add.c": "int main(void) { return 0; }\n",
        "tests/programs/sub.c": "int main(void) { return 1; }\n",
    }.items():
        (soc / name).parent.mkdir(parents=True, exist_ok=True)
        (soc / name).write_text(text, encoding="utf-8")
    sources = [soc / "tests" / "programs" / "add.c", soc / "tests" / "programs" / "sub.c"]
    built: list[str] = []

    def run_tool(command, **_options):
        name = command[command.index("--name") + 1]
        out_dir = Path(command[command.index("--out_dir") + 1])
        (out_dir / f"{name}.soc.bin").write_bytes(b"\x13\x00\x00\x00")
        built.append(name)
        return ToolResult(0, "", False)

    monkeypatch.setattr(runner, "run_tool", run_tool)
    monkeypatch.setattr(runner, "_program_sources_to_build", lambda _workspace: sources)
    monkeypatch.setattr(runner, "_build_test_script", lambda _workspace: soc / "scripts" / "build_test.sh")
    monkeypatch.setattr(runner, "_explicit_soc_tests_out_dir", lambda _workspace: None)
    monkeypatch.setattr(runner, "_sim_images", lambda _workspace: [])
    monkeypatch.setenv("PATH", os.environ["PATH"])
    log = tmp_path / "build.log"

    def prepare() -> tuple[list[str], str]:
        built.clear()
        images, ok = runner._prepare_sim_images(
            {"directory": str(tmp_path), "sim_build_jobs": 2},
            build_log_path=log,
            case_output_root=tmp_path / "cases",
        )
        assert ok and len(images) == 2
        return sorted(built), log.read_text(encoding="utf-8")

    return prepare


def test_unchanged_test_programs_are_not_built_again(monkeypatch, tmp_path) -> None:
    prepare = _program_build(monkeypatch, tmp_path)

    assert prepare()[0] == ["add", "sub"]
    built, log = prepare()

    assert built == []
    assert log.count("up to date") == 2


def test_a_clean_build_builds_every_test_program_again(monkeypatch, tmp_path) -> None:
    prepare = _program_build(monkeypatch, tmp_path)
    prepare()

    with build_policy.clean_build():
        built, log = prepare()

    assert built == ["add", "sub"]
    assert "clean build requested: rebuilding every program" in log
    assert "up to date" not in log
    # The records it leaves are good again for the next ordinary run.
    assert prepare()[0] == []
    assert program_build.record_path(tmp_path / "cases" / "add.soc" / "add.soc.bin").is_file()
