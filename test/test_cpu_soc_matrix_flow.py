#!/usr/bin/env python
"""CPU variant integration flow tests using the single shipped real SoC."""

from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path

from fecompiler.config import DEFAULT_PROJECTS_ROOT
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow


REPO_ROOT = Path(__file__).resolve().parent.parent

CPU_VARIANTS = [
    REPO_ROOT / "examples/cl3",
]
SOC_VARIANTS = [
    REPO_ROOT / "fecompiler/thirdparty/SoC",
]

CPU_VARIANT_COUNT = len(CPU_VARIANTS)
SOC_VARIANT_COUNT = len(SOC_VARIANTS)
SIM_MAX_CYCLES = "50000000"
RTTHREAD_SIM_MAX_CYCLES = "10000000"
DEFAULT_AM_HOME = Path("/home/luyoung/ysyx-workbench/abstract-machine")


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


def _soc_diff_run_args(soc_root: Path, *, max_cycles: str = SIM_MAX_CYCLES) -> list[str]:
    return [
        "--max-cycles",
        max_cycles,
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
        include_rtthread = cpu_idx == 1 and soc_idx == 1
        if include_rtthread:
            if shutil.which("scons") is None:
                raise unittest.SkipTest("scons not available for RT-Thread case")
            if not _am_home_ready():
                raise unittest.SkipTest("AM_HOME/AbstractMachine not available for RT-Thread case")
            rtthread_bsp = REPO_ROOT / "fecompiler/thirdparty/rt-thread-am/bsp/abstract-machine"
            if not (rtthread_bsp / "Makefile").exists():
                raise unittest.SkipTest(f"RT-Thread BSP not available: {rtthread_bsp}")

        project_name = f"cpu_soc_matrix_cpu{cpu_idx}_soc{soc_idx}"
        ws_dir = DEFAULT_PROJECTS_ROOT / project_name

        if ws_dir.exists():
            shutil.rmtree(ws_dir)

        spec = CreateWorkspaceData(
            directory=str(ws_dir),
            parameters={"Design": project_name, "Top module": "ecos_sim_top"},
            cpu_filelist=str(cpu_root / "filelist.cpu.f"),
            soc_filelist=str(soc_root / "filelist.soc.f"),
            testbench=str(soc_root / "driver/main.cpp"),
            sim_cpp_sources=_soc_sim_cpp_sources(soc_root),
            sim_cflags=[f"-I{soc_root}"],
            sim_ldflags=_soc_sim_ldflags(soc_root),
            sim_build_all_programs=True,
            sim_program_names=["rtthread"] if include_rtthread else [],
            sim_programs_dir=str(soc_root / "tests" / "programs"),
            sim_run_args=_soc_diff_run_args(
                soc_root,
                max_cycles=RTTHREAD_SIM_MAX_CYCLES if include_rtthread else SIM_MAX_CYCLES,
            ),
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
        if include_rtthread:
            expected_case_names.add("rtthread.soc")
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

        cases_by_name = {
            str(entry.get("name", "")): entry
            for entry in cases
            if isinstance(entry, dict)
        }
        output_cases_root = ws_dir / "sim_verilator" / "output" / "cases"
        self.assertFalse((ws_dir / "rtthread_tests_out").exists())

        for case_name in sorted(expected_case_names):
            case_dir = output_cases_root / case_name
            case_log = case_dir / "log.txt"
            self.assertTrue(case_log.exists(), f"missing case log: {case_log}")
            case_content = case_log.read_text(encoding="utf-8")
            self.assertTrue(case_content, f"empty case log: {case_log}")
            self.assertNotIn("FAILED", case_content)
            self.assertNotIn("%Error", case_content)

            case_entry = cases_by_name.get(case_name)
            self.assertIsNotNone(case_entry, f"missing case entry: {case_name}")
            assert case_entry is not None
            image_path = Path(str(case_entry.get("image", ""))).resolve()
            self.assertTrue(image_path.exists(), f"missing case image: {image_path}")
            self.assertTrue(
                str(image_path).startswith(str(case_dir.resolve())),
                f"case image should be under {case_dir}: {image_path}",
            )

        if include_rtthread:
            rtthread_log = output_cases_root / "rtthread.soc" / "log.txt"
            rtthread_content = rtthread_log.read_text(encoding="utf-8")
            self.assertIn("Thread Operating System", rtthread_content)
            self.assertIn("Hello RISC-V!", rtthread_content)
            self.assertIn("msh />help", rtthread_content)


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
