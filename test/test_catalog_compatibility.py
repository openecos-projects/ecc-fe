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
    assert len(payload["soc_harnesses"]) == 12
    assert len(payload["compatibility"]) == len(payload["cores"]) * len(payload["soc_harnesses"])


def test_stable_custom_filelist_combination_supports_rtthread():
    item = _compatibility_by_pair()[("custom-filelist", "ysyx-am-soc")]
    assert item["can_create_workspace"] is True
    assert item["support_level"] == "supported"
    assert item["status"] == "requires_filelist"
    assert item["requires_cpu_filelist"] is True
    assert item["supported_test_suites"] == ["smoke", "cpu-tests", "rtthread"]


def test_experimental_open_cpu_combination_only_supports_cpu_smoke_tests():
    item = _compatibility_by_pair()[("picorv32", "minimal-riscv-soc")]
    assert item["can_create_workspace"] is True
    assert item["support_level"] == "experimental"
    assert item["status"] == "experimental"
    assert item["supported_test_suites"] == ["smoke", "cpu-tests"]


def test_selected_catalog_cpu_keeps_user_filelist_and_adds_adapter_filelist(tmp_path):
    user_filelist = tmp_path / "filelist.cpu.f"
    user_filelist.write_text("picorv32_user.v\n", encoding="utf-8")

    result = validate_frontend_config({
        "core_id": "picorv32",
        "soc_harness_id": "minimal-riscv-soc",
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
        "soc_harness_id": "minimal-riscv-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "cpu-tests",
        "cpu_filelist": str(missing),
    })

    assert result.ok is False
    assert any(issue.code == "cpu_filelist_not_found" for issue in result.issues)


def test_darkriscv_adapter_is_not_marked_cpu_test_ready_until_sim_handshake_is_fixed():
    item = _compatibility_by_pair()[("darkriscv", "minimal-riscv-soc")]
    assert item["can_create_workspace"] is False
    assert item["support_level"] == "unsupported"
    assert item["status"] == "needs_cpu_adapter"
    assert item["supported_test_suites"] == []

    result = validate_frontend_config({
        "core_id": "darkriscv",
        "soc_harness_id": "minimal-riscv-soc",
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
        "soc_wrapper_id": "minimal-riscv-soc",
    }

    assert _workspace_supported_test_suites(workspace) == []
    with pytest.raises(WorkspaceCliError):
        _validate_workspace_test_suite_supported(workspace, "cpu-tests")


def test_cva6_adapter_can_create_basic_cpu_test_workspace():
    matrix = _compatibility_by_pair()
    item = matrix[("cva6", "minimal-riscv-soc")]
    assert item["can_create_workspace"] is True
    assert item["support_level"] == "experimental"
    assert item["status"] == "experimental"
    assert item["supported_test_suites"] == ["smoke", "cpu-tests"]

    result = validate_frontend_config({
        "core_id": "cva6",
        "soc_harness_id": "minimal-riscv-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "cpu-tests",
    })
    assert result.ok is True
    assert result.support_level == "experimental"
    assert result.normalized["core_cpu_filelist"].endswith("fecompiler/adapters/cva6/filelist.cpu.f")
    assert result.normalized["cpu_wrapper_top"] == "ecos_cva6_cpu_wrapper"


def test_vexriscv_adapter_can_create_basic_cpu_test_workspace():
    item = _compatibility_by_pair()[("vexriscv", "minimal-riscv-soc")]
    assert item["can_create_workspace"] is True
    assert item["support_level"] == "experimental"
    assert item["status"] == "experimental"
    assert item["supported_test_suites"] == ["smoke", "cpu-tests"]

    result = validate_frontend_config({
        "core_id": "vexriscv",
        "soc_harness_id": "minimal-riscv-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "cpu-tests",
    })
    assert result.ok is True
    assert result.support_level == "experimental"
    assert result.normalized["core_cpu_filelist"].endswith("fecompiler/adapters/vexriscv/filelist.cpu.f")
    assert result.normalized["cpu_wrapper_top"] == "ecos_vexriscv_cpu_wrapper"


def test_open_soc_profiles_are_cpu_test_ready_without_rtthread():
    payload = catalog_payload()
    profile_ids = {
        "darksocv",
        "ibex-demo-system",
        "litex-vexriscv-soc",
        "neorv32-soc",
        "opentitan-earlgrey",
        "swervolf",
    }
    profiles = {
        str(item["id"]): item
        for item in payload["soc_harnesses"]
        if str(item["id"]) in profile_ids
    }

    assert set(profiles) == profile_ids
    for item in profiles.values():
        assert item["integration_level"] == "sim_ready"
        assert item["status"] == "experimental"
        assert item["supports_difftest"] is False
        assert item["supported_test_suites"] == ["smoke", "cpu-tests"]

        pair = _compatibility_by_pair()[("picorv32", str(item["id"]))]
        assert pair["can_create_workspace"] is True
        assert pair["supported_test_suites"] == ["smoke", "cpu-tests"]


def test_validate_rejects_rtthread_for_non_difftest_experimental_core():
    result = validate_frontend_config({
        "core_id": "picorv32",
        "soc_harness_id": "ysyx-am-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "rtthread",
    })
    assert result.ok is False
    assert any(issue.code == "combination_test_suite_not_supported" for issue in result.issues)
