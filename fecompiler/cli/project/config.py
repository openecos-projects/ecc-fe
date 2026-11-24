from __future__ import annotations

import json
import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fecompiler.allflow.profile import parameter_groups_outside
from fecompiler.cli.project.params import (
    PARAMETERS,
    effective_default,
    lookup_parameter,
    normalize_stored_value,
)
from fecompiler.cli.project.layout import RUN_ID_RULE, is_valid_run_id
from fecompiler.cli.workspace_access import read_json_object
from fecompiler.resources import frontend_repo_root, managed_tools_dir, resource_roots
from fecompiler.utility.atomic_write import atomic_write_json, atomic_write_text

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


_CONFIG_HEADER = (
    "# ECC-FE project configuration.",
    "#",
    "# [params] holds the settings new runs are created with; edit it here or",
    "# with `ecc-fe param set`. [flow].run is the run used without --run-id.",
    "# [design], [frontend] and [defaults] record how the project was created.",
    "# They are not read back: the inputs themselves are in .ecc-fe/template.",
    "",
)


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
        if not isinstance(value, str) or not is_valid_run_id(value):
            raise ProjectConfigError(f"Unsupported flow.run {value!r}: {RUN_ID_RULE}")
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
    # Parameters that do not apply to this design are left out.
    try:
        absent = parameter_groups_outside(parameters)
    except ValueError:
        absent = frozenset()
    defaults = {
        schema.param: effective_default(parameters, schema)
        for schema in PARAMETERS
        if schema.group not in absent
    }
    design = {
        "kind": "rtl"
        if str(parameters.get("frontend_design_kind", "")).strip() == "generic_rtl"
        else "cpu",
        "name": str(parameters.get("Design", Path(directory).name)),
        "top": str(parameters.get("Top module", "top")),
        "clock_port": str(parameters.get("Clock", "clk")),
        "frequency_mhz": defaults.get("design.frequency_mhz"),
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
        # A filelist that ships with ECC-FE or an installed resource follows
        # from the ids above; its location on this machine is not recorded.
        and not (key.endswith("_filelist") and _is_managed_path(parameters[key]))
    }
    flow = {"run": "default"} if project_layout else {}
    text = _render_config(design, frontend, flow, defaults, {})
    atomic_write_text(path, text)
    loaded = load_project_config(directory)
    if loaded is None:
        raise ProjectConfigError(f"Unable to create project config: {path}")
    return loaded


def _is_managed_path(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        path = Path(value).expanduser().resolve()
        roots = [frontend_repo_root(), managed_tools_dir(), *resource_roots()]
        return any(path.is_relative_to(Path(root).resolve()) for root in roots)
    except (OSError, ValueError):
        return False


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
    updated = _updated_config_text(original, overrides, config.path)
    atomic_write_text(config.path, updated)
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
    atomic_write_json(parameters_path, parameters)
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
    return set_workspace_parameter(
        directory, key, config.defaults.get(key, schema.default)
    )


def set_workspace_parameter(directory: str, key: str, value: object) -> bool:
    """Write one parameter into a workspace's home/parameters.json.

    Returns whether the stored value changed. In a direct workspace this file
    is the only source of parameter values.
    """
    schema = lookup_parameter(key)
    if schema is None:
        raise ProjectConfigError(f"Unknown parameter: {key}")
    parameters_path = (
        Path(directory).expanduser().resolve() / "home" / "parameters.json"
    )
    parameters = read_json_object(parameters_path)
    current = parameters.get(schema.parameter_key)
    try:
        normalized_current = normalize_stored_value(current, schema)
    except (TypeError, ValueError):
        normalized_current = current
    if normalized_current == value:
        return False
    parameters[schema.parameter_key] = value
    atomic_write_json(parameters_path, parameters)
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
    lines = [*_CONFIG_HEADER, f"schema_version = {CONFIG_SCHEMA_VERSION}", ""]
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
_HEADER_LINE = re.compile(r"[ \t]*\[\[?[^\]]+\]\]?[ \t]*(?:#.*)?")
_KEY_LINE = re.compile(
    r"""[ \t]*("(?:[^"\\]|\\.)*"|'[^']*'|[A-Za-z0-9_-]+)[ \t]*="""
)


def _updated_config_text(text: str, overrides: dict[str, object], path: Path) -> str:
    """Return *text* with ``[params]`` set to *overrides* and nothing else changed.

    The edit keeps comments and untouched entries. Whatever it produces is
    parsed before it is written: if the result is not the same document with
    only ``[params]`` changed, a plain rewrite of the table is tried, and if
    that fails too the file is left alone.
    """
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ProjectConfigError(f"Unable to read {path}: {error}") from error
    expected = {**before, "params": overrides}
    if not overrides and "params" not in before:
        return text
    # A line that looks like the [params] header can also sit inside a
    # multi-line string, so every such line is tried until one checks out.
    headers = [
        index
        for index, line in enumerate(text.splitlines())
        if _is_params_header(line)
    ]
    candidates = [
        *(_edit_params_table(text, overrides, start) for start in headers),
        _replace_params_table(text, overrides),
    ]
    for candidate in candidates:
        try:
            after = tomllib.loads(candidate)
        except tomllib.TOMLDecodeError:
            continue
        after.setdefault("params", {})
        if _same_document(after, {**expected, "params": dict(overrides)}):
            return candidate
    raise ProjectConfigError(
        f"Unable to update [params] in {path} without changing the rest of the "
        "file; edit the table by hand"
    )


def _same_document(left: dict[str, Any], right: dict[str, Any]) -> bool:
    def canonical(value: object) -> object:
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in sorted(value.items())}
        if isinstance(value, (list, tuple)):
            return [canonical(item) for item in value]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    return canonical(left) == canonical(right)


