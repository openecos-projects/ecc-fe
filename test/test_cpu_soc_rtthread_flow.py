#!/usr/bin/env python
"""CPU+SoC RT-Thread smoke test using backend APIs only."""

from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path

from fecompiler.config import DEFAULT_PROJECTS_ROOT
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow


REPO_ROOT = Path(__file__).resolve().parent.parent
CPU_FILELIST = REPO_ROOT / "docs/examples/cl3/filelist.cpu.f"
SOC_ROOT = REPO_ROOT / "fecompiler/thirdparty/SoC"
SOC_FILELIST = SOC_ROOT / "filelist.soc.f"
TESTBENCH = SOC_ROOT / "driver/main.cpp"
DPI_CPP = SOC_ROOT / "driver/dpi_mem.cpp"
DIFFTEST_CPP = SOC_ROOT / "driver/difftest.cpp"
REF_SO = SOC_ROOT / "tools/riscv32-spike-so"
RTTHREAD_AM = REPO_ROOT / "fecompiler/thirdparty/rt-thread-am"
RTTHREAD_BSP = RTTHREAD_AM / "bsp/abstract-machine"
DEFAULT_AM_HOME = Path("/home/luyoung/ysyx-workbench/abstract-machine")
SIM_MAX_CYCLES = "10000000"
WS_DIR = DEFAULT_PROJECTS_ROOT / "cpu_soc_rtthread_test"


def _tool_ready() -> bool:
    slang_local = REPO_ROOT / "fecompiler/tools/slang/bin/slang"
    verilator_local = REPO_ROOT / "fecompiler/tools/verilator/bin/verilator"
    return (
        slang_local.exists() or shutil.which("slang") is not None
    ) and (
        verilator_local.exists() or shutil.which("verilator") is not None
    )


def _riscv_toolchain_ready() -> bool:
    candidates = [
        "riscv64-none-elf-gcc",
        "riscv-none-elf-gcc",
        "riscv64-unknown-linux-gnu-gcc",
        "riscv64-linux-gnu-gcc",
    ]
    return any(shutil.which(x) is not None for x in candidates)


def _am_home_ready() -> bool:
    env_home = os.environ.get("AM_HOME", "").strip()
    if env_home and (Path(env_home) / "Makefile").exists():
        return True
    return (DEFAULT_AM_HOME / "Makefile").exists()


def _required_paths() -> list[Path]:
    return [
        CPU_FILELIST,
        SOC_FILELIST,
        TESTBENCH,
        DPI_CPP,
        DIFFTEST_CPP,
        REF_SO,
        RTTHREAD_BSP / "Makefile",
    ]


class TestCpuSocRtThreadFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _tool_ready():
            raise unittest.SkipTest("slang/verilator not available")
        if not _riscv_toolchain_ready():
            raise unittest.SkipTest("riscv gcc toolchain not available")
        if shutil.which("scons") is None:
            raise unittest.SkipTest("scons not available")
        if not _am_home_ready():
            raise unittest.SkipTest("AM_HOME/AbstractMachine not available")

        missing = [str(p) for p in _required_paths() if not p.exists()]
        if missing:
            raise unittest.SkipTest(f"required files missing: {missing}")

        if WS_DIR.exists():
            shutil.rmtree(WS_DIR)

        spec = CreateWorkspaceData(
            directory=str(WS_DIR),
            parameters={"Design": "cpu_soc_rtthread_test", "Top module": "ysyxSoCTop"},
            cpu_filelist=str(CPU_FILELIST),
            soc_filelist=str(SOC_FILELIST),
            testbench=str(TESTBENCH),
            sim_cpp_sources=[str(DPI_CPP), str(DIFFTEST_CPP)],
            sim_cflags=[f"-I{SOC_ROOT}"],
            sim_ldflags=["-ldl"],
            sim_program_names=["rtthread"],
            sim_programs_dir=str(SOC_ROOT / "tests/programs"),
            sim_run_args=["--max-cycles", SIM_MAX_CYCLES, "--wave", "/dev/null"],
        )
        ws = create_workspace(spec)
        if ws is None:
            raise AssertionError("failed to create cpu_soc_rtthread_test workspace")

    def test_rtthread_boots_with_default_difftest(self) -> None:
        ws = load_workspace(str(WS_DIR))
        self.assertIsNotNone(ws)
        assert ws is not None

        engine = EngineFlow(workspace=ws)
        engine.create_step_workspaces()

        self.assertEqual(engine.run_step("prepare", rerun=True), StateEnum.Success)
        state = engine.run_step("sim", rerun=True)
        self.assertEqual(state, StateEnum.Success)

        image_path = WS_DIR / "sim_verilator" / "output" / "cases" / "rtthread.soc" / "rtthread.soc.bin"
        self.assertTrue(image_path.exists(), f"expected RT-Thread image: {image_path}")

        report_dir = WS_DIR / "sim_verilator" / "report"
        cases_json = report_dir / "cases.json"
        self.assertTrue(cases_json.exists(), f"missing cases report: {cases_json}")
        payload = json.loads(cases_json.read_text(encoding="utf-8"))
        cases = payload.get("cases", []) if isinstance(payload, dict) else []
        rtthread_case = next(
            (case for case in cases if isinstance(case, dict) and case.get("name") == "rtthread.soc"),
            None,
        )
        self.assertIsNotNone(rtthread_case, f"rtthread.soc case missing: {cases}")
        assert rtthread_case is not None
        self.assertTrue(rtthread_case.get("ok"), f"rtthread case failed: {rtthread_case}")
        self.assertEqual(str(image_path.resolve()), str(Path(str(rtthread_case["image"])).resolve()))

        log_path = Path(str(rtthread_case["log"]))
        self.assertTrue(log_path.exists(), f"missing RT-Thread log: {log_path}")
        content = log_path.read_text(encoding="utf-8")
        self.assertIn("[soc-sim][difftest] enabled", content)
        self.assertIn("[soc-sim][difftest] compare starts at pc=0x80000000", content)
        self.assertIn("Thread Operating System", content)
        self.assertIn("Hello RISC-V!", content)
        self.assertIn("msh />help", content)
        self.assertIn("RT-Thread shell commands:", content)
        self.assertIn("[soc-sim] timeout after", content)
        self.assertNotIn("FAILED", content)
        self.assertNotIn("%Error", content)


if __name__ == "__main__":
    unittest.main()
