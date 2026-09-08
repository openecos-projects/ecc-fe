from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ParameterSchema:
    param: str
    parameter_key: str
    value_type: str
    default: object
    applies: str
    description: str
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    advanced: bool = False

    @property
    def group(self) -> str:
        return self.param.partition(".")[0]


PARAMETERS = (
    ParameterSchema(
        "design.frequency_mhz",
        "Frequency max [MHz]",
        "float",
        100.0,
        "review,sim",
        "Target clock frequency in MHz.",
        minimum=0.000001,
        maximum=10000.0,
    ),
    ParameterSchema(
        "sim.compile_preset",
        "sim_compile_preset",
        "string",
        "balanced",
        "sim",
        "Simulation program compiler preset.",
        choices=("debug", "balanced", "speed", "size", "custom"),
    ),
    ParameterSchema(
        "sim.compile_opt_level",
        "sim_compile_opt_level",
        "string",
        "-O2",
        "sim",
        "Compiler optimization level for simulation programs.",
        choices=("-O0", "-O1", "-O2", "-O3", "-Os", "-Og"),
        advanced=True,
    ),
    ParameterSchema(
        "sim.compile_march",
        "sim_compile_march",
        "string",
        "rv32im_zicsr",
        "sim",
        "RISC-V ISA string used to compile simulation programs.",
        advanced=True,
    ),
    ParameterSchema(
        "sim.compile_mabi",
        "sim_compile_mabi",
        "string",
        "ilp32",
        "sim",
        "RISC-V ABI used to compile simulation programs.",
        choices=("ilp32", "ilp32e", "ilp32f", "ilp32d"),
        advanced=True,
    ),
    ParameterSchema(
        "sim.compile_extra_cflags",
        "sim_compile_extra_cflags",
        "string_list",
        [],
        "sim",
        "Additional compiler flags as a JSON string array.",
        advanced=True,
    ),
    ParameterSchema(
        "sim.coremark_iterations",
        "sim_coremark_iterations",
        "integer",
        1,
        "sim",
        "CoreMark iteration count.",
        minimum=1,
        maximum=1_000_000_000,
        advanced=True,
    ),
    ParameterSchema(
        "sim.coremark_total_data_size",
        "sim_coremark_total_data_size",
        "integer",
        2000,
        "sim",
        "CoreMark total data size in bytes.",
        minimum=1,
        maximum=1_000_000_000,
        advanced=True,
    ),
    ParameterSchema(
        "sim.coremark_max_cycles",
        "sim_coremark_max_cycles",
        "integer",
        200_000_000,
        "sim",
        "Maximum simulation cycles for CoreMark.",
        minimum=1,
        maximum=1_000_000_000_000,
        advanced=True,
    ),
    ParameterSchema(
        "sim.coremark_has_float",
        "sim_coremark_has_float",
        "boolean",
        True,
        "sim",
        "Whether the CoreMark port has floating-point support.",
        advanced=True,
    ),
    ParameterSchema(
        "sim.coremark_use_difftest",
        "sim_coremark_use_difftest",
        "boolean",
        False,
        "sim",
        "Enable reference-model comparison for CoreMark.",
        advanced=True,
    ),
)

_BY_NAME = {schema.param: schema for schema in PARAMETERS}


def lookup_parameter(name: str) -> ParameterSchema | None:
    return _BY_NAME.get(name.strip())


def parse_cli_value(raw: str, schema: ParameterSchema) -> object:
    value: object
    if schema.value_type == "string":
        value = raw.strip()
        if not value:
            raise ValueError("value must not be empty")
    elif schema.value_type == "integer":
        try:
            value = int(raw, 10)
        except ValueError as error:
            raise ValueError("expected an integer") from error
    elif schema.value_type == "float":
        try:
            value = float(raw)
        except ValueError as error:
            raise ValueError("expected a floating-point number") from error
    elif schema.value_type == "boolean":
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            value = True
        elif normalized in {"0", "false", "no", "off"}:
            value = False
        else:
            raise ValueError("expected true or false")
    elif schema.value_type == "string_list":
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("expected a JSON string array") from error
    else:
        raise ValueError(f"unsupported parameter type: {schema.value_type}")
    validate_value(value, schema)
    return value


def validate_value(value: object, schema: ParameterSchema) -> None:
    if schema.value_type == "string":
        valid_type = isinstance(value, str) and bool(value.strip())
    elif schema.value_type == "integer":
        valid_type = isinstance(value, int) and not isinstance(value, bool)
    elif schema.value_type == "float":
        valid_type = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    elif schema.value_type == "boolean":
        valid_type = isinstance(value, bool)
    elif schema.value_type == "string_list":
        valid_type = isinstance(value, list) and all(
            isinstance(item, str) for item in value
        )
    else:
        valid_type = False
    if not valid_type:
        raise ValueError(f"expected {schema.value_type}")
    if schema.choices and value not in schema.choices:
        raise ValueError(f"expected one of: {', '.join(schema.choices)}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if schema.minimum is not None and value < schema.minimum:
            raise ValueError(f"value must be at least {schema.minimum:g}")
        if schema.maximum is not None and value > schema.maximum:
            raise ValueError(f"value must be at most {schema.maximum:g}")


def effective_default(parameters: dict[str, Any], schema: ParameterSchema) -> object:
    raw = parameters.get(schema.parameter_key)
    if raw is None or raw == "":
        return schema.default
    try:
        return normalize_stored_value(raw, schema)
    except (TypeError, ValueError):
        return schema.default


def normalize_stored_value(value: object, schema: ParameterSchema) -> object:
    if schema.value_type == "integer" and isinstance(value, str):
        value = int(value, 10)
    elif schema.value_type == "float" and isinstance(value, str):
        value = float(value)
    elif schema.value_type == "boolean" and isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            value = True
        elif normalized in {"0", "false", "no", "off"}:
            value = False
    validate_value(value, schema)
    return float(value) if schema.value_type == "float" else value


def parameter_records(
    parameters: dict[str, Any],
    *,
    defaults: dict[str, object] | None = None,
    overrides: dict[str, object] | None = None,
    step: str | None = None,
) -> list[dict[str, object]]:
    configured_defaults = defaults or {}
    configured_overrides = overrides or {}
    selected_step = (step or "").strip().lower()
    records: list[dict[str, object]] = []
    for schema in PARAMETERS:
        if selected_step and selected_step not in {
            schema.group,
            *(item.strip() for item in schema.applies.split(",")),
        }:
            continue
        default = configured_defaults.get(
            schema.param, effective_default(parameters, schema)
        )
        if schema.param in configured_overrides:
            value = configured_overrides[schema.param]
            source = "ecc-fe.toml"
        elif schema.parameter_key in parameters:
            try:
                value = normalize_stored_value(parameters[schema.parameter_key], schema)
            except (TypeError, ValueError):
                value = parameters[schema.parameter_key]
            source = "workspace" if value != default else "default"
        else:
            value = default
            source = "default"
        records.append(
            {
                "kind": "param",
                "param": schema.param,
                "group": schema.group,
                "value": value,
                "default": default,
                "source": source,
                "type": schema.value_type,
                "applies": schema.applies,
                "maps_to": f"home/parameters.json:{schema.parameter_key}",
                "description": schema.description,
                "choices": list(schema.choices),
                "minimum": schema.minimum,
                "maximum": schema.maximum,
                "advanced": schema.advanced,
            }
        )
    return records
