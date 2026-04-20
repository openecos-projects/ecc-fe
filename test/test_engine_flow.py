#!/usr/bin/env python
"""Tests for fecompiler.engine.flow — EngineFlow and _format_runtime."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow, _format_runtime
from fecompiler.allflow.builder import DEFAULT_FLOW_STEPS

FIRST_STEP, FIRST_TOOL = DEFAULT_FLOW_STEPS[0]


# ── helpers ────────────────────────────────────────────────────────────────────

def _build_engine(tmp_path: Path) -> tuple[EngineFlow, dict]:
    # provide a minimal valid RTL so the verilator sim step can lint-pass
    rtl = tmp_path / "chip_top.v"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws"))
    engine = EngineFlow(workspace=ws)
    if not engine.has_init():
        engine.init_default_steps()
        engine.load()
    engine.create_step_workspaces()
    return engine, ws


# ── _format_runtime ────────────────────────────────────────────────────────────

def test_format_runtime_zero():      assert _format_runtime(0) == "00:00:00"
def test_format_runtime_sub_second():assert _format_runtime(0.3) == "00:00:00"
def test_format_runtime_one_minute():assert _format_runtime(60) == "00:01:00"
def test_format_runtime_one_hour():  assert _format_runtime(3600) == "01:00:00"
def test_format_runtime_complex():   assert _format_runtime(3661) == "01:01:01"
def test_format_runtime_negative():  assert _format_runtime(-5) == "00:00:00"


# ── has_init ───────────────────────────────────────────────────────────────────

def test_has_init_false_on_fresh_workspace(tmp_path):
    # create_workspace already writes a full flow.json, so has_init is True
    spec = CreateWorkspaceData(directory=str(tmp_path / "ws"), parameters={"Design": "d"})
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws"))
    engine = EngineFlow(workspace=ws)
    assert engine.has_init() is True


def test_has_init_true_after_init_default_steps(tmp_path):
    engine, _ = _build_engine(tmp_path)
    assert engine.has_init() is True


# ── init_default_steps ─────────────────────────────────────────────────────────

def test_init_default_steps_creates_all_steps(tmp_path):
    engine, _ = _build_engine(tmp_path)
    assert len(engine.flow["steps"]) == len(DEFAULT_FLOW_STEPS)


def test_init_default_steps_all_unstart(tmp_path):
    spec = CreateWorkspaceData(directory=str(tmp_path / "ws"), parameters={"Design": "d"})
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws"))
    engine = EngineFlow(workspace=ws)
    engine.init_default_steps()
    for step in engine.flow["steps"]:
        assert step["state"] == "Unstart"


# ── get_step ───────────────────────────────────────────────────────────────────

def test_get_step_returns_matching(tmp_path):
    engine, _ = _build_engine(tmp_path)
    step = engine.get_step(FIRST_STEP, FIRST_TOOL)
    assert step is not None and step["name"] == FIRST_STEP


def test_get_step_returns_none_for_unknown(tmp_path):
    engine, _ = _build_engine(tmp_path)
    assert engine.get_step("ghost", "ecc") is None


# ── set_state ──────────────────────────────────────────────────────────────────

def test_set_state_updates_step(tmp_path):
    engine, _ = _build_engine(tmp_path)
    ok = engine.set_state(name=FIRST_STEP, tool=FIRST_TOOL, state=StateEnum.Ongoing)
    assert ok and engine.get_step(FIRST_STEP, FIRST_TOOL)["state"] == "Ongoing"


def test_set_state_returns_false_for_unknown(tmp_path):
    engine, _ = _build_engine(tmp_path)
    assert engine.set_state(name="ghost", tool="ecc", state=StateEnum.Success) is False


def test_set_state_persists_to_disk(tmp_path):
    engine, ws = _build_engine(tmp_path)
    engine.set_state(name=FIRST_STEP, tool=FIRST_TOOL, state=StateEnum.Success)
    data = json.loads(Path(ws["flow_path"]).read_text())
    s = next(x for x in data["steps"] if x["name"] == FIRST_STEP)
    assert s["state"] == "Success"


# ── clear_states ───────────────────────────────────────────────────────────────

def test_clear_states_resets_all(tmp_path):
    engine, _ = _build_engine(tmp_path)
    engine.set_state(name=FIRST_STEP, tool=FIRST_TOOL, state=StateEnum.Success, runtime="00:01:00")
    engine.clear_states()
    for step in engine.flow["steps"]:
        assert step["state"] == "Unstart" and step["runtime"] == ""


# ── is_flow_success ────────────────────────────────────────────────────────────

def test_is_flow_success_false_when_unstart(tmp_path):
    engine, _ = _build_engine(tmp_path)
    assert engine.is_flow_success() is False


def test_is_flow_success_true_when_all_success(tmp_path):
    engine, _ = _build_engine(tmp_path)
    for name, tool in DEFAULT_FLOW_STEPS:
        engine.set_state(name=name, tool=tool, state=StateEnum.Success)
    assert engine.is_flow_success() is True


# ── create_step_workspaces ─────────────────────────────────────────────────────

def test_create_step_workspaces_returns_summary(tmp_path):
    engine, _ = _build_engine(tmp_path)
    result = engine.create_step_workspaces()
    assert len(result) == len(DEFAULT_FLOW_STEPS)
    for entry in result:
        assert "step" in entry and "tool" in entry and "directory" in entry


def test_create_step_workspaces_dirs_on_disk(tmp_path):
    engine, ws = _build_engine(tmp_path)
    project = Path(ws["directory"])
    for name, tool in DEFAULT_FLOW_STEPS:
        assert (project / f"{name}_{tool}").is_dir()


# ── run_step ───────────────────────────────────────────────────────────────────

def test_run_step_returns_success_for_stub(tmp_path):
    engine, _ = _build_engine(tmp_path)
    state = engine.run_step(FIRST_STEP)
    assert state == StateEnum.Success


def test_run_step_invalid_for_unknown(tmp_path):
    engine, _ = _build_engine(tmp_path)
    assert engine.run_step("ghost_step") == StateEnum.Invalid


def test_run_step_skips_already_successful(tmp_path):
    engine, _ = _build_engine(tmp_path)
    engine.run_step(FIRST_STEP)
    state = engine.run_step(FIRST_STEP, rerun=False)
    assert state == StateEnum.Success


def test_run_step_updates_state(tmp_path):
    engine, _ = _build_engine(tmp_path)
    engine.run_step(FIRST_STEP)
    assert engine.get_step(FIRST_STEP, FIRST_TOOL)["state"] == "Success"


# ── run_all ────────────────────────────────────────────────────────────────────

def test_run_all_succeeds(tmp_path):
    engine, _ = _build_engine(tmp_path)
    ok, reports = engine.run_all()
    assert ok is True
    assert len(reports) == len(DEFAULT_FLOW_STEPS)
    for r in reports:
        assert r["state"] == "Success"


def test_run_all_with_rerun(tmp_path):
    engine, _ = _build_engine(tmp_path)
    engine.run_all()
    ok, _ = engine.run_all(rerun=True)
    assert ok is True


# ── load ───────────────────────────────────────────────────────────────────────

def test_load_restores_state_from_disk(tmp_path):
    engine, ws = _build_engine(tmp_path)
    engine.set_state(name=FIRST_STEP, tool=FIRST_TOOL, state=StateEnum.Success)
    engine2 = EngineFlow(workspace=load_workspace(ws["directory"]))
    engine2.load()
    assert engine2.get_step(FIRST_STEP, FIRST_TOOL)["state"] == "Success"


def test_sim_compile_failure_is_incomplete(tmp_path):
    bad_rtl = tmp_path / "bad_top.v"
    bad_rtl.write_text("module chip_top( ; endmodule\n", encoding="utf-8")
    tb = tmp_path / "tb.cpp"
    tb.write_text("int main(){return 0;}\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_bad"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(bad_rtl),
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_bad"))
    ws["testbench"] = str(tb)

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("sim", rerun=True)

    assert state == StateEnum.Incomplete
    assert engine.get_step("sim", "verilator")["state"] == "Incomplete"


def test_prepare_merges_cpu_and_soc_filelists(tmp_path):
    cpu_root = tmp_path / "cpu"
    soc_root = tmp_path / "soc"
    cpu_inc = cpu_root / "include"
    soc_inc = soc_root / "include"
    cpu_root.mkdir()
    soc_root.mkdir()
    cpu_inc.mkdir()
    soc_inc.mkdir()

    (cpu_root / "cpu_top.sv").write_text("module cpu_top(); endmodule\n", encoding="utf-8")
    (soc_root / "soc_top.v").write_text("module soc_top(); endmodule\n", encoding="utf-8")
    (cpu_root / "filelist.cpu.f").write_text(
        "+incdir+include\n+define+CPU_CFG=1\ncpu_top.sv\n",
        encoding="utf-8",
    )
    (soc_root / "filelist.soc.f").write_text(
        "+incdir+include\n+define+SOC_CFG=1\nsoc_top.v\n",
        encoding="utf-8",
    )

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_prepare"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        cpu_filelist=str(cpu_root / "filelist.cpu.f"),
        soc_filelist=str(soc_root / "filelist.soc.f"),
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_prepare"))

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("prepare", rerun=True)

    merged = Path(ws["directory"]) / "prepare_fe" / "output" / "merged_rtl.f"
    manifest = Path(ws["directory"]) / "prepare_fe" / "output" / "prepared_inputs.json"
    lines = [l.strip() for l in merged.read_text(encoding="utf-8").splitlines() if l.strip()]
    prepared = json.loads(manifest.read_text(encoding="utf-8"))

    assert state == StateEnum.Success
    assert len(lines) == 2
    assert ws["prepared_manifest"] == str(manifest)
    assert set(prepared["rtl_files"]) == set(lines)
    assert set(prepared["incdirs"]) == {str(cpu_inc.resolve()), str(soc_inc.resolve())}
    assert prepared["defines"] == ["CPU_CFG=1", "SOC_CFG=1"]


def test_prepare_supports_nested_filelist_and_multi_tokens(tmp_path):
    cpu_root = tmp_path / "cpu"
    sub_root = cpu_root / "sub"
    inc_a = cpu_root / "inc_a"
    inc_b = cpu_root / "inc_b"
    cpu_root.mkdir()
    sub_root.mkdir()
    inc_a.mkdir()
    inc_b.mkdir()

    (cpu_root / "cpu_top.sv").write_text("module cpu_top(); endmodule\n", encoding="utf-8")
    (sub_root / "sub_top.v").write_text("module sub_top(); endmodule\n", encoding="utf-8")
    (cpu_root / "nested.f").write_text(
        "+incdir+inc_b\n+define+SUB_CFG=1\nsub/sub_top.v\n",
        encoding="utf-8",
    )
    (cpu_root / "filelist.cpu.f").write_text(
        "+incdir+inc_a+inc_b\n+define+CPU_CFG=1+SUB_CFG=1\n-f nested.f\ncpu_top.sv\n",
        encoding="utf-8",
    )

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_prepare_nested"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        cpu_filelist=str(cpu_root / "filelist.cpu.f"),
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_prepare_nested"))

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("prepare", rerun=True)

    manifest = Path(ws["directory"]) / "prepare_fe" / "output" / "prepared_inputs.json"
    prepared = json.loads(manifest.read_text(encoding="utf-8"))

    assert state == StateEnum.Success
    assert len(prepared["rtl_files"]) == 2
    assert set(prepared["incdirs"]) == {str(inc_a.resolve()), str(inc_b.resolve())}
    assert prepared["defines"] == ["CPU_CFG=1", "SUB_CFG=1"]


def test_sim_supports_extra_cpp_flags_and_runtime_args(tmp_path, monkeypatch):
    rtl = tmp_path / "chip_top.v"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")
    tb = tmp_path / "tb_main.cpp"
    helper = tmp_path / "tb_helper.cpp"
    inc = tmp_path / "include"
    inc.mkdir()
    tb.write_text("int main(int argc, char** argv){ return 0; }\n", encoding="utf-8")
    helper.write_text("int helper(){return 0;}\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_sim_opts"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
        testbench=str(tb),
        sim_cpp_sources=[str(helper)],
        sim_cflags=[f"-I{inc}", "-O2"],
        sim_ldflags=["-lm"],
        sim_run_args=["--image", "tests/out/min2.soc.bin", "--max-cycles", "100"],
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_sim_opts"))

    run_calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True):
        run_calls.append(list(cmd))
        if "--binary" in cmd:
            sim_bin = Path(cmd[cmd.index("-o") + 1])
            sim_bin.parent.mkdir(parents=True, exist_ok=True)
            sim_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            sim_bin.chmod(0o755)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("sim", rerun=True)

    compile_cmd = next(c for c in run_calls if "--binary" in c)
    simulate_cmd = next(c for c in run_calls if "--binary" not in c)

    assert state == StateEnum.Success
    assert str(tb.resolve()) in compile_cmd
    assert str(helper.resolve()) in compile_cmd
    assert "-CFLAGS" in compile_cmd
    assert f"-I{inc}" in compile_cmd[compile_cmd.index("-CFLAGS") + 1]
    assert "-LDFLAGS" in compile_cmd
    assert "-lm" in compile_cmd[compile_cmd.index("-LDFLAGS") + 1]
    assert simulate_cmd[-4:] == ["--image", "tests/out/min2.soc.bin", "--max-cycles", "100"]
