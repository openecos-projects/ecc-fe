#!/usr/bin/env python
"""Catalog compatibility matrix tests."""

from __future__ import annotations

from fecompiler.catalog.registry import catalog_payload, validate_frontend_config


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


def test_darkriscv_adapter_can_create_basic_cpu_test_workspace():
    item = _compatibility_by_pair()[("darkriscv", "minimal-riscv-soc")]
    assert item["can_create_workspace"] is True
    assert item["support_level"] == "experimental"
    assert item["status"] == "experimental"
    assert item["supported_test_suites"] == ["smoke", "cpu-tests"]

    result = validate_frontend_config({
        "core_id": "darkriscv",
        "soc_harness_id": "minimal-riscv-soc",
        "toolchain_id": "riscv32-unknown-elf",
        "test_suite_id": "cpu-tests",
    })
    assert result.ok is True
    assert result.support_level == "experimental"
    assert result.normalized["core_cpu_filelist"].endswith("fecompiler/adapters/darkriscv/filelist.cpu.f")
    assert result.normalized["core_sim_program_link_base"] == "0x0"


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
