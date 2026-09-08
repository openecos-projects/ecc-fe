from __future__ import annotations

from fecompiler.catalog import (
    catalog_payload,
    check_catalog_contracts,
    validate_frontend_config,
)
from fecompiler.cli.core.inputs import (
    CatalogListInput,
    CatalogShowInput,
    CatalogValidateInput,
    CommandInput,
)
from fecompiler.cli.core.records import error_record
from fecompiler.cli.core.types import CommandContext, CommandResult

_KINDS = {
    "core": "cores",
    "soc": "soc_harnesses",
    "toolchain": "toolchains",
    "test-suite": "test_suites",
}


def list_catalog(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, CatalogListInput)
    categories = _selected_categories(command_input.kind)
    if categories is None:
        return _invalid_kind(command_input.kind)
    payload = catalog_payload()
    statuses = sorted(
        {
            str(raw.get("status"))
            for category in _KINDS.values()
            for raw in payload.get(category, [])
            if isinstance(raw, dict) and raw.get("status")
        }
    )
    if command_input.status and command_input.status not in statuses:
        return CommandResult.err(
            [
                error_record(
                    "invalid_catalog_status",
                    status=command_input.status,
                    expected=statuses,
                )
            ],
            exit_code=2,
        )
    records: list[dict[str, object]] = []
    for kind, category in categories:
        for raw in payload.get(category, []):
            if not isinstance(raw, dict):
                continue
            if command_input.status and str(raw.get("status")) != command_input.status:
                continue
            records.append(_entry_record(kind, raw))
    return CommandResult.ok(records)


def show_catalog(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, CatalogShowInput)
    payload = catalog_payload()
    records = [
        _entry_record(kind, raw, include_data=True)
        for kind, category in _KINDS.items()
        for raw in payload.get(category, [])
        if isinstance(raw, dict) and str(raw.get("id")) == command_input.entry_id
    ]
    if not records:
        return CommandResult.err(
            [error_record("catalog_entry_not_found", entry_id=command_input.entry_id)]
        )
    return CommandResult.ok(records)


def check_catalog(
    command_input: CommandInput, context: CommandContext
) -> CommandResult:
    result = check_catalog_contracts()
    records: list[dict[str, object]] = [
        {
            "kind": "catalog_check",
            "status": "pass" if result.ok else "fail",
            "summary": result.summary,
            "counts": result.counts,
        }
    ]
    records.extend(
        {"kind": "catalog_issue", **issue.to_dict()} for issue in result.issues
    )
    return CommandResult.ok(records) if result.ok else CommandResult.err(records)


def validate_catalog(
    command_input: CommandInput, context: CommandContext
) -> CommandResult:
    assert isinstance(command_input, CatalogValidateInput)
    request = {
        key: value
        for key, value in {
            "core_id": command_input.core_id,
            "soc_harness_id": command_input.soc_harness_id,
            "toolchain_id": command_input.toolchain_id,
            "test_suite_id": command_input.test_suite_id,
            "cpu_filelist": command_input.cpu_filelist,
            "cpu_top_module": command_input.cpu_top_module,
        }.items()
        if value
    }
    result = validate_frontend_config(request)
    records: list[dict[str, object]] = [
        {
            "kind": "catalog_validation",
            "status": "pass" if result.ok else "fail",
            "support_level": result.support_level,
            "summary": result.summary,
            "normalized": result.normalized,
        }
    ]
    records.extend(
        {"kind": "catalog_issue", **issue.to_dict()} for issue in result.issues
    )
    return CommandResult.ok(records) if result.ok else CommandResult.err(records)


def _selected_categories(kind: str | None) -> list[tuple[str, str]] | None:
    if kind is None:
        return list(_KINDS.items())
    category = _KINDS.get(kind)
    return [(kind, category)] if category else None


def _invalid_kind(kind: str | None) -> CommandResult:
    return CommandResult.err(
        [error_record("invalid_catalog_kind", kind=kind, expected=list(_KINDS))],
        exit_code=2,
    )


def _entry_record(
    kind: str, raw: dict[str, object], *, include_data: bool = False
) -> dict[str, object]:
    record: dict[str, object] = {
        "kind": "catalog_entry",
        "catalog_kind": kind,
        "id": str(raw.get("id", "")),
        "name": str(raw.get("name", "")),
        "status": str(raw.get("status", "")),
        "integration_level": str(raw.get("integration_level", "")),
        "isa": raw.get("isa", []),
        "tags": raw.get("tags", []),
        "description": str(raw.get("description", "")),
    }
    if include_data:
        record["data"] = raw
    return record
