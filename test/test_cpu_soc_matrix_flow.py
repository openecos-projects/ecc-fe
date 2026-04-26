#!/usr/bin/env python
"""3x3 CPU+SoC matrix integration flow tests using fixed source variants."""

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
    REPO_ROOT / "docs/examples/cl3",
    REPO_ROOT / "docs/examples/cl3_1",
    REPO_ROOT / "docs/examples/cl3_2",
]
SOC_VARIANTS = [
    REPO_ROOT / "fecompiler/thirdparty/SoC",
    REPO_ROOT / "fecompiler/thirdparty/SoC2",
    REPO_ROOT / "fecompiler/thirdparty/SoC3",
]

CPU_VARIANT_COUNT = 3
SOC_VARIANT_COUNT = 3
SIM_MAX_CYCLES = "50000000"


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


def _required_paths() -> list[Path]:
    paths: list[Path] = []
    for cpu_root in CPU_VARIANTS:
        paths.extend([
            cpu_root / "filelist.cpu.f",
            cpu_root / "cl3_verilog/CL3Top.sv",
        ])
    for soc_root in SOC_VARIANTS:
        paths.extend([
            soc_root / "filelist.soc.f",
            soc_root / "driver/main.cpp",
            soc_root / "driver/dpi_mem.cpp",
            soc_root / "driver/difftest.cpp",
            soc_root / "tools/riscv32-spike-so",
            soc_root / "tests/programs",
            soc_root / "tests/out",
        ])
    return paths


def _soc_program_sources(soc_root: Path) -> list[Path]:
    programs_dir = soc_root / "tests" / "programs"
    sources = sorted(programs_dir.glob("*.c"))
    if sources:
        return sources
    raise FileNotFoundError(f"no C test programs found in {programs_dir}")


def _soc_sim_cpp_sources(soc_root: Path) -> list[str]:
    sources = [soc_root / "driver/dpi_mem.cpp"]
    difftest_cpp = soc_root / "driver/difftest.cpp"
    if difftest_cpp.exists():
        sources.append(difftest_cpp)
    return [str(src) for src in sources]


def _soc_sim_ldflags(soc_root: Path) -> list[str]:
    return ["-ldl"] if (soc_root / "driver/difftest.cpp").exists() else []


def _soc_diff_run_args(soc_root: Path) -> list[str]:
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
        test_sources = _soc_program_sources(soc_root)
        tests_out_dir = soc_root / "tests" / "out"

        project_name = f"cpu_soc_matrix_cpu{cpu_idx}_soc{soc_idx}"
        ws_dir = DEFAULT_PROJECTS_ROOT / project_name

        if ws_dir.exists():
            shutil.rmtree(ws_dir)

        spec = CreateWorkspaceData(
            directory=str(ws_dir),
            parameters={"Design": project_name, "Top module": "ysyxSoCTop"},
            cpu_filelist=str(cpu_root / "filelist.cpu.f"),
            soc_filelist=str(soc_root / "filelist.soc.f"),
            testbench=str(soc_root / "driver/main.cpp"),
            sim_cpp_sources=_soc_sim_cpp_sources(soc_root),
            sim_cflags=[f"-I{soc_root}"],
            sim_ldflags=_soc_sim_ldflags(soc_root),
            sim_build_all_programs=True,
            sim_programs_dir=str(soc_root / "tests" / "programs"),
            sim_tests_out_dir=str(tests_out_dir),
            sim_run_args=_soc_diff_run_args(soc_root),
        )
        ws = create_workspace(spec)
        self.assertIsNotNone(ws, f"failed to create workspace for {project_name}")

        loaded = load_workspace(str(ws_dir))
        self.assertIsNotNone(loaded, f"workspace not found: {ws_dir}")
        assert loaded is not None

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

        expected_case_names = {f"{src.stem}.soc" for src in test_sources}
        executed_case_names = {
            str(entry.get("name", ""))
            for entry in cases
            if isinstance(entry, dict)
        }
        self.assertTrue(
            expected_case_names.issubset(executed_case_names),
            f"missing cases: {sorted(expected_case_names - executed_case_names)}",
        )
        self.assertEqual(
            len(expected_case_names),
            len(executed_case_names & expected_case_names),
            "some test images were not executed",
        )

        for case_name in sorted(expected_case_names):
            case_log = ws_dir / "sim_verilator" / "output" / "cases" / case_name / "log.txt"
            self.assertTrue(case_log.exists(), f"missing case log: {case_log}")
            case_content = case_log.read_text(encoding="utf-8")
            self.assertTrue(case_content, f"empty case log: {case_log}")
            self.assertNotIn("FAILED", case_content)
            self.assertNotIn("%Error", case_content)


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
