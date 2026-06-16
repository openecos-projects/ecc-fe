#!/usr/bin/env python
"""Static catalog contract tests for CPU/SoC adapter integrations."""

from __future__ import annotations

import json
from pathlib import Path

from fecompiler.catalog.registry import catalog_payload
from fecompiler.catalog.contract import check_catalog_contracts
from fecompiler.cli import workspace as workspace_cli
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import load_workspace
from fecompiler.engine.flow import EngineFlow


def test_sim_ready_catalog_entries_have_adapter_collateral():
    result = check_catalog_contracts()

    assert result.ok is True
    assert result.counts["cpu_total"] == 10
    assert result.counts["soc_total"] == 12
    assert result.counts["sim_ready_cpu"] == 10
    assert result.counts["sim_ready_soc"] == 12
    assert result.counts["creatable_pairs"] == 120
    assert result.issues == []


def test_workspace_catalog_check_cli_returns_contract_summary(capsys):
    assert workspace_cli.run(["catalog-check", "--json"]) == 0

    response = json.loads(capsys.readouterr().out)
    assert response["cmd"] == "catalog_check"
    assert response["response"] == "success"
    assert response["data"]["ok"] is True
    assert response["data"]["counts"]["sim_ready_cpu"] == 10
    assert response["data"]["counts"]["sim_ready_soc"] == 12


def test_all_creatable_catalog_pairs_prepare_with_one_cpu_alias(tmp_path):
    payload = catalog_payload()
    creatable = [
        item for item in payload["compatibility"]
        if item.get("can_create_workspace") and "cpu-tests" in item.get("supported_test_suites", [])
    ]

    assert len(creatable) == 120

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

        report_path = workspace_dir / "prepare_fe" / "report" / "prepare.rpt"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        alias = report.get("compatibility_alias", {})
        if alias.get("count") != 1:
            failures.append(f"{core_id}+{soc_id}: alias count={alias.get('count')}")

    assert failures == []
