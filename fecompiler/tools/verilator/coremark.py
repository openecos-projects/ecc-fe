"""CoreMark as a simulation case: its build settings and how its result is read."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fecompiler.tools.verilator.lint import (  # noqa: F401
    VerilatorLintStep,
    build_lint_summary,
    parse_verilator_diagnostics,
)
from fecompiler.tools.verilator.sim_settings import (
    _bool_workspace_value,
    _normalize_string_list,
    _positive_int,
    _sim_cycles_from_output,
    _workspace_frequency_mhz,
)

_COREMARK_EXPECTED_CRC = "0x3df51153"


_COREMARK_ITERATIONS = 1


_COREMARK_DEFAULT_TOTAL_DATA_SIZE = 2000


_COREMARK_DEFAULT_HAS_FLOAT = True


_COREMARK_COMPILE_PRESETS = {
    "debug": "-O0",
    "balanced": "-O2",
    "speed": "-O3",
    "size": "-Os",
    "custom": "-O2",
}


_COREMARK_ALLOWED_OPT_LEVELS = {"-O0", "-O1", "-O2", "-O3", "-Os", "-Og"}


_COREMARK_ITERATIONS_RE = re.compile(r"^\s*Iterations\s*:\s*(?P<value>[0-9]+)\s*$", re.MULTILINE)


_COREMARK_ITERATIONS_PER_SEC_RE = re.compile(r"^\s*Iterations/Sec\s*:\s*(?P<value>[0-9]+(?:\.[0-9]+)?)\s*$", re.MULTILINE)


_COREMARK_PER_MHZ_RE = re.compile(r"^\s*CoreMark/MHz\s*:\s*(?P<value>[0-9]+(?:\.[0-9]+)?)\s*$", re.MULTILINE)


def _coremark_requested(workspace: dict[str, Any]) -> bool:
    if str(workspace.get("test_suite_id", "")).strip() == "coremark":
        return True
    for name in workspace.get("sim_program_names", []) or []:
        text = str(name).strip()
        if text == "coremark" or Path(text).stem == "coremark":
            return True
    for source in workspace.get("sim_program_sources", []) or []:
        if Path(str(source).strip()).stem == "coremark":
            return True
    return False


def _is_coremark_case(case_name: str, image: str) -> bool:
    if case_name == "coremark.soc":
        return True
    return Path(str(image)).stem == "coremark.soc"


def _coremark_metrics(workspace: dict[str, Any], output: str, ok: bool) -> dict[str, Any]:
    cycles = _sim_cycles_from_output(output)
    frequency_mhz = _workspace_frequency_mhz(workspace)
    official_iterations = _coremark_int_from_output(_COREMARK_ITERATIONS_RE, output)
    official_iterations_per_sec = _coremark_float_from_output(_COREMARK_ITERATIONS_PER_SEC_RE, output)
    official_coremark_per_mhz = _coremark_float_from_output(_COREMARK_PER_MHZ_RE, output)
    compile_settings = _coremark_compile_settings(workspace)
    metrics: dict[str, Any] = {
        "benchmark": "CoreMark",
        "iterations": official_iterations or int(compile_settings["iterations"]),
        "expected_crc": "reported by CoreMark validation",
        "cycles": cycles,
        "frequency_mhz": frequency_mhz,
        "compile": compile_settings,
    }
    if not ok:
        metrics["score_available"] = False
        metrics["score_unavailable_reason"] = "simulation did not pass"
        return metrics
    if official_coremark_per_mhz is not None:
        metrics.update({
            "score_available": True,
            "coremark_per_mhz": official_coremark_per_mhz,
        })
        if official_iterations_per_sec is not None:
            metrics["coremark_per_second"] = official_iterations_per_sec
        elif frequency_mhz is not None:
            metrics["coremark_per_second"] = official_coremark_per_mhz * frequency_mhz
        if cycles is not None and metrics["iterations"]:
            metrics["cycles_per_iteration"] = cycles / int(metrics["iterations"])
        return metrics
    if cycles is None or cycles <= 0:
        metrics["score_available"] = False
        metrics["score_unavailable_reason"] = "simulation cycle count not found"
        return metrics

    iterations = int(metrics["iterations"] or _COREMARK_ITERATIONS)
    cycles_per_iteration = cycles / max(iterations, 1)
    coremark_per_mhz = 1_000_000 / cycles_per_iteration
    metrics.update({
        "score_available": True,
        "cycles_per_iteration": cycles_per_iteration,
        "coremark_per_mhz": coremark_per_mhz,
    })
    if frequency_mhz is not None:
        metrics["estimated_coremark_per_second"] = coremark_per_mhz * frequency_mhz
    return metrics


def _program_compile_settings(workspace: dict[str, Any]) -> dict[str, Any]:
    preset = str(workspace.get("sim_compile_preset", "balanced") or "balanced").strip().lower()
    if preset not in _COREMARK_COMPILE_PRESETS:
        preset = "balanced"

    opt_level = str(workspace.get("sim_compile_opt_level", "") or "").strip()
    if not opt_level:
        opt_level = _COREMARK_COMPILE_PRESETS[preset]
    if opt_level not in _COREMARK_ALLOWED_OPT_LEVELS:
        opt_level = _COREMARK_COMPILE_PRESETS[preset]

    extra_cflags = _normalize_string_list(workspace.get("sim_compile_extra_cflags", []))
    return {
        "preset": preset,
        "opt_level": opt_level,
        "march": str(workspace.get("sim_compile_march", "rv32im_zicsr") or "rv32im_zicsr").strip(),
        "mabi": str(workspace.get("sim_compile_mabi", "ilp32") or "ilp32").strip(),
        "extra_cflags": extra_cflags,
    }


def _coremark_compile_settings(workspace: dict[str, Any]) -> dict[str, Any]:
    return {
        **_program_compile_settings(workspace),
        "iterations": _positive_int(workspace.get("sim_coremark_iterations"), _COREMARK_ITERATIONS),
        "total_data_size": _positive_int(
            workspace.get("sim_coremark_total_data_size"),
            _COREMARK_DEFAULT_TOTAL_DATA_SIZE,
        ),
        "has_float": _bool_workspace_value(workspace.get("sim_coremark_has_float"), _COREMARK_DEFAULT_HAS_FLOAT),
    }


def _coremark_int_from_output(pattern: re.Pattern[str], output: str) -> int | None:
    match = pattern.search(output)
    if not match:
        return None
    try:
        return int(match.group("value"))
    except ValueError:
        return None


def _coremark_float_from_output(pattern: re.Pattern[str], output: str) -> float | None:
    match = pattern.search(output)
    if not match:
        return None
    try:
        return float(match.group("value"))
    except ValueError:
        return None


def _format_coremark_score_lines(metrics: dict[str, Any]) -> list[str]:
    lines = [
        f"Iterations  : {int(metrics.get('iterations') or _COREMARK_ITERATIONS)}",
        f"Cycles      : {_format_metric_value(metrics.get('cycles'))}",
    ]
    frequency_mhz = metrics.get("frequency_mhz")
    if frequency_mhz is not None:
        lines.append(f"Clock       : {_format_float(float(frequency_mhz), 3)} MHz")
    if not metrics.get("score_available"):
        reason = str(metrics.get("score_unavailable_reason") or "unknown")
        lines.append(f"Score       : unavailable ({reason})")
        return lines

    lines.extend([
        f"Cycles/iter : {_format_float(float(metrics.get('cycles_per_iteration') or 0), 3)}",
        f"CoreMark/MHz: {_format_float(float(metrics.get('coremark_per_mhz') or 0), 9)}",
    ])
    estimated = metrics.get("estimated_coremark_per_second")
    official = metrics.get("coremark_per_second")
    if official is not None:
        lines.append(f"CoreMark/s  : {_format_float(float(official), 3)}")
    elif estimated is not None:
        lines.append(f"CoreMark/s  : {_format_float(float(estimated), 3)}")
    return lines


def _format_metric_value(value: Any) -> str:
    if value is None:
        return "unavailable"
    return str(value)


def _format_float(value: float, precision: int) -> str:
    text = f"{value:.{precision}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
