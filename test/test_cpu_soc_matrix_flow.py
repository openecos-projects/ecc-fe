#!/usr/bin/env python
"""CPU variant integration flow tests using the single shipped real SoC."""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from fecompiler.config import DEFAULT_PROJECTS_ROOT
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow


REPO_ROOT = Path(__file__).resolve().parent.parent

CPU_VARIANTS = [
    REPO_ROOT / "examples/ysyx_00000000",
]
SOC_VARIANTS = [
    REPO_ROOT / "fecompiler/thirdparty/SoC",
]

CPU_VARIANT_COUNT = len(CPU_VARIANTS)
SOC_VARIANT_COUNT = len(SOC_VARIANTS)
SIM_MAX_CYCLES = "50000000"


def _sim_run_args(soc_root: Path) -> list[str]:
    return [
        "--max-cycles",
        SIM_MAX_CYCLES,
        "--diff",
        "--ref",
        str(soc_root / "tools/riscv32-spike-so"),
        "--diff-image-offset",
        "0x100",
        "--diff-reset-vector",
        "0x80000000",
    ]


def _tool_ready() -> bool:
    return shutil.which("slang") is not None and shutil.which("verilator") is not None


def _riscv_toolchain_ready() -> bool:
    candidates = [
        "riscv32-unknown-elf-gcc",
        "riscv64-unknown-elf-gcc",
        "riscv64-none-elf-gcc",
        "riscv-none-elf-gcc",
        "riscv64-unknown-linux-gnu-gcc",
        "riscv64-linux-gnu-gcc",
    ]
    return any(shutil.which(x) is not None for x in candidates)


def _required_paths() -> list[Path]:
    paths: list[Path] = []
    for cpu_root in CPU_VARIANTS:
        paths.extend([
            cpu_root / "filelist.cpu.f",
            cpu_root / "rtl/ysyx_00000000.sv",
        ])
    for soc_root in SOC_VARIANTS:
        paths.extend([
            soc_root / "filelist.soc.f",
            soc_root / "driver/main.cpp",
            soc_root / "driver/dpi_mem.cpp",
            soc_root / "driver/difftest_stub.cpp",
            soc_root / "driver/difftest.cpp",
            soc_root / "tools/riscv32-spike-so",
            soc_root / "tests/programs/add.c",
        ])
    return paths


def _soc_sim_cpp_sources(soc_root: Path) -> list[str]:
    return [
        str(soc_root / "driver/dpi_mem.cpp"),
        str(soc_root / "driver/difftest_stub.cpp"),
    ]