def _is_params_header(line: str) -> bool:
    return (
        _HEADER_LINE.fullmatch(line) is not None
        and not line.lstrip().startswith("[[")
        and line.split("#", 1)[0].strip().strip("[]").strip() == "params"
    )


def _edit_params_table(text: str, overrides: dict[str, object], start: int) -> str:
    """Edit the ``[params]`` table whose header is line *start*, entry by entry.

    Comments and entries whose value does not change are kept as written.
    """
    lines = text.splitlines()
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if _HEADER_LINE.fullmatch(lines[index])
        ),
        len(lines),
    )
    body = lines[start + 1 : end]
    kept: list[str] = []
    seen: set[str] = set()
    last_entry_end: int | None = None
    index = 0
    while index < len(body):
        entry = _entry_at(body, index)
        if entry is None:
            kept.append(body[index])
            index += 1
            continue
        key, value, length = entry
        if key in overrides:
            seen.add(key)
            if _same_document({"v": value}, {"v": overrides[key]}):
                kept.extend(body[index : index + length])
            else:
                kept.append(f"{json.dumps(key, ensure_ascii=False)} = {_toml_value(overrides[key])}")
            last_entry_end = len(kept)
        index += length
    added = [
        f"{json.dumps(key, ensure_ascii=False)} = {_toml_value(value)}"
        for key, value in sorted(overrides.items())
        if key not in seen
    ]
    # New entries follow the last entry; in an empty table they follow
    # whatever comes first, before the blank lines that end the table.
    tail = last_entry_end
    if tail is None:
        tail = len(kept)
        while tail and not kept[tail - 1].strip():
            tail -= 1
    kept[tail:tail] = added
    result = "\n".join([*lines[: start + 1], *kept, *lines[end:]])
    return result + "\n" if text.endswith("\n") or not result.endswith("\n") else result


def _entry_at(body: list[str], index: int) -> tuple[str, object, int] | None:
    """Parse the ``key = value`` entry starting at *index*, which may span lines."""
    if _KEY_LINE.match(body[index]) is None:
        return None
    for length in range(1, len(body) - index + 1):
        try:
            parsed = tomllib.loads("\n".join(body[index : index + length]))
        except tomllib.TOMLDecodeError:
            continue
        if len(parsed) == 1:
            key, value = next(iter(parsed.items()))
            if not isinstance(value, dict):
                return key, value, length
        return None
    return None


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


def load_run_parameter_overrides(run_dir: str) -> dict[str, object]:
    """Return the ``run --set`` overrides recorded when a project run was created."""
    path = Path(run_dir) / "home" / "cli-param-overrides.json"
    if not path.is_file():
        return {}
    raw = read_json_object(path)
    overrides: dict[str, object] = {}
    for key, value in raw.items():
        schema = lookup_parameter(key)
        if schema is None:
            raise ProjectConfigError(
                f"Invalid CLI parameter provenance: unknown parameter {key}"
            )
        try:
            overrides[schema.param] = normalize_stored_value(value, schema)
        except (TypeError, ValueError) as error:
            raise ProjectConfigError(
                f"Invalid CLI parameter provenance for {schema.param}: {error}"
            ) from error
    return overrides

