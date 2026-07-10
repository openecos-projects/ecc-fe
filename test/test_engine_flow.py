#!/usr/bin/env python
"""Tests for fecompiler.engine.flow — EngineFlow and _format_runtime."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.cli import workspace as workspace_cli
from fecompiler.catalog import registry as catalog_registry
from fecompiler.engine.flow import EngineFlow, _format_runtime
from fecompiler.cli.workspace import _apply_default_sim_smoke_suite, _apply_sim_test_suite, run as workspace_cli_run
from fecompiler.soc.registry import soc_runtime_options
from fecompiler.allflow.builder import DEFAULT_FLOW_STEPS
from fecompiler.tools.common.rtl_inputs import (
    prepared_inputs_current,
    slang_defines,
    verilator_lint_defines,
    workspace_input_fingerprint,
)
from fecompiler.tools.slang.runner import SlangElabStep, parse_slang_diagnostics, scan_rtl_structure
from fecompiler.tools.verilator.runner import (
    build_lint_summary,
    parse_verilator_diagnostics,
    _prepare_sim_images,
    _rtthread_build_preflight_errors,
    _rtthread_prepare_helper,
    _sim_cases_from_images,
    _sim_cflags_args,
    _sim_output_ok,
    _sim_run_args,
)

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


def test_generic_simulation_requires_explicit_good_trap():
    assert _sim_output_ok(0, "HIT GOOD TRAP\n") is True
    assert _sim_output_ok(0, "finish after 10 cycles\n") is False
    assert _sim_output_ok(0, "timeout after 10 cycles\n") is False
    assert _sim_output_ok(0, "HIT GOOD TRAP\nHIT BAD TRAP\n") is False
    assert _sim_output_ok(1, "HIT GOOD TRAP\n") is False


def test_prepare_fingerprint_tracks_filelist_and_referenced_rtl_contents(tmp_path):
    rtl = tmp_path / "cpu_top.sv"
    filelist = tmp_path / "filelist.cpu.f"
    rtl.write_text("module cpu_top; endmodule\n", encoding="utf-8")
    filelist.write_text("cpu_top.sv\n", encoding="utf-8")
    workspace = {"cpu_filelist": str(filelist)}
    prepared = {"source_fingerprint": workspace_input_fingerprint(workspace)}

    assert prepared_inputs_current(workspace, prepared) is True

    rtl.write_text("module cpu_top; wire changed; endmodule\n", encoding="utf-8")
    assert prepared_inputs_current(workspace, prepared) is False

    prepared = {"source_fingerprint": workspace_input_fingerprint(workspace)}
    filelist.write_text("# changed filelist\ncpu_top.sv\n", encoding="utf-8")
    assert prepared_inputs_current(workspace, prepared) is False


def _make_fake_soc_root(fe_root: Path, directory_name: str) -> Path:
    soc_root = fe_root / "fecompiler" / "thirdparty" / directory_name
    driver_dir = soc_root / "driver"
    scripts_dir = soc_root / "scripts"
    programs_dir = soc_root / "tests" / "programs"
    out_dir = soc_root / "tests" / "out"
    driver_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    programs_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)
    (soc_root / "filelist.soc.f").write_text("ysyxSoCFull.v\n", encoding="utf-8")
    (driver_dir / "main.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (driver_dir / "dpi_mem.cpp").write_text("int dpi_mem(){return 0;}\n", encoding="utf-8")
    (driver_dir / "difftest.cpp").write_text("int difftest(){return 0;}\n", encoding="utf-8")
    (scripts_dir / "build_test.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    return soc_root.resolve()


def _write_fake_soc_manifests(soc_root: Path, directory_value: str = ".") -> None:
    (soc_root / "catalog.json").write_text(
        json.dumps(
            {
                "id": "ysyx-am-soc",
                "name": "YSYX AM SoC Harness",
                "description": "External test SoC harness.",
                "variant": "soc1",
                "isa": ["rv32"],
                "bus": "ysyx-soc",
                "integration_level": "sim_ready",
                "status": "stable",
                "wrapper_contract": "ecos-sim-wrapper-v1",
                "wrapper_top": "ecos_sim_top",
                "cpu_socket_contract": "ysyx-axi-cpu-socket-v1",
                "supports_difftest": True,
                "supported_test_suites": ["smoke", "cpu-tests", "rtthread", "coremark"],
                "directory": directory_value,
                "tags": ["default"],
            },
        ),
        encoding="utf-8",
    )
    (soc_root / "manifest.json").write_text(
        json.dumps(
            {
                "id": "ysyx-am-soc",
                "name": "YSYX AM SoC Harness",
                "variant": "soc1",
                "top_module": "ecos_sim_top",
                "sim_ready": True,
                "contract": "ecos-sim-wrapper-v1",
                "soc_filelist": "filelist.soc.f",
                "testbench": "driver/main.cpp",
                "sim_cpp_sources": ["driver/dpi_mem.cpp", "driver/difftest.cpp"],
                "sim_cflags": ["-I{soc_root}"],
                "sim_ldflags": ["-ldl"],
                "sim_programs_dir": "tests/programs",
                "sim_tests_dir": "tests/out",
                "sim_build_test_script": "scripts/build_test.sh",
                "supports_difftest": True,
            },
        ),
        encoding="utf-8",
    )


def _is_verilator_compile_cmd(cmd: list[str]) -> bool:
    return "--cc" in cmd and "--exe" in cmd and "--build" in cmd


def _write_fake_sim_binary(cmd: list[str]) -> None:
    sim_bin = Path(cmd[cmd.index("-o") + 1])
    sim_bin.parent.mkdir(parents=True, exist_ok=True)
    sim_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sim_bin.chmod(0o755)


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


def test_create_step_workspaces_lint_data_dir_is_empty(tmp_path):
    engine, ws = _build_engine(tmp_path)
    lint_data_dir = Path(ws["directory"]) / "lint_verilator" / "data"
    assert lint_data_dir.is_dir()
    assert list(lint_data_dir.iterdir()) == []


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


def test_run_step_interruption_clears_ongoing_state(tmp_path, monkeypatch):
    engine, _ = _build_engine(tmp_path)

    def raise_interrupt(_step):
        raise KeyboardInterrupt()

    monkeypatch.setattr(engine, "_run_single_step", raise_interrupt)

    try:
        engine.run_step(FIRST_STEP)
    except KeyboardInterrupt:
        pass

    assert engine.get_step(FIRST_STEP, FIRST_TOOL)["state"] == "Incomplete"


def test_clear_stale_ongoing_states_marks_incomplete(tmp_path):
    engine, _ = _build_engine(tmp_path)
    engine.set_state(name=FIRST_STEP, tool=FIRST_TOOL, state=StateEnum.Ongoing)

    assert engine.clear_stale_ongoing_states() is True
    assert engine.get_step(FIRST_STEP, FIRST_TOOL)["state"] == "Incomplete"
    assert engine.clear_stale_ongoing_states() is False


def test_cpu_tests_selected_empty_cases_falls_back_to_smoke_defaults(tmp_path):
    programs_dir = tmp_path / "programs"
    programs_dir.mkdir()
    (programs_dir / "add.c").write_text("int main() { return 0; }\n", encoding="utf-8")
    (programs_dir / "load-store.c").write_text("int main() { return 0; }\n", encoding="utf-8")
    workspace = {"sim_programs_dir": str(programs_dir)}

    _apply_sim_test_suite(workspace, "cpu_tests", "selected", [])

    assert workspace["sim_build_all_programs"] is False
    assert workspace["sim_program_names"] == ["add"]


def test_cpu_tests_empty_mode_defaults_to_selected_smoke_case(tmp_path):
    programs_dir = tmp_path / "programs"
    programs_dir.mkdir()
    for name in ("add", "load-store", "fib"):
        (programs_dir / f"{name}.c").write_text("int main() { return 0; }\n", encoding="utf-8")
    workspace = {"sim_programs_dir": str(programs_dir)}

    _apply_sim_test_suite(workspace, "cpu_tests", "", [])

    assert workspace["sim_build_all_programs"] is False
    assert workspace["sim_program_names"] == ["add"]


def test_coremark_suite_selects_benchmark_program(tmp_path):
    programs_dir = tmp_path / "programs"
    programs_dir.mkdir()
    (programs_dir / "add.c").write_text("int main() { return 0; }\n", encoding="utf-8")
    (programs_dir / "coremark.c").write_text("int main() { return 0; }\n", encoding="utf-8")
    workspace = {
        "sim_programs_dir": str(programs_dir),
        "core_supported_test_suites": ["smoke", "cpu-tests", "coremark"],
        "soc_supported_test_suites": ["smoke", "cpu-tests", "coremark"],
    }

    _apply_sim_test_suite(workspace, "coremark")

    assert workspace["sim_build_all_programs"] is False
    assert workspace["sim_program_names"] == ["coremark"]
    assert workspace["sim_run_args"] == ["--max-cycles", "200000000"]
    assert workspace["sim_compile_preset"] == "balanced"
    assert workspace["sim_compile_opt_level"] == "-O2"
    assert workspace["sim_compile_march"] == "rv32im_zicsr"
    assert workspace["sim_compile_mabi"] == "ilp32"
    assert workspace["sim_coremark_iterations"] == 1
    assert workspace["sim_coremark_total_data_size"] == 2000


def test_coremark_suite_uses_workspace_runtime_profile(tmp_path):
    programs_dir = tmp_path / "programs"
    programs_dir.mkdir()
    (programs_dir / "coremark.c").write_text("int main() { return 0; }\n", encoding="utf-8")
    workspace = {
        "sim_programs_dir": str(programs_dir),
        "core_supported_test_suites": ["smoke", "cpu-tests", "coremark"],
        "soc_supported_test_suites": ["smoke", "cpu-tests", "coremark"],
        "sim_compile_march": "rv32i_zicsr",
        "sim_coremark_has_float": False,
        "sim_coremark_max_cycles": "12345",
        "sim_coremark_use_difftest": False,
    }

    _apply_sim_test_suite(workspace, "coremark")

    assert workspace["sim_run_args"] == ["--max-cycles", "12345"]
    assert workspace["sim_compile_march"] == "rv32i_zicsr"
    assert workspace["sim_coremark_has_float"] is False


def test_frontend_create_uses_soc_wrapper_top_even_with_legacy_top_param(tmp_path):
    request = tmp_path / "create_frontend.json"
    request.write_text(
        json.dumps(
            {
                "directory": str(tmp_path / "ws_frontend_top"),
                "core_id": "picorv32",
                "soc_harness_id": "ysyx-am-soc",
                "toolchain_id": "riscv32-unknown-elf",
                "test_suite_id": "cpu-tests",
                "parameters": {
                    "Design": "chip",
                    "Top module": "ysyxSoCTop",
                },
            }
        ),
        encoding="utf-8",
    )

    assert workspace_cli_run(["create", "--input-json", str(request), "--json"]) == 0
    ws = load_workspace(str(tmp_path / "ws_frontend_top"))

    assert ws["top_module"] == "ecos_sim_top"
    assert ws["soc_wrapper_top"] == "ecos_sim_top"
    assert ws["soc_wrapper_id"] == "ysyx-am-soc"


def test_frontend_create_persists_default_cpu_test_smoke_case(tmp_path):
    request = tmp_path / "create_frontend_smoke.json"
    request.write_text(
        json.dumps(
            {
                "directory": str(tmp_path / "ws_frontend_smoke"),
                "core_id": "picorv32",
                "soc_harness_id": "ysyx-am-soc",
                "toolchain_id": "riscv32-unknown-elf",
                "test_suite_id": "cpu-tests",
                "parameters": {
                    "Design": "chip",
                    "Top module": "ecos_sim_top",
                },
            }
        ),
        encoding="utf-8",
    )

    assert workspace_cli_run(["create", "--input-json", str(request), "--json"]) == 0
    ws = load_workspace(str(tmp_path / "ws_frontend_smoke"))

    assert ws["sim_build_all_programs"] is False
    assert ws["sim_program_names"] == ["add"]
    assert ws["sim_run_args"] == ["--max-cycles", "50000000"]


def test_frontend_create_with_catalog_cpu_and_user_filelist_adds_adapter_wrapper(tmp_path):
    user_cpu_root = tmp_path / "user_cpu"
    user_cpu_root.mkdir()
    user_cpu_rtl = user_cpu_root / "picorv32_user.v"
    user_cpu_filelist = user_cpu_root / "filelist.cpu.f"
    user_cpu_rtl.write_text(
        "module picorv32(input clk, input resetn, output trap); assign trap = 1'b0; endmodule\n",
        encoding="utf-8",
    )
    user_cpu_filelist.write_text("picorv32_user.v\n", encoding="utf-8")
    request = tmp_path / "create_frontend_user_cpu.json"
    request.write_text(
        json.dumps(
            {
                "directory": str(tmp_path / "ws_frontend_user_cpu"),
                "core_id": "picorv32",
                "soc_harness_id": "ysyx-am-soc",
                "toolchain_id": "riscv32-unknown-elf",
                "test_suite_id": "cpu-tests",
                "cpu_filelist": str(user_cpu_filelist),
                "parameters": {
                    "Design": "chip",
                    "Top module": "ecos_sim_top",
                },
            }
        ),
        encoding="utf-8",
    )

    assert workspace_cli_run(["create", "--input-json", str(request), "--json"]) == 0
    ws = load_workspace(str(tmp_path / "ws_frontend_user_cpu"))

    assert ws["cpu_filelist"] == str(user_cpu_filelist.resolve())
    assert ws["cpu_adapter_filelist"].endswith("fecompiler/adapters/picorv32/filelist.cpu.f")
    assert ws["cpu_wrapper_top"] == "ecos_picorv32_cpu_wrapper"

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    assert engine.run_step("prepare", rerun=True) == StateEnum.Success

    manifest = Path(ws["directory"]) / "prepare_fe" / "output" / "prepared_inputs.json"
    report = Path(ws["directory"]) / "prepare_fe" / "report" / "prepare.rpt"
    prepared = json.loads(manifest.read_text(encoding="utf-8"))
    prepare_report = json.loads(report.read_text(encoding="utf-8"))
    rtl_files = {str(Path(item).resolve()) for item in prepared["rtl_files"]}

    assert str(user_cpu_rtl.resolve()) in rtl_files
    assert any(path.endswith("ecos_picorv32_cpu_wrapper.v") for path in rtl_files)
    assert not any(path.endswith("/SoC/ysyx_00000000.sv") for path in rtl_files)
    assert prepare_report["inputs"]["cpu_filelist"]["path"] == str(user_cpu_filelist.resolve())
    assert prepare_report["inputs"]["cpu_adapter_filelist"]["path"] == ws["cpu_adapter_filelist"]
    assert prepare_report["inputs"]["soc_filelist"]["filtered_rtl_files"] == 1


def test_frontend_create_applies_catalog_coremark_profile(tmp_path, monkeypatch):
    monkeypatch.delenv("ECOS_FE_COMPILER_ROOT", raising=False)
    catalog_registry._catalog.cache_clear()

    request = tmp_path / "create_vexriscv_coremark.json"
    request.write_text(
        json.dumps(
            {
                "directory": str(tmp_path / "ws_vexriscv_coremark"),
                "core_id": "vexriscv",
                "soc_harness_id": "ysyx-am-soc",
                "toolchain_id": "riscv32-unknown-elf",
                "test_suite_id": "coremark",
                "parameters": {
                    "Design": "chip",
                    "Top module": "ecos_sim_top",
                },
            }
        ),
        encoding="utf-8",
    )

    assert workspace_cli_run(["create", "--input-json", str(request), "--json"]) == 0
    ws = load_workspace(str(tmp_path / "ws_vexriscv_coremark"))

    assert ws["sim_program_names"] == ["coremark"]
    assert ws["sim_compile_march"] == "rv32i_zicsr"
    assert ws["sim_coremark_has_float"] is False
    assert ws["sim_coremark_use_difftest"] is False
    assert ws["sim_run_args"] == ["--max-cycles", "200000000"]


def test_prepare_frontend_detail_returns_readiness_payload(tmp_path, capsys):
    request = tmp_path / "create_prepare_detail.json"
    request.write_text(
        json.dumps(
            {
                "directory": str(tmp_path / "ws_prepare_detail"),
                "core_id": "picorv32",
                "soc_harness_id": "ysyx-am-soc",
                "toolchain_id": "riscv32-unknown-elf",
                "test_suite_id": "cpu-tests",
                "parameters": {
                    "Design": "chip",
                    "Top module": "ecos_sim_top",
                },
            }
        ),
        encoding="utf-8",
    )

    assert workspace_cli_run(["create", "--input-json", str(request), "--json"]) == 0
    ws = load_workspace(str(tmp_path / "ws_prepare_detail"))
    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    assert engine.run_step("prepare", rerun=True) == StateEnum.Success

    assert workspace_cli_run([
        "get-info",
        "--directory",
        str(tmp_path / "ws_prepare_detail"),
        "--step",
        "prepare",
        "--id",
        "frontend_detail",
        "--json",
    ]) == 0
    response = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    prepare = response["data"]["info"]["prepare"]

    assert prepare["readiness"]["status"] in {"Ready", "Warning"}
    assert prepare["inputs"]["total_rtl_files"] > 0
    assert prepare["inputs"]["cpu_rtl_files"] > 0
    assert any(item["label"] == "CPU" and item["value"] == "picorv32" for item in prepare["configuration"])
    assert any(item["label"] == "ecos_sim_top" and item["status"] == "OK" for item in prepare["contracts"])
    assert any(item["label"] == "Sim Top" and item["value"] == "ecos_sim_top" for item in prepare["runtime"])


def test_elab_frontend_detail_returns_readiness_and_hierarchy(tmp_path, capsys):
    rtl = tmp_path / "chip_top.v"
    rtl.write_text(
        """
