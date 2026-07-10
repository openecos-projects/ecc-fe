"""CPU and SoC compatibility helpers for frontend catalog entries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fecompiler.catalog.schema import CatalogEntry


@dataclass(frozen=True, slots=True)
class CompatibilityEntry:
    core_id: str
    soc_harness_id: str
    can_create_workspace: bool
    support_level: str
    status: str
    summary: str
    supported_test_suites: list[str] = field(default_factory=list)
    issues: list[dict[str, str]] = field(default_factory=list)
    requires_cpu_filelist: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "core_id": self.core_id,
            "soc_harness_id": self.soc_harness_id,
            "can_create_workspace": self.can_create_workspace,
            "support_level": self.support_level,
            "status": self.status,
            "summary": self.summary,
            "supported_test_suites": list(self.supported_test_suites),
            "issues": [dict(issue) for issue in self.issues],
            "requires_cpu_filelist": self.requires_cpu_filelist,
        }


def compatibility_matrix(
    cores: list[CatalogEntry],
    soc_harnesses: list[CatalogEntry],
    test_suites: list[CatalogEntry],
) -> list[CompatibilityEntry]:
    return [
        compatibility_for_pair(core, soc, test_suites)
        for core in cores
        for soc in soc_harnesses
    ]


def compatibility_for_pair(
    core: CatalogEntry,
    soc: CatalogEntry,
    test_suites: list[CatalogEntry],
) -> CompatibilityEntry:
    issues: list[dict[str, str]] = []

    if not _isa_compatible(core, soc):
        issues.append(_issue("isa_not_compatible", "CPU and SoC harness do not share a compatible ISA."))

    if not _cpu_socket_compatible(core, soc):
        issues.append(
            _issue(
                "socket_not_compatible",
                "CPU wrapper and SoC harness use different CPU socket contracts.",
            )
        )

    if not core.sim_ready:
        issues.append(
            _issue(
                "core_sim_adapter_not_ready",
                f"{core.name} is {core.integration_level}; a simulation CPU wrapper is still required.",
            )
        )

    if not soc.sim_ready:
        issues.append(
            _issue(
                "soc_sim_adapter_not_ready",
                f"{soc.name} is {soc.integration_level}; a simulation SoC wrapper is still required.",
            )
        )

    suites = _supported_test_suites(core, soc, test_suites) if not issues else []
    if core.sim_ready and soc.sim_ready and not suites:
        issues.append(
            _issue(
                "no_common_test_suite",
                "CPU and SoC harness do not have a common implemented test suite.",
            )
        )

    can_create = not issues
    support_level = _support_level(core, soc, can_create)
    status = _status(core, soc, can_create)
    summary = _summary(core, soc, support_level, suites, issues)
    return CompatibilityEntry(
        core_id=core.id,
        soc_harness_id=soc.id,
        can_create_workspace=can_create,
        support_level=support_level,
        status=status,
        summary=summary,
        supported_test_suites=suites,
        issues=issues,
        requires_cpu_filelist=bool(core.data.get("requires_filelist")),
    )


def _supported_test_suites(
    core: CatalogEntry,
    soc: CatalogEntry,
    test_suites: list[CatalogEntry],
) -> list[str]:
    implemented = [suite.id for suite in test_suites if suite.status != "planned"]
    core_suites = _entry_supported_test_suites(core, implemented)
    soc_suites = _entry_supported_test_suites(soc, implemented)
    common = [suite_id for suite_id in implemented if suite_id in core_suites and suite_id in soc_suites]

    return common


def _entry_supported_test_suites(entry: CatalogEntry, implemented: list[str]) -> list[str]:
    declared = [
        str(item).strip()
        for item in entry.data.get("supported_test_suites", [])
        if str(item).strip()
    ]
    if declared:
        return [suite_id for suite_id in implemented if suite_id in declared]
    if entry.sim_ready:
        return list(implemented)
    return []


def _support_level(core: CatalogEntry, soc: CatalogEntry, can_create: bool) -> str:
    if not can_create:
        return "unsupported"
    if core.status == "stable" and soc.status == "stable":
        return "supported"
    return "experimental"


def _status(core: CatalogEntry, soc: CatalogEntry, can_create: bool) -> str:
    if can_create:
        if bool(core.data.get("requires_filelist")):
            return "requires_filelist"
        if core.status == "stable" and soc.status == "stable":
            return "ready"
        return "experimental"
    if core.filelist_ready and not core.sim_ready:
        return "needs_cpu_adapter"
    if soc.filelist_ready and not soc.sim_ready:
        return "needs_soc_adapter"
    return "planned"


def _summary(
    core: CatalogEntry,
    soc: CatalogEntry,
    support_level: str,
    suites: list[str],
    issues: list[dict[str, str]],
) -> str:
    if issues:
        return str(issues[0].get("message", "CPU/SoC combination is not ready."))
    suites_text = ", ".join(suites) if suites else "no tests"
    if support_level == "supported":
        return f"{core.name} can run {suites_text} on {soc.name}."
    return f"{core.name} can experimentally run {suites_text} on {soc.name}."


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _cpu_socket_compatible(core: CatalogEntry, soc: CatalogEntry) -> bool:
    core_socket = str(core.data.get("cpu_socket_contract", "")).strip()
    soc_socket = str(soc.data.get("cpu_socket_contract", "")).strip()
    return not core_socket or not soc_socket or core_socket == soc_socket


def _isa_compatible(core: CatalogEntry, soc: CatalogEntry) -> bool:
    core_isa = _expanded_isa_set(core.isa)
    soc_isa = _expanded_isa_set(soc.isa)
    return not core_isa or not soc_isa or bool(core_isa.intersection(soc_isa))


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
