#!/usr/bin/env python
"""Tests for fecompiler.data.workspace."""

from __future__ import annotations

import json
from pathlib import Path

from fecompiler.data.workspace import (
    CreateWorkspaceData,
    create_workspace,
    load_workspace,
    load_flow,
    save_flow,
)
from fecompiler.allflow.builder import DEFAULT_FLOW_STEPS


def _make_spec(tmp_path: Path, **kwargs) -> CreateWorkspaceData:
    return CreateWorkspaceData(
        directory=str(tmp_path / "ws"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        **kwargs,
    )


def test_create_workspace_makes_required_dirs(tmp_path):
    create_workspace(_make_spec(tmp_path))
    for d in ("origin", "log", "home"):
        assert (tmp_path / "ws" / d).is_dir()


def test_create_workspace_writes_home_files(tmp_path):
    create_workspace(_make_spec(tmp_path))
    home = tmp_path / "ws" / "home"
    assert (home / "parameters.json").exists()
    assert (home / "flow.json").exists()
    assert (home / "home.json").exists()


def test_create_workspace_flow_has_all_steps(tmp_path):
    create_workspace(_make_spec(tmp_path))
    flow = json.loads((tmp_path / "ws" / "home" / "flow.json").read_text())
    assert [s["name"] for s in flow["steps"]] == [n for n, _ in DEFAULT_FLOW_STEPS]


def test_create_workspace_writes_parameters(tmp_path):
    create_workspace(_make_spec(tmp_path))
    params = json.loads((tmp_path / "ws" / "home" / "parameters.json").read_text())
    assert params["Design"] == "chip"


def test_load_workspace_returns_dict(tmp_path):
    create_workspace(_make_spec(tmp_path))
    ws = load_workspace(str(tmp_path / "ws"))
    assert ws is not None
    assert ws["design"] == "chip"
    assert ws["top_module"] == "chip_top"


def test_load_workspace_missing_returns_none(tmp_path):
    assert load_workspace(str(tmp_path / "does_not_exist")) is None


def test_load_flow_returns_dict(tmp_path):
    create_workspace(_make_spec(tmp_path))
    flow = load_flow(tmp_path / "ws" / "home" / "flow.json")
    assert "steps" in flow


def test_save_flow_persists(tmp_path):
    create_workspace(_make_spec(tmp_path))
    path = tmp_path / "ws" / "home" / "flow.json"
    flow = load_flow(path)
    flow["steps"][0]["state"] = "Success"
    save_flow(path, flow)
    assert load_flow(path)["steps"][0]["state"] == "Success"


def test_create_workspace_creates_sdc(tmp_path):
    create_workspace(_make_spec(tmp_path))
    sdcs = list((tmp_path / "ws" / "origin").glob("*.sdc"))
    assert len(sdcs) == 1


def test_create_workspace_copies_verilog(tmp_path):
    rtl = tmp_path / "design.v"
    rtl.write_text("module chip_top(); endmodule\n")
    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
    )
    create_workspace(spec)
    assert (tmp_path / "ws" / "origin" / "design.v").exists()


def test_create_workspace_default_design_from_dir_name(tmp_path):
    spec = CreateWorkspaceData(directory=str(tmp_path / "my_chip"))
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "my_chip"))
    assert ws["design"] == "my_chip"


def test_create_workspace_persists_sim_options(tmp_path):
    tb = tmp_path / "tb.cpp"
    helper = tmp_path / "helper.cpp"
    tb.write_text("int main(){return 0;}\n", encoding="utf-8")
    helper.write_text("int helper(){return 0;}\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        testbench=str(tb),
        sim_cpp_sources=[str(helper)],
        sim_cflags=["-I/tmp/inc", "-O2"],
        sim_ldflags=["-lm"],
        sim_run_args=["--image", "tests/out/min2.soc.bin"],
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws"))

    assert ws["testbench"] == str(tb.resolve())
    assert ws["sim_cpp_sources"] == [str(helper.resolve())]
    assert ws["sim_cflags"] == ["-I/tmp/inc", "-O2"]
    assert ws["sim_ldflags"] == ["-lm"]
    assert ws["sim_run_args"] == ["--image", "tests/out/min2.soc.bin"]