module leaf(input logic clk);
endmodule

module chip_top(input logic clk);
  leaf u_leaf(.clk(clk));
endmodule
""".strip() + "\n",
        encoding="utf-8",
    )
    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_elab_detail"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_elab_detail"))
    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    assert engine.run_step("prepare", rerun=True) == StateEnum.Success
    assert engine.run_step("elab", rerun=True) == StateEnum.Success

    assert workspace_cli_run([
        "get-info",
        "--directory",
        str(tmp_path / "ws_elab_detail"),
        "--step",
        "elab",
        "--id",
        "frontend_detail",
        "--json",
    ]) == 0
    response = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    elab = response["data"]["info"]["elab"]

    assert elab["readiness"]["status"] == "Ready"
    assert elab["readiness"]["top_module"] == "chip_top"
    assert elab["readiness"]["top_found"] is True
    assert elab["hierarchy"]["top_module"] == "chip_top"
    assert "leaf" in elab["hierarchy"]["top_children"]
    assert elab["next_action"]["target"] == "next"


def test_sim_cflags_auto_include_soc_root_when_missing(tmp_path):
    soc_root = tmp_path / "SoC"
    soc_root.mkdir()
    workspace = {
        "sim_cflags": [],
        "sim_soc_root": str(soc_root),
    }

    args = _sim_cflags_args(workspace)

    assert args == ["-CFLAGS", f"-std=c++20 -I{soc_root}"]


def test_sim_without_testbench_fails_instead_of_fake_success(tmp_path):
    rtl = tmp_path / "chip_top.v"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")
    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_no_tb"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_no_tb"))

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("sim", rerun=True)

    assert state == StateEnum.Incomplete
    log_path = Path(ws["directory"]) / "sim_verilator" / "report" / "log.txt"
    assert "simulation testbench is not configured" in log_path.read_text(encoding="utf-8")


def test_workspace_create_fills_soc_defaults_for_empty_gui_sim_lists(tmp_path, monkeypatch):
    fe_root = tmp_path / "ecc-fe"
    soc_root = _make_fake_soc_root(fe_root, "SoC")
    cpu_filelist = tmp_path / "cpu" / "filelist.cpu.f"
    cpu_filelist.parent.mkdir()
    cpu_filelist.write_text("", encoding="utf-8")
    request = tmp_path / "create.json"
    request.write_text(
        json.dumps(
            {
                "directory": str(tmp_path / "ws_soc_defaults"),
                "cpu_filelist": str(cpu_filelist),
                "soc_variant": "soc2",
                "sim_cflags": [],
                "sim_cpp_sources": [],
                "sim_ldflags": [],
                "parameters": {"Design": "chip", "Top module": "ysyxSoCTop"},
            },
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ECOS_FE_COMPILER_ROOT", str(fe_root))
    catalog_registry._catalog.cache_clear()

    assert workspace_cli_run(["create", "--input-json", str(request), "--json"]) == 0
    ws = load_workspace(str(tmp_path / "ws_soc_defaults"))

    assert ws["soc_wrapper_id"] == "ysyx-am-soc"
    assert ws["soc_variant"] == "soc1"
    assert ws["sim_soc_root"] == str(soc_root)
    assert ws["soc_filelist"] == str(soc_root / "filelist.soc.f")
    assert ws["testbench"] == str(soc_root / "driver" / "main.cpp")
    assert ws["sim_cpp_sources"] == [
        str(soc_root / "driver" / "dpi_mem.cpp"),
        str(soc_root / "driver" / "difftest.cpp"),
    ]
    assert ws["sim_cflags"] == [f"-I{soc_root}"]
    assert ws["sim_ldflags"] == ["-ldl"]
    assert ws["sim_programs_dir"] == str(soc_root / "tests" / "programs")
    assert ws["sim_build_test_script"] == str(soc_root / "scripts" / "build_test.sh")


def test_workspace_create_discovers_external_soc_resource_root(tmp_path, monkeypatch):
    fe_root = tmp_path / "ecc-fe-runtime"
    fe_root.mkdir()
    soc_root = _make_fake_soc_root(tmp_path / "resources", "ysyx-am-soc")
    _write_fake_soc_manifests(soc_root)
    cpu_filelist = tmp_path / "cpu" / "filelist.cpu.f"
    cpu_filelist.parent.mkdir()
    cpu_filelist.write_text("", encoding="utf-8")
    request = tmp_path / "create_external_soc.json"
    request.write_text(
        json.dumps(
            {
                "directory": str(tmp_path / "ws_external_soc"),
                "cpu_filelist": str(cpu_filelist),
                "soc_harness_id": "ysyx-am-soc",
                "sim_cflags": [],
                "sim_cpp_sources": [],
                "sim_ldflags": [],
                "parameters": {"Design": "chip", "Top module": "ysyxSoCTop"},
            },
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ECOS_FE_COMPILER_ROOT", str(fe_root))
    monkeypatch.setenv("ECOS_FE_RESOURCE_ROOTS", str(soc_root))
    catalog_registry._catalog.cache_clear()

    assert workspace_cli_run(["create", "--input-json", str(request), "--json"]) == 0
    ws = load_workspace(str(tmp_path / "ws_external_soc"))

    assert ws["soc_wrapper_id"] == "ysyx-am-soc"
    assert ws["sim_soc_root"] == str(soc_root)
    assert ws["soc_filelist"] == str(soc_root / "filelist.soc.f")
    assert ws["testbench"] == str(soc_root / "driver" / "main.cpp")
    assert ws["sim_cpp_sources"] == [
        str(soc_root / "driver" / "dpi_mem.cpp"),
        str(soc_root / "driver" / "difftest.cpp"),
    ]


def test_soc_runtime_options_discovers_external_soc_root(tmp_path, monkeypatch):
    fe_root = tmp_path / "ecc-fe-runtime"
    fe_root.mkdir()
    soc_root = _make_fake_soc_root(tmp_path / "resources", "ysyx-am-soc")
    _write_fake_soc_manifests(soc_root)

    monkeypatch.setenv("ECOS_FE_COMPILER_ROOT", str(fe_root))
    monkeypatch.setenv("ECOS_FE_RESOURCE_ROOTS", str(soc_root))

    options = soc_runtime_options("ysyx-am-soc")

    assert options["sim_soc_root"] == str(soc_root)
    assert options["soc_filelist"] == str(soc_root / "filelist.soc.f")


def test_soc_filelist_script_discovers_examples_resource_root(tmp_path):
    source_soc = Path(__file__).resolve().parent.parent / "fecompiler" / "thirdparty" / "SoC"
    soc_root = tmp_path / "ysyx-am-soc"
    shutil.copytree(source_soc, soc_root)

    examples_root = tmp_path / "ecc-fe-examples"
    cpu_root = examples_root / "examples" / "cl3"
    (cpu_root / "cl3_verilog").mkdir(parents=True)
    (cpu_root / "cl3_verilog" / "filelist.f").write_text("cpu_top.sv\n", encoding="utf-8")

    env = {
        **os.environ,
        "ECOS_FE_RESOURCE_ROOTS": str(examples_root),
    }
    env.pop("CPU_ROOT", None)
    result = subprocess.run(
        [str(soc_root / "scripts" / "gen_filelists.sh")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (cpu_root / "filelist.cpu.f").read_text(encoding="utf-8").splitlines() == [
        "cl3_verilog/cpu_top.sv",
    ]


def test_external_soc_catalog_can_keep_legacy_builtin_directory(tmp_path, monkeypatch):
    fe_root = tmp_path / "ecc-fe-runtime"
    fe_root.mkdir()
    soc_root = _make_fake_soc_root(tmp_path / "resources", "ysyx-am-soc")
    _write_fake_soc_manifests(soc_root, "fecompiler/thirdparty/SoC")

    monkeypatch.setenv("ECOS_FE_COMPILER_ROOT", str(fe_root))
    monkeypatch.setenv("ECOS_FE_RESOURCE_ROOTS", str(soc_root))
    catalog_registry._catalog.cache_clear()

    payload = catalog_registry.catalog_payload()
    soc = next(item for item in payload["soc_harnesses"] if item["id"] == "ysyx-am-soc")

    assert soc["directory"] == str(soc_root)
    assert all(item["id"] != "ysyx-am-soc" for item in payload["cores"])


def test_workspace_create_rejects_removed_placeholder_soc_harness(tmp_path, monkeypatch):
    fe_root = tmp_path / "ecc-fe"
    old_soc_root = _make_fake_soc_root(fe_root, "SoC")
    cpu_filelist = tmp_path / "cpu" / "filelist.cpu.f"
    cpu_filelist.parent.mkdir()
    cpu_filelist.write_text("", encoding="utf-8")
    request = tmp_path / "create_catalog_soc.json"
    request.write_text(
        json.dumps(
            {
                "directory": str(tmp_path / "ws_catalog_soc"),
                "cpu_filelist": str(cpu_filelist),
                "soc_harness_id": "litex-vexriscv-soc",
                "soc_variant": "litex-vexriscv",
                "soc_filelist": str(old_soc_root / "filelist.soc.f"),
                "sim_soc_root": str(old_soc_root),
                "testbench": str(old_soc_root / "driver" / "main.cpp"),
                "sim_cpp_sources": [
                    str(old_soc_root / "driver" / "dpi_mem.cpp"),
                    str(old_soc_root / "driver" / "difftest.cpp"),
                ],
                "sim_cflags": [f"-I{old_soc_root}"],
                "parameters": {"Design": "chip", "Top module": "ecos_sim_top"},
            },
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ECOS_FE_COMPILER_ROOT", str(fe_root))
    catalog_registry._catalog.cache_clear()

    assert workspace_cli_run(["create", "--input-json", str(request), "--json"]) == 1
    assert load_workspace(str(tmp_path / "ws_catalog_soc")) is None


def test_run_step_refreshes_stale_prepare_manifest(tmp_path):
    old_soc = tmp_path / "old_soc.f"
    new_soc = tmp_path / "new_soc.f"
    cpu = tmp_path / "cpu.f"
    old_rtl = tmp_path / "old_soc.v"
    new_rtl = tmp_path / "new_soc.v"
    cpu_rtl = tmp_path / "cpu.v"
    old_rtl.write_text("module old_soc(); endmodule\n", encoding="utf-8")
    new_rtl.write_text("module ecos_sim_top(); endmodule\n", encoding="utf-8")
    cpu_rtl.write_text("module ysyx_00000000(); endmodule\n", encoding="utf-8")
    old_soc.write_text(str(old_rtl) + "\n", encoding="utf-8")
    new_soc.write_text(str(new_rtl) + "\n", encoding="utf-8")
    cpu.write_text(str(cpu_rtl) + "\n", encoding="utf-8")
    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_stale_prepare"),
        parameters={
            "Design": "chip",
            "Top module": "ecos_sim_top",
            "cpu_wrapper_top": "ysyx_00000000",
            "soc_wrapper_id": "ysyx-am-soc",
        },
        cpu_filelist=str(cpu),
        soc_filelist=str(old_soc),
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_stale_prepare"))
    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()

    assert engine.run_step("prepare", rerun=True) == StateEnum.Success
    manifest = Path(ws["prepared_manifest"])
    assert str(old_rtl) in manifest.read_text(encoding="utf-8")

    params_path = Path(ws["parameters_path"])
    params = json.loads(params_path.read_text(encoding="utf-8"))
    params["soc_filelist"] = str(new_soc)
    params_path.write_text(json.dumps(params), encoding="utf-8")
    ws = load_workspace(str(tmp_path / "ws_stale_prepare"))
    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()

    assert workspace_cli_run([
        "run-step",
        "--directory",
        str(tmp_path / "ws_stale_prepare"),
        "--step",
        "lint",
        "--json",
    ]) == 0
    refreshed = json.loads(manifest.read_text(encoding="utf-8"))
    assert str(new_rtl) in refreshed["rtl_files"]
    assert str(old_rtl) not in refreshed["rtl_files"]


def test_workspace_load_repairs_old_frontend_soc_workspace_defaults(tmp_path, monkeypatch):
    fe_root = tmp_path / "ecc-fe"
    soc_root = _make_fake_soc_root(fe_root, "SoC")
    cpu_filelist = tmp_path / "cpu" / "filelist.cpu.f"
    cpu_filelist.parent.mkdir()
    cpu_filelist.write_text("", encoding="utf-8")
    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_old_soc"),
        parameters={"Design": "chip", "Top module": "ysyxSoCTop", "soc_variant": "soc3"},
        cpu_filelist=str(cpu_filelist),
    )
    create_workspace(spec)

    monkeypatch.setenv("ECOS_FE_COMPILER_ROOT", str(fe_root))

    assert workspace_cli_run(["load", "--directory", str(tmp_path / "ws_old_soc"), "--json"]) == 0
    ws = load_workspace(str(tmp_path / "ws_old_soc"))

    assert ws["soc_wrapper_id"] == "ysyx-am-soc"
    assert ws["soc_variant"] == "soc1"
    assert ws["sim_soc_root"] == str(soc_root)
    assert ws["soc_filelist"] == str(soc_root / "filelist.soc.f")
    assert ws["testbench"] == str(soc_root / "driver" / "main.cpp")
    assert ws["sim_cflags"] == [f"-I{soc_root}"]


def test_workspace_help_uses_typer_when_available(capsys):
    if not workspace_cli._typer_available():
        return

    assert workspace_cli_run(["--help"]) == 0

    output = capsys.readouterr().out
    assert "Usage: ecc-fe workspace" in output
    assert "create" in output
    assert "run-step" in output


def test_workspace_create_help_lists_gui_compatible_options(capsys):
    if not workspace_cli._typer_available():
        return

    assert workspace_cli_run(["create", "--help"]) == 0

    output = capsys.readouterr().out
    assert "Usage: ecc-fe workspace create" in output
    assert "--input-json" in output
    assert "--cpu-filelist" in output
    assert "--soc-variant" in output
    assert "--sim-cpp" in output
    assert "--sim-program-source" in output


def test_workspace_cli_falls_back_to_argparse_when_typer_is_missing(
    tmp_path,
    monkeypatch,
    capsys,
):
    import fecompiler.cli.workspace as workspace_module

    monkeypatch.setattr(workspace_module, "click", None)
    monkeypatch.setattr(workspace_module, "typer", None)

    assert workspace_module.run(["load", "--directory", str(tmp_path / "missing"), "--json"]) == 1

    response = json.loads(capsys.readouterr().out)
    assert response["cmd"] == "load_workspace"
    assert response["response"] == "failed"
    assert response["data"]["directory"] == str(tmp_path / "missing")


def test_sim_suite_switching_resets_cpu_and_rtthread_runtime_fields(tmp_path):
    programs_dir = tmp_path / "programs"
    programs_dir.mkdir()
    for name in ("add", "load-store", "fib", "coremark"):
        (programs_dir / f"{name}.c").write_text("int main() { return 0; }\n", encoding="utf-8")
    soc_root = tmp_path / "SoC"
    soc_root.mkdir()
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    workspace = {
        "sim_programs_dir": str(programs_dir),
        "sim_soc_root": str(soc_root),
        "soc_filelist": str(soc_root / "filelist.soc.f"),
    }

    _apply_sim_test_suite(workspace, "rtthread")
    assert workspace["sim_program_names"] == ["rtthread"]
    assert workspace["sim_build_all_programs"] is False

    _apply_sim_test_suite(workspace, "cpu_tests", "selected", ["fib"])
    assert workspace["sim_program_names"] == ["fib"]
    assert workspace["sim_build_all_programs"] is False
    assert "--diff" in workspace["sim_run_args"]
    assert "--timeout-ok" not in workspace["sim_run_args"]

    _apply_sim_test_suite(workspace, "rtthread")
    assert workspace["sim_program_names"] == ["rtthread"]
    assert workspace["sim_run_args"] == [
        "--max-cycles",
        "10000000",
        "--diff",
        "--ref",
        str(soc_root / "tools" / "riscv32-spike-so"),
        "--diff-image-offset",
        "0x100",
        "--diff-reset-vector",
        "0x80000000",
        "--timeout-ok",
    ]

    _apply_sim_test_suite(workspace, "coremark")
    assert workspace["sim_program_names"] == ["coremark"]
    assert workspace["sim_build_all_programs"] is False
    assert "--diff" not in workspace["sim_run_args"]
    assert "--timeout-ok" not in workspace["sim_run_args"]
    assert workspace["sim_compile_opt_level"] == "-O2"


def test_build_all_programs_skips_coremark_benchmark(tmp_path, monkeypatch):
    soc_root = tmp_path / "SoC"
    programs_dir = soc_root / "tests" / "programs"
    build_script = soc_root / "scripts" / "build_test.sh"
    programs_dir.mkdir(parents=True)
    build_script.parent.mkdir(parents=True)
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    (programs_dir / "add.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    (programs_dir / "coremark.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    build_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    build_script.chmod(0o755)

    run_calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, env=None):
        run_calls.append(list(cmd))
        name = cmd[cmd.index("--name") + 1]
        out_dir = Path(cmd[cmd.index("--out_dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.soc.bin").write_bytes(b"\x00")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)

    images, ok = _prepare_sim_images(
        {
            "soc_filelist": str(soc_root / "filelist.soc.f"),
            "sim_build_all_programs": True,
            "sim_programs_dir": str(programs_dir),
        },
        case_output_root=tmp_path / "cases",
    )

    assert ok is True
    assert [call[call.index("--name") + 1] for call in run_calls] == ["add"]
    assert {Path(image).name for image in images} == {"add.soc.bin"}


def test_coremark_build_env_uses_workspace_compile_options(tmp_path, monkeypatch):
    soc_root = tmp_path / "SoC"
    programs_dir = soc_root / "tests" / "programs"
    build_script = soc_root / "scripts" / "build_test.sh"
    programs_dir.mkdir(parents=True)
    build_script.parent.mkdir(parents=True)
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    (programs_dir / "coremark.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    build_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    build_script.chmod(0o755)

    captured_env: dict[str, str] = {}

    def _fake_run(cmd, capture_output=True, text=True, env=None):
        captured_env.update(env or {})
        name = cmd[cmd.index("--name") + 1]
        out_dir = Path(cmd[cmd.index("--out_dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.soc.bin").write_bytes(b"\x00")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)

    build_log = tmp_path / "build.log"
    images, ok = _prepare_sim_images(
        {
            "soc_filelist": str(soc_root / "filelist.soc.f"),
            "sim_programs_dir": str(programs_dir),
            "sim_program_names": ["coremark"],
            "sim_compile_preset": "speed",
            "sim_compile_opt_level": "-O3",
            "sim_compile_march": "rv32imc_zicsr",
            "sim_compile_mabi": "ilp32",
            "sim_compile_extra_cflags": ["-funroll-loops"],
            "sim_coremark_iterations": 64,
            "sim_coremark_total_data_size": 2000,
            "sim_coremark_has_float": False,
        },
        build_log_path=build_log,
        case_output_root=tmp_path / "cases",
    )

    assert ok is True
    assert {Path(image).name for image in images} == {"coremark.soc.bin"}
    assert captured_env["ECOS_SIM_OPT_LEVEL"] == "-O3"
    assert captured_env["ECOS_SIM_MARCH"] == "rv32imc_zicsr"
    assert captured_env["ECOS_SIM_MABI"] == "ilp32"
    assert captured_env["ECOS_SIM_EXTRA_CFLAGS"] == "-funroll-loops"
    assert captured_env["ECOS_SIM_EXTRA_CFLAGS_LINES"] == "-funroll-loops"
    assert captured_env["ECOS_COREMARK_ITERATIONS"] == "64"
    assert captured_env["ECOS_COREMARK_TOTAL_DATA_SIZE"] == "2000"
    assert captured_env["ECOS_COREMARK_HAS_FLOAT"] == "0"
    assert "coremark compile preset=speed opt=-O3" in build_log.read_text(encoding="utf-8")


def test_ibex_program_build_uses_entry_offset(tmp_path, monkeypatch):
    soc_root = tmp_path / "SoC"
    programs_dir = soc_root / "tests" / "programs"
    build_script = soc_root / "scripts" / "build_test.sh"
    programs_dir.mkdir(parents=True)
    build_script.parent.mkdir(parents=True)
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    (programs_dir / "string.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    build_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    build_script.chmod(0o755)

    captured_env: dict[str, str] = {}

    def _fake_run(cmd, capture_output=True, text=True, env=None):
        captured_env.update(env or {})
        name = cmd[cmd.index("--name") + 1]
        out_dir = Path(cmd[cmd.index("--out_dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.soc.bin").write_bytes(b"\x00")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)

    build_log = tmp_path / "build.log"
    images, ok = _prepare_sim_images(
        {
            "cpu_wrapper_id": "ibex",
            "soc_filelist": str(soc_root / "filelist.soc.f"),
            "sim_programs_dir": str(programs_dir),
            "sim_program_names": ["string"],
        },
        build_log_path=build_log,
        case_output_root=tmp_path / "cases",
    )

    assert ok is True
    assert {Path(image).name for image in images} == {"string.soc.bin"}
    assert captured_env["SOC_PROGRAM_ENTRY_OFFSET"] == "0x80"
    assert "ibex program entry offset=0x80" in build_log.read_text(encoding="utf-8")


def test_program_build_failure_does_not_reuse_stale_images(tmp_path, monkeypatch):
    soc_root = tmp_path / "SoC"
    programs_dir = soc_root / "tests" / "programs"
    build_script = soc_root / "scripts" / "build_test.sh"
    stale_image = tmp_path / "old" / "coremark.soc.bin"
    programs_dir.mkdir(parents=True)
    build_script.parent.mkdir(parents=True)
    stale_image.parent.mkdir()
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    (programs_dir / "coremark.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    build_script.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    build_script.chmod(0o755)
    stale_image.write_bytes(b"stale")

    def _fake_run(cmd, capture_output=True, text=True, env=None):
        return SimpleNamespace(returncode=1, stdout="", stderr="compile failed\n")

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)

    build_log = tmp_path / "build.log"
    images, ok = _prepare_sim_images(
        {
            "soc_filelist": str(soc_root / "filelist.soc.f"),
            "sim_programs_dir": str(programs_dir),
            "sim_program_names": ["coremark"],
            "sim_images": [str(stale_image)],
        },
        build_log_path=build_log,
        case_output_root=tmp_path / "cases",
    )

    assert ok is False
    assert images == []
    assert str(stale_image) not in build_log.read_text(encoding="utf-8")


def test_default_sim_smoke_suite_uses_one_case_not_all(tmp_path):
    programs_dir = tmp_path / "programs"
    programs_dir.mkdir()
    for name in ("add", "load-store", "fib"):
        (programs_dir / f"{name}.c").write_text("int main() { return 0; }\n", encoding="utf-8")
    workspace = {"sim_programs_dir": str(programs_dir)}

    _apply_default_sim_smoke_suite(workspace)

    assert workspace["sim_build_all_programs"] is False
    assert workspace["sim_program_names"] == ["add"]


# ── run_all ────────────────────────────────────────────────────────────────────

def test_run_all_stops_at_sim_without_testbench(tmp_path):
    engine, _ = _build_engine(tmp_path)
    ok, reports = engine.run_all()
    assert ok is False
    assert len(reports) == len(DEFAULT_FLOW_STEPS)
    assert [report["step"] for report in reports] == [name for name, _ in DEFAULT_FLOW_STEPS]
    assert all(report["state"] == "Success" for report in reports[:-1])
    assert reports[-1]["step"] == "sim"
    assert reports[-1]["state"] == "Incomplete"


def test_run_all_with_rerun_stops_at_sim_without_testbench(tmp_path):
    engine, _ = _build_engine(tmp_path)
    engine.run_all()
    ok, _ = engine.run_all(rerun=True)
    assert ok is False
    assert engine.get_step("sim", "verilator")["state"] == "Incomplete"


# ── load ───────────────────────────────────────────────────────────────────────

def test_load_restores_state_from_disk(tmp_path):
    engine, ws = _build_engine(tmp_path)
    engine.set_state(name=FIRST_STEP, tool=FIRST_TOOL, state=StateEnum.Success)
    engine2 = EngineFlow(workspace=load_workspace(ws["directory"]))
    engine2.load()
    assert engine2.get_step(FIRST_STEP, FIRST_TOOL)["state"] == "Success"


def test_sync_flow_drops_non_default_steps(tmp_path):
    engine, ws = _build_engine(tmp_path)
    flow_path = Path(ws["flow_path"])
    flow = json.loads(flow_path.read_text(encoding="utf-8"))
    flow["steps"].append(
        {
            "name": "legacy_step",
            "tool": "ecc",
            "state": "Success",
            "runtime": "00:00:01",
            "peak memory (mb)": 0,
            "info": {},
        }
    )
    flow_path.write_text(json.dumps(flow, indent=2), encoding="utf-8")

    synced = EngineFlow(workspace=load_workspace(ws["directory"]))
    names = [s["name"] for s in synced.flow["steps"]]
    assert names == [name for name, _ in DEFAULT_FLOW_STEPS]


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


def test_prepare_filters_soc_cpu_alias_when_cpu_filelist_provides_adapter(tmp_path):
    cpu_root = tmp_path / "cpu"
    soc_root = tmp_path / "soc"
    cpu_root.mkdir()
    soc_root.mkdir()

    cpu_alias = cpu_root / "ecos_cpu_wrapper.v"
    soc_alias = soc_root / "ysyx_00000000.sv"
    soc_top = soc_root / "ecos_sim_top.v"
    cpu_alias.write_text("module ysyx_00000000(); endmodule\n", encoding="utf-8")
    soc_alias.write_text("module ysyx_00000000(); endmodule\n", encoding="utf-8")
    soc_top.write_text("module ecos_sim_top(); endmodule\n", encoding="utf-8")
    (cpu_root / "filelist.cpu.f").write_text("ecos_cpu_wrapper.v\n", encoding="utf-8")
    (soc_root / "filelist.soc.f").write_text("ecos_sim_top.v\nysyx_00000000.sv\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_prepare_filter"),
        parameters={
            "Design": "chip",
            "Top module": "ecos_sim_top",
            "cpu_wrapper_top": "ecos_cpu_wrapper",
        },
        cpu_filelist=str(cpu_root / "filelist.cpu.f"),
        soc_filelist=str(soc_root / "filelist.soc.f"),
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_prepare_filter"))

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("prepare", rerun=True)

    manifest = Path(ws["directory"]) / "prepare_fe" / "output" / "prepared_inputs.json"
    report = Path(ws["directory"]) / "prepare_fe" / "report" / "prepare.rpt"
    prepared = json.loads(manifest.read_text(encoding="utf-8"))
    prepare_report = json.loads(report.read_text(encoding="utf-8"))

    assert state == StateEnum.Success
    assert str(cpu_alias.resolve()) in prepared["rtl_files"]
    assert str(soc_top.resolve()) in prepared["rtl_files"]
    assert str(soc_alias.resolve()) not in prepared["rtl_files"]
    assert prepare_report["inputs"]["soc_filelist"]["filtered"] == [str(soc_alias.resolve())]


def test_prepare_generates_cpu_alias_for_cpu_top_filelist(tmp_path):
    cpu_root = tmp_path / "cpu"
    soc_root = tmp_path / "soc"
    cpu_root.mkdir()
    soc_root.mkdir()

    cpu_top = cpu_root / "cpu_top.v"
    soc_alias = soc_root / "ysyx_00000000.sv"
    soc_top = soc_root / "ecos_sim_top.v"
    cpu_top.write_text("module cpu_top(); endmodule\n", encoding="utf-8")
    soc_alias.write_text("module ysyx_00000000(); endmodule\n", encoding="utf-8")
    soc_top.write_text("module ecos_sim_top(); endmodule\n", encoding="utf-8")
    (cpu_root / "filelist.cpu.f").write_text("cpu_top.v\n", encoding="utf-8")
    (soc_root / "filelist.soc.f").write_text("ecos_sim_top.v\nysyx_00000000.sv\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_prepare_generated_alias"),
        parameters={
            "Design": "chip",
            "Top module": "ecos_sim_top",
            "cpu_wrapper_top": "ysyx_00000000",
            "cpu_standard_top": "cpu_top",
            "cpu_wrapper_generation": "standard_alias_v1",
        },
        cpu_filelist=str(cpu_root / "filelist.cpu.f"),
        soc_filelist=str(soc_root / "filelist.soc.f"),
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_prepare_generated_alias"))

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("prepare", rerun=True)

    manifest = Path(ws["directory"]) / "prepare_fe" / "output" / "prepared_inputs.json"
    report = Path(ws["directory"]) / "prepare_fe" / "report" / "prepare.rpt"
    prepared = json.loads(manifest.read_text(encoding="utf-8"))
    prepare_report = json.loads(report.read_text(encoding="utf-8"))
    generated = Path(ws["directory"]) / "prepare_fe" / "output" / "generated_standard_cpu_wrapper.sv"
    generated_text = generated.read_text(encoding="utf-8")

    assert state == StateEnum.Success
    assert generated.is_file()
    assert "cpu_top cl3_top" in generated_text
    assert ".io_extIrq" in generated_text
    assert ".io_master_aw_ready" in generated_text
    assert ".io_master_aw_bits_awaddr" in generated_text
    assert ".io_interrupt" not in generated_text
    assert ".io_master_awready" not in generated_text
    assert str(cpu_top.resolve()) in prepared["rtl_files"]
    assert str(generated.resolve()) in prepared["rtl_files"]
    assert str(soc_alias.resolve()) not in prepared["rtl_files"]
    assert prepare_report["inputs"]["generated_cpu_wrapper"]["generated"] is True
    assert prepare_report["inputs"]["soc_filelist"]["filtered"] == [str(soc_alias.resolve())]


def test_prepare_fails_when_frontend_workspace_has_duplicate_cpu_alias(tmp_path):
    cpu_root = tmp_path / "cpu"
    soc_root = tmp_path / "soc"
    cpu_root.mkdir()
    soc_root.mkdir()

    (cpu_root / "cpu_alias.v").write_text("module ysyx_00000000(); endmodule\n", encoding="utf-8")
    (soc_root / "soc_alias.v").write_text("module ysyx_00000000(); endmodule\n", encoding="utf-8")
    (cpu_root / "filelist.cpu.f").write_text(
        f"cpu_alias.v\n{soc_root / 'soc_alias.v'}\n",
        encoding="utf-8",
    )

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_prepare_duplicate_alias"),
        parameters={
            "Design": "chip",
            "Top module": "ecos_sim_top",
            "cpu_wrapper_top": "ysyx_00000000",
            "soc_wrapper_id": "ysyx-am-soc",
        },
        cpu_filelist=str(cpu_root / "filelist.cpu.f"),
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_prepare_duplicate_alias"))

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("prepare", rerun=True)

    assert state == StateEnum.Incomplete
    prepare_subflow = (Path(ws["directory"]) / "prepare_fe" / "subflow.json").read_text(encoding="utf-8")
    assert "requires exactly one ysyx_00000000 compatibility module" in prepare_subflow


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
    img = tmp_path / "tests" / "out" / "min2.soc.bin"
    inc.mkdir()
    img.parent.mkdir(parents=True)
    img.write_bytes(b"\x00")
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
        sim_run_args=["--image", str(img), "--max-cycles", "100"],
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_sim_opts"))

    run_calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True):
        run_calls.append(list(cmd))
        if _is_verilator_compile_cmd(cmd):
            _write_fake_sim_binary(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="HIT GOOD TRAP\n", stderr="")

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("sim", rerun=True)

    compile_cmd = next(c for c in run_calls if _is_verilator_compile_cmd(c))
    simulate_cmd = next(c for c in run_calls if not _is_verilator_compile_cmd(c))

    assert state == StateEnum.Success
    assert str(tb.resolve()) in compile_cmd
    assert str(helper.resolve()) in compile_cmd
    assert "--timing" in compile_cmd
    assert "-CFLAGS" in compile_cmd
    assert f"-I{inc}" in compile_cmd[compile_cmd.index("-CFLAGS") + 1]
    assert "-LDFLAGS" in compile_cmd
    assert "-lm" in compile_cmd[compile_cmd.index("-LDFLAGS") + 1]
    assert "--image" in simulate_cmd
    assert simulate_cmd[simulate_cmd.index("--image") + 1] == str(img)
    assert "--max-cycles" in simulate_cmd
    assert simulate_cmd[simulate_cmd.index("--max-cycles") + 1] == "100"
    assert "--wave" in simulate_cmd
    expected_wave = (
        Path(ws["directory"]) / "sim_verilator" / "output" / "cases" / "min2.soc" / "wave.vcd"
    ).resolve()
    assert Path(simulate_cmd[simulate_cmd.index("--wave") + 1]) == expected_wave


def test_sim_resolves_relative_include_flag_from_workspace_root(tmp_path, monkeypatch):
    rtl = tmp_path / "chip_top.v"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")
    tb = tmp_path / "tb_main.cpp"
    helper = tmp_path / "tb_helper.cpp"
    inc = tmp_path / "fecompiler" / "thirdparty" / "SoC"
    inc.mkdir(parents=True)
    tb.write_text("int main(int argc, char** argv){ return 0; }\n", encoding="utf-8")
    helper.write_text("int helper(){return 0;}\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_sim_rel_inc"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
        testbench=str(tb),
        sim_cpp_sources=[str(helper)],
        sim_cflags=["-Ifecompiler/thirdparty/SoC"],
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_sim_rel_inc"))

    run_calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True):
        run_calls.append(list(cmd))
        if _is_verilator_compile_cmd(cmd):
            _write_fake_sim_binary(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="HIT GOOD TRAP\n", stderr="")

    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(tmp_path))
    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("sim", rerun=True)

    compile_cmd = next(c for c in run_calls if _is_verilator_compile_cmd(c))
    cflags = compile_cmd[compile_cmd.index("-CFLAGS") + 1]

    assert state == StateEnum.Success
    assert f"-I{inc.resolve()}" in cflags


def test_sim_runs_multiple_images_with_separate_logs(tmp_path, monkeypatch):
    rtl = tmp_path / "chip_top.v"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")
    tb = tmp_path / "tb_main.cpp"
    helper = tmp_path / "tb_helper.cpp"
    img1 = tmp_path / "tests" / "out" / "a.soc.bin"
    img2 = tmp_path / "tests" / "out" / "b.soc.bin"
    img1.parent.mkdir(parents=True, exist_ok=True)
    img1.write_bytes(b"\x01")
    img2.write_bytes(b"\x02")
    tb.write_text("int main(int argc, char** argv){ return 0; }\n", encoding="utf-8")
    helper.write_text("int helper(){return 0;}\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_sim_multi"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
        testbench=str(tb),
        sim_cpp_sources=[str(helper)],
        sim_run_args=["--max-cycles", "100"],
        sim_images=[str(img1), str(img2)],
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_sim_multi"))

    run_calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True):
        run_calls.append(list(cmd))
        if _is_verilator_compile_cmd(cmd):
            _write_fake_sim_binary(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        image = ""
        if "--image" in cmd:
            image = cmd[cmd.index("--image") + 1]
        return SimpleNamespace(returncode=0, stdout=f"ok:{image}\nHIT GOOD TRAP\n", stderr="")

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("sim", rerun=True)

    assert state == StateEnum.Success
    sim_calls = [c for c in run_calls if not _is_verilator_compile_cmd(c)]
    assert len(sim_calls) == 2
    out_cases_dir = Path(ws["directory"]) / "sim_verilator" / "output" / "cases"
    for call in sim_calls:
        assert "--wave" in call
        wave_path = Path(call[call.index("--wave") + 1]).resolve()
        assert str(wave_path).startswith(str(out_cases_dir.resolve()))

    report_dir = Path(ws["directory"]) / "sim_verilator" / "report"
    cases_json = json.loads((report_dir / "cases.json").read_text(encoding="utf-8"))
    assert len(cases_json["cases"]) == 2
    assert (report_dir / "cases" / "a.soc" / "log.txt").exists()
    assert (report_dir / "cases" / "b.soc" / "log.txt").exists()


def test_sim_single_image_args_still_writes_cases_structure(tmp_path, monkeypatch):
    rtl = tmp_path / "chip_top.v"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")
    tb = tmp_path / "tb_main.cpp"
    img = tmp_path / "tests" / "out" / "single.soc.bin"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\x01")
    tb.write_text("int main(int argc, char** argv){ return 0; }\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_sim_single_case"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
        testbench=str(tb),
        sim_run_args=["--image", str(img), "--max-cycles", "100"],
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_sim_single_case"))
    run_calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True):
        run_calls.append(list(cmd))
        if _is_verilator_compile_cmd(cmd):
            _write_fake_sim_binary(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="ok-single\nHIT GOOD TRAP\n", stderr="")

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("sim", rerun=True)
    assert state == StateEnum.Success

    report_dir = Path(ws["directory"]) / "sim_verilator" / "report"
    assert (report_dir / "cases" / "single.soc" / "log.txt").exists()
    runs_root = report_dir / "runs"
    run_dirs = sorted([p for p in runs_root.iterdir() if p.is_dir()])
    assert run_dirs
    latest_run = run_dirs[-1]
    assert (latest_run / "cases" / "single.soc" / "log.txt").exists()
    simulate_cmd = next(c for c in run_calls if not _is_verilator_compile_cmd(c))
    assert "--wave" in simulate_cmd
    expected_wave = (
        Path(ws["directory"]) / "sim_verilator" / "output" / "cases" / "single.soc" / "wave.vcd"
    ).resolve()
    assert Path(simulate_cmd[simulate_cmd.index("--wave") + 1]) == expected_wave


def test_rtthread_run_does_not_reuse_previous_cpu_tests_cases(tmp_path, monkeypatch):
    rtl = tmp_path / "chip_top.v"
    tb = tmp_path / "tb_main.cpp"
    soc_root = tmp_path / "SoC"
    programs_dir = soc_root / "tests" / "programs"
    build_script = soc_root / "scripts" / "build_test.sh"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")
    tb.write_text("int main(int argc, char** argv){ return 0; }\n", encoding="utf-8")
    programs_dir.mkdir(parents=True)
    build_script.parent.mkdir(parents=True)
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    (programs_dir / "add.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    build_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_rtthread_no_reuse"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
        soc_filelist=str(soc_root / "filelist.soc.f"),
        testbench=str(tb),
        sim_programs_dir=str(programs_dir),
        sim_build_test_script=str(build_script),
        sim_program_names=["add"],
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_rtthread_no_reuse"))

    def _fake_run(cmd, capture_output=True, text=True, env=None):
        if _is_verilator_compile_cmd(cmd):
            _write_fake_sim_binary(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "--name" in cmd:
            name = cmd[cmd.index("--name") + 1]
            out_dir = Path(cmd[cmd.index("--out_dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{name}.soc.bin").write_bytes(b"\x00")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "--image" in cmd and Path(cmd[cmd.index("--image") + 1]).name == "rtthread.soc.bin":
            return SimpleNamespace(returncode=0, stdout="booted but no shell transcript\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="cpu ok\nHIT GOOD TRAP\n", stderr="")

    def _fake_sim_process(cmd, *, stream_output):
        if "--image" in cmd and Path(cmd[cmd.index("--image") + 1]).name == "rtthread.soc.bin":
            return 0, "booted but no shell transcript\n"
        return 0, "cpu ok\nHIT GOOD TRAP\n"

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)
    monkeypatch.setattr("fecompiler.tools.verilator.runner._run_sim_process", _fake_sim_process)
    monkeypatch.setattr("fecompiler.tools.verilator.runner._rtthread_build_preflight_errors", lambda workspace: [])

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()

    ws["sim_program_names"] = ["add"]
    state = engine.run_step("sim", rerun=True)
    assert state == StateEnum.Success
    report_dir = Path(ws["directory"]) / "sim_verilator" / "report"
    cpu_payload = json.loads((report_dir / "cases.json").read_text(encoding="utf-8"))
    assert cpu_payload["suite"] == "cpu_tests"
    assert cpu_payload["cases"][0]["name"] == "add.soc"

    ws["sim_program_names"] = ["rtthread"]
    ws["sim_run_args"] = ["--max-cycles", "10000000", "--wave", "/dev/null"]
    state = engine.run_step("sim", rerun=True)

    rtthread_payload = json.loads((report_dir / "cases.json").read_text(encoding="utf-8"))
    rtthread_case = rtthread_payload["cases"][0]
    assert state == StateEnum.Incomplete
    assert rtthread_payload["suite"] == "rtthread"
    assert rtthread_case["name"] == "rtthread.soc"
    assert rtthread_case["ok"] is False
    assert "Thread Operating System" in rtthread_case["validation"]["missing_markers"]
    assert cpu_payload["run_id"] != rtthread_payload["run_id"]


def test_rtthread_terminal_markers_are_required_for_success(tmp_path, monkeypatch):
    rtl = tmp_path / "chip_top.v"
    tb = tmp_path / "tb_main.cpp"
    soc_root = tmp_path / "SoC"
    programs_dir = soc_root / "tests" / "programs"
    build_script = soc_root / "scripts" / "build_test.sh"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")
    tb.write_text("int main(int argc, char** argv){ return 0; }\n", encoding="utf-8")
    programs_dir.mkdir(parents=True)
    build_script.parent.mkdir(parents=True)
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    build_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_rtthread_markers"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
        soc_filelist=str(soc_root / "filelist.soc.f"),
        testbench=str(tb),
        sim_programs_dir=str(programs_dir),
        sim_build_test_script=str(build_script),
        sim_program_names=["rtthread"],
        sim_run_args=["--max-cycles", "10000000", "--wave", "/dev/null"],
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_rtthread_markers"))

    def _fake_run(cmd, capture_output=True, text=True, env=None):
        if _is_verilator_compile_cmd(cmd):
            _write_fake_sim_binary(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "--name" in cmd:
            name = cmd[cmd.index("--name") + 1]
            out_dir = Path(cmd[cmd.index("--out_dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{name}.soc.bin").write_bytes(b"\x00")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def _fake_sim_process(cmd, *, stream_output):
        output = "\n".join([
            "[soc-sim][difftest] enabled",
            "Thread Operating System",
            "Hello RISC-V!",
            "msh />help",
            "RT-Thread shell commands:",
            "[soc-sim] timeout after 10000000 cycles",
            "",
        ])
        return 0, output

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)
    monkeypatch.setattr("fecompiler.tools.verilator.runner._run_sim_process", _fake_sim_process)
    monkeypatch.setattr("fecompiler.tools.verilator.runner._rtthread_build_preflight_errors", lambda workspace: [])

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("sim", rerun=True)

    assert state == StateEnum.Success
    report_dir = Path(ws["directory"]) / "sim_verilator" / "report"
    payload = json.loads((report_dir / "cases.json").read_text(encoding="utf-8"))
    case = payload["cases"][0]
    assert payload["suite"] == "rtthread"
    assert case["ok"] is True
    assert case["validation"]["missing_markers"] == []


def test_sim_can_reuse_existing_binary_without_recompile(tmp_path, monkeypatch):
    rtl = tmp_path / "chip_top.v"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")
    img = tmp_path / "tests" / "out" / "min2.soc.bin"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\x00")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_sim_reuse"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
        sim_run_args=["--image", str(img), "--max-cycles", "100"],
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_sim_reuse"))

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()

    sim_bin = Path(ws["directory"]) / "sim_verilator" / "output" / "chip_sim"
    sim_bin.parent.mkdir(parents=True, exist_ok=True)
    sim_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sim_bin.chmod(0o755)
    ws["sim_reuse_binary"] = True

    run_calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True):
        run_calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="HIT GOOD TRAP\n", stderr="")

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)
    monkeypatch.setattr("fecompiler.tools.verilator.runner._rtthread_build_preflight_errors", lambda workspace: [])
    state = engine.run_step("sim", rerun=True)

    assert state == StateEnum.Success
    assert all(not _is_verilator_compile_cmd(call) for call in run_calls)


def test_coremark_sim_log_includes_readable_result_summary(tmp_path, monkeypatch):
    rtl = tmp_path / "chip_top.v"
    tb = tmp_path / "tb_main.cpp"
    soc_root = tmp_path / "SoC"
    programs_dir = soc_root / "tests" / "programs"
    build_script = soc_root / "scripts" / "build_test.sh"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")
    tb.write_text("int main(int argc, char** argv){ return 0; }\n", encoding="utf-8")
    programs_dir.mkdir(parents=True)
    build_script.parent.mkdir(parents=True)
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    (programs_dir / "coremark.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    build_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_coremark_summary"),
        parameters={"Design": "chip", "Top module": "chip_top", "Frequency max [MHz]": 100},
        origin_verilog=str(rtl),
        soc_filelist=str(soc_root / "filelist.soc.f"),
        testbench=str(tb),
        sim_programs_dir=str(programs_dir),
        sim_build_test_script=str(build_script),
        sim_program_names=["coremark"],
        sim_run_args=["--max-cycles", "200000000"],
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_coremark_summary"))
    run_calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, env=None):
        run_calls.append(list(cmd))
        if _is_verilator_compile_cmd(cmd):
            _write_fake_sim_binary(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "--name" in cmd:
            name = cmd[cmd.index("--name") + 1]
            out_dir = Path(cmd[cmd.index("--out_dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{name}.soc.bin").write_bytes(b"\x00")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout="\n".join([
                "2K performance run parameters for coremark.",
                "Iterations       : 1",
                "Correct operation validated. See README.md for run and reporting rules.",
                "CoreMark 1.0 : 1234.5 / GCC -O3",
                "CoreMark/MHz: 12.345",
                "",
            ]),
            stderr="[soc-sim] finish after 64000 cycles\n",
        )

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("sim", rerun=True)

    report_dir = Path(ws["directory"]) / "sim_verilator" / "report"
    payload = json.loads((report_dir / "cases.json").read_text(encoding="utf-8"))
    case = payload["cases"][0]
    log_text = Path(case["log"]).read_text(encoding="utf-8")
    summary_text = (report_dir / "log.txt").read_text(encoding="utf-8")

    assert state == StateEnum.Success
    simulate_cmd = next(c for c in run_calls if not _is_verilator_compile_cmd(c) and "--name" not in c)
    assert "--wave" not in simulate_cmd
    assert payload["suite"] == "coremark"
    assert case["name"] == "coremark.soc"
    assert case["wave"] == ""
    assert case["metrics"]["cycles_per_iteration"] == 64000
    assert case["metrics"]["coremark_per_mhz"] == 12.345
    assert case["metrics"]["coremark_per_second"] == 1234.5
    assert "ECOS Simulation Result" in log_text
    assert "Suite       : CoreMark" in log_text
    assert "Status      : PASS" in log_text
    assert "Benchmark   : EEMBC CoreMark" in log_text
    assert "Iterations  : 1" in log_text
    assert "Cycles      : 64000" in log_text
    assert "Clock       : 100 MHz" in log_text
    assert "Cycles/iter : 64000" in log_text
    assert "CoreMark/MHz: 12.345" in log_text
    assert "CoreMark/s  : 1234.5" in log_text
    assert "Correct operation validated" in log_text
    assert case["validation"]["validated"] is True
    assert case["validation"]["errors_detected"] is False
    assert "[coremark.soc] status=PASS rc=0 suite=coremark" in summary_text


def test_coremark_sim_log_explains_missing_score_when_cycles_absent(tmp_path, monkeypatch):
    rtl = tmp_path / "chip_top.v"
    tb = tmp_path / "tb_main.cpp"
    soc_root = tmp_path / "SoC"
    programs_dir = soc_root / "tests" / "programs"
    build_script = soc_root / "scripts" / "build_test.sh"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")
    tb.write_text("int main(int argc, char** argv){ return 0; }\n", encoding="utf-8")
    programs_dir.mkdir(parents=True)
    build_script.parent.mkdir(parents=True)
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    (programs_dir / "coremark.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    build_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_coremark_no_cycles"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
        soc_filelist=str(soc_root / "filelist.soc.f"),
        testbench=str(tb),
        sim_programs_dir=str(programs_dir),
        sim_build_test_script=str(build_script),
        sim_program_names=["coremark"],
        sim_run_args=["--max-cycles", "200000000"],
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_coremark_no_cycles"))

    def _fake_run(cmd, capture_output=True, text=True, env=None):
        if _is_verilator_compile_cmd(cmd):
            _write_fake_sim_binary(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "--name" in cmd:
            name = cmd[cmd.index("--name") + 1]
            out_dir = Path(cmd[cmd.index("--out_dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{name}.soc.bin").write_bytes(b"\x00")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout="coremark done\nCorrect operation validated. See README.md for run and reporting rules.\n",
            stderr="",
        )

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("sim", rerun=True)

    report_dir = Path(ws["directory"]) / "sim_verilator" / "report"
    payload = json.loads((report_dir / "cases.json").read_text(encoding="utf-8"))
    case = payload["cases"][0]
    log_text = Path(case["log"]).read_text(encoding="utf-8")

    assert state == StateEnum.Success
    assert case["metrics"]["score_available"] is False
    assert case["metrics"]["score_unavailable_reason"] == "simulation cycle count not found"
    assert "Score       : unavailable (simulation cycle count not found)" in log_text


def test_coremark_validation_errors_fail_sim_case(tmp_path, monkeypatch):
    rtl = tmp_path / "chip_top.v"
    tb = tmp_path / "tb_main.cpp"
    soc_root = tmp_path / "SoC"
    programs_dir = soc_root / "tests" / "programs"
    build_script = soc_root / "scripts" / "build_test.sh"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")
    tb.write_text("int main(int argc, char** argv){ return 0; }\n", encoding="utf-8")
    programs_dir.mkdir(parents=True)
    build_script.parent.mkdir(parents=True)
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    (programs_dir / "coremark.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    build_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    spec = CreateWorkspaceData(
        directory=str(tmp_path / "ws_coremark_validation_error"),
        parameters={"Design": "chip", "Top module": "chip_top"},
        origin_verilog=str(rtl),
        soc_filelist=str(soc_root / "filelist.soc.f"),
        testbench=str(tb),
        sim_programs_dir=str(programs_dir),
        sim_build_test_script=str(build_script),
        sim_program_names=["coremark"],
        sim_run_args=["--max-cycles", "200000000"],
    )
    create_workspace(spec)
    ws = load_workspace(str(tmp_path / "ws_coremark_validation_error"))

    def _fake_run(cmd, capture_output=True, text=True, env=None):
        if _is_verilator_compile_cmd(cmd):
            _write_fake_sim_binary(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "--name" in cmd:
            name = cmd[cmd.index("--name") + 1]
            out_dir = Path(cmd[cmd.index("--out_dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{name}.soc.bin").write_bytes(b"\x00")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout="[0]ERROR! list crc 0x0000 - should be 0xe714\nErrors detected\nHIT GOOD TRAP\n",
            stderr="[soc-sim] finish after 64000 cycles\n",
        )

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)

    engine = EngineFlow(workspace=ws)
    engine.create_step_workspaces()
    state = engine.run_step("sim", rerun=True)

    report_dir = Path(ws["directory"]) / "sim_verilator" / "report"
    payload = json.loads((report_dir / "cases.json").read_text(encoding="utf-8"))
    case = payload["cases"][0]
    log_text = Path(case["log"]).read_text(encoding="utf-8")

    assert state == StateEnum.Incomplete
    assert case["ok"] is False
    assert case["validation"] == {
        "type": "coremark_validation",
        "validated": False,
        "errors_detected": True,
    }
    assert "Errors detected: yes" in log_text


def test_rtthread_program_enables_default_difftest_args(tmp_path):
    soc_root = tmp_path / "SoC"
    soc_root.mkdir()
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    ref_so = soc_root / "tools" / "riscv32-spike-so"
    ref_so.parent.mkdir()
    ref_so.write_bytes(b"")

    args = _sim_run_args({
        "soc_filelist": str(soc_root / "filelist.soc.f"),
        "sim_program_names": ["rtthread"],
    })

    assert "--max-cycles" in args
    assert args[args.index("--max-cycles") + 1] == "10000000"
    assert "--diff" in args
    assert args[args.index("--ref") + 1] == str(ref_so)
    assert args[args.index("--diff-image-offset") + 1] == "0x100"
    assert args[args.index("--diff-reset-vector") + 1] == "0x80000000"
    assert "--timeout-ok" in args


def test_rtthread_program_omits_difftest_for_generic_cpu():
    args = _sim_run_args({
        "sim_program_names": ["rtthread"],
        "cpu_supports_difftest": False,
        "soc_supports_difftest": True,
        "sim_run_args": ["--max-cycles", "10000000", "--timeout-ok"],
    })

    assert args == ["--max-cycles", "10000000", "--timeout-ok"]


def test_rtthread_program_uses_external_difftest_ref_when_soc_ref_is_split(tmp_path, monkeypatch):
    soc_root = tmp_path / "SoC"
    ref_root = tmp_path / "ecc-fe-difftest-ref"
    ref_so = ref_root / "tools" / "riscv32-spike-so"
    soc_root.mkdir()
    ref_so.parent.mkdir(parents=True)
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    ref_so.write_bytes(b"")
    monkeypatch.setenv("ECOS_FE_RESOURCE_ROOTS", str(ref_root))

    args = _sim_run_args({
        "soc_filelist": str(soc_root / "filelist.soc.f"),
        "sim_program_names": ["rtthread"],
    })

    assert args[args.index("--ref") + 1] == str(ref_so.resolve())


def test_rtthread_program_keeps_explicit_difftest_args(tmp_path):
    soc_root = tmp_path / "SoC"
    soc_root.mkdir()
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")

    explicit = [
        "--max-cycles",
        "1234",
        "--diff",
        "--ref",
        "/tmp/custom-ref.so",
    ]
    args = _sim_run_args({
        "soc_filelist": str(soc_root / "filelist.soc.f"),
        "sim_program_names": ["rtthread"],
        "sim_run_args": explicit,
    })

    assert args == explicit


def test_build_all_programs_and_rtthread_emit_case_images(tmp_path, monkeypatch):
    soc_root = tmp_path / "SoC"
    programs_dir = soc_root / "tests" / "programs"
    build_script = soc_root / "scripts" / "build_test.sh"
    programs_dir.mkdir(parents=True)
    build_script.parent.mkdir(parents=True)
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    (programs_dir / "add.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    (programs_dir / "bit.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    build_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    build_script.chmod(0o755)

    run_calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, env=None):
        run_calls.append(list(cmd))
        name = cmd[cmd.index("--name") + 1]
        out_dir = Path(cmd[cmd.index("--out_dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.soc.bin").write_bytes(b"\x00")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)
    monkeypatch.setattr("fecompiler.tools.verilator.runner._rtthread_build_preflight_errors", lambda workspace: [])

    case_root = tmp_path / "ws" / "sim_verilator" / "output" / "cases"
    images, ok = _prepare_sim_images(
        {
            "soc_filelist": str(soc_root / "filelist.soc.f"),
            "sim_build_all_programs": True,
            "sim_program_names": ["rtthread"],
            "sim_programs_dir": str(programs_dir),
            "sim_run_args": ["--diff"],
        },
        case_output_root=case_root,
    )

    assert ok is True
    assert [Path(call[call.index("--name") + 1]).name for call in run_calls] == ["add", "bit", "rtthread"]
    rtthread_call = next(call for call in run_calls if call[call.index("--name") + 1] == "rtthread")
    assert "--src" not in rtthread_call
    expected = {
        case_root / "add.soc" / "add.soc.bin",
        case_root / "bit.soc" / "bit.soc.bin",
        case_root / "rtthread.soc" / "rtthread.soc.bin",
    }
    assert {Path(image) for image in images} == expected

    cases = _sim_cases_from_images(images, ["--diff"])
    rtthread_case = next(case for case in cases if case["name"] == "rtthread.soc")
    add_case = next(case for case in cases if case["name"] == "add.soc")
    assert "--timeout-ok" in rtthread_case["args"]
    assert "--timeout-ok" not in add_case["args"]


def test_rtthread_build_failure_reports_dependency_diagnosis(tmp_path, monkeypatch):
    soc_root = tmp_path / "SoC"
    programs_dir = soc_root / "tests" / "programs"
    build_script = soc_root / "scripts" / "build_test.sh"
    programs_dir.mkdir(parents=True)
    build_script.parent.mkdir(parents=True)
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    build_script.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    build_script.chmod(0o755)

    run_calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, env=None):
        run_calls.append(list(cmd))
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="scons is required to build rt-thread-am\n",
        )

    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)
    monkeypatch.setattr("fecompiler.tools.verilator.runner._rtthread_build_preflight_errors", lambda workspace: [])

    build_log = tmp_path / "build_programs.log.txt"
    images, ok = _prepare_sim_images(
        {
            "soc_filelist": str(soc_root / "filelist.soc.f"),
            "sim_program_names": ["rtthread"],
            "sim_programs_dir": str(programs_dir),
            "sim_run_args": ["--diff"],
        },
        build_log_path=build_log,
        case_output_root=tmp_path / "cases",
    )

    assert images == []
    assert ok is False
    assert run_calls and "--src" not in run_calls[0]
    content = build_log.read_text(encoding="utf-8")
    assert "src=rtthread-am BSP" in content
    assert "scons is required to build rt-thread-am" in content
    assert "diagnosis: missing dependency: install scons or keep the RT-Thread fallback helper available" in content


def test_rtthread_build_preflight_reports_missing_scons_without_spawn(tmp_path, monkeypatch):
    soc_root = tmp_path / "SoC"
    programs_dir = soc_root / "tests" / "programs"
    build_script = soc_root / "scripts" / "build_test.sh"
    rtthread_bsp = soc_root.parent / "rt-thread-am" / "bsp" / "abstract-machine"
    am_home = tmp_path / "abstract-machine"
    programs_dir.mkdir(parents=True)
    build_script.parent.mkdir(parents=True)
    rtthread_bsp.mkdir(parents=True)
    am_home.mkdir()
    (am_home / "Makefile").write_text("", encoding="utf-8")
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")
    build_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    build_script.chmod(0o755)

    def _fake_which(command):
        if command == "scons":
            return None
        return f"/usr/bin/{command}"

    def _fake_run(*args, **kwargs):
        raise AssertionError("rtthread preflight should fail before subprocess.run")

    monkeypatch.setenv("AM_HOME", str(am_home))
    monkeypatch.setattr("fecompiler.tools.verilator.runner.shutil.which", _fake_which)
    monkeypatch.setattr("fecompiler.tools.verilator.runner._rtthread_prepare_helper", lambda workspace: None)
    monkeypatch.setattr("fecompiler.tools.verilator.runner.subprocess.run", _fake_run)

    build_log = tmp_path / "build_programs.log.txt"
    images, ok = _prepare_sim_images(
        {
            "soc_filelist": str(soc_root / "filelist.soc.f"),
            "sim_program_names": ["rtthread"],
            "sim_programs_dir": str(programs_dir),
            "sim_run_args": ["--diff"],
        },
        build_log_path=build_log,
        case_output_root=tmp_path / "cases",
    )

    assert images == []
    assert ok is False
    content = build_log.read_text(encoding="utf-8")
    assert "scons is required to build rt-thread-am" in content
    assert "diagnosis: missing dependency: install scons or keep the RT-Thread fallback helper available" in content


def test_rtthread_build_preflight_allows_fallback_helper_without_scons(tmp_path, monkeypatch):
    soc_root = tmp_path / "thirdparty" / "SoC"
    helper = soc_root.parent / "rtthread_prepare.py"
    rtthread_bsp = soc_root.parent / "rt-thread-am" / "bsp" / "abstract-machine"
    am_home = tmp_path / "abstract-machine"
    soc_root.mkdir(parents=True)
    rtthread_bsp.mkdir(parents=True)
    am_home.mkdir()
    helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (am_home / "Makefile").write_text("", encoding="utf-8")
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")

    def _fake_which(command):
        if command == "scons":
            return None
        return f"/usr/bin/{command}"

    monkeypatch.setenv("AM_HOME", str(am_home))
    monkeypatch.setattr("fecompiler.tools.verilator.runner.shutil.which", _fake_which)

    workspace = {"soc_filelist": str(soc_root / "filelist.soc.f")}

    assert _rtthread_prepare_helper(workspace) == helper.resolve()
    assert _rtthread_build_preflight_errors(workspace) == []


def test_rtthread_build_preflight_resolves_external_cpu_rtl_resource(tmp_path, monkeypatch):
    soc_root = tmp_path / "ecc-fe-soc-ysyx-am" / "SoC"
    cpu_resource = tmp_path / "ecc-fe-cpu-rtl"
    helper = cpu_resource / "thirdparty" / "rtthread_prepare.py"
    rtthread_bsp = cpu_resource / "thirdparty" / "rt-thread-am" / "bsp" / "abstract-machine"
    am_home = tmp_path / "abstract-machine"
    soc_root.mkdir(parents=True)
    helper.parent.mkdir(parents=True)
    rtthread_bsp.mkdir(parents=True)
    am_home.mkdir()
    helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (am_home / "Makefile").write_text("", encoding="utf-8")
    (soc_root / "filelist.soc.f").write_text("", encoding="utf-8")

    def _fake_which(command):
        if command == "scons":
            return None
        return f"/usr/bin/{command}"

    monkeypatch.setenv("AM_HOME", str(am_home))
    monkeypatch.setenv("ECOS_FE_RESOURCE_ROOTS", str(cpu_resource))
    monkeypatch.setattr("fecompiler.tools.verilator.runner.shutil.which", _fake_which)

    workspace = {"soc_filelist": str(soc_root / "filelist.soc.f")}

    assert _rtthread_prepare_helper(workspace) == helper.resolve()
    assert _rtthread_build_preflight_errors(workspace) == []


def test_elab_check_result_rejects_20_errors_log(tmp_path):
    step = SimpleNamespace(report={"dir": str(tmp_path)})
    (tmp_path / "log.txt").write_text(
        "Build failed: 20 errors, 0 warnings\nerror: something bad\n",
        encoding="utf-8",
    )
    (tmp_path / "elab_summary.json").write_text(
        json.dumps({"status": "fail", "returncode": 1}),
        encoding="utf-8",
    )
    assert SlangElabStep().check_result(step) is False


def test_elab_check_result_accepts_zero_errors_log(tmp_path):
    step = SimpleNamespace(report={"dir": str(tmp_path)})
    (tmp_path / "log.txt").write_text(
        "Build succeeded: 0 errors, 0 warnings\n",
        encoding="utf-8",
    )
    (tmp_path / "elab_summary.json").write_text(
        json.dumps({"status": "pass", "returncode": 0}),
        encoding="utf-8",
    )
    assert SlangElabStep().check_result(step) is True


def test_elab_check_result_rejects_nonzero_returncode_without_error_text(tmp_path):
    step = SimpleNamespace(report={"dir": str(tmp_path)})
    (tmp_path / "log.txt").write_text("slang terminated unexpectedly\n", encoding="utf-8")
    (tmp_path / "elab_summary.json").write_text(
        json.dumps({"status": "fail", "returncode": 2}),
        encoding="utf-8",
    )

    assert SlangElabStep().check_result(step) is False


def test_elab_parses_clickable_slang_diagnostics(tmp_path):
    source = tmp_path / "cpu.sv"
    log = f"{source}:12:7: error: unknown module 'foo'\nBuild failed: 1 error, 0 warnings\n"

    diagnostics = parse_slang_diagnostics(log)

    assert diagnostics == [
        {
            "severity": "error",
            "message": "unknown module 'foo'",
            "source": str(source),
            "line": 12,
            "column": 7,
        }
    ]


def test_lint_parses_clickable_verilator_diagnostics(tmp_path):
    source = tmp_path / "cpu.sv"
    log = f"%Warning-WIDTH: {source}:12:7: Operator ASSIGN expects 32 bits\n"

    diagnostics = parse_verilator_diagnostics(log)

    assert diagnostics == [
        {
            "severity": "warning",
            "code": "WIDTH",
            "message": "Operator ASSIGN expects 32 bits",
            "source": str(source),
            "line": 12,
            "column": 7,
            "raw": log.strip(),
            "category": "width",
        }
    ]


def test_lint_summary_groups_rules_and_files(tmp_path):
    source = tmp_path / "cpu.sv"
    log = "\n".join([
        f"%Warning-WIDTH: {source}:12:7: Operator ASSIGN expects 32 bits",
        f"%Error-UNSUPPORTED: {source}:20:1: Unsupported construct",
    ])
    summary = build_lint_summary(
        {"top_module": "cpu_top", "prepared_manifest": ""},
        {
            "returncode": 1,
            "rtl_files": [str(source)],
            "top_module": "cpu_top",
            "command": ["verilator", "--lint-only"],
            "log_path": str(tmp_path / "log.txt"),
        },
        log,
        summary_path=tmp_path / "lint_summary.json",
    )

    assert summary["status"] == "fail"
    assert summary["summary"]["errors"] == 1
    assert summary["summary"]["warnings"] == 1
    assert [rule["code"] for rule in summary["rules"]] == ["UNSUPPORTED", "WIDTH"]
    assert summary["files"][0]["path"] == str(source)
    assert summary["files"][0]["total"] == 2


def test_elab_scans_module_inventory_and_unresolved_modules(tmp_path):
    source = tmp_path / "top.sv"
    source.write_text(
        """
