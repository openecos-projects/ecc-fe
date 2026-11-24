"""An RTL design that is not a CPU: project creation and the commands around it."""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest

from fecompiler.cli import main as cli_main
from fecompiler.cli.project.relocate import relocate_project
from fecompiler.tools.common.rtl_ownership import classify_rtl_source
from fecompiler.tools.review.structural_probe import _design_wording

_COUNTER = """`include "defs.svh"
module counter(input clk, input [`WIDTH-1:0] d, output logic [`WIDTH-1:0] q);
  always_ff @(posedge clk) q <= d;
endmodule
"""


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)


@pytest.fixture
def rtl(tmp_path) -> Path:
    root = tmp_path / "rtl"
    (root / "inc").mkdir(parents=True)
    (root / "inc" / "defs.svh").write_text("`define WIDTH 8\n", encoding="utf-8")
    (root / "counter.sv").write_text(_COUNTER, encoding="utf-8")
    (root / "extra.v").write_text("module extra; endmodule\n", encoding="utf-8")
    (root / "design.f").write_text("counter.sv extra.v\n+incdir+inc\n", encoding="utf-8")
    return root


def _call(capsys, *args: str) -> tuple[int, list[dict[str, object]]]:
    exit_code = cli_main.run([*args, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)["records"]


@pytest.fixture
def project(tmp_path, rtl, capsys) -> Path:
    directory = tmp_path / "project"
    exit_code, records = _call(
        capsys, "init", str(directory), "--top", "counter", "--filelist", str(rtl / "design.f")
    )
    assert exit_code == 0, records
    return directory


def _flow_steps(workspace: Path) -> list[str]:
    flow = json.loads((workspace / "home" / "flow.json").read_text(encoding="utf-8"))
    return [step["name"] for step in flow["steps"]]


# --- init -----------------------------------------------------------------


def test_filelist_alone_creates_an_rtl_project(project, rtl) -> None:
    template = project / ".ecc-fe" / "template"
    parameters = json.loads((template / "home" / "parameters.json").read_text())
    config = tomllib.loads((project / "ecc-fe.toml").read_text(encoding="utf-8"))

    assert _flow_steps(template) == ["prepare", "review", "elab", "lint"]
    assert parameters["frontend_design_kind"] == "generic_rtl"
    assert parameters["Top module"] == "counter"
    # The filelist is used where it is, so edits are seen by the next run.
    assert parameters["input_filelist"] == str(rtl / "design.f")
    assert config["design"]["kind"] == "rtl"
    assert config["frontend"] == {}


def test_repeated_rtl_files_create_an_rtl_project(tmp_path, rtl, capsys) -> None:
    directory = tmp_path / "from-files"

    exit_code, records = _call(
        capsys,
        "init", str(directory), "--kind", "rtl", "--top", "counter",
        "--rtl", str(rtl / "counter.sv"), "--rtl", str(rtl / "extra.v"),
    )

    assert exit_code == 0
    assert records[0]["design_kind"] == "rtl"
    template = directory / ".ecc-fe" / "template"
    generated = (template / "origin" / ".rtl_sources.f").read_text(encoding="utf-8")
    assert str(rtl / "counter.sv") in generated and str(rtl / "extra.v") in generated


def test_cpu_projects_are_unchanged(tmp_path, capsys) -> None:
    directory = tmp_path / "cpu"

    exit_code, records = _call(capsys, "init", str(directory))

    assert exit_code == 0
    assert records[0]["design_kind"] == "cpu"
    assert _flow_steps(directory / ".ecc-fe" / "template")[-1] == "sim"
    config = tomllib.loads((directory / "ecc-fe.toml").read_text(encoding="utf-8"))
    assert config["design"]["kind"] == "cpu"


@pytest.mark.parametrize(
    ("options", "error"),
    [
        (["--kind", "rtl"], "init_input_required"),
        (["--kind", "rtl", "--rtl", "RTL/counter.sv", "--filelist", "RTL/design.f"], "init_input_required"),
        (["--filelist", "RTL/design.f", "--core-id", "picorv32"], "init_option_conflict"),
        (["--kind", "rtl", "--rtl", "RTL/counter.sv", "--cpu-top-module", "x"], "init_option_conflict"),
        (["--kind", "cpu", "--filelist", "RTL/design.f"], "init_option_conflict"),
        (["--kind", "cpu", "--rtl", "RTL/counter.sv", "--rtl", "RTL/extra.v"], "init_option_conflict"),
        (["--kind", "soc", "--rtl", "RTL/counter.sv"], "invalid_kind"),
    ],
)
def test_init_usage_errors(tmp_path, rtl, capsys, options, error) -> None:
    directory = tmp_path / "bad"
    options = [option.replace("RTL", str(rtl)) for option in options]

    exit_code, records = _call(capsys, "init", str(directory), "--top", "counter", *options)

    assert exit_code == 2
    assert records[0]["error"] == error
    assert not directory.exists()


@pytest.mark.parametrize(
    ("top", "source", "code"),
    [
        ("nosuch", "design.f", "top_module_not_found"),
        ("counter", "absent.f", "filelist_not_found"),
    ],
)
def test_init_checks_the_design_before_creating_anything(
    tmp_path, rtl, capsys, top, source, code
) -> None:
    directory = tmp_path / "bad"

    exit_code, records = _call(
        capsys, "init", str(directory), "--top", top, "--filelist", str(rtl / source)
    )

    assert exit_code == 1
    assert records[0]["error"] == "init_failed"
    assert records[1]["kind"] == "init_issue"
    assert records[1]["code"] == code
    assert not directory.exists()


def test_init_reports_a_listed_file_that_is_missing(tmp_path, rtl, capsys) -> None:
    (rtl / "design.f").write_text("counter.sv gone.sv\n", encoding="utf-8")

    exit_code, records = _call(
        capsys, "init", str(tmp_path / "bad"), "--top", "counter", "--filelist", str(rtl / "design.f")
    )

    assert exit_code == 1
    assert records[1]["code"] == "rtl_file_not_found"
    assert str(rtl / "gone.sv") in records[1]["message"]


def test_failed_cpu_init_from_rtl_points_at_kind_rtl(tmp_path, rtl, capsys) -> None:
    exit_code, records = _call(
        capsys, "init", str(tmp_path / "bad"), "--top", "counter", "--rtl", str(rtl / "counter.sv")
    )

    assert exit_code == 1
    assert records[0]["error"] == "init_failed"
    assert "--kind rtl" in records[0]["hint"]


# --- commands follow the design's flow --------------------------------------


def test_check_asks_only_for_the_tools_the_flow_uses(project, capsys) -> None:
    _, records = _call(capsys, "check", "--project", str(project))

    checks = {record.get("check") for record in records}
    assert {"slang", "verilator", "yosys"} <= checks
    assert not checks & {"cxx", "make", "riscv"}


def test_cpu_project_check_still_asks_for_the_simulation_tools(tmp_path, capsys) -> None:
    directory = tmp_path / "cpu"
    assert _call(capsys, "init", str(directory))[0] == 0

    _, records = _call(capsys, "check", "--project", str(directory))

    assert {"cxx", "make", "riscv"} <= {record.get("check") for record in records}


def test_parameters_of_absent_steps_are_hidden_and_refused(project, capsys) -> None:
    _, listed = _call(capsys, "param", "list", "--all", "--project", str(project))
    names = [record["param"] for record in listed]
    assert "design.frequency_mhz" in names and "lint.profile" in names
    assert not [name for name in names if name.startswith("sim.")]

    for command in (("show", "sim.compile_preset"), ("set", "sim.compile_preset", "speed")):
        exit_code, records = _call(capsys, "param", *command, "--project", str(project))
        assert exit_code == 2
        assert records[0]["error"] == "parameter_not_applicable"
    config = tomllib.loads((project / "ecc-fe.toml").read_text(encoding="utf-8"))
    assert config["params"] == {}
    assert not [name for name in config["defaults"] if name.startswith("sim.")]


@pytest.mark.parametrize("selector", [["--only", "sim"], ["--to", "sim"], ["--from", "sim"]])
def test_selecting_a_step_the_flow_lacks_is_a_usage_error(project, capsys, selector) -> None:
    exit_code, records = _call(capsys, "run", "--project", str(project), *selector)

    assert exit_code == 2
    assert records[0]["error"] == "step_not_in_flow"
    assert records[0]["expected"] == ["prepare", "review", "elab", "lint"]
    assert not (project / "runs" / "default").exists()


# --- reports ----------------------------------------------------------------


def test_design_headers_belong_to_the_design(tmp_path, rtl) -> None:
    manifest = tmp_path / "prepared_inputs.json"
    manifest.write_text(
        json.dumps({"header_files": [], "incdirs": [str(rtl / "inc")], "rtl_files": []}),
        encoding="utf-8",
    )
    workspace = {
        "frontend_design_kind": "generic_rtl",
        "input_filelist": str(rtl / "design.f"),
        "prepared_manifest": str(manifest),
    }
    outside = tmp_path / "vendor" / "lib.svh"

    assert classify_rtl_source(rtl / "inc" / "defs.svh", workspace=workspace) == "design"
    assert classify_rtl_source(rtl / "local.vh", workspace=workspace) == "design"
    assert classify_rtl_source(outside, workspace=workspace) == "unknown"
    # A CPU workspace keeps its own rules.
    assert classify_rtl_source(rtl / "inc" / "defs.svh", workspace={}) == "unknown"


def test_design_scope_issues_do_not_talk_about_a_cpu() -> None:
    issue = {
        "title": "Yosys could not resolve the CPU top hierarchy",
        "message": "The CPU RTL cannot pass the Yosys Verilog frontend in its current form.",
        "recommendation": "Confirm the selected CPU wrapper/top exists in the CPU filelist.",
        "line": 3,
    }

    reworded = _design_wording(issue)

    assert "CPU" not in " ".join(str(value) for value in reworded.values())
    assert reworded["line"] == 3
    assert issue["title"].startswith("Yosys could not resolve the CPU")


# --- relocation -------------------------------------------------------------


def test_moved_project_has_its_own_paths_rewritten(tmp_path, capsys) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sources = project / "rtl"
    sources.mkdir()
    (sources / "counter.sv").write_text("module counter; endmodule\n", encoding="utf-8")
    (sources / "design.f").write_text("counter.sv\n", encoding="utf-8")
    outside = tmp_path / "shared.sv"
    outside.write_text("module shared; endmodule\n", encoding="utf-8")
    assert _call(
        capsys, "init", str(project), "--top", "counter", "--filelist", str(sources / "design.f")
    )[0] == 0
    location = json.loads((project / ".ecc-fe" / "location.json").read_text())
    assert location == {"project_dir": str(project)}

    moved = tmp_path / "elsewhere" / "renamed"
    moved.parent.mkdir()
    shutil.move(str(project), str(moved))
    assert relocate_project(str(moved)) == {
        "from": str(project),
        "to": str(moved),
        "workspaces": [str(moved / ".ecc-fe" / "template")],
    }

    parameters = json.loads(
        (moved / ".ecc-fe" / "template" / "home" / "parameters.json").read_text()
    )
    assert parameters["input_filelist"] == str(moved / "rtl" / "design.f")
    assert json.loads((moved / ".ecc-fe" / "location.json").read_text()) == {
        "project_dir": str(moved)
    }
    # Nothing left to do the second time.
    assert relocate_project(str(moved)) is None


def test_moved_project_is_repaired_by_the_next_command(tmp_path, rtl, capsys) -> None:
    project = tmp_path / "project"
    shutil.copytree(rtl, project / "rtl")
    assert _call(
        capsys, "init", str(project), "--top", "counter",
        "--filelist", str(project / "rtl" / "design.f"),
    )[0] == 0
    moved = tmp_path / "moved"
    shutil.move(str(project), str(moved))

    assert cli_main.run(["param", "list", "--project", str(moved), "--json"]) == 0

    captured = capsys.readouterr()
    assert f"project moved from {project}" in captured.err
    parameters = json.loads(
        (moved / ".ecc-fe" / "template" / "home" / "parameters.json").read_text()
    )
    assert parameters["input_filelist"] == str(moved / "rtl" / "design.f")


def test_project_without_a_location_record_gets_one_and_is_left_alone(
    project,
) -> None:
    record = project / ".ecc-fe" / "location.json"
    before = (project / ".ecc-fe" / "template" / "home" / "parameters.json").read_text()
    record.unlink()

    assert relocate_project(str(project)) is None

    assert json.loads(record.read_text()) == {"project_dir": str(project)}
    assert (project / ".ecc-fe" / "template" / "home" / "parameters.json").read_text() == before
