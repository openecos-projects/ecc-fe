#!/usr/bin/env python
"""Static catalog contract tests for CPU/SoC adapter integrations."""

from __future__ import annotations

import json
from pathlib import Path

from fecompiler.catalog.registry import catalog_payload
from fecompiler.catalog.contract import check_catalog_contracts
from fecompiler.cli import workspace as workspace_cli
from fecompiler.cli.workspace import _adapt_sim_cpp_sources_for_cpu
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import load_workspace
from fecompiler.engine.flow import EngineFlow
from fecompiler.soc import soc_runtime_options
from fecompiler.tools.common.rtl_inputs import workspace_input_fingerprint
from fecompiler.tools.prepare.runner import (
    COMPATIBILITY_CPU_ALIAS_TOP,
    STANDARD_CPU_TOP,
    PrepareStep,
)


def test_sim_ready_catalog_entries_have_adapter_collateral():
    result = check_catalog_contracts()

    assert result.ok is True
    assert result.counts["cpu_total"] == 11
    assert result.counts["soc_total"] == 1
    assert result.counts["sim_ready_cpu"] == 10
    assert result.counts["sim_ready_soc"] == 1
    assert result.counts["creatable_pairs"] == 10
    assert result.issues == []


def test_workspace_catalog_check_cli_returns_contract_summary(capsys):
    assert workspace_cli.run(["catalog-check", "--json"]) == 0

    response = json.loads(capsys.readouterr().out)
    assert response["cmd"] == "catalog_check"
    assert response["response"] == "success"
    assert response["data"]["ok"] is True
    assert response["data"]["counts"]["sim_ready_cpu"] == 10
    assert response["data"]["counts"]["sim_ready_soc"] == 1


