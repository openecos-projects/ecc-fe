"""`ecc-fe handoff`: the manifest a consumer takes a design from."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from fecompiler.cli import commands as cli_commands
from fecompiler.cli import main as cli_main
from fecompiler.cli.handlers import handoff as handoff_handler
from fecompiler.cli.handlers.handoff import (
    HANDOFF_KIND,
    HANDOFF_SCHEMA_VERSION,
    _design_inputs,
    _include_closure,
)
from fecompiler.data.step import StateEnum


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    monkeypatch.delenv("ECOS_SLANG", raising=False)
    # No tool is run in these tests; staleness is controlled explicitly.
    monkeypatch.setattr(cli_commands, "_stale_step_reasons", lambda workspace, steps: {})
    monkeypatch.setattr(cli_commands, "_tool_version", lambda path: "tool 1.0")
    monkeypatch.setattr(handoff_handler.shutil, "which", lambda name: None)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def rtl(tmp_path) -> Path:
    root = tmp_path / "src" / "rtl"
    _write(root / "inc" / "defs.svh", "`define WIDTH 8\n")
    _write(root / "a" / "local.vh", "`define LOCAL 1\n")
    _write(
        root / "a" / "util.sv",
        '`include "defs.svh"\n`include "local.vh"\n// `include "commented.vh"\n'
        "module util_a(input d, output q); assign q = d; endmodule\n",
    )
    _write(root / "b" / "util.sv", "module util_b(input d, output q); assign q = ~d; endmodule\n")
    _write(tmp_path / "src" / "lib" / "helper.v", "module helper(input a, output b); assign b = a; endmodule\n")
    _write(
        root / "top.sv",
        "module counter(input clk, input rst_n, input d, output q);\n"
        "  util_a ua(.d(d), .q(q));\nendmodule\n",
    )
    _write(
        root / "design.f",
        "top.sv a/util.sv b/util.sv\n+incdir+inc\n+define+WITH_FEATURE\n-y ../lib\n+libext+.v\n-Wno-fatal\n",
    )
    return root


def _call(capsys, *args: str) -> tuple[int, list[dict]]:
    exit_code = cli_main.run([*args, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)["records"]


def _finish(run_dir: Path, **overrides: StateEnum) -> None:
    path = run_dir / "home" / "flow.json"
    flow = json.loads(path.read_text(encoding="utf-8"))
    for step in flow["steps"]:
        step["state"] = overrides.get(step["name"], StateEnum.Success).value
        step["provenance"] = {"signature": f"sig-{step['name']}", "input_fingerprint": "in"}
    path.write_text(json.dumps(flow), encoding="utf-8")


@pytest.fixture
def project(tmp_path, rtl, capsys) -> Path:
    directory = tmp_path / "project"
    assert _call(
        capsys, "init", str(directory), "--top", "counter", "--filelist", str(rtl / "design.f")
    )[0] == 0
    assert _call(capsys, "run", "--project", str(directory), "--to", "prepare")[0] in (0, 1)
    _finish(directory / "runs" / "default")
    return directory


def _manifest(project: Path, *parts: str) -> dict:
    return json.loads(project.joinpath(*parts, "handoff.json").read_text(encoding="utf-8"))


# --- the manifest -----------------------------------------------------------


def test_manifest_describes_the_design_by_reference(project, rtl, capsys) -> None:
    exit_code, records = _call(capsys, "handoff", "--project", str(project))

    assert exit_code == 0
    record = records[0]
    assert (record["status"], record["bundled"], record["top"]) == ("complete", False, "counter")
    manifest = _manifest(project, "handoff", "default")
    assert manifest["schema_version"] == HANDOFF_SCHEMA_VERSION
    assert manifest["kind"] == HANDOFF_KIND
    assert manifest["generator"]["name"] == "ecc-fe"
    assert manifest["source"] == {
        "project": str(project), "run": "default", "workspace": str(project / "runs" / "default"),
    }
    assert manifest["design"] == {
        "name": "project", "kind": "rtl", "top": "counter",
        "clock_port": "clk", "clock_port_source": "detected",
        "frequency_mhz": 100.0, "parameters": [], "sdc": None,
    }
    design = manifest["rtl"]
    assert [entry["path"] for entry in design["files"]] == [
        str(rtl / "top.sv"), str(rtl / "a" / "util.sv"), str(rtl / "b" / "util.sv"),
    ]
    assert all(len(entry["sha256"]) == 64 for entry in design["files"])
    # Headers reached through `include are found although the filelist names none.
    assert sorted(entry["path"] for entry in design["headers"]) == [
        str(rtl / "a" / "local.vh"), str(rtl / "inc" / "defs.svh"),
    ]
    # The directories the frontend searches implicitly are written out.
    assert design["incdirs"] == [str(rtl / "inc"), str(rtl), str(rtl / "a"), str(rtl / "b")]
    assert design["defines"] == ["WITH_FEATURE"]
    assert design["libdirs"] == [str(rtl.parent / "lib")]
    assert design["libexts"] == [".v"]
    assert design["not_passed_on"] == ["-Wno-fatal"]
    assert design["unresolved_includes"] == []
    assert manifest["conventions"] == {
        "frontend_defines": ["SYNTHESIS"], "default_timescale": "1ns/1ps",
    }
    assert manifest["standalone_check"]["status"] == "skipped"
    assert manifest["constraints"] == {"sdc": None}


def test_manifest_records_what_the_flow_concluded(project, capsys) -> None:
    assert _call(capsys, "handoff", "--project", str(project))[0] == 0

    flow = _manifest(project, "handoff", "default")["flow"]

    assert (flow["status"], flow["state"], flow["blockers"]) == ("complete", "success", [])
    assert [step["step"] for step in flow["steps"]] == ["prepare", "review", "elab", "lint"]
    assert flow["steps"][2]["signature"] == "sig-elab"
    assert all(step["state"] == "success" and step["stale"] is False for step in flow["steps"])


def test_generated_filelist_carries_incdirs_and_defines(project, rtl, capsys) -> None:
    assert _call(capsys, "handoff", "--project", str(project))[0] == 0

    lines = (project / "handoff" / "default" / "design.f").read_text().splitlines()

    assert f"+incdir+{rtl / 'inc'}" in lines
    assert "+define+WITH_FEATURE" in lines
    assert f"-y {rtl.parent / 'lib'}" in lines
    assert "+libext+.v" in lines
    assert lines[-3:] == [str(rtl / "top.sv"), str(rtl / "a" / "util.sv"), str(rtl / "b" / "util.sv")]


# --- bundle -----------------------------------------------------------------


def test_bundle_is_self_contained_and_keeps_the_layout(project, rtl, tmp_path, capsys) -> None:
    out = tmp_path / "out"

    exit_code, records = _call(capsys, "handoff", "--project", str(project), "--bundle", "-o", str(out))

    assert exit_code == 0 and records[0]["bundled"] is True
    design = json.loads((out / "handoff.json").read_text())["rtl"]
    assert design["root"] == "rtl"
    # Two files named util.sv stay apart; the library module travels along.
    assert [entry["path"] for entry in design["files"]] == [
        "rtl/rtl/top.sv", "rtl/rtl/a/util.sv", "rtl/rtl/b/util.sv",
    ]
    assert sorted(str(path.relative_to(out)) for path in (out / "rtl").rglob("*") if path.is_file()) == [
        "rtl/lib/helper.v", "rtl/rtl/a/local.vh", "rtl/rtl/a/util.sv",
        "rtl/rtl/b/util.sv", "rtl/rtl/inc/defs.svh", "rtl/rtl/top.sv",
    ]
    assert design["incdirs"] == ["rtl/rtl/inc", "rtl/rtl", "rtl/rtl/a", "rtl/rtl/b"]
    assert design["libdirs"] == ["rtl/lib"]
    assert (out / "rtl" / "rtl" / "a" / "util.sv").read_text() == (rtl / "a" / "util.sv").read_text()
    # Nothing in the bundle points back at where the sources came from.
    shutil.rmtree(rtl.parent)
    text = (out / "design.f").read_text()
    assert str(rtl.parent) not in text
    assert all(not entry["path"].startswith("/") for entry in design["files"] + design["headers"])


def test_bundle_replaces_a_previous_bundle(project, rtl, tmp_path, capsys) -> None:
    out = tmp_path / "out"
    assert _call(capsys, "handoff", "--project", str(project), "--bundle", "-o", str(out))[0] == 0
    _write(out / "rtl" / "stale.sv", "module stale; endmodule\n")

    assert _call(capsys, "handoff", "--project", str(project), "--bundle", "-o", str(out))[0] == 0

    assert not (out / "rtl" / "stale.sv").exists()


# --- readiness --------------------------------------------------------------


def test_unfinished_or_failed_run_is_refused(project, capsys) -> None:
    _finish(project / "runs" / "default", lint=StateEnum.Incomplete, elab=StateEnum.Unstart)

    exit_code, records = _call(capsys, "handoff", "--project", str(project))

    assert exit_code == 1
    assert records[0]["error"] == "handoff_not_ready"
    assert records[0]["blockers"] == [
        {"step": "elab", "reason": "not finished (unstart)"},
        {"step": "lint", "reason": "failed"},
    ]
    assert not (project / "handoff").exists()


def test_stale_run_is_refused(project, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli_commands, "_stale_step_reasons", lambda workspace, steps: {"lint": "step inputs changed"}
    )

    exit_code, records = _call(capsys, "handoff", "--project", str(project))

    assert exit_code == 1
    assert records[0]["blockers"] == [{"step": "lint", "reason": "stale: step inputs changed"}]


def test_allow_incomplete_writes_a_manifest_that_says_so(project, capsys) -> None:
    _finish(project / "runs" / "default", lint=StateEnum.Incomplete)

    exit_code, records = _call(capsys, "handoff", "--project", str(project), "--allow-incomplete")

    assert exit_code == 0
    assert records[0]["status"] == "incomplete"
    flow = _manifest(project, "handoff", "default")["flow"]
    assert (flow["status"], flow["state"]) == ("incomplete", "failed")
    assert flow["blockers"] == [{"step": "lint", "reason": "failed"}]


def test_missing_run_is_reported(tmp_path, capsys) -> None:
    exit_code, records = _call(capsys, "handoff", "--workspace", str(tmp_path / "nowhere"))

    assert exit_code == 1
    assert records[0]["error"] == "workspace_not_found"


# --- standalone check -------------------------------------------------------


def _fake_slang(tmp_path: Path, monkeypatch, *, returncode: int) -> Path:
    log = tmp_path / "slang-args.txt"
    script = _write(
        tmp_path / "bin" / "slang",
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > {log}\n'
        + ('echo "top.sv:1:1: error: boom"\n' if returncode else 'echo "Build succeeded"\n')
        + f"exit {returncode}\n",
    )
    script.chmod(0o755)
    monkeypatch.setenv("ECOS_SLANG", str(script))
    return log


def test_design_is_elaborated_from_the_manifest_alone(project, rtl, tmp_path, monkeypatch, capsys) -> None:
    arguments = _fake_slang(tmp_path, monkeypatch, returncode=0)

    exit_code, records = _call(capsys, "handoff", "--project", str(project))

    assert exit_code == 0
    assert records[0]["standalone_check"] == "pass"
    passed = arguments.read_text().splitlines()
    assert passed[:2] == ["--top", "counter"]
    for expected in ("SYNTHESIS", "WITH_FEATURE", "1ns/1ps", str(rtl / "inc"), str(rtl / "top.sv")):
        assert expected in passed
    check = _manifest(project, "handoff", "default")["standalone_check"]
    assert (check["status"], check["tool"], check["errors"]) == ("pass", "slang", 0)


def test_design_that_does_not_stand_alone_is_refused(project, tmp_path, monkeypatch, capsys) -> None:
    _fake_slang(tmp_path, monkeypatch, returncode=1)

    exit_code, records = _call(capsys, "handoff", "--project", str(project))

    assert exit_code == 1
    assert records[0]["error"] == "handoff_design_incomplete"
    assert records[0]["errors"] == 1
    assert not (project / "handoff" / "default" / "handoff.json").exists()
    assert "error: boom" in (project / "handoff" / "default" / "standalone_check.log").read_text()


def test_no_check_skips_the_elaboration(project, tmp_path, monkeypatch, capsys) -> None:
    arguments = _fake_slang(tmp_path, monkeypatch, returncode=1)

    exit_code, records = _call(capsys, "handoff", "--project", str(project), "--no-check")

    assert exit_code == 0
    assert records[0]["standalone_check"] == "skipped"
    assert not arguments.exists()


# --- what is handed off -----------------------------------------------------


def test_cpu_project_hands_off_the_cpu_and_counts_the_harness(tmp_path) -> None:
    cpu = _write(tmp_path / "cpu" / "core.v", "module cpu_top(input clock, input reset); endmodule\n")
    _write(tmp_path / "cpu" / "cpu.f", "core.v\n")
    _write(tmp_path / "soc" / "top.v", "module ecos_sim_top; endmodule\n")
    _write(tmp_path / "soc" / "soc.f", "top.v\n")
    parameters = {
        "frontend_design_kind": "cpu_core",
        "cpu_filelist": str(tmp_path / "cpu" / "cpu.f"),
        "soc_filelist": str(tmp_path / "soc" / "soc.f"),
        "input_filelist": str(tmp_path / "merged_rtl.f"),
        "cpu_wrapper_top": "cpu_top",
        "Clock": "clk",
    }

    inputs = _design_inputs(parameters)
    design = handoff_handler._design(parameters, {}, inputs)

    assert inputs["sources"] == [cpu]
    assert inputs["excluded"]["soc_harness"]["rtl_files"] == 1
    # The project's clock names the harness port; the CPU's own is detected.
    assert (design["kind"], design["top"], design["clock_port"], design["clock_port_source"]) == (
        "cpu", "cpu_top", "clock", "detected",
    )


def test_a_cpu_is_handed_off_only_on_its_whole_test_suite() -> None:
    blocker = handoff_handler._partial_suite_blocker
    steps = [{"step": "lint"}, {"step": "sim"}]
    cpu = {"frontend_design_kind": "cpu_core", "sim_program_names": ["add"]}

    assert blocker(cpu, steps) == {
        "step": "sim",
        "reason": "ran a selection of the CPU test suite, not all of it",
    }
    assert blocker({**cpu, "sim_build_all_programs": True}, steps) is None
    # A design with its own testbench, or without simulation, has no suite.
    assert blocker({"frontend_design_kind": "generic_rtl"}, steps) is None
    assert blocker(cpu, [{"step": "lint"}]) is None


def test_cpu_workspace_without_a_cpu_filelist_cannot_be_handed_off(tmp_path) -> None:
    with pytest.raises(ValueError, match="no CPU filelist"):
        _design_inputs({"frontend_design_kind": "cpu_core", "input_filelist": str(tmp_path / "m.f")})


def test_clock_port_is_left_open_when_it_cannot_be_told(tmp_path) -> None:
    source = _write(tmp_path / "top.sv", "module top(input clk_a, input clk_b, input d); endmodule\n")
    _write(tmp_path / "design.f", "top.sv\n")
    parameters = {"frontend_design_kind": "generic_rtl", "input_filelist": str(tmp_path / "design.f"), "Top module": "top"}

    design = handoff_handler._design(parameters, {}, _design_inputs(parameters))

    assert (design["clock_port"], design["clock_port_source"]) == (None, None)
    parameters["Clock"] = "clk_b"
    design = handoff_handler._design(parameters, {}, _design_inputs(parameters))
    assert (design["clock_port"], design["clock_port_source"]) == ("clk_b", "parameter")
    assert source.is_file()


def test_unresolved_includes_are_listed(tmp_path) -> None:
    source = _write(tmp_path / "top.sv", '`include "missing.vh"\nmodule top; endmodule\n')

    headers, unresolved = _include_closure([source], [tmp_path])

    assert headers == []
    assert unresolved == [{"include": "missing.vh", "from": str(source)}]


def test_includes_are_followed_through_headers(tmp_path) -> None:
    source = _write(tmp_path / "top.sv", '`include "a.vh"\nmodule top; endmodule\n')
    first = _write(tmp_path / "inc" / "a.vh", '`include "b.vh"\n')
    second = _write(tmp_path / "inc" / "b.vh", '`include "a.vh"\n')  # a cycle

    headers, unresolved = _include_closure([source], [tmp_path / "inc"])

    assert sorted(headers) == [first, second]
    assert unresolved == []


# --- timing constraints -----------------------------------------------------


def _sdc(tmp_path: Path, text: str = "create_clock -period 10 [get_ports clk]\n") -> Path:
    return _write(tmp_path / "src" / "constraints" / "design.sdc", text)


def test_constraints_named_at_handoff_are_described_by_reference(project, tmp_path, capsys) -> None:
    sdc = _sdc(tmp_path)

    exit_code, records = _call(capsys, "handoff", "--project", str(project), "--sdc", str(sdc))

    assert exit_code == 0
    assert records[0]["sdc"] == str(sdc)
    described = _manifest(project, "handoff", "default")["constraints"]["sdc"]
    assert described["path"] == str(sdc)
    assert len(described["sha256"]) == 64
    assert not (project / "handoff" / "default" / "constraints").exists()
    # The field of schema version 1 keeps its documented value.
    assert _manifest(project, "handoff", "default")["design"]["sdc"] is None


def test_a_bundle_takes_the_constraints_along(project, tmp_path, capsys) -> None:
    sdc = _sdc(tmp_path)
    out = tmp_path / "out"

    exit_code, records = _call(
        capsys, "handoff", "--project", str(project), "--bundle", "--sdc", str(sdc),
        "--output", str(out),
    )

    assert exit_code == 0
    assert records[0]["sdc"] == "constraints/design.sdc"
    described = json.loads((out / "handoff.json").read_text(encoding="utf-8"))["constraints"]["sdc"]
    assert described["path"] == "constraints/design.sdc"
    assert (out / "constraints" / "design.sdc").read_text(encoding="utf-8") == sdc.read_text(encoding="utf-8")

    # Without constraints the next handoff does not keep the old copy.
    assert _call(capsys, "handoff", "--project", str(project), "--bundle", "--output", str(out))[0] == 0
    assert json.loads((out / "handoff.json").read_text(encoding="utf-8"))["constraints"]["sdc"] is None
    assert not (out / "constraints").exists()


def test_the_runs_own_setting_is_used_and_resolved_from_the_project(project, tmp_path, capsys) -> None:
    sdc = _write(project / "constraints.sdc", "create_clock -period 5 [get_ports clk]\n")
    parameters_path = project / "runs" / "default" / "home" / "parameters.json"
    parameters = json.loads(parameters_path.read_text(encoding="utf-8"))
    parameters["design_sdc"] = "constraints.sdc"
    parameters_path.write_text(json.dumps(parameters), encoding="utf-8")

    exit_code, records = _call(capsys, "handoff", "--project", str(project))

    assert exit_code == 0
    assert records[0]["sdc"] == str(sdc)

    # The option given now wins over the setting.
    other = _sdc(tmp_path, "create_clock -period 2 [get_ports clk]\n")
    _, records = _call(capsys, "handoff", "--project", str(project), "--sdc", str(other))
    assert records[0]["sdc"] == str(other)


def test_missing_constraints_are_refused_before_anything_is_written(project, tmp_path, capsys) -> None:
    exit_code, records = _call(
        capsys, "handoff", "--project", str(project), "--sdc", str(tmp_path / "gone.sdc")
    )

    assert exit_code == 1
    assert records[0]["error"] == "handoff_sdc_not_found"
    assert records[0]["reason"] == "named by --sdc"
    assert not (project / "handoff" / "default" / "handoff.json").exists()


def test_design_sdc_is_a_parameter_no_step_depends_on(project, capsys) -> None:
    from fecompiler.allflow.steps import step_definitions

    exit_code, records = _call(
        capsys, "param", "set", "design.sdc", "constraints.sdc", "--project", str(project)
    )

    assert exit_code == 0, records
    assert all("design_sdc" not in definition.config_fields for definition in step_definitions())