module child(input logic clk);
endmodule

module top(input logic clk, output logic done);
  child u_child(.clk(clk));
  missing u_missing(.clk(clk));
endmodule
""",
        encoding="utf-8",
    )

    structure = scan_rtl_structure([str(source)])

    assert [item["module"] for item in structure["modules"]] == ["top", "child"]
    top = structure["modules"][0]
    assert top["instances"] == 2
    assert top["instantiates"] == ["child", "missing"]
    assert structure["unresolved_modules"] == ["missing"]


def test_elab_scans_module_ports_params_and_refs(tmp_path):
    source = tmp_path / "inventory.sv"
    source.write_text(
        """
module child #(
  parameter WIDTH = 32,
  parameter DEPTH = 4
) (
  input logic clk,
  input logic [WIDTH-1:0] data_i,
  output logic done_o
);
endmodule

module legacy(a, b, y);
  input a;
  input b;
  output y;
  localparam LEGACY_CFG = 1;
endmodule

module top(input logic clk, output logic done);
  child #(.WIDTH(8)) u_child(.clk(clk), .data_i('0), .done_o(done));
  legacy u_legacy(.a(clk), .b(done), .y());
endmodule
""",
        encoding="utf-8",
    )

    structure = scan_rtl_structure([str(source)])
    modules = {item["module"]: item for item in structure["modules"]}

    assert modules["child"]["ports"] == 3
    assert modules["child"]["parameters"] == 2
    assert modules["legacy"]["ports"] == 3
    assert modules["legacy"]["parameters"] == 1
    assert modules["top"]["ports"] == 2
    assert modules["top"]["instances"] == 2
    assert modules["top"]["instantiates"] == ["child", "legacy"]


def test_slang_defines_include_synthesis_default_and_preserve_manifest_order(tmp_path):
    assert slang_defines({"prepared_manifest": ""}) == ["SYNTHESIS"]

    manifest = tmp_path / "prepared_manifest.json"
    manifest.write_text(json.dumps({
        "rtl_files": ["/tmp/demo.sv"],
        "defines": ["FOO=1", "SYNTHESIS", "BAR"],
    }), encoding="utf-8")
    assert slang_defines({"prepared_manifest": str(manifest)}) == ["SYNTHESIS", "FOO=1", "BAR"]


def test_verilator_lint_defines_include_synthesis_without_changing_manifest_order(tmp_path):
    assert verilator_lint_defines({"prepared_manifest": ""}) == ["SYNTHESIS"]

    manifest = tmp_path / "prepared_manifest.json"
    manifest.write_text(json.dumps({
        "rtl_files": ["/tmp/demo.sv"],
        "defines": ["FOO=1", "SYNTHESIS", "BAR"],
    }), encoding="utf-8")
    assert verilator_lint_defines({"prepared_manifest": str(manifest)}) == [
        "SYNTHESIS",
        "FOO=1",
        "BAR",
    ]
