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
from fecompiler.tools.prepare.runner import COMPATIBILITY_CPU_ALIAS_TOP, PrepareStep


def test_sim_ready_catalog_entries_have_adapter_collateral():
    result = check_catalog_contracts()

    assert result.ok is True
    assert result.counts["cpu_total"] == 10
    assert result.counts["soc_total"] == 12
    assert result.counts["sim_ready_cpu"] == 9
    assert result.counts["sim_ready_soc"] == 12
    assert result.counts["creatable_pairs"] == 108
    assert result.issues == []


def test_workspace_catalog_check_cli_returns_contract_summary(capsys):
    assert workspace_cli.run(["catalog-check", "--json"]) == 0

    response = json.loads(capsys.readouterr().out)
    assert response["cmd"] == "catalog_check"
    assert response["response"] == "success"
    assert response["data"]["ok"] is True
    assert response["data"]["counts"]["sim_ready_cpu"] == 9
    assert response["data"]["counts"]["sim_ready_soc"] == 12


def test_all_creatable_catalog_pairs_prepare_with_one_cpu_alias(tmp_path):
    payload = catalog_payload()
    cores = {str(item["id"]): item for item in payload["cores"]}
    creatable = [
        item for item in payload["compatibility"]
        if item.get("can_create_workspace") and "cpu-tests" in item.get("supported_test_suites", [])
    ]

    assert len(creatable) == 108

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
            request["cpu_filelist"] = str(
                Path(__file__).resolve().parent.parent / "docs/examples/cl3/filelist.cpu.f"
            )
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
    cpu_parsed = PrepareStep._parse_sv_filelist(cpu_filelist)
    expected_cpu = {str(Path(item).resolve()) for item in cpu_parsed["rtl_files"]}
    expected_soc = _expected_soc_rtl_files(workspace, cpu_parsed["rtl_files"])
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
        return str(Path(__file__).resolve().parent.parent / "docs/examples/cl3/filelist.cpu.f")
    return str((Path(__file__).resolve().parent.parent / str(core["cpu_filelist"])).resolve())


def _expected_soc_rtl_files(workspace: dict, cpu_files: list) -> set[str]:
    parsed = PrepareStep._parse_sv_filelist(str(workspace["soc_filelist"]))
    filtered = PrepareStep._filter_soc_filelist_for_cpu_wrapper(
        parsed,
        workspace,
        cpu_filelist_defines_alias=PrepareStep._filelist_defines_module(cpu_files, COMPATIBILITY_CPU_ALIAS_TOP),
    )
    return {str(Path(item).resolve()) for item in filtered["rtl_files"]}
