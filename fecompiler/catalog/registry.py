"""Builtin frontend catalog registry and compatibility checks."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from fecompiler.catalog.schema import CatalogEntry, ValidationIssue, ValidationResult

CATALOG_VERSION = 1
DEFAULT_CORE_ID = "custom-filelist"
DEFAULT_SOC_HARNESS_ID = "ysyx-am-soc"
DEFAULT_TOOLCHAIN_ID = "riscv32-unknown-elf"
DEFAULT_TEST_SUITE_ID = "cpu-tests"

_CATEGORY_FILES = {
    "cores": "cores.json",
    "soc_harnesses": "soc_harnesses.json",
    "toolchains": "toolchains.json",
    "test_suites": "test_suites.json",
}


def catalog_payload() -> dict[str, Any]:
    """Return the full catalog payload used by CLI and GUI."""
    catalog = _catalog()
    return {
        "version": CATALOG_VERSION,
        "defaults": {
            "core_id": DEFAULT_CORE_ID,
            "soc_harness_id": DEFAULT_SOC_HARNESS_ID,
            "toolchain_id": DEFAULT_TOOLCHAIN_ID,
            "test_suite_id": DEFAULT_TEST_SUITE_ID,
        },
        **{
            category: [entry.to_dict() for entry in entries]
            for category, entries in catalog.items()
        },
    }


def validate_frontend_config(config: dict[str, Any]) -> ValidationResult:
    """Validate a proposed frontend workspace combination."""
    catalog = _catalog()
    core_id = _read_id(config, "core_id", DEFAULT_CORE_ID)
    soc_id = _read_id(config, "soc_harness_id", DEFAULT_SOC_HARNESS_ID)
    toolchain_id = _read_id(config, "toolchain_id", DEFAULT_TOOLCHAIN_ID)
    test_suite_id = _read_id(config, "test_suite_id", DEFAULT_TEST_SUITE_ID)

    core = _find(catalog["cores"], core_id)
    soc = _find(catalog["soc_harnesses"], soc_id)
    toolchain = _find(catalog["toolchains"], toolchain_id)
    test_suite = _find(catalog["test_suites"], test_suite_id)

    issues: list[ValidationIssue] = []
    for field, entry_id, entry in (
        ("core_id", core_id, core),
        ("soc_harness_id", soc_id, soc),
        ("toolchain_id", toolchain_id, toolchain),
        ("test_suite_id", test_suite_id, test_suite),
    ):
        if entry is None:
            issues.append(ValidationIssue("error", "unknown_catalog_id", f"Unknown {field}: {entry_id}", field))

    effective_cpu_filelist = _effective_cpu_filelist(config, core)
    if core is not None and bool(core.data.get("requires_filelist")):
        filelist = effective_cpu_filelist
        if not filelist:
            issues.append(ValidationIssue("error", "missing_cpu_filelist", "CPU filelist is required.", "cpu_filelist"))
        elif not Path(filelist).expanduser().exists():
            issues.append(ValidationIssue("error", "cpu_filelist_not_found", f"CPU filelist not found: {filelist}", "cpu_filelist"))

    entries = [entry for entry in (core, soc, toolchain, test_suite) if entry is not None]
    if len(entries) == 4:
        if not _isa_compatible(core, soc, toolchain, test_suite):
            issues.append(
                ValidationIssue(
                    "error",
                    "isa_not_compatible",
                    "Selected CPU, SoC harness, toolchain, and test suite do not share a compatible ISA.",
                    "isa",
                )
            )

        if not core.sim_ready:
            issues.append(
                ValidationIssue(
                    "error",
                    "core_sim_adapter_not_implemented",
                    _core_adapter_message(core, soc),
                    "core_id",
                )
            )
        if not soc.sim_ready:
            issues.append(
                ValidationIssue(
                    "error",
                    "soc_adapter_not_implemented",
                    f"{soc.name} is listed as {soc.status}; simulation is not wired yet.",
                    "soc_harness_id",
                )
            )
        if not _suite_supported_by_harness(test_suite, soc):
            issues.append(
                ValidationIssue(
                    "warning",
                    "suite_harness_contract",
                    f"{test_suite.name} is not declared for {soc.name}.",
                    "test_suite_id",
                )
            )
        if test_suite.status == "planned":
            issues.append(
                ValidationIssue(
                    "error",
                    "test_suite_not_implemented",
                    f"{test_suite.name} is listed as planned; its runner is not wired yet.",
                    "test_suite_id",
                )
            )

    has_errors = any(issue.severity == "error" for issue in issues)
    warnings = [issue for issue in issues if issue.severity == "warning"]
    support_level = "supported" if not has_errors and not warnings else "experimental" if not has_errors else "unsupported"
    summary = _summary_for(support_level, core, soc, test_suite)
    normalized = {
        "core_id": core_id,
        "soc_harness_id": soc_id,
        "soc_variant": str(soc.data.get("variant", "")) if soc is not None else "",
        "toolchain_id": toolchain_id,
        "test_suite_id": test_suite_id,
        "cpu_filelist": effective_cpu_filelist,
        "core_cpu_filelist": str(core.data.get("cpu_filelist", "")) if core is not None else "",
        "core_capability": core.integration_level if core is not None else "",
        "soc_harness_capability": soc.integration_level if soc is not None else "",
        "required_capability": "sim_ready",
    }
    return ValidationResult(
        ok=not has_errors,
        support_level=support_level,
        summary=summary,
        normalized=normalized,
        issues=issues,
    )


@lru_cache(maxsize=1)
def _catalog() -> dict[str, list[CatalogEntry]]:
    return {
        category: [CatalogEntry.from_dict(item) for item in _load_builtin(filename)]
        for category, filename in _CATEGORY_FILES.items()
    }


def _load_builtin(filename: str) -> list[dict[str, Any]]:
    package = "fecompiler.catalog.builtin"
    with resources.files(package).joinpath(filename).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"catalog file must contain a list: {filename}")
    return [dict(item) for item in data if isinstance(item, dict)]


def _read_id(config: dict[str, Any], field: str, fallback: str) -> str:
    aliases = {
        "core_id": ("core_id", "coreId", "frontend_core_id"),
        "soc_harness_id": ("soc_harness_id", "socHarnessId", "soc_id", "soc_variant"),
        "toolchain_id": ("toolchain_id", "toolchainId"),
        "test_suite_id": ("test_suite_id", "testSuiteId", "sim_test_suite"),
    }
    for key in aliases.get(field, (field,)):
        value = str(config.get(key, "")).strip()
        if not value:
            continue
        if field == "soc_harness_id":
            return _normalize_soc_id(value)
        if field == "test_suite_id" and value == "cpu_tests":
            return "cpu-tests"
        return value
    return fallback


def _normalize_soc_id(value: str) -> str:
    variant_map = {
        "soc1": "ysyx-am-soc",
        "soc2": "ysyx-am-soc-alt",
        "soc3": "ysyx-am-soc-extended",
    }
    return variant_map.get(value, value)


def _find(entries: list[CatalogEntry], entry_id: str) -> CatalogEntry | None:
    return next((entry for entry in entries if entry.id == entry_id), None)


def _effective_cpu_filelist(config: dict[str, Any], core: CatalogEntry | None) -> str:
    filelist = str(config.get("cpu_filelist", "")).strip()
    if filelist:
        return filelist
    if core is None or bool(core.data.get("requires_filelist")):
        return ""
    return str(core.data.get("cpu_filelist", "")).strip()


def _core_adapter_message(core: CatalogEntry, soc: CatalogEntry | None) -> str:
    soc_name = soc.name if soc is not None else "the selected SoC harness"
    if core.filelist_ready:
        return (
            f"{core.name} has a built-in RTL filelist, but it does not yet have "
            f"a simulation adapter for {soc_name}."
        )
    return f"{core.name} is listed as {core.status}; use it as metadata until an adapter is implemented."


def _isa_compatible(*entries: CatalogEntry) -> bool:
    isa_sets = [_expanded_isa_set(entry.isa) for entry in entries if entry.isa]
    if not isa_sets:
        return True
    common = isa_sets[0]
    for isa in isa_sets[1:]:
        common = common.intersection(isa)
    return bool(common)


def _expanded_isa_set(values: list[str]) -> set[str]:
    out: set[str] = set()
    for value in values:
        lower = value.lower()
        out.add(lower)
        if lower.startswith("rv32"):
            out.add("rv32")
        if lower.startswith("rv64"):
            out.add("rv64")
        if lower.startswith("loongarch32"):
            out.add("loongarch32")
        if lower.startswith("loongarch64"):
            out.add("loongarch64")
    return out


def _suite_supported_by_harness(test_suite: CatalogEntry, soc: CatalogEntry) -> bool:
    requirements = {str(item) for item in test_suite.data.get("requires", [])}
    if not requirements:
        return True
    if soc.id in requirements:
        return True
    if "sim_ready_harness" in requirements and soc.sim_ready:
        return True
    if "ysyx-am-soc" in requirements and soc.id.startswith("ysyx-am-soc"):
        return True
    return not any(req.endswith("-soc") or req == "sim_ready_harness" for req in requirements)


def _summary_for(
    support_level: str,
    core: CatalogEntry | None,
    soc: CatalogEntry | None,
    test_suite: CatalogEntry | None,
) -> str:
    if support_level == "supported":
        return f"{core.name} can run {test_suite.name} on {soc.name}." if core and soc and test_suite else "Configuration is supported."
    if support_level == "experimental":
        return "Configuration is usable for catalog exploration, but one or more adapters are not implemented yet."
    if core is not None and core.filelist_ready and not core.sim_ready:
        return f"{core.name} RTL filelist is ready, but simulation workspace creation still needs a SoC adapter."
    return "Configuration is not ready to create a frontend workspace."