class TestCpuSocMatrixFlow(unittest.TestCase):
    cpu_variants: list[Path] = []
    soc_variants: list[Path] = []

    @classmethod
    def setUpClass(cls) -> None:
        if not _tool_ready():
            raise unittest.SkipTest("slang/verilator not available")
        if not _riscv_toolchain_ready():
            raise unittest.SkipTest("riscv gcc toolchain not available")

        missing = [str(p) for p in _required_paths() if not p.exists()]
        if missing:
            raise unittest.SkipTest(f"required files missing: {missing}")

        cls.cpu_variants = list(CPU_VARIANTS)
        cls.soc_variants = list(SOC_VARIANTS)

    def _run_full_flow_for_combo(self, cpu_idx: int, soc_idx: int) -> None:
        cpu_root = self.cpu_variants[cpu_idx - 1]
        soc_root = self.soc_variants[soc_idx - 1]

        project_name = f"cpu_soc_matrix_cpu{cpu_idx}_soc{soc_idx}"
        ws_dir = DEFAULT_PROJECTS_ROOT / project_name

        if ws_dir.exists():
            shutil.rmtree(ws_dir)

        spec = CreateWorkspaceData(
            directory=str(ws_dir),
            parameters={
                "Design": project_name,
                "Top module": "ecos_sim_top",
                "frontend_core_id": "custom-filelist",
                "required_cpu_top_module": "ysyx_00000000",
                "cpu_wrapper_top": "ysyx_00000000",
            },
            cpu_filelist=str(cpu_root / "filelist.cpu.f"),
            soc_filelist=str(soc_root / "filelist.soc.f"),
            testbench=str(soc_root / "driver/main.cpp"),
            sim_cpp_sources=_soc_sim_cpp_sources(soc_root),
            sim_cflags=[f"-I{soc_root}"],
            sim_ldflags=["-ldl"],
            sim_build_all_programs=False,
            sim_program_names=["add"],
            sim_programs_dir=str(soc_root / "tests" / "programs"),
            sim_run_args=_sim_run_args(soc_root),
            cpu_supports_difftest=True,
            sim_compile_march="rv32i_zicsr",
            sim_compile_mabi="ilp32",
        )
        ws = create_workspace(spec)
        self.assertIsNotNone(ws, f"failed to create workspace for {project_name}")

        loaded = load_workspace(str(ws_dir))
        self.assertIsNotNone(loaded, f"workspace not found: {ws_dir}")
        assert loaded is not None
        self.assertEqual(loaded["frontend_core_id"], "custom-filelist")
        self.assertEqual(loaded["required_cpu_top_module"], "ysyx_00000000")
        self.assertEqual(loaded["cpu_wrapper_top"], "ysyx_00000000")
        self.assertTrue(loaded["cpu_supports_difftest"])
        self.assertEqual(loaded["sim_compile_march"], "rv32i_zicsr")
        self.assertEqual(loaded["sim_compile_mabi"], "ilp32")

        engine = EngineFlow(workspace=loaded)
        engine.create_step_workspaces()
        ok, reports = engine.run_all(rerun=True)

        failed_non_sim = [
            r for r in reports
            if r.get("step") != "sim" and r.get("state") != "Success"
        ]
        self.assertFalse(
            failed_non_sim,
            f"non-sim steps failed for {project_name}: {failed_non_sim}; all reports: {reports}",
        )
        sim_entries = [r for r in reports if r.get("step") == "sim"]
        self.assertTrue(sim_entries, f"sim step missing in reports: {reports}")
        sim_state = str(sim_entries[0].get("state", ""))
        self.assertEqual(sim_state, "Success", f"sim failed for {project_name}: {reports}")

        self.assertTrue((ws_dir / "prepare_fe" / "output" / "merged_rtl.f").exists())
        self.assertTrue((ws_dir / "elab_slang" / "report" / "log.txt").exists())

        lint_log = ws_dir / "lint_verilator" / "report" / "log.txt"
        self.assertTrue(lint_log.exists())
        self.assertNotIn("%Error", lint_log.read_text(encoding="utf-8"))

        report_dir = ws_dir / "sim_verilator" / "report"
        cases_payload = (report_dir / "cases.json").read_text(encoding="utf-8")
        self.assertTrue(cases_payload, "cases.json should not be empty")
        payload = json.loads(cases_payload)
        cases = payload.get("cases", []) if isinstance(payload, dict) else []
        self.assertTrue(cases, "simulation should run at least one case")

        expected_case_names = {"add.soc"}
        executed_case_names = {
            str(entry.get("name", ""))
            for entry in cases
            if isinstance(entry, dict)
        }
        self.assertEqual(executed_case_names, expected_case_names)

        cases_by_name = {
            str(entry.get("name", "")): entry
            for entry in cases
            if isinstance(entry, dict)
        }
        output_cases_root = ws_dir / "sim_verilator" / "output" / "cases"

        for case_name in sorted(expected_case_names):
            case_dir = output_cases_root / case_name
            case_log = case_dir / "log.txt"
            self.assertTrue(case_log.exists(), f"missing case log: {case_log}")
            case_content = case_log.read_text(encoding="utf-8")
            self.assertTrue(case_content, f"empty case log: {case_log}")
            self.assertNotIn("FAILED", case_content)
            self.assertNotIn("%Error", case_content)
            self.assertIn("[soc-sim][difftest] compare starts", case_content)

            case_entry = cases_by_name.get(case_name)
            self.assertIsNotNone(case_entry, f"missing case entry: {case_name}")
            assert case_entry is not None
            difftest = case_entry.get("metrics", {}).get("difftest", {})
            self.assertTrue(difftest.get("enabled"))
            self.assertEqual(difftest.get("status"), "passed")
            image_path = Path(str(case_entry.get("image", ""))).resolve()
            self.assertTrue(image_path.exists(), f"missing case image: {image_path}")
            self.assertTrue(
                str(image_path).startswith(str(case_dir.resolve())),
                f"case image should be under {case_dir}: {image_path}",
            )

def _make_combo_test(cpu_idx: int, soc_idx: int):
    def _test(self: TestCpuSocMatrixFlow) -> None:
        self._run_full_flow_for_combo(cpu_idx, soc_idx)

    _test.__name__ = f"test_full_flow_cpu{cpu_idx}_soc{soc_idx}"
    return _test


for _cpu in range(1, CPU_VARIANT_COUNT + 1):
    for _soc in range(1, SOC_VARIANT_COUNT + 1):
        setattr(TestCpuSocMatrixFlow, f"test_full_flow_cpu{_cpu}_soc{_soc}", _make_combo_test(_cpu, _soc))


if __name__ == "__main__":
    unittest.main()
