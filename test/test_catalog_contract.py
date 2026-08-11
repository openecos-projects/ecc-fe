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
from fecompiler.tools.prepare.runner import PrepareStep


def test_sim_ready_catalog_entries_have_adapter_collateral():
    result = check_catalog_contracts()

    assert result.ok is True
    assert result.counts["cpu_total"] == 10
    assert result.counts["soc_total"] == 1
    assert result.counts["sim_ready_cpu"] == 9
    assert result.counts["sim_ready_soc"] == 1
    assert result.counts["creatable_pairs"] == 9
    assert result.issues == []


def test_workspace_catalog_check_cli_returns_contract_summary(capsys):
    assert workspace_cli.run(["catalog-check", "--json"]) == 0

    response = json.loads(capsys.readouterr().out)
    assert response["cmd"] == "catalog_check"
    assert response["response"] == "success"
    assert response["data"]["ok"] is True
    assert response["data"]["counts"]["sim_ready_cpu"] == 9
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


def test_builtin_cpu_adapters_expose_cpu_top_without_bridge_file():
    root = Path(__file__).resolve().parent.parent
    payload = catalog_payload()
    failures: list[str] = []

    for core in payload["cores"]:
        core_id = str(core["id"])
        if core_id == "custom-filelist" or core.get("integration_level") != "sim_ready":
            continue
        filelist_text = str(core.get("cpu_filelist", "")).strip()
        if not filelist_text:
            failures.append(f"{core_id}: missing cpu_filelist")
            continue

        filelist = root / filelist_text
        raw_filelist = filelist.read_text(encoding="utf-8")
        bridge_marker = "cpu_top" "_bridge"
        if bridge_marker in raw_filelist:
            failures.append(f"{core_id}: still depends on removed bridge file")

        parsed = PrepareStep._parse_sv_filelist(str(filelist))
        cpu_top_files = [
            str(path.relative_to(root))
            for path in parsed["rtl_files"]
            if PrepareStep._file_defines_module(path, "cpu_top")
        ]
        if len(cpu_top_files) != 1:
            failures.append(f"{core_id}: cpu_top files={cpu_top_files}")

    assert failures == []


def test_custom_cpu_harness_selects_configured_ysyx_top():
    root = Path(__file__).resolve().parent.parent
    harness = (
        root / "fecompiler/thirdparty/SoC/perip/easy_box/easy_box_core_wrapper.v"
    ).read_text(encoding="utf-8")
    custom = next(
        core for core in catalog_payload()["cores"]
        if core["id"] == "custom-filelist"
    )

    assert "`ifdef ECOS_CUSTOM_CPU_TOP" in harness
    assert "`ECOS_CUSTOM_CPU_TOP u_core" in harness
    assert "`else\n  cpu_top u_core" in harness
    assert [
        port for port in custom["required_cpu_top_ports"]
        if f".{port}" not in harness
    ] == []


def test_difftest_bridge_uses_commit_contract_instead_of_cpu_hierarchy():
    source = (
        Path(__file__).resolve().parent.parent
        / "fecompiler/thirdparty/SoC/driver/difftest.cpp"
    ).read_text(encoding="utf-8")

    assert "apply_commit_state" in source
    assert '#include "Vecos_sim_top.h"' not in source
    assert "Vecos_sim_top___024root.h" not in source
    assert "SOC_ROOT_FIELD" not in source
    assert "__DOT__" not in source


def test_all_creatable_catalog_pairs_prepare_with_one_cpu_top(tmp_path):
    payload = catalog_payload()
    cores = {str(item["id"]): item for item in payload["cores"]}
    creatable = [
        item for item in payload["compatibility"]
        if item.get("can_create_workspace") and "cpu-tests" in item.get("supported_test_suites", [])
    ]

    assert len(creatable) == 9

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
            request["cpu_top_module"] = "ysyx_00000000"
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

        expected_top = str(workspace.get("required_cpu_top_module", "")).strip() or "cpu_top"
        cpu_tops = [
            path for path in manifest.get("rtl_files", [])
            if PrepareStep._file_defines_module(Path(path), expected_top)
        ]
        if len(cpu_tops) != 1:
            failures.append(f"{core_id}+{soc_id}: {expected_top} count={len(cpu_tops)}")

    assert failures == []


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
    expected_soc = _expected_soc_rtl_files(workspace)
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
        return str(
            Path(__file__).resolve().parent.parent
            / "examples/ysyx_00000000/filelist.cpu.f"
        )
    return str((Path(__file__).resolve().parent.parent / str(core["cpu_filelist"])).resolve())


def _cpu_filelist_for_required_core(tmp_path: Path, core_id: str) -> str:
    return str(
        Path(__file__).resolve().parent.parent
        / "examples/ysyx_00000000/filelist.cpu.f"
    )


def _expected_soc_rtl_files(workspace: dict) -> set[str]:
    parsed = PrepareStep._parse_sv_filelist(str(workspace["soc_filelist"]))
    return {str(Path(item).resolve()) for item in parsed["rtl_files"]}
