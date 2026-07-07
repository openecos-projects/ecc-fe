#!/usr/bin/env python
"""Catalog compatibility matrix tests."""

from __future__ import annotations

import pytest

from fecompiler.catalog.registry import catalog_payload, validate_frontend_config
from fecompiler.cli.workspace import (
    WorkspaceCliError,
    _validate_workspace_test_suite_supported,
    _workspace_supported_test_suites,
)
from fecompiler.cpu.registry import get_cpu_wrapper


def _compatibility_by_pair() -> dict[tuple[str, str], dict]:
    payload = catalog_payload()
    return {
        (str(item["core_id"]), str(item["soc_harness_id"])): item
        for item in payload["compatibility"]
    }


def test_catalog_payload_includes_full_cpu_soc_compatibility_matrix():
    payload = catalog_payload()
    assert len(payload["cores"]) == 10
    assert [item["id"] for item in payload["soc_harnesses"]] == ["ysyx-am-soc"]
    assert len(payload["compatibility"]) == len(payload["cores"]) * len(payload["soc_harnesses"])


def test_stable_custom_filelist_combination_supports_rtthread(tmp_path):
    item = _compatibility_by_pair()[("custom-filelist", "ysyx-am-soc")]
    assert item["can_create_workspace"] is True
    assert item["support_level"] == "supported"
    assert item["status"] == "requires_filelist"
    assert item["requires_cpu_filelist"] is True
    assert item["supported_test_suites"] == ["smoke", "cpu-tests", "rtthread", "coremark"]

    cpu_top = tmp_path / "ysyx_00000000.sv"
    cpu_top.write_text("module ysyx_00000000(); endmodule\n", encoding="utf-8")
    user_filelist = tmp_path / "filelist.f"
    user_filelist.write_text("ysyx_00000000.sv\n", encoding="utf-8")
    result = validate_frontend_config({
        "core_id": "custom-filelist",
        "soc_harness_id": "ysyx-am-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "coremark",
        "cpu_filelist": str(user_filelist),
    })
    assert result.ok is True
    assert result.normalized["core_sim_coremark_use_difftest"] is False
    assert result.normalized["required_cpu_top_module"] == "ysyx_00000000"


def test_custom_filelist_requires_soc_facing_cpu_top(tmp_path):
    cpu_top = tmp_path / "CL3Top.sv"
    cpu_top.write_text("module CL3Top(); endmodule\n", encoding="utf-8")
    user_filelist = tmp_path / "filelist.f"
    user_filelist.write_text("CL3Top.sv\n", encoding="utf-8")
    result = validate_frontend_config({
        "core_id": "custom-filelist",
        "soc_harness_id": "ysyx-am-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "cpu-tests",
        "cpu_filelist": str(user_filelist),
    })

    assert result.ok is False
    assert any(issue.code == "cpu_top_module_not_found" for issue in result.issues)


def test_experimental_open_cpu_combination_only_supports_cpu_smoke_tests():
    item = _compatibility_by_pair()[("picorv32", "ysyx-am-soc")]
    assert item["can_create_workspace"] is True
    assert item["support_level"] == "experimental"
    assert item["status"] == "experimental"
    assert item["supported_test_suites"] == ["smoke", "cpu-tests", "coremark"]


def test_selected_catalog_cpu_keeps_user_filelist_and_adds_adapter_filelist(tmp_path):
    user_filelist = tmp_path / "filelist.cpu.f"
    user_filelist.write_text("picorv32_user.v\n", encoding="utf-8")

    result = validate_frontend_config({
        "core_id": "picorv32",
        "soc_harness_id": "ysyx-am-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "cpu-tests",
        "cpu_filelist": str(user_filelist),
    })

    assert result.ok is True
    assert result.normalized["cpu_filelist"] == str(user_filelist)
    assert result.normalized["core_cpu_filelist"].endswith("fecompiler/adapters/picorv32/filelist.cpu.f")
    assert result.normalized["cpu_adapter_filelist"].endswith("fecompiler/adapters/picorv32/filelist.cpu.f")


def test_selected_catalog_cpu_rejects_missing_user_filelist(tmp_path):
    missing = tmp_path / "missing.f"

    result = validate_frontend_config({
        "core_id": "picorv32",
        "soc_harness_id": "ysyx-am-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "cpu-tests",
        "cpu_filelist": str(missing),
    })

    assert result.ok is False
    assert any(issue.code == "cpu_filelist_not_found" for issue in result.issues)


