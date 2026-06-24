"""Builtin frontend catalog registry and compatibility checks."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from fecompiler.catalog.compatibility import compatibility_for_pair, compatibility_matrix
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
    compatibility = compatibility_matrix(
        catalog["cores"],
        catalog["soc_harnesses"],
        catalog["test_suites"],
    )
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
        "compatibility": [entry.to_dict() for entry in compatibility],
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

    user_cpu_filelist = str(config.get("cpu_filelist", "")).strip()
    core_cpu_filelist = _core_cpu_filelist(core)
    effective_cpu_filelist = user_cpu_filelist or core_cpu_filelist
    adapter_cpu_filelist = _adapter_cpu_filelist(config, core, user_cpu_filelist, core_cpu_filelist)
    if user_cpu_filelist and not Path(user_cpu_filelist).expanduser().exists():
        issues.append(ValidationIssue("error", "cpu_filelist_not_found", f"CPU filelist not found: {user_cpu_filelist}", "cpu_filelist"))
    if core is not None and bool(core.data.get("requires_filelist")):
        filelist = effective_cpu_filelist
        if not filelist:
            issues.append(ValidationIssue("error", "missing_cpu_filelist", "CPU filelist is required.", "cpu_filelist"))
        elif not user_cpu_filelist and not Path(filelist).expanduser().exists():
            issues.append(ValidationIssue("error", "cpu_filelist_not_found", f"CPU filelist not found: {filelist}", "cpu_filelist"))

    entries = [entry for entry in (core, soc, toolchain, test_suite) if entry is not None]
    compatibility = None
    if len(entries) == 4:
        compatibility = compatibility_for_pair(core, soc, catalog["test_suites"])
        if not _isa_compatible(core, soc, toolchain, test_suite):
            issues.append(
                ValidationIssue(
                    "error",
                    "isa_not_compatible",
                    "Selected CPU, SoC harness, toolchain, and test suite do not share a compatible ISA.",
                    "isa",
                )
            )

        if not compatibility.can_create_workspace:
            for issue in compatibility.issues:
                issues.append(
                    ValidationIssue(
                        "error",
                        str(issue.get("code", "combination_not_ready")),
                        str(issue.get("message", "CPU/SoC combination is not ready.")),
                        "compatibility",
                    )
                )
        if not _cpu_socket_compatible(core, soc):
            issues.append(
                ValidationIssue(
                    "error",
                    "catalog_wrapper_contract_violation",
                    f"Internal catalog error: CPU wrapper {core.name} declares "
                    f"{core.data.get('cpu_socket_contract', 'unknown CPU socket')}, "
                    f"but SoC wrapper {soc.name} declares "
                    f"{soc.data.get('cpu_socket_contract', 'unknown CPU socket')}. "
                    "Wrapper authors must keep this contract consistent.",
                    "catalog",
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
            severity = "error" if _soc_supported_test_suites(soc) else "warning"
            issues.append(
                ValidationIssue(
                    severity,
                    "soc_test_suite_not_supported" if severity == "error" else "suite_harness_contract",
                    f"{test_suite.name} is not declared for {soc.name}.",
                    "test_suite_id",
                )
            )
        if compatibility is not None and test_suite.id not in compatibility.supported_test_suites:
            issues.append(
                ValidationIssue(
                    "error",
                    "combination_test_suite_not_supported",
                    f"{test_suite.name} is not ready for {core.name} on {soc.name}.",
                    "test_suite_id",
                )
            )
        if not _suite_supported_by_core(test_suite, core):
            issues.append(
                ValidationIssue(
                    "error",
                    "core_test_suite_not_supported",
                    f"{core.name} does not support {test_suite.name} in the current ECOS adapter.",
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
    if has_errors:
        support_level = "unsupported"
    elif warnings:
        support_level = "experimental"
    elif compatibility is not None:
        support_level = compatibility.support_level
    else:
        support_level = "supported"
    summary = _summary_for(support_level, core, soc, test_suite)
    normalized = {
        "core_id": core_id,
        "soc_harness_id": soc_id,
        "soc_variant": str(soc.data.get("variant", "")) if soc is not None else "",
        "toolchain_id": toolchain_id,
        "test_suite_id": test_suite_id,
        "cpu_filelist": effective_cpu_filelist,
        "core_cpu_filelist": core_cpu_filelist,
        "cpu_adapter_filelist": adapter_cpu_filelist,
        "core_capability": core.integration_level if core is not None else "",
        "cpu_wrapper_contract": str(core.data.get("cpu_wrapper_contract", "")) if core is not None else "",
        "cpu_socket_contract": str(core.data.get("cpu_socket_contract", "")) if core is not None else "",
        "cpu_wrapper_top": str(core.data.get("cpu_wrapper_top", "")) if core is not None else "",
        "cpu_supports_difftest": _core_supports_difftest(core),
        "core_supported_test_suites": _core_supported_test_suites(core),
        "core_sim_program_link_base": str(core.data.get("sim_program_link_base", "")) if core is not None else "",
        "core_sim_compile_preset": str(core.data.get("sim_compile_preset", "")) if core is not None else "",
        "core_sim_compile_opt_level": str(core.data.get("sim_compile_opt_level", "")) if core is not None else "",
        "core_sim_compile_march": str(core.data.get("sim_compile_march", "")) if core is not None else "",
        "core_sim_compile_mabi": str(core.data.get("sim_compile_mabi", "")) if core is not None else "",
        "core_sim_compile_extra_cflags": _core_sim_compile_extra_cflags(core),
        "core_sim_coremark_iterations": str(core.data.get("sim_coremark_iterations", "")) if core is not None else "",
        "core_sim_coremark_total_data_size": str(core.data.get("sim_coremark_total_data_size", "")) if core is not None else "",
        "core_sim_coremark_max_cycles": str(core.data.get("sim_coremark_max_cycles", "")) if core is not None else "",
        "core_sim_coremark_has_float": core.data.get("sim_coremark_has_float", "") if core is not None else "",
        "core_sim_coremark_use_difftest": core.data.get("sim_coremark_use_difftest", "") if core is not None else "",
        "soc_harness_capability": soc.integration_level if soc is not None else "",
        "soc_wrapper_contract": str(soc.data.get("wrapper_contract", "")) if soc is not None else "",
        "soc_wrapper_top": str(soc.data.get("wrapper_top", "")) if soc is not None else "",
        "soc_cpu_socket_contract": str(soc.data.get("cpu_socket_contract", "")) if soc is not None else "",
        "soc_supports_difftest": _soc_supports_difftest(soc),
        "soc_supported_test_suites": _soc_supported_test_suites(soc),
        "required_capability": "sim_ready",
        "compatibility_status": compatibility.status if compatibility is not None else "",
        "compatibility_summary": compatibility.summary if compatibility is not None else "",
        "compatible_test_suites": compatibility.supported_test_suites if compatibility is not None else [],
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
    catalog = {
        category: [CatalogEntry.from_dict(item) for item in _load_builtin(filename)]
        for category, filename in _CATEGORY_FILES.items()
    }
    catalog["cores"] = _merge_catalog_manifests(catalog["cores"], _catalog_manifest_dir("adapters"))
    catalog["soc_harnesses"] = _merge_catalog_manifests(catalog["soc_harnesses"], _catalog_manifest_dir("thirdparty"))
    return catalog


def _load_builtin(filename: str) -> list[dict[str, Any]]:
    package = "fecompiler.catalog.builtin"
    with resources.files(package).joinpath(filename).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"catalog file must contain a list: {filename}")
    return [dict(item) for item in data if isinstance(item, dict)]


def _merge_catalog_manifests(entries: list[CatalogEntry], root: Path) -> list[CatalogEntry]:
    by_id = {entry.id: entry for entry in entries}
    order = [entry.id for entry in entries]

    for item in _load_catalog_manifests(root):
        entry = CatalogEntry.from_dict(item)
        if not entry.id:
            continue
        if entry.id not in by_id:
            order.append(entry.id)
        by_id[entry.id] = entry

    return [by_id[entry_id] for entry_id in order if entry_id in by_id]


def _load_catalog_manifests(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for manifest_path in sorted(root.glob("*/catalog.json")):
        try:
            with manifest_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            item = dict(data)
            item.setdefault("manifest_path", str(manifest_path))
            items.append(item)
    return items


def _catalog_manifest_dir(name: str) -> Path:
    return _frontend_repo_root() / "fecompiler" / name


def _frontend_repo_root() -> Path:
    env_root = os.getenv("ECOS_FE_COMPILER_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


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
        "SoC": "ysyx-am-soc",
        "SoC2": "ysyx-am-soc",
        "SoC3": "ysyx-am-soc",
        "soc1": "ysyx-am-soc",
        "soc2": "ysyx-am-soc",
        "soc3": "ysyx-am-soc",
        "ysyx-am-soc-alt": "ysyx-am-soc",
        "ysyx-am-soc-extended": "ysyx-am-soc",
    }
    return variant_map.get(value, value)


def _find(entries: list[CatalogEntry], entry_id: str) -> CatalogEntry | None:
    return next((entry for entry in entries if entry.id == entry_id), None)


def _core_cpu_filelist(core: CatalogEntry | None) -> str:
    if core is None or bool(core.data.get("requires_filelist")):
        return ""
    builtin = str(core.data.get("cpu_filelist", "")).strip()
    if not builtin:
        return ""
    path = Path(builtin).expanduser()
    if path.is_absolute():
        return str(path)
    return str((Path(__file__).resolve().parents[2] / path).resolve())


def _adapter_cpu_filelist(
    config: dict[str, Any],
    core: CatalogEntry | None,
    user_cpu_filelist: str,
    core_cpu_filelist: str,
) -> str:
    if not user_cpu_filelist or not core_cpu_filelist:
        return ""
    core_id = _read_id(config, "core_id", DEFAULT_CORE_ID)
    if core_id == DEFAULT_CORE_ID or (core is not None and bool(core.data.get("requires_filelist"))):
        return ""
    return core_cpu_filelist


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


def _cpu_socket_compatible(core: CatalogEntry, soc: CatalogEntry) -> bool:
    core_socket = str(core.data.get("cpu_socket_contract", "")).strip()
    soc_socket = str(soc.data.get("cpu_socket_contract", "")).strip()
    return not core_socket or not soc_socket or core_socket == soc_socket


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
    supported = _soc_supported_test_suites(soc)
    if supported:
        return test_suite.id in supported

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


def _suite_supported_by_core(test_suite: CatalogEntry, core: CatalogEntry) -> bool:
    supported = _core_supported_test_suites(core)
    return not supported or test_suite.id in supported


def _core_supported_test_suites(core: CatalogEntry | None) -> list[str]:
    if core is None:
        return []
    return [str(item).strip() for item in core.data.get("supported_test_suites", []) if str(item).strip()]


def _core_sim_compile_extra_cflags(core: CatalogEntry | None) -> list[str]:
    if core is None:
        return []
    return [str(item).strip() for item in core.data.get("sim_compile_extra_cflags", []) if str(item).strip()]


def _core_supports_difftest(core: CatalogEntry | None) -> bool:
    if core is None:
        return True
    return bool(core.data.get("supports_difftest", True))


def _soc_supports_difftest(soc: CatalogEntry | None) -> bool:
    if soc is None:
        return True
    return bool(soc.data.get("supports_difftest", True))


def _soc_supported_test_suites(soc: CatalogEntry | None) -> list[str]:
    if soc is None:
        return []
    return [str(item).strip() for item in soc.data.get("supported_test_suites", []) if str(item).strip()]


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
