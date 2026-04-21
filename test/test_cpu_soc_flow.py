#!/usr/bin/env python
"""CPU+SoC integration flow tests using backend APIs only."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from fecompiler.config import DEFAULT_PROJECTS_ROOT
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow


REPO_ROOT = Path(__file__).resolve().parent.parent
CPU_FILELIST = REPO_ROOT / "docs/examples/cl3/filelist.cpu.f"
SOC_FILELIST = REPO_ROOT / "fecompiler/thirdparty/SoC/filelist.soc.f"
TESTBENCH = REPO_ROOT / "fecompiler/thirdparty/SoC/driver/main.cpp"
DPI_CPP = REPO_ROOT / "fecompiler/thirdparty/SoC/driver/dpi_mem.cpp"
SOC_INC = REPO_ROOT / "fecompiler/thirdparty/SoC"

SIM_IMAGES = [
    REPO_ROOT / "fecompiler/thirdparty/SoC/tests/out/min2.soc.bin",
    REPO_ROOT / "fecompiler/thirdparty/SoC/tests/out/add.soc.bin",
]

WS_DIR = DEFAULT_PROJECTS_ROOT / "cpu_soc_test"


def _tool_ready() -> bool:
    slang_local = REPO_ROOT / "fecompiler/tools/slang/bin/slang"
    verilator_local = REPO_ROOT / "fecompiler/tools/verilator/bin/verilator"
    return (
        slang_local.exists() or shutil.which("slang") is not None
    ) and (
        verilator_local.exists() or shutil.which("verilator") is not None
    )


def _required_paths() -> list[Path]:
    return [CPU_FILELIST, SOC_FILELIST, TESTBENCH, DPI_CPP, *SIM_IMAGES]


def _new_engine() -> tuple[EngineFlow, dict]:
    ws = load_workspace(str(WS_DIR))
    assert ws is not None
    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    return engine, ws


@pytest.fixture(scope="module", autouse=True)
def cpu_soc_workspace():
    if not _tool_ready():
        pytest.skip("slang/verilator not available")

    missing = [str(p) for p in _required_paths() if not p.exists()]
    if missing:
        pytest.skip(f"required files missing: {missing}")

    if WS_DIR.exists():
        shutil.rmtree(WS_DIR)

    spec = CreateWorkspaceData(
        directory=str(WS_DIR),
        parameters={"Design": "cpu_soc_test", "Top module": "ysyxSoCTop"},
        cpu_filelist=str(CPU_FILELIST),
        soc_filelist=str(SOC_FILELIST),
        testbench=str(TESTBENCH),
        sim_cpp_sources=[str(DPI_CPP)],
        sim_cflags=[f"-I{SOC_INC}"],
        sim_run_args=["--max-cycles", "2000000"],
    )
    ws = create_workspace(spec)
    assert ws is not None


def test_cpu_soc_project_created():
    ws = load_workspace(str(WS_DIR))
    assert ws is not None
    assert Path(ws["flow_path"]).exists()
    assert Path(ws["parameters_path"]).exists()
    assert Path(ws["directory"]).name == "cpu_soc_test"


def test_cpu_soc_prepare_step_success():
    engine, ws = _new_engine()
    state = engine.run_step("prepare", rerun=True)
    assert state == StateEnum.Success
    assert (Path(ws["directory"]) / "prepare_fe" / "output" / "merged_rtl.f").exists()
    assert (Path(ws["directory"]) / "prepare_fe" / "output" / "prepared_inputs.json").exists()


def test_cpu_soc_elab_step_success():
    engine, ws = _new_engine()
    state = engine.run_step("elab", rerun=True)
    assert state == StateEnum.Success
    log_path = Path(ws["directory"]) / "elab_slang" / "report" / "log.txt"
    assert log_path.exists()


def test_cpu_soc_lint_step_success():
    engine, ws = _new_engine()
    state = engine.run_step("lint", rerun=True)
    assert state == StateEnum.Success
    log_path = Path(ws["directory"]) / "lint_verilator" / "report" / "log.txt"
    assert log_path.exists()
    assert "%Error" not in log_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("image_path", SIM_IMAGES)
def test_cpu_soc_sim_each_program_success(image_path: Path):
    engine, ws = _new_engine()
    ws["sim_images"] = [str(image_path)]

    sim_bin = Path(ws["directory"]) / "sim_verilator" / "output" / "cpu_soc_test_sim"
    ws["sim_reuse_binary"] = sim_bin.exists()
    ws["sim_run_args"] = ["--max-cycles", "2000000"]

    state = engine.run_step("sim", rerun=True)
    assert state == StateEnum.Success

    case_name = image_path.stem  # e.g. min2.soc
    case_log = Path(ws["directory"]) / "sim_verilator" / "report" / "cases" / case_name / "log.txt"
    assert case_log.exists()
    content = case_log.read_text(encoding="utf-8")
    assert "FAILED" not in content
    assert "%Error" not in content


def test_cpu_soc_sim_batch_has_separate_logs_for_each_program():
    engine, ws = _new_engine()
    ws["sim_images"] = [str(p) for p in SIM_IMAGES]
    ws["sim_run_args"] = ["--max-cycles", "2000000"]

    sim_bin = Path(ws["directory"]) / "sim_verilator" / "output" / "cpu_soc_test_sim"
    ws["sim_reuse_binary"] = sim_bin.exists()

    state = engine.run_step("sim", rerun=True)
    assert state == StateEnum.Success

    report_dir = Path(ws["directory"]) / "sim_verilator" / "report"
    case_logs = [report_dir / "cases" / p.stem / "log.txt" for p in SIM_IMAGES]
    for log in case_logs:
        assert log.exists(), f"missing case log: {log}"
        content = log.read_text(encoding="utf-8")
        assert "FAILED" not in content
        assert "%Error" not in content

    assert case_logs[0] != case_logs[1]
