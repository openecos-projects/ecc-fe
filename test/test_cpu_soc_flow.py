#!/usr/bin/env python
"""CPU+SoC integration flow tests using backend APIs only."""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from fecompiler.config import DEFAULT_PROJECTS_ROOT
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow


REPO_ROOT = Path(__file__).resolve().parent.parent
CPU_FILELIST = REPO_ROOT / "docs/examples/cl3/filelist.cpu.f"
SOC_FILELIST = REPO_ROOT / "fecompiler/thirdparty/SoC/filelist.soc.f"
TESTBENCH = REPO_ROOT / "fecompiler/thirdparty/SoC/driver/main.cpp"
DPI_CPP = REPO_ROOT / "fecompiler/thirdparty/SoC/driver/dpi_mem.cpp"
DIFFTEST_CPP = REPO_ROOT / "fecompiler/thirdparty/SoC/driver/difftest.cpp"
SOC_INC = REPO_ROOT / "fecompiler/thirdparty/SoC"
REF_SO = REPO_ROOT / "fecompiler/thirdparty/SoC/tools/riscv32-spike-so"
SOC_TEST_PROGRAMS_DIR = REPO_ROOT / "fecompiler/thirdparty/SoC/tests/programs"
SOC_TEST_OUT_DIR = REPO_ROOT / "fecompiler/thirdparty/SoC/tests/out"
SIM_MAX_CYCLES = "50000000"
DIFF_SIM_RUN_ARGS = [
    "--max-cycles",
    SIM_MAX_CYCLES,
    "--diff",
    "--ref",
    str(REF_SO),
    "--diff-image-offset",
    "0x100",
    "--diff-reset-vector",
    "0x80000000",
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
    return [CPU_FILELIST, SOC_FILELIST, TESTBENCH, DPI_CPP, DIFFTEST_CPP, REF_SO, SOC_TEST_PROGRAMS_DIR]


def _riscv_toolchain_ready() -> bool:
    candidates = [
        "riscv64-none-elf-gcc",
        "riscv-none-elf-gcc",
        "riscv64-unknown-linux-gnu-gcc",
        "riscv64-linux-gnu-gcc",
    ]
    return any(shutil.which(x) is not None for x in candidates)


def _new_engine() -> tuple[EngineFlow, dict]:
    ws = load_workspace(str(WS_DIR))
    if ws is None:
        raise AssertionError(f"workspace not found: {WS_DIR}")
    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    return engine, ws


class TestCpuSocFlow(unittest.TestCase):
    @classmethod
    def _program_sources(cls) -> list[Path]:
        return sorted(SOC_TEST_PROGRAMS_DIR.glob("*.c"))

    @classmethod
    def setUpClass(cls):
        if not _tool_ready():
            raise unittest.SkipTest("slang/verilator not available")
        if not _riscv_toolchain_ready():
            raise unittest.SkipTest("riscv gcc toolchain not available")

        missing = [str(p) for p in _required_paths() if not p.exists()]
        if missing:
            raise unittest.SkipTest(f"required files missing: {missing}")
        if not cls._program_sources():
            raise unittest.SkipTest(f"no C tests in {SOC_TEST_PROGRAMS_DIR}")

        if WS_DIR.exists():
            shutil.rmtree(WS_DIR)

        spec = CreateWorkspaceData(
            directory=str(WS_DIR),
            parameters={"Design": "cpu_soc_test", "Top module": "ysyxSoCTop"},
            cpu_filelist=str(CPU_FILELIST),
            soc_filelist=str(SOC_FILELIST),
            testbench=str(TESTBENCH),
            sim_cpp_sources=[str(DPI_CPP), str(DIFFTEST_CPP)],
            sim_cflags=[f"-I{SOC_INC}"],
            sim_ldflags=["-ldl"],
            sim_run_args=DIFF_SIM_RUN_ARGS,
        )
        ws = create_workspace(spec)
        if ws is None:
            raise AssertionError("failed to create cpu_soc_test workspace")

    def test_cpu_soc_project_created(self):
        ws = load_workspace(str(WS_DIR))
        self.assertIsNotNone(ws)
        assert ws is not None
        self.assertTrue(Path(ws["flow_path"]).exists())
        self.assertTrue(Path(ws["parameters_path"]).exists())
        self.assertEqual(Path(ws["directory"]).name, "cpu_soc_test")

    def test_cpu_soc_prepare_step_success(self):
        engine, ws = _new_engine()
        state = engine.run_step("prepare", rerun=True)
        self.assertEqual(state, StateEnum.Success)
        self.assertTrue((Path(ws["directory"]) / "prepare_fe" / "output" / "merged_rtl.f").exists())
        self.assertTrue((Path(ws["directory"]) / "prepare_fe" / "output" / "prepared_inputs.json").exists())

    def test_cpu_soc_elab_step_success(self):
        engine, ws = _new_engine()
        state = engine.run_step("elab", rerun=True)
        self.assertEqual(state, StateEnum.Success)
        log_path = Path(ws["directory"]) / "elab_slang" / "report" / "log.txt"
        self.assertTrue(log_path.exists())

    def test_cpu_soc_lint_step_success(self):
        engine, ws = _new_engine()
        state = engine.run_step("lint", rerun=True)
        self.assertEqual(state, StateEnum.Success)
        log_path = Path(ws["directory"]) / "lint_verilator" / "report" / "log.txt"
        self.assertTrue(log_path.exists())
        self.assertNotIn("%Error", log_path.read_text(encoding="utf-8"))

    def test_cpu_soc_sim_each_program_success(self):
        engine, ws = _new_engine()
        sim_bin = Path(ws["directory"]) / "sim_verilator" / "output" / "cpu_soc_test_sim"
        self.assertEqual(engine.run_step("prepare", rerun=True), StateEnum.Success)

        ws["sim_build_all_programs"] = True
        ws["sim_programs_dir"] = str(SOC_TEST_PROGRAMS_DIR)
        ws["sim_tests_out_dir"] = str(SOC_TEST_OUT_DIR)
        # Only run images built from tests/programs/*.c in this test.
        ws["sim_all_tests"] = False
        ws["sim_tests_dir"] = str(SOC_TEST_OUT_DIR)
        ws["sim_images"] = []
        ws["sim_program_sources"] = []
        ws["sim_run_args"] = DIFF_SIM_RUN_ARGS
        ws["sim_reuse_binary"] = sim_bin.exists()

        state = engine.run_step("sim", rerun=True)
        self.assertEqual(state, StateEnum.Success)

        report_dir = Path(ws["directory"]) / "sim_verilator" / "report"
        cases_payload = (report_dir / "cases.json").read_text(encoding="utf-8")
        self.assertTrue(cases_payload)

        import json

        data = json.loads(cases_payload)
        cases = data.get("cases", [])
        self.assertTrue(cases, "cases.json should contain all executed cases")

        expected_names = {f"{p.stem}.soc" for p in self._program_sources()}
        executed_names = {str(c.get("name", "")) for c in cases if isinstance(c, dict)}
        self.assertTrue(expected_names.issubset(executed_names))

        for name in sorted(expected_names):
            latest_log = report_dir / "cases" / name / "log.txt"
            self.assertTrue(latest_log.exists(), f"missing latest case log: {latest_log}")
            content = latest_log.read_text(encoding="utf-8")
            self.assertNotIn("FAILED", content)
            self.assertNotIn("%Error", content)

    def test_cpu_soc_sim_batch_has_separate_logs_for_each_program(self):
        engine, ws = _new_engine()
        self.assertEqual(engine.run_step("prepare", rerun=True), StateEnum.Success)
        first_src = self._program_sources()[0]
        case_name = f"{first_src.stem}.soc"
        image_path = SOC_TEST_OUT_DIR / f"{first_src.stem}.soc.bin"
        ws["sim_program_sources"] = [str(first_src)]
        ws["sim_tests_out_dir"] = str(SOC_TEST_OUT_DIR)
        ws["sim_all_tests"] = False
        ws["sim_images"] = []
        ws["sim_run_args"] = DIFF_SIM_RUN_ARGS

        sim_bin = Path(ws["directory"]) / "sim_verilator" / "output" / "cpu_soc_test_sim"
        ws["sim_reuse_binary"] = sim_bin.exists()

        state = engine.run_step("sim", rerun=True)
        self.assertEqual(state, StateEnum.Success)
        self.assertTrue(image_path.exists(), f"expected built image: {image_path}")

        # second run reuses the compiled image, so we can verify run-log history
        ws["sim_program_sources"] = []
        ws["sim_images"] = [str(image_path)]
        ws["sim_reuse_binary"] = True

        report_dir = Path(ws["directory"]) / "sim_verilator" / "report"
        runs_root = report_dir / "runs"
        run_dirs_1 = sorted([p for p in runs_root.iterdir() if p.is_dir()])
        self.assertTrue(run_dirs_1, f"missing run history dir: {runs_root}")
        first_run = run_dirs_1[-1]
        first_run_log = first_run / "cases" / case_name / "log.txt"
        self.assertTrue(first_run_log.exists(), f"missing run log: {first_run_log}")
        first_content = first_run_log.read_text(encoding="utf-8")

        state = engine.run_step("sim", rerun=True)
        self.assertEqual(state, StateEnum.Success)
        run_dirs_2 = sorted([p for p in runs_root.iterdir() if p.is_dir()])
        self.assertGreaterEqual(len(run_dirs_2), len(run_dirs_1) + 1)
        second_run = run_dirs_2[-1]
        self.assertNotEqual(first_run, second_run)

        second_run_log = second_run / "cases" / case_name / "log.txt"
        self.assertTrue(second_run_log.exists(), f"missing second run log: {second_run_log}")
        self.assertTrue(first_run_log.exists(), "old run log should be retained")
        self.assertEqual(first_content, first_run_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