def test_prepare_filelist_resolves_external_thirdparty_resource(tmp_path, monkeypatch):
    resource_root = tmp_path / "ecc-fe-cpu-rtl"
    rtl = resource_root / "thirdparty" / "picorv32" / "picorv32.v"
    incdir = resource_root / "thirdparty" / "picorv32" / "include"
    rtl.parent.mkdir(parents=True)
    incdir.mkdir()
    rtl.write_text("module picorv32; endmodule\n", encoding="utf-8")

    adapter_dir = tmp_path / "runtime" / "fecompiler" / "adapters" / "picorv32"
    adapter_dir.mkdir(parents=True)
    filelist = adapter_dir / "filelist.cpu.f"
    filelist.write_text(
        "+incdir+../../thirdparty/picorv32/include\n"
        "../../thirdparty/picorv32/picorv32.v\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("ECOS_FE_RESOURCE_ROOTS", str(resource_root))

    parsed = PrepareStep._parse_sv_filelist(str(filelist))

    assert parsed["rtl_files"] == [rtl.resolve()]
    assert parsed["incdirs"] == [incdir.resolve()]


def test_obi_cpu_wrappers_register_local_mmio_write_response():
    root = Path(__file__).resolve().parent.parent
    for rel in (
        "fecompiler/adapters/ibex/ecos_ibex_cpu_wrapper.sv",
        "fecompiler/adapters/cv32e40p/ecos_cv32e40p_cpu_wrapper.sv",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        assert "reg         local_write_resp_q;" in text
        assert "data_rvalid_q || local_write_resp_q" in text
        assert "data_rvalid_q || local_write)" not in text


def test_all_creatable_catalog_pairs_prepare_with_one_cpu_alias(tmp_path):
    payload = catalog_payload()
    cores = {str(item["id"]): item for item in payload["cores"]}
    creatable = [
        item for item in payload["compatibility"]
        if item.get("can_create_workspace") and "cpu-tests" in item.get("supported_test_suites", [])
    ]

    assert len(creatable) == 10

    failures: list[str] = []
    for item in creatable:
        core_id = str(item["core_id"])
        soc_id = str(item["soc_harness_id"])
        workspace_dir = tmp_path / f"{core_id}__{soc_id}"
        create_request = tmp_path / f"{core_id}__{soc_id}.json"
        request: dict[str, object] = {
            "directory": str(workspace_dir),
            "core_id": core_id,
            "soc_harness_id": soc_id,
            "toolchain_id": "riscv32-unknown-elf",
            "test_suite_id": "cpu-tests",
            "parameters": {
                "Design": f"{core_id}_{soc_id}".replace("-", "_"),
                "Top module": "ecos_sim_top",
            },
        }
        if bool(item.get("requires_cpu_filelist")):
            request["cpu_filelist"] = _cpu_filelist_for_required_core(tmp_path, core_id)
        create_request.write_text(json.dumps(request), encoding="utf-8")

        create_rc = workspace_cli.run(["create", "--input-json", str(create_request), "--json"])
        if create_rc != 0:
            failures.append(f"{core_id}+{soc_id}: create rc={create_rc}")
            continue

        workspace = load_workspace(str(workspace_dir))
        if workspace is None:
            failures.append(f"{core_id}+{soc_id}: workspace not loadable")
            continue

        engine = EngineFlow(workspace=workspace)
        engine.create_step_workspaces()
        state = engine.run_step("prepare", rerun=True)
        if state != StateEnum.Success:
            failures.append(f"{core_id}+{soc_id}: prepare state={state.value}")
            continue

        runtime_errors = _runtime_mismatches(workspace)
        if runtime_errors:
            failures.extend(f"{core_id}+{soc_id}: {error}" for error in runtime_errors)

        manifest_path = workspace_dir / "prepare_fe" / "output" / "prepared_inputs.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        report_path = workspace_dir / "prepare_fe" / "report" / "prepare.rpt"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        prepare_errors = _prepare_mismatches(manifest, report, workspace, cores[core_id], bool(item.get("requires_cpu_filelist")))
        if prepare_errors:
            failures.extend(f"{core_id}+{soc_id}: {error}" for error in prepare_errors)

        alias = report.get("compatibility_alias", {})
        if alias.get("count") != 1:
            failures.append(f"{core_id}+{soc_id}: alias count={alias.get('count')}")

    assert failures == []


def test_standard_cpu_filelist_generates_compatibility_wrapper(tmp_path):
    cpu_filelist = _write_standard_cpu_fixture(tmp_path)
    workspace_dir = tmp_path / "standard_cpu"
    request = tmp_path / "standard_cpu.json"
    request.write_text(
        json.dumps(
            {
                "directory": str(workspace_dir),
                "core_id": "standard-cpu-filelist",
                "soc_harness_id": "ysyx-am-soc",
                "toolchain_id": "riscv32-unknown-elf",
                "test_suite_id": "cpu-tests",
                "cpu_filelist": str(cpu_filelist),
                "parameters": {
                    "Design": "standard_cpu",
                    "Top module": "ecos_sim_top",
                },
            },
        ),
        encoding="utf-8",
    )

    assert workspace_cli.run(["create", "--input-json", str(request), "--json"]) == 0
    workspace = load_workspace(str(workspace_dir))
    assert workspace is not None
    assert workspace["cpu_wrapper_generation"] == "standard_alias_v1"
    assert workspace["cpu_standard_top"] == STANDARD_CPU_TOP
    assert workspace["cpu_supports_difftest"] is False

    engine = EngineFlow(workspace=workspace)
    engine.create_step_workspaces()
    assert engine.run_step("prepare", rerun=True) == StateEnum.Success

    report = json.loads((workspace_dir / "prepare_fe/report/prepare.rpt").read_text(encoding="utf-8"))
    manifest = json.loads((workspace_dir / "prepare_fe/output/prepared_inputs.json").read_text(encoding="utf-8"))
    generated = workspace_dir / "prepare_fe/output/generated_standard_cpu_wrapper.sv"
    assert generated.is_file()
    generated_text = generated.read_text(encoding="utf-8")
    assert f"module {COMPATIBILITY_CPU_ALIAS_TOP}" in generated_text
    assert f"{STANDARD_CPU_TOP} u_cpu" in generated_text
    assert "HALT_ADDR = 32'h1000_000c" in generated_text
    assert "UART_ADDR = 32'h1000_0000" in generated_text
    assert str(generated) in [str(Path(item)) for item in manifest["rtl_files"]]
    assert report["compatibility_alias"]["count"] == 1
    assert report["inputs"]["generated_cpu_wrapper"]["generated"] is True


def _runtime_mismatches(workspace: dict) -> list[str]:
    expected = soc_runtime_options(workspace)
    expected = dict(expected)
    expected["sim_cpp_sources"] = _adapt_sim_cpp_sources_for_cpu(
        workspace,
        [str(item) for item in expected.get("sim_cpp_sources", [])],
    )
    fields = (
        "soc_wrapper_id",
        "soc_wrapper_contract",
        "soc_variant",
        "top_module",
        "sim_soc_root",
        "soc_filelist",
        "testbench",
        "sim_programs_dir",
        "sim_tests_dir",
        "sim_build_test_script",
        "soc_supports_difftest",
        "sim_cpp_sources",
        "sim_cflags",
        "sim_ldflags",
    )
    errors: list[str] = []
    for field in fields:
        if field not in expected:
            continue
        actual = workspace.get(field)
        wanted = expected[field]
        if field in {"sim_cpp_sources", "sim_cflags", "sim_ldflags"}:
            actual = [str(item) for item in (actual or [])]
            wanted = [str(item) for item in (wanted or [])]
        if actual != wanted:
            errors.append(f"runtime {field} got={actual!r} expected={wanted!r}")
    return errors


def _prepare_mismatches(
    manifest: dict,
    report: dict,
    workspace: dict,
    core: dict,
    requires_cpu_filelist: bool,
) -> list[str]:
    errors: list[str] = []
    rtl_files = {str(Path(item).resolve()) for item in manifest.get("rtl_files", [])}
    cpu_filelist = _expected_cpu_filelist(core, requires_cpu_filelist)
    if not cpu_filelist:
        cpu_filelist = str(workspace.get("cpu_filelist", ""))
    cpu_parsed = PrepareStep._parse_sv_filelist(cpu_filelist)
    expected_cpu = {str(Path(item).resolve()) for item in cpu_parsed["rtl_files"]}
    expected_soc = _expected_soc_rtl_files(
        workspace,
        cpu_parsed["rtl_files"],
        generated_standard_wrapper=core["id"] == "standard-cpu-filelist",
    )
    missing_cpu = sorted(expected_cpu - rtl_files)
    missing_soc = sorted(expected_soc - rtl_files)

    if manifest.get("source_fingerprint") != workspace_input_fingerprint(workspace):
        errors.append("prepare source fingerprint is stale")
    if missing_cpu:
        errors.append(f"prepare is missing {len(missing_cpu)} CPU RTL file(s)")
    if missing_soc:
        errors.append(f"prepare is missing {len(missing_soc)} SoC RTL file(s)")

    inputs = report.get("inputs", {})
    cpu_input = inputs.get("cpu_filelist", {})
    soc_input = inputs.get("soc_filelist", {})
    if Path(str(cpu_input.get("path", ""))).resolve() != Path(cpu_filelist).resolve():
        errors.append(f"prepare CPU filelist path got={cpu_input.get('path')!r} expected={cpu_filelist!r}")
    if Path(str(soc_input.get("path", ""))).resolve() != Path(str(workspace.get("soc_filelist", ""))).resolve():
        errors.append(f"prepare SoC filelist path got={soc_input.get('path')!r} expected={workspace.get('soc_filelist')!r}")
    return errors


def _expected_cpu_filelist(core: dict, requires_cpu_filelist: bool) -> str:
    if requires_cpu_filelist:
        if core["id"] == "standard-cpu-filelist":
            return ""
        return str(Path(__file__).resolve().parent.parent / "examples/cl3/filelist.cpu.f")
    return str((Path(__file__).resolve().parent.parent / str(core["cpu_filelist"])).resolve())


def _cpu_filelist_for_required_core(tmp_path: Path, core_id: str) -> str:
    if core_id == "standard-cpu-filelist":
        return str(_write_standard_cpu_fixture(tmp_path))
    return str(Path(__file__).resolve().parent.parent / "examples/cl3/filelist.cpu.f")


def _write_standard_cpu_fixture(tmp_path: Path) -> Path:
    source = tmp_path / "ecos_user_cpu_top.sv"
    source.write_text(
        f"""module {STANDARD_CPU_TOP} (
  input         clock,
  input         reset,
  input         io_interrupt,
  input         io_master_awready,
  output        io_master_awvalid,
  output [31:0] io_master_awaddr,
  output [3:0]  io_master_awid,
  output [7:0]  io_master_awlen,
  output [2:0]  io_master_awsize,
  output [1:0]  io_master_awburst,
  output        io_master_awlock,
  output [3:0]  io_master_awcache,
  output [2:0]  io_master_awprot,
  output [3:0]  io_master_awqos,
  output [3:0]  io_master_awregion,
  input         io_master_wready,
  output        io_master_wvalid,
  output [31:0] io_master_wdata,
  output [3:0]  io_master_wstrb,
  output        io_master_wlast,
  output        io_master_bready,
  input         io_master_bvalid,
  input  [1:0]  io_master_bresp,
  input  [3:0]  io_master_bid,
  input         io_master_arready,
  output        io_master_arvalid,
  output [31:0] io_master_araddr,
  output [3:0]  io_master_arid,
  output [7:0]  io_master_arlen,
  output [2:0]  io_master_arsize,
  output [1:0]  io_master_arburst,
  output        io_master_arlock,
  output [3:0]  io_master_arcache,
  output [2:0]  io_master_arprot,
  output [3:0]  io_master_arqos,
  output [3:0]  io_master_arregion,
  output        io_master_rready,
  input         io_master_rvalid,
  input  [1:0]  io_master_rresp,
  input  [31:0] io_master_rdata,
  input         io_master_rlast,
  input  [3:0]  io_master_rid
);
  assign io_master_awvalid = 1'b0;
  assign io_master_awaddr = 32'b0;
  assign io_master_awid = 4'b0;
  assign io_master_awlen = 8'b0;
  assign io_master_awsize = 3'b010;
  assign io_master_awburst = 2'b01;
  assign io_master_awlock = 1'b0;
  assign io_master_awcache = 4'b0;
  assign io_master_awprot = 3'b0;
  assign io_master_awqos = 4'b0;
  assign io_master_awregion = 4'b0;
  assign io_master_wvalid = 1'b0;
  assign io_master_wdata = 32'b0;
  assign io_master_wstrb = 4'b0;
  assign io_master_wlast = 1'b0;
  assign io_master_bready = 1'b1;
  assign io_master_arvalid = 1'b0;
  assign io_master_araddr = 32'b0;
  assign io_master_arid = 4'b0;
  assign io_master_arlen = 8'b0;
  assign io_master_arsize = 3'b010;
  assign io_master_arburst = 2'b01;
  assign io_master_arlock = 1'b0;
  assign io_master_arcache = 4'b0;
  assign io_master_arprot = 3'b0;
  assign io_master_arqos = 4'b0;
  assign io_master_arregion = 4'b0;
  assign io_master_rready = 1'b1;
endmodule
""",
        encoding="utf-8",
    )
    filelist = tmp_path / "filelist.cpu.f"
    filelist.write_text(str(source) + "\n", encoding="utf-8")
    return filelist


def _expected_soc_rtl_files(
    workspace: dict,
    cpu_files: list,
    *,
    generated_standard_wrapper: bool = False,
) -> set[str]:
    parsed = PrepareStep._parse_sv_filelist(str(workspace["soc_filelist"]))
    filtered = PrepareStep._filter_soc_filelist_for_cpu_wrapper(
        parsed,
        workspace,
        cpu_filelist_defines_alias=generated_standard_wrapper
        or PrepareStep._filelist_defines_module(cpu_files, COMPATIBILITY_CPU_ALIAS_TOP),
    )
    return {str(Path(item).resolve()) for item in filtered["rtl_files"]}
