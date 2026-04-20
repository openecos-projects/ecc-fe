#!/usr/bin/env python
"""Integration tests using docs/examples — filelist.f + slang elab + verilator lint/sim."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from fecompiler.config import DEFAULT_PROJECTS_ROOT
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow

EXAMPLES_DIR = Path(__file__).parent.parent / "docs" / "examples"
FILELIST      = EXAMPLES_DIR / "filelist.f"
TEST_WS_DIR   = DEFAULT_PROJECTS_ROOT / "test_adder"


@pytest.fixture(scope="module", autouse=True)
def adder_workspace():
    """Create a fresh adder workspace once for the whole module."""
    if TEST_WS_DIR.exists():
        shutil.rmtree(TEST_WS_DIR)

    spec = CreateWorkspaceData(
        directory=str(TEST_WS_DIR),
        parameters={"Design": "adder", "Top module": "adder"},
        filelist=str(FILELIST),
    )
    ws = create_workspace(spec)
    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    engine.run_all(rerun=True)
    return ws


# ── workspace creation from filelist ─────────────────────────────────────────

def test_filelist_sources_copied_to_origin():
    origin = TEST_WS_DIR / "origin"
    assert (origin / "adder.v").exists()
    assert (origin / "mux.v").exists()


def test_filelist_itself_copied_to_origin():
    assert (TEST_WS_DIR / "origin" / "filelist.f").exists()


def test_filelist_in_origin_has_absolute_paths():
    fl_lines = [
        l.strip()
        for l in (TEST_WS_DIR / "origin" / "filelist.f").read_text().splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]
    for line in fl_lines:
        assert Path(line).is_absolute(), f"Expected absolute path, got: {line}"
        assert Path(line).exists(),      f"Path does not exist: {line}"


def test_load_workspace_finds_filelist():
    ws = load_workspace(str(TEST_WS_DIR))
    assert ws is not None
    assert ws["input_filelist"] != ""
    assert Path(ws["input_filelist"]).exists()


# ── prepare step ──────────────────────────────────────────────────────────────

def test_prepare_step_state_is_success():
    ws     = load_workspace(str(TEST_WS_DIR))
    engine = EngineFlow(workspace=ws)
    step   = engine.get_step("prepare", "fe")
    assert step is not None
    assert step["state"] == "Success"


def test_prepare_merged_filelist_written():
    merged = TEST_WS_DIR / "prepare_fe" / "output" / "merged_rtl.f"
    assert merged.exists()
    lines = [l.strip() for l in merged.read_text().splitlines() if l.strip()]
    assert len(lines) >= 2


# ── slang elab step ───────────────────────────────────────────────────────────

def test_elab_step_state_is_success():
    ws     = load_workspace(str(TEST_WS_DIR))
    engine = EngineFlow(workspace=ws)
    step   = engine.get_step("elab", "slang")
    assert step is not None
    assert step["state"] == "Success"


def test_elab_report_written():
    elab_txt = TEST_WS_DIR / "elab_slang" / "report" / "elab.txt"
    assert elab_txt.exists()
    assert "error:" not in elab_txt.read_text().lower()


def test_elab_subflow_all_success():
    subflow = json.loads((TEST_WS_DIR / "elab_slang" / "subflow.json").read_text())
    for sub in subflow["steps"]:
        assert sub["state"] == "Success", f"sub-step {sub['name']} not Success"


# ── lint step ─────────────────────────────────────────────────────────────────

def test_lint_step_state_is_success():
    ws     = load_workspace(str(TEST_WS_DIR))
    engine = EngineFlow(workspace=ws)
    step   = engine.get_step("lint", "verilator")
    assert step is not None
    assert step["state"] == "Success"


def test_lint_report_written():
    lint_txt = TEST_WS_DIR / "lint_verilator" / "report" / "lint.txt"
    assert lint_txt.exists()
    assert "%Error" not in lint_txt.read_text()


# ── sim step ──────────────────────────────────────────────────────────────────

def test_sim_step_state_is_success():
    ws     = load_workspace(str(TEST_WS_DIR))
    engine = EngineFlow(workspace=ws)
    step   = engine.get_step("sim", "verilator")
    assert step is not None
    assert step["state"] == "Success"


# ── full flow ─────────────────────────────────────────────────────────────────

def test_run_all_completes():
    flow = json.loads((TEST_WS_DIR / "home" / "flow.json").read_text())
    for step in flow["steps"]:
        assert step["state"] == "Success", f"step {step['name']} not Success"
