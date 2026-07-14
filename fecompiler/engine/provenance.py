"""Deterministic provenance fingerprints for frontend flow steps."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from fecompiler.resources import frontend_repo_root
from fecompiler.tools.common.rtl_inputs import workspace_input_fingerprint
from fecompiler.utility.json import json_read


_SCHEMA_VERSION = 1
_STEP_CONFIG_FIELDS: dict[str, tuple[str, ...]] = {
    "prepare": (
        "top_module",
        "cpu_wrapper_id",
        "cpu_wrapper_contract",
        "cpu_socket_contract",
        "cpu_wrapper_top",
        "required_cpu_top_module",
        "required_cpu_top_ports",
        "required_cpu_top_port_contract",
        "cpu_reset_vector",
        "soc_cpu_reset_vector",
        "soc_wrapper_id",
        "soc_wrapper_contract",
        "soc_wrapper_top",
    ),
    "review": ("review_baseline", "review_waivers"),
    "elab": ("top_module",),
    "lint": ("top_module", "lint_profile", "lint_baseline", "lint_waivers"),
}
_SIM_CONFIG_FIELDS = (
    "testbench",
    "frequency_max",
    "Frequency max [MHz]",
    "cpu_supports_difftest",
    "soc_supports_difftest",
)
_STEP_SOURCE_FILES: dict[str, tuple[str, ...]] = {
    "prepare": ("tools/prepare/runner.py",),
    "review": (
        "tools/review/runner.py",
        "tools/review/analyzer.py",
        "tools/review/structural_probe.py",
    ),
    "elab": ("tools/slang/runner.py",),
    "lint": ("tools/verilator/runner.py",),
    "sim": ("tools/verilator/runner.py",),
}
_STEP_EXECUTABLES: dict[str, tuple[str, ...]] = {
    "review": ("yosys",),
    "elab": ("slang",),
    "lint": ("verilator",),
    "sim": ("verilator",),
}


def build_step_provenance(
    workspace: dict[str, Any],
    step_name: str,
    tool: str,
    upstream: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the current provenance inputs and their deterministic signature."""
    parameters = _workspace_parameters(workspace)
    source = workspace_input_fingerprint(workspace)
    configuration = _step_configuration(workspace, parameters, step_name)
    resources = _resource_versions(parameters)
    tools = _tool_identity(step_name, tool)
    upstream_identity = {
        "step": str((upstream or {}).get("step", "")),
        "signature": str((upstream or {}).get("signature", "")),
        "output_fingerprint": str((upstream or {}).get("output_fingerprint", "")),
    }
    inputs = {
        "source": source,
        "upstream": upstream_identity,
    }
    record = {
        "schema_version": _SCHEMA_VERSION,
        "step": step_name,
        "tool": tool,
        "inputs": inputs,
        "configuration": configuration,
        "resources": resources,
        "tools": tools,
        "input_fingerprint": stable_fingerprint(inputs),
        "config_fingerprint": stable_fingerprint(configuration),
        "tool_fingerprint": stable_fingerprint({"resources": resources, "tools": tools}),
    }
    record["signature"] = stable_fingerprint({
        "schema_version": record["schema_version"],
        "step": step_name,
        "input_fingerprint": record["input_fingerprint"],
        "config_fingerprint": record["config_fingerprint"],
        "tool_fingerprint": record["tool_fingerprint"],
    })
    return record


def output_fingerprint(paths: list[str]) -> str:
    """Fingerprint selected result artifacts without traversing large output trees."""
    digest = hashlib.sha256()
    for raw_path in sorted({str(path) for path in paths if str(path).strip()}):
        path = Path(raw_path).expanduser().resolve()
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def stable_fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _workspace_parameters(workspace: dict[str, Any]) -> dict[str, Any]:
    path = str(workspace.get("parameters_path", "")).strip()
    if not path:
        return {}
    try:
        data = json_read(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _step_configuration(
    workspace: dict[str, Any],
    parameters: dict[str, Any],
    step_name: str,
) -> dict[str, Any]:
    merged = dict(parameters)
    merged.update(workspace)
    fields = list(_STEP_CONFIG_FIELDS.get(step_name, ()))
    if step_name == "sim":
        fields.extend(_SIM_CONFIG_FIELDS)
        fields.extend(sorted(key for key in merged if str(key).startswith("sim_")))
    return {
        field: _normalize_value(merged.get(field))
        for field in dict.fromkeys(fields)
        if field in merged
    }


def _resource_versions(parameters: dict[str, Any]) -> Any:
    versions = parameters.get("resource_versions", {})
    return _normalize_value(versions) if isinstance(versions, (dict, list)) else {}


def _tool_identity(step_name: str, tool: str) -> dict[str, Any]:
    root = frontend_repo_root()
    source_files = [root / "fecompiler" / relative for relative in _STEP_SOURCE_FILES.get(step_name, ())]
    executables = {
        name: _path_identity(shutil.which(name) or name)
        for name in _STEP_EXECUTABLES.get(step_name, ())
    }
    return {
        "runner": tool,
        "runtime_root": str(root),
        "runner_sources": {
            str(path.relative_to(root)): _file_sha256(path)
            for path in source_files
        },
        "executables": executables,
    }


def _path_identity(raw_path: str) -> dict[str, Any]:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        return {"path": raw_path, "available": False}
    resolved = path.resolve()
    identity: dict[str, Any] = {"path": str(resolved), "available": resolved.is_file()}
    try:
        stat = resolved.stat()
    except OSError:
        return identity
    identity.update({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    return identity


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _normalize_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, Path):
        return str(value.expanduser().resolve())
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
