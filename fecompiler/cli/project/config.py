from __future__ import annotations

import json
import math
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fecompiler.cli.project.params import (
    PARAMETERS,
    effective_default,
    lookup_parameter,
    normalize_stored_value,
)
from fecompiler.cli.workspace_access import read_json_object

CONFIG_FILENAME = "ecc-fe.toml"
CONFIG_SCHEMA_VERSION = 1
_CONFIG_KEYS = {
    "schema_version",
    "design",
    "frontend",
    "flow",
    "defaults",
    "params",
}


class ProjectConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    path: Path
    design: dict[str, object]
    frontend: dict[str, object]
    flow: dict[str, object]
    defaults: dict[str, object]
    overrides: dict[str, object]

    @property
    def run_id(self) -> str:
        value = self.flow.get("run", "default")
        if not isinstance(value, str) or not value or value != value.strip():
            raise ProjectConfigError(f"Unsupported flow.run: {value}")
        if "\x00" in value:
            raise ProjectConfigError("Unsupported flow.run: contains a null byte")
        return value

    @property
    def uses_project_layout(self) -> bool:
        return bool(self.flow)


def project_config_path(directory: str) -> Path:
    return Path(directory).expanduser().resolve() / CONFIG_FILENAME


def load_project_config(directory: str) -> ProjectConfig | None:
    path = project_config_path(directory)
    if not path.is_file():
        return None
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ProjectConfigError(f"Unable to read {path}: {error}") from error
    if raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ProjectConfigError(
            f"Unsupported {CONFIG_FILENAME} schema_version: {raw.get('schema_version')}"
        )
    unknown_keys = sorted(set(raw) - _CONFIG_KEYS)
    if unknown_keys:
        raise ProjectConfigError(
            f"Unknown top-level key in {path}: {', '.join(unknown_keys)}"
        )
    design = _table(raw, "design", path)
    frontend = _table(raw, "frontend", path)
    flow = _table(raw, "flow", path)
    defaults = _validated_parameter_table(raw, "defaults", path)
    overrides = _validated_parameter_table(raw, "params", path)
    config = ProjectConfig(path, design, frontend, flow, defaults, overrides)
    if flow:
        config.run_id
    return config


def _validated_parameter_table(
    raw: dict[str, Any], key: str, path: Path
) -> dict[str, object]:
    values = _parameter_table(raw, key, path)
    normalized: dict[str, object] = {}
    for param, value in values.items():
        schema = lookup_parameter(param)
        if schema is None:
            raise ProjectConfigError(f"Unknown parameter in {path}: {param}")
        try:
            normalized[param] = normalize_stored_value(value, schema)
        except (TypeError, ValueError) as error:
            raise ProjectConfigError(f"Invalid {param} in {path}: {error}") from error
    return normalized


def create_project_config(
    directory: str,
    parameters: dict[str, Any],
    *,
    overwrite: bool = False,
    project_layout: bool = False,
) -> ProjectConfig:
    path = project_config_path(directory)
    if path.exists() and not overwrite:
        raise ProjectConfigError(f"Project config already exists: {path}")
    defaults = {
        schema.param: effective_default(parameters, schema) for schema in PARAMETERS
    }
    design = {
        "name": str(parameters.get("Design", Path(directory).name)),
        "top": str(parameters.get("Top module", "top")),
        "clock_port": str(parameters.get("Clock", "clk")),
        "frequency_mhz": defaults["design.frequency_mhz"],
    }
    frontend = {
        key: parameters[key]
        for key in (
            "frontend_core_id",
            "soc_harness_id",
            "toolchain_id",
            "test_suite_id",
            "cpu_filelist",
            "soc_filelist",
        )
        if key in parameters
        and parameters[key] is not None
        and parameters[key] != ""
        and parameters[key] != []
    }
    flow = {"run": "default"} if project_layout else {}
    text = _render_config(design, frontend, flow, defaults, {})
    _atomic_write_text(path, text)
    loaded = load_project_config(directory)
    if loaded is None:
        raise ProjectConfigError(f"Unable to create project config: {path}")
    return loaded


def ensure_project_config(directory: str, parameters: dict[str, Any]) -> ProjectConfig:
    config = load_project_config(directory)
    return config or create_project_config(directory, parameters)


def write_parameter_override(
    config: ProjectConfig, key: str, value: object | None, *, unset: bool = False
) -> ProjectConfig:
    overrides = dict(config.overrides)
    if unset:
        overrides.pop(key, None)
    else:
        overrides[key] = value
    original = config.path.read_text(encoding="utf-8")
    updated = _replace_params_table(original, overrides)
    _atomic_write_text(config.path, updated)
    loaded = load_project_config(str(config.path.parent))
    if loaded is None:
        raise ProjectConfigError(f"Unable to update project config: {config.path}")
    return loaded


