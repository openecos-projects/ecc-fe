"""Static checks for ECOS frontend CPU/SoC adapter contracts."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fecompiler.catalog.registry import catalog_payload
from fecompiler.resources import frontend_repo_root, resolve_thirdparty_path

CPU_WRAPPER_CONTRACT = "ecos-cpu-wrapper-v1"
CPU_SOCKET_CONTRACT = "ysyx-axi-cpu-socket-v1"
SOC_WRAPPER_CONTRACT = "ecos-sim-wrapper-v1"
SOC_WRAPPER_TOP = "ecos_sim_top"
COMPATIBILITY_CPU_ALIAS_TOP = "ysyx_00000000"

_RTL_SUFFIXES = (".v", ".sv", ".vh", ".svh")


@dataclass(frozen=True, slots=True)
class ContractIssue:
    severity: str
    code: str
    message: str
    entry_type: str = ""
    entry_id: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "entry_type": self.entry_type,
            "entry_id": self.entry_id,
            "path": self.path,
        }


@dataclass(frozen=True, slots=True)
class ContractCheckResult:
    ok: bool
    summary: str
    counts: dict[str, int]
    issues: list[ContractIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "counts": dict(self.counts),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def check_catalog_contracts() -> ContractCheckResult:
    """Check that sim-ready catalog entries have concrete adapter collateral."""
    payload = catalog_payload()
    root = _frontend_repo_root()
    cores = _dict_items(payload.get("cores", []))
    socs = _dict_items(payload.get("soc_harnesses", []))
    compatibility = _dict_items(payload.get("compatibility", []))

    issues: list[ContractIssue] = []
    for core in cores:
        issues.extend(_check_cpu_entry(root, core))
    for soc in socs:
        issues.extend(_check_soc_entry(root, soc))
    issues.extend(_check_compatibility_entries(compatibility))

    counts = {
        "cpu_total": len(cores),
        "soc_total": len(socs),
        "sim_ready_cpu": sum(1 for item in cores if _is_sim_ready(item)),
        "sim_ready_soc": sum(1 for item in socs if _is_sim_ready(item)),
        "creatable_pairs": sum(1 for item in compatibility if bool(item.get("can_create_workspace"))),
    }
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        summary = f"frontend catalog contract check failed with {len(errors)} error(s)"
    else:
        summary = "frontend catalog contract check passed"
    return ContractCheckResult(ok=not errors, summary=summary, counts=counts, issues=issues)


def _check_cpu_entry(root: Path, entry: dict[str, Any]) -> list[ContractIssue]:
    entry_id = str(entry.get("id", "")).strip()
    if not _is_sim_ready(entry):
        return []

    issues: list[ContractIssue] = []
    issues.extend(_require_value(entry, "cpu", entry_id, "cpu_wrapper_contract", CPU_WRAPPER_CONTRACT))
    issues.extend(_require_value(entry, "cpu", entry_id, "cpu_socket_contract", CPU_SOCKET_CONTRACT))
    issues.extend(_require_text(entry, "cpu", entry_id, "cpu_wrapper_top"))
    issues.extend(_require_nonempty_list(entry, "cpu", entry_id, "supported_test_suites"))

    if bool(entry.get("requires_filelist")):
        if str(entry.get("cpu_wrapper_generation", "")).strip() == "standard_alias_v1":
            issues.extend(_require_value(entry, "cpu", entry_id, "cpu_standard_top", "ecos_user_cpu_top"))
        return issues

    filelist_path = _resolve_repo_path(root, str(entry.get("cpu_filelist", "")).strip())
    if filelist_path is None:
        issues.append(_issue("error", "missing_cpu_filelist", "sim-ready CPU requires cpu_filelist", "cpu", entry_id))
        return issues
    if not filelist_path.is_file():
        issues.append(_issue("error", "cpu_filelist_not_found", f"CPU filelist not found: {filelist_path}", "cpu", entry_id, str(filelist_path)))
        return issues

    parsed = _parse_filelist(filelist_path)
    issues.extend(_filelist_missing_issues(parsed, "cpu", entry_id))
    wrapper_top = str(entry.get("cpu_wrapper_top", "")).strip()
    if wrapper_top and not _filelist_defines_module(parsed.files, wrapper_top):
        issues.append(
            _issue(
                "error",
                "cpu_wrapper_top_not_in_filelist",
                f"CPU wrapper module not found in filelist RTL: {wrapper_top}",
                "cpu",
                entry_id,
                str(filelist_path),
            )
        )
    if not _filelist_defines_module(parsed.files, COMPATIBILITY_CPU_ALIAS_TOP):
        issues.append(
            _issue(
                "error",
                "cpu_compatibility_alias_not_in_filelist",
                f"sim-ready CPU filelist must define the SoC-facing compatibility module: {COMPATIBILITY_CPU_ALIAS_TOP}",
                "cpu",
                entry_id,
                str(filelist_path),
            )
        )
    return issues


def _check_soc_entry(root: Path, entry: dict[str, Any]) -> list[ContractIssue]:
    entry_id = str(entry.get("id", "")).strip()
    if not _is_sim_ready(entry):
        return []

    issues: list[ContractIssue] = []
    issues.extend(_require_value(entry, "soc", entry_id, "wrapper_contract", SOC_WRAPPER_CONTRACT))
    issues.extend(_require_value(entry, "soc", entry_id, "wrapper_top", SOC_WRAPPER_TOP))
    issues.extend(_require_value(entry, "soc", entry_id, "cpu_socket_contract", CPU_SOCKET_CONTRACT))
    issues.extend(_require_nonempty_list(entry, "soc", entry_id, "supported_test_suites"))

    directory = _resolve_repo_path(root, str(entry.get("directory", "")).strip())
    if directory is None:
        issues.append(_issue("error", "missing_soc_directory", "sim-ready SoC requires directory", "soc", entry_id))
        return issues
    if not directory.is_dir():
        issues.append(_issue("error", "soc_directory_not_found", f"SoC directory not found: {directory}", "soc", entry_id, str(directory)))
        return issues

    manifest = _load_json(directory / "manifest.json")
    if manifest is None:
        issues.append(_issue("error", "soc_manifest_not_found", f"SoC manifest not found: {directory / 'manifest.json'}", "soc", entry_id, str(directory)))
        return issues

    for field in ("soc_filelist", "testbench", "sim_build_test_script"):
        value = str(manifest.get(field, "")).strip()
        path = _resolve_manifest_path(directory, value)
        if path is None or not path.is_file():
            issues.append(_issue("error", f"soc_{field}_not_found", f"SoC {field} not found: {value}", "soc", entry_id, str(path or directory)))

    for value in _str_list(manifest.get("sim_cpp_sources", [])):
        path = _resolve_manifest_path(directory, value)
        if path is None or not path.is_file():
            issues.append(_issue("error", "soc_sim_cpp_source_not_found", f"SoC simulator C++ source not found: {value}", "soc", entry_id, str(path or directory)))

    filelist_path = _resolve_manifest_path(directory, str(manifest.get("soc_filelist", "")).strip())
    if filelist_path is not None and filelist_path.is_file():
        parsed = _parse_filelist(filelist_path)
        issues.extend(_filelist_missing_issues(parsed, "soc", entry_id))
        wrapper_top = str(entry.get("wrapper_top", "")).strip()
        if wrapper_top and not _filelist_defines_module(parsed.files, wrapper_top):
            issues.append(
                _issue(
                    "error",
                    "soc_wrapper_top_not_in_filelist",
                    f"SoC wrapper module not found in filelist RTL: {wrapper_top}",
                    "soc",
                    entry_id,
                    str(filelist_path),
                )
            )

    if "cpu-tests" in _str_list(entry.get("supported_test_suites", [])):
        programs_dir = _resolve_manifest_path(directory, str(manifest.get("sim_programs_dir", "")).strip())
        add_case = programs_dir / "add.c" if programs_dir is not None else None
        if add_case is None or not add_case.is_file():
            issues.append(
                _issue(
                    "error",
                    "soc_cpu_tests_smoke_case_not_found",
                    "SoC declares cpu-tests support but its manifest cannot resolve tests/programs/add.c.",
                    "soc",
                    entry_id,
                    str(add_case or directory),
                )
            )
    return issues


def _check_compatibility_entries(entries: list[dict[str, Any]]) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for entry in entries:
        if not bool(entry.get("can_create_workspace")):
            continue
        suites = _str_list(entry.get("supported_test_suites", []))
        if not suites:
            pair = f"{entry.get('core_id', '')}+{entry.get('soc_harness_id', '')}"
            issues.append(
                _issue(
                    "error",
                    "creatable_pair_without_tests",
                    "Creatable CPU/SoC pair must expose at least one supported test suite.",
                    "compatibility",
                    pair,
                )
            )
    return issues


def _require_text(entry: dict[str, Any], entry_type: str, entry_id: str, field_name: str) -> list[ContractIssue]:
    if str(entry.get(field_name, "")).strip():
        return []
    return [_issue("error", f"missing_{field_name}", f"sim-ready {entry_type} requires {field_name}", entry_type, entry_id)]


def _require_value(
    entry: dict[str, Any],
    entry_type: str,
    entry_id: str,
    field_name: str,
    expected: str,
) -> list[ContractIssue]:
    actual = str(entry.get(field_name, "")).strip()
    if actual == expected:
        return []
    message = f"sim-ready {entry_type} requires {field_name}={expected}, got {actual or '<empty>'}"
    return [_issue("error", f"invalid_{field_name}", message, entry_type, entry_id)]


def _require_nonempty_list(
    entry: dict[str, Any],
    entry_type: str,
    entry_id: str,
    field_name: str,
) -> list[ContractIssue]:
    if _str_list(entry.get(field_name, [])):
        return []
    return [_issue("error", f"missing_{field_name}", f"sim-ready {entry_type} requires {field_name}", entry_type, entry_id)]


@dataclass(frozen=True, slots=True)
class ParsedFilelist:
    path: Path
    files: list[Path]
    missing: list[Path]


def _parse_filelist(path: Path, visited: set[Path] | None = None) -> ParsedFilelist:
    resolved = path.expanduser().resolve()
    if visited is None:
        visited = set()
    if resolved in visited:
        return ParsedFilelist(path=resolved, files=[], missing=[])
    visited.add(resolved)

    files: list[Path] = []
    missing: list[Path] = []
    for raw_line in resolved.read_text(encoding="utf-8", errors="ignore").splitlines():
        tokens = _filelist_tokens(raw_line)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            nested = ""
            if token == "-f" and index + 1 < len(tokens):
                nested = tokens[index + 1]
                index += 2
            elif token.startswith("-f") and len(token) > 2:
                nested = token[2:]
                index += 1
            else:
                index += 1

            if nested:
                nested_path = _resolve_manifest_path(resolved.parent, nested)
                if nested_path is not None:
                    nested_path = resolve_thirdparty_path(nested_path)
                if nested_path is not None and nested_path.is_file():
                    nested_result = _parse_filelist(nested_path, visited)
                    files.extend(nested_result.files)
                    missing.extend(nested_result.missing)
                elif nested_path is not None:
                    missing.append(nested_path)
                continue

            if not _is_rtl_path_token(token):
                continue
            file_path = _resolve_manifest_path(resolved.parent, token)
            if file_path is None:
                continue
            file_path = resolve_thirdparty_path(file_path)
            if file_path.is_file():
                files.append(file_path)
            else:
                missing.append(file_path)
    return ParsedFilelist(path=resolved, files=files, missing=missing)


def _filelist_tokens(raw_line: str) -> list[str]:
    line = raw_line.strip()
    if not line or line.startswith("#") or line.startswith("//"):
        return []
    try:
        return shlex.split(line, comments=True, posix=True)
    except ValueError:
        return line.split()


def _is_rtl_path_token(token: str) -> bool:
    if token.startswith(("+", "-", "$")):
        return False
    return Path(token).suffix.lower() in _RTL_SUFFIXES


def _filelist_missing_issues(parsed: ParsedFilelist, entry_type: str, entry_id: str) -> list[ContractIssue]:
    return [
        _issue(
            "error",
            "filelist_rtl_not_found",
            f"RTL file referenced by filelist was not found: {path}",
            entry_type,
            entry_id,
            str(parsed.path),
        )
        for path in parsed.missing
    ]


def _filelist_defines_module(files: list[Path], module_name: str) -> bool:
    pattern = re.compile(rf"\bmodule\s+{re.escape(module_name)}\b")
    for path in files:
        if path.name == f"{module_name}.v" or path.name == f"{module_name}.sv":
            return True
        try:
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                return True
        except OSError:
            continue
    return False


def _resolve_repo_path(root: Path, value: str) -> Path | None:
    text = value.strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if path.is_absolute():
        return path.resolve()
    candidate = (root / path).resolve()
    return resolve_thirdparty_path(candidate)


def _resolve_manifest_path(base: Path, value: str) -> Path | None:
    text = value.strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if path.is_absolute():
        return path.resolve()
    candidate = (base / path).resolve()
    return resolve_thirdparty_path(candidate)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return dict(data) if isinstance(data, dict) else None


def _frontend_repo_root() -> Path:
    return frontend_repo_root()


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _is_sim_ready(entry: dict[str, Any]) -> bool:
    return str(entry.get("integration_level", "")).strip() == "sim_ready"


def _issue(
    severity: str,
    code: str,
    message: str,
    entry_type: str = "",
    entry_id: str = "",
    path: str = "",
) -> ContractIssue:
    return ContractIssue(
        severity=severity,
        code=code,
        message=message,
        entry_type=entry_type,
        entry_id=entry_id,
        path=path,
    )