def test_darkriscv_adapter_is_not_marked_cpu_test_ready_until_sim_handshake_is_fixed():
    item = _compatibility_by_pair()[("darkriscv", "ysyx-am-soc")]
    assert item["can_create_workspace"] is False
    assert item["support_level"] == "unsupported"
    assert item["status"] == "needs_cpu_adapter"
    assert item["supported_test_suites"] == []

    result = validate_frontend_config({
        "core_id": "darkriscv",
        "soc_harness_id": "ysyx-am-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "cpu-tests",
    })
    assert result.ok is False
    assert result.support_level == "unsupported"
    assert any(issue.code == "core_sim_adapter_not_implemented" for issue in result.issues)
    assert any(issue.code == "combination_test_suite_not_supported" for issue in result.issues)
    assert result.normalized["core_cpu_filelist"].endswith("fecompiler/adapters/darkriscv/filelist.cpu.f")
    assert result.normalized["core_sim_program_link_base"] == "0x0"
    assert get_cpu_wrapper("darkriscv").sim_ready is False


def test_darkriscv_legacy_workspace_does_not_inherit_soc_cpu_tests():
    workspace = {
        "cpu_wrapper_id": "darkriscv",
        "soc_wrapper_id": "ysyx-am-soc",
    }

    assert _workspace_supported_test_suites(workspace) == []
    with pytest.raises(WorkspaceCliError):
        _validate_workspace_test_suite_supported(workspace, "cpu-tests")


def test_cva6_adapter_can_create_basic_cpu_test_workspace():
    matrix = _compatibility_by_pair()
    item = matrix[("cva6", "ysyx-am-soc")]
    assert item["can_create_workspace"] is True
    assert item["support_level"] == "experimental"
    assert item["status"] == "experimental"
    assert item["supported_test_suites"] == ["smoke", "cpu-tests"]

    result = validate_frontend_config({
        "core_id": "cva6",
        "soc_harness_id": "ysyx-am-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "cpu-tests",
    })
    assert result.ok is True
    assert result.support_level == "experimental"
    assert result.normalized["core_cpu_filelist"].endswith("fecompiler/adapters/cva6/filelist.cpu.f")
    assert result.normalized["cpu_wrapper_top"] == "ecos_cva6_cpu_wrapper"

    coremark = validate_frontend_config({
        "core_id": "cva6",
        "soc_harness_id": "ysyx-am-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "coremark",
    })
    assert coremark.ok is False
    assert any(issue.code == "combination_test_suite_not_supported" for issue in coremark.issues)


def test_vexriscv_adapter_can_create_basic_cpu_test_workspace():
    item = _compatibility_by_pair()[("vexriscv", "ysyx-am-soc")]
    assert item["can_create_workspace"] is True
    assert item["support_level"] == "experimental"
    assert item["status"] == "experimental"
    assert item["supported_test_suites"] == ["smoke", "cpu-tests", "coremark"]

    result = validate_frontend_config({
        "core_id": "vexriscv",
        "soc_harness_id": "ysyx-am-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "cpu-tests",
    })
    assert result.ok is True
    assert result.support_level == "experimental"
    assert result.normalized["core_cpu_filelist"].endswith("fecompiler/adapters/vexriscv/filelist.cpu.f")
    assert result.normalized["cpu_wrapper_top"] == "ecos_vexriscv_cpu_wrapper"
    assert result.normalized["core_sim_compile_march"] == "rv32i_zicsr"
    assert result.normalized["core_sim_coremark_has_float"] is False


def test_removed_placeholder_soc_profiles_are_not_exposed():
    payload = catalog_payload()
    removed_ids = {
        "ysyx-am-soc-alt",
        "ysyx-am-soc-extended",
        "minimal-riscv-soc",
        "corev-mini-soc",
        "femtorv-mini-soc",
        "darksocv",
        "ibex-demo-system",
        "litex-vexriscv-soc",
        "neorv32-soc",
        "opentitan-earlgrey",
        "swervolf",
    }
    exposed_ids = {str(item["id"]) for item in payload["soc_harnesses"]}

    assert exposed_ids == {"ysyx-am-soc"}
    assert not (removed_ids & exposed_ids)


def test_validate_rejects_rtthread_for_non_difftest_experimental_core():
    result = validate_frontend_config({
        "core_id": "picorv32",
        "soc_harness_id": "ysyx-am-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "rtthread",
    })
    assert result.ok is False
    assert any(issue.code == "combination_test_suite_not_supported" for issue in result.issues)