def apply_project_overrides(
    directory: str,
    *,
    config_directory: str | None = None,
    extra_overrides: dict[str, object] | None = None,
) -> tuple[ProjectConfig | None, list[str]]:
    config, parameters, changed = _project_override_state(
        directory,
        config_directory=config_directory,
        extra_overrides=extra_overrides,
    )
    if config is None or not changed:
        return config, changed
    parameters_path = (
        Path(directory).expanduser().resolve() / "home" / "parameters.json"
    )
    for key in changed:
        schema = lookup_parameter(key)
        assert schema is not None
        overrides = extra_overrides or {}
        parameters[schema.parameter_key] = overrides.get(
            key,
            config.overrides.get(key, config.defaults.get(key, schema.default)),
        )
    _atomic_write_json(parameters_path, parameters)
    return config, changed


def pending_project_overrides(
    directory: str,
    *,
    config_directory: str | None = None,
    extra_overrides: dict[str, object] | None = None,
) -> tuple[ProjectConfig | None, list[str]]:
    config, _, changed = _project_override_state(
        directory,
        config_directory=config_directory,
        extra_overrides=extra_overrides,
    )
    return config, changed


def _project_override_state(
    directory: str,
    *,
    config_directory: str | None = None,
    extra_overrides: dict[str, object] | None = None,
) -> tuple[ProjectConfig | None, dict[str, Any], list[str]]:
    config = load_project_config(config_directory or directory)
    parameters_path = (
        Path(directory).expanduser().resolve() / "home" / "parameters.json"
    )
    parameters = read_json_object(parameters_path)
    if config is None:
        return config, parameters, []
    changed: list[str] = []
    for schema in PARAMETERS:
        key = schema.param
        runtime_overrides = extra_overrides or {}
        if (
            key not in runtime_overrides
            and key not in config.overrides
            and schema.parameter_key not in parameters
        ):
            continue
        value = runtime_overrides.get(
            key, config.overrides.get(key, config.defaults.get(key, schema.default))
        )
        current = parameters.get(schema.parameter_key)
        try:
            normalized_current = normalize_stored_value(current, schema)
        except (TypeError, ValueError):
            normalized_current = current
        if normalized_current != value:
            changed.append(key)
    return config, parameters, changed


def restore_parameter_default(directory: str, config: ProjectConfig, key: str) -> bool:
    schema = lookup_parameter(key)
    if schema is None:
        raise ProjectConfigError(f"Unknown parameter: {key}")
    parameters_path = (
        Path(directory).expanduser().resolve() / "home" / "parameters.json"
    )
    parameters = read_json_object(parameters_path)
    value = config.defaults.get(key, schema.default)
    current = parameters.get(schema.parameter_key)
    try:
        normalized_current = normalize_stored_value(current, schema)
    except (TypeError, ValueError):
        normalized_current = current
    if normalized_current == value:
        return False
    parameters[schema.parameter_key] = value
    _atomic_write_json(parameters_path, parameters)
    return True


def _table(raw: dict[str, Any], key: str, path: Path) -> dict[str, object]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ProjectConfigError(f"[{key}] in {path} must be a table")
    return dict(value)


def _parameter_table(raw: dict[str, Any], key: str, path: Path) -> dict[str, object]:
    value = _table(raw, key, path)
    if any(isinstance(item, dict) for item in value.values()):
        raise ProjectConfigError(
            f"[{key}] in {path} must use quoted flat parameter names"
        )
    return value


def _render_config(
    design: dict[str, object],
    frontend: dict[str, object],
    flow: dict[str, object],
    defaults: dict[str, object],
    overrides: dict[str, object],
) -> str:
    lines = [f"schema_version = {CONFIG_SCHEMA_VERSION}", ""]
    lines.extend(_render_table("design", design))
    lines.extend(_render_table("frontend", frontend))
    if flow:
        lines.extend(_render_table("flow", flow))
    lines.extend(_render_table("defaults", defaults, quote_keys=True))
    lines.extend(_render_table("params", overrides, quote_keys=True))
    return "\n".join(lines).rstrip() + "\n"


def _render_table(
    name: str, values: dict[str, object], *, quote_keys: bool = False
) -> list[str]:
    lines = [f"[{name}]"]
    for key, value in values.items():
        rendered_key = json.dumps(key, ensure_ascii=False) if quote_keys else key
        lines.append(f"{rendered_key} = {_toml_value(value)}")
    lines.append("")
    return lines


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProjectConfigError("TOML values must be finite")
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ProjectConfigError(f"Unsupported TOML value: {type(value).__name__}")


_TABLE_RE = re.compile(r"(?m)^[ \t]*\[([^\]]+)\][ \t]*(?:#.*)?$")


def _replace_params_table(text: str, overrides: dict[str, object]) -> str:
    body = _render_table("params", dict(sorted(overrides.items())), quote_keys=True)
    replacement = "\n".join(body).rstrip() + "\n"
    match = next(
        (
            item
            for item in _TABLE_RE.finditer(text)
            if item.group(1).strip() == "params"
        ),
        None,
    )
    if match is None:
        return text.rstrip() + "\n\n" + replacement
    next_table = _TABLE_RE.search(text, match.end())
    end = next_table.start() if next_table else len(text)
    suffix = text[end:]
    if suffix and not replacement.endswith("\n\n"):
        replacement += "\n"
    return text[: match.start()] + replacement + suffix


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
    )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
