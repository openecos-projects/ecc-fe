#!/usr/bin/env python
"""Tests for fecompiler.engine.flow — EngineFlow and _format_runtime."""

from __future__ import annotations

import json
from pathlib import Path

from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow, _format_runtime
from fecompiler.allflow.builder import DEFAULT_FLOW_STEPS

FIRST_STEP, FIRST_TOOL = DEFAULT_FLOW_STEPS[0]


# ── helpers ────────────────────────────────────────────────────────────────────

def _build_engine(tmp_path: Path) -> tuple[EngineFlow, dict]:
    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws"),
        parameters={"Design": "chip", "Top module": "chip_top"},
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
