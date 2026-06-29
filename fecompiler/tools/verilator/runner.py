"""Verilator step implementations — VerilatorLintStep and VerilatorSimStep."""

from __future__ import annotations

import os
import re
import shutil
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fecompiler.tools.fe.base import BaseStep
from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.common.rtl_inputs import (
    incdirs,
    rtl_files,
    verilator_lint_defines,
    verilator_define_args,
    verilator_incdir_args,
    verilator_lint_define_args,
)
from fecompiler.tools.fe.subflow import update_substep_ok

from fecompiler.tools.verilator.subflow import (
    LintSubFlowEnum,
    SimSubFlowEnum,
    init_lint_subflow,
    init_sim_subflow,
)
from fecompiler.utility.json import json_read, json_write


# ── shared helper ─────────────────────────────────────────────────────────────

_WORKSPACE_REL_SOC_ROOT = Path("fecompiler/thirdparty/SoC")
_WORKSPACE_REL_RTTHREAD_PREPARE = Path("fecompiler/thirdparty/rtthread_prepare.py")
_BENCHMARK_PROGRAM_NAMES = {"coremark"}
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
_CPU_PROGRAM_ENTRY_OFFSETS = {
    "ibex": "0x80",
}
_VERILATOR_DIAGNOSTIC_RE = re.compile(
    r"^%(?P<severity>Error|Warning)(?:-(?P<code>[A-Za-z0-9_]+))?:\s+"
    r"(?P<source>.+?):(?P<line>\d+):(?:(?P<column>\d+):)?\s*(?P<message>.*)$"
)
_SIM_CYCLE_RE = re.compile(r"\b(?:after|timeout after)\s+([0-9]+)\s+cycles\b")
_COREMARK_ITERATIONS_RE = re.compile(r"^\s*Iterations\s*:\s*(?P<value>[0-9]+)\s*$", re.MULTILINE)
_COREMARK_ITERATIONS_PER_SEC_RE = re.compile(r"^\s*Iterations/Sec\s*:\s*(?P<value>[0-9]+(?:\.[0-9]+)?)\s*$", re.MULTILINE)
_COREMARK_PER_MHZ_RE = re.compile(r"^\s*CoreMark/MHz\s*:\s*(?P<value>[0-9]+(?:\.[0-9]+)?)\s*$", re.MULTILINE)
_RTTHREAD_REQUIRED_LOG_MARKERS = (
    "Thread Operating System",
    "Hello RISC-V!",
    "msh />help",
    "RT-Thread shell commands:",
    "[soc-sim] timeout after",
)


def _verilator_cmd() -> str:
    """Return the Resource Manager/PATH-provided verilator executable."""
    for env_name in ("ECOS_VERILATOR", "VERILATOR"):
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return shutil.which("verilator") or "verilator"


def _sim_cpp_sources(workspace: dict[str, Any]) -> list[str]:
    """Return C++ simulation sources (testbench first, then extras)."""
    seen: set[str] = set()
    ordered: list[str] = []

    tb = str(workspace.get("testbench", "")).strip()
    if tb:
        seen.add(tb)
        ordered.append(tb)

    for src in workspace.get("sim_cpp_sources", []) or []:
        s = str(src).strip()
        if s and s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def _sim_cflags_args(workspace: dict[str, Any]) -> list[str]:
    user_flags = [str(f).strip() for f in workspace.get("sim_cflags", []) or [] if str(f).strip()]
    user_flags = [_normalize_sim_cflag(flag) for flag in user_flags]
    user_flags = _ensure_soc_include_flag(workspace, user_flags)
    has_std = any(flag.startswith("-std=") for flag in user_flags)
    flags = ([] if has_std else ["-std=c++20"]) + user_flags
    if not flags:
        return []
    return ["-CFLAGS", " ".join(flags)]


def _ensure_soc_include_flag(workspace: dict[str, Any], flags: list[str]) -> list[str]:
    soc_root = _workspace_soc_root(workspace)
    if soc_root is None:
        return flags

    include = f"-I{soc_root}"
    if any(flag == include or flag == f"-I{soc_root}/" for flag in flags):
        return flags
    return [*flags, include]


def _sim_ldflags_args(workspace: dict[str, Any]) -> list[str]:
    flags = [str(f).strip() for f in workspace.get("sim_ldflags", []) or [] if str(f).strip()]
    if not flags:
        return []
    return ["-LDFLAGS", " ".join(flags)]


def _sim_run_args(workspace: dict[str, Any]) -> list[str]:
    args = [str(arg) for arg in workspace.get("sim_run_args", []) or []]
    if _rtthread_requested(workspace) and not _arg_present(args, "--diff"):
        args = _append_rtthread_difftest_args(workspace, args)
    return args


def _sim_difftest_enabled(workspace: dict[str, Any]) -> bool:
    return "--diff" in _sim_run_args(workspace)


def _run_sim_process(cmd: list[str], *, stream_output: bool) -> tuple[int, str]:
    if not stream_output:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return int(result.returncode), result.stdout + result.stderr

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    output_chunks: list[str] = []
    assert process.stdout is not None
    try:
        try:
            while True:
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                output_chunks.append(text)
                sys.stdout.write(text)
                sys.stdout.flush()
        finally:
            process.stdout.close()
    except BaseException:
        process.kill()
        process.wait()
        raise

    return int(process.wait()), "".join(output_chunks)


def _rtthread_requested(workspace: dict[str, Any]) -> bool:
    for name in workspace.get("sim_program_names", []) or []:
        text = str(name).strip()
        if text == "rtthread" or Path(text).stem == "rtthread":
            return True
    for source in workspace.get("sim_program_sources", []) or []:
        if Path(str(source).strip()).stem == "rtthread":
            return True
    return False


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


def _sim_suite_name(workspace: dict[str, Any]) -> str:
    if _rtthread_requested(workspace):
        return "rtthread"
    if _coremark_requested(workspace):
        return "coremark"
    return "cpu_tests"


def _suite_label(suite: str) -> str:
    if suite == "rtthread":
        return "RT-Thread"
    if suite == "coremark":
        return "CoreMark"
    return "CPU Tests"


def _arg_present(args: list[str], option: str) -> bool:
    return option in args or any(arg.startswith(f"{option}=") for arg in args)


def _append_rtthread_difftest_args(workspace: dict[str, Any], args: list[str]) -> list[str]:
    out = list(args)
    if not _arg_present(out, "--max-cycles"):
        out.extend(["--max-cycles", "10000000"])
    out.extend([
        "--diff",
        "--ref",
        str(_rtthread_ref_so(workspace)),
        "--diff-image-offset",
        "0x100",
        "--diff-reset-vector",
        "0x80000000",
        "--timeout-ok",
    ])
    return out


def _rtthread_ref_so(workspace: dict[str, Any]) -> Path:
    soc_root = _workspace_soc_root(workspace)
    if soc_root is None:
        soc_root = _invocation_root() / _WORKSPACE_REL_SOC_ROOT
    return soc_root / "tools" / "riscv32-spike-so"


def _resolve_path(path_text: str, *, base: Path | None = None) -> Path:
    p = Path(path_text).expanduser()
    if p.is_absolute():
        return p.resolve()
    if base is not None:
        return (base / p).resolve()
    return (_invocation_root() / p).resolve()


def _workspace_soc_root(workspace: dict[str, Any]) -> Path | None:
    explicit_root = str(workspace.get("sim_soc_root", "")).strip()
    if explicit_root:
        p = _resolve_path(explicit_root)
        if p.exists():
            return p

    soc_filelist = str(workspace.get("soc_filelist", "")).strip()
    if soc_filelist:
        p = _resolve_path(soc_filelist)
        if p.exists():
            return p.parent

    root = _invocation_root()
    candidate = root / _WORKSPACE_REL_SOC_ROOT
    if candidate.exists():
        return candidate.resolve()

    return None


def _soc_tests_out_dir(workspace: dict[str, Any]) -> Path:
    explicit = str(workspace.get("sim_tests_out_dir", "")).strip()
    if explicit:
        return _resolve_path(explicit)

    soc_root = _workspace_soc_root(workspace)
    if soc_root is not None:
        return soc_root / "tests" / "out"
    return _invocation_root() / "tests" / "out"


def _explicit_soc_tests_out_dir(workspace: dict[str, Any]) -> Path | None:
    explicit = str(workspace.get("sim_tests_out_dir", "")).strip()
    return _resolve_path(explicit) if explicit else None


def _sim_images(workspace: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    images: list[str] = []

    for image in workspace.get("sim_images", []) or []:
        text = str(image).strip()
        if not text:
            continue
        canonical = str(_resolve_path(text))
        if canonical not in seen:
            seen.add(canonical)
            images.append(canonical)

    if workspace.get("sim_all_tests"):
        tests_dir_raw = str(workspace.get("sim_tests_dir", "")).strip()
        tests_dir = _resolve_path(tests_dir_raw) if tests_dir_raw else _soc_tests_out_dir(workspace)
        for image in sorted(tests_dir.glob("*.soc.bin")):
            canonical = str(image.resolve())
            if canonical not in seen:
                seen.add(canonical)
                images.append(canonical)
    return images


def _strip_image_args(args: list[str]) -> list[str]:
    return _strip_option_with_value(args, "--image")


def _strip_option_with_value(args: list[str], option: str) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == option:
            i += 2
            continue
        if arg.startswith(f"{option}="):
            i += 1
            continue
        out.append(arg)
        i += 1
    return out


def _safe_case_name(name: str) -> str:
    token = "".join(ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_" for ch in name)
    token = token.strip("._")
    return token or "case"


def _sim_cases_from_images(images: list[str], run_args: list[str]) -> list[dict[str, Any]]:
    if not images:
        return []

    base_args = _strip_image_args(run_args)
    seen: dict[str, int] = {}
    cases: list[dict[str, Any]] = []
    for image in images:
        base_name = _safe_case_name(Path(image).stem)
        idx = seen.get(base_name, 0)
        seen[base_name] = idx + 1
        case_name = base_name if idx == 0 else f"{base_name}_{idx + 1}"
        args = ["--image", image, *base_args]
        if case_name == "rtthread.soc" and not _arg_present(args, "--timeout-ok"):
            args.append("--timeout-ok")
        cases.append(
            {
                "name": case_name,
                "image": image,
                "args": args,
            }
        )
    return cases


def _effective_sim_cases(images: list[str], run_args: list[str]) -> list[dict[str, Any]]:
    """Build simulation cases, including a default single-run case when no image list exists."""
    cases = _sim_cases_from_images(images, run_args)
    if cases:
        return cases

    image = _image_from_run_args(run_args)
    case_name = _safe_case_name(Path(image).stem) if image else "default"
    return [{"name": case_name, "image": image, "args": run_args}]


def _is_rtthread_case(case_name: str, image: str) -> bool:
    if case_name == "rtthread.soc":
        return True
    return Path(str(image)).stem == "rtthread.soc"


def _is_coremark_case(case_name: str, image: str) -> bool:
    if case_name == "coremark.soc":
        return True
    return Path(str(image)).stem == "coremark.soc"


def _case_output_ok(case_name: str, image: str, returncode: int, output: str) -> tuple[bool, dict[str, Any]]:
    if _is_coremark_case(case_name, image):
        has_validated = "Correct operation validated" in output
        has_error = "Errors detected" in output or "ERROR!" in output
        validation = {
            "type": "coremark_validation",
            "validated": has_validated,
            "errors_detected": has_error,
        }
        ok = returncode == 0 and has_validated and not has_error and "%Error" not in output
        return ok, validation

    if not _is_rtthread_case(case_name, image):
        return _sim_output_ok(returncode, output), {}

    missing = [marker for marker in _RTTHREAD_REQUIRED_LOG_MARKERS if marker not in output]
    validation = {
        "type": "rtthread_terminal",
        "required_markers": list(_RTTHREAD_REQUIRED_LOG_MARKERS),
        "missing_markers": missing,
    }
    ok = returncode == 0 and not missing and "FAILED" not in output and "%Error" not in output
    return ok, validation


def _workspace_frequency_mhz(workspace: dict[str, Any]) -> float | None:
    params_path = str(workspace.get("parameters_path", "")).strip()
    candidates: list[Any] = []
    if params_path:
        try:
            params = json_read(params_path)
            candidates.extend([
                params.get("Frequency max [MHz]"),
                params.get("frequency_max"),
                params.get("freq"),
            ])
        except Exception:
            pass
    candidates.extend([
        workspace.get("frequency_mhz"),
        workspace.get("frequency_max"),
        workspace.get("freq"),
    ])
    for value in candidates:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > 0:
            return numeric
    return None


def _sim_cycles_from_output(output: str) -> int | None:
    matches = list(_SIM_CYCLE_RE.finditer(output))
    if not matches:
        return None
    try:
        return int(matches[-1].group(1))
    except ValueError:
        return None


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


def _coremark_compile_settings(workspace: dict[str, Any]) -> dict[str, Any]:
    preset = str(workspace.get("sim_compile_preset", "balanced") or "balanced").strip().lower()
    if preset not in _COREMARK_COMPILE_PRESETS:
        preset = "balanced"

    opt_level = str(workspace.get("sim_compile_opt_level", "") or "").strip()
    if not opt_level:
        opt_level = _COREMARK_COMPILE_PRESETS[preset]
    if opt_level not in _COREMARK_ALLOWED_OPT_LEVELS:
        opt_level = _COREMARK_COMPILE_PRESETS[preset]

    iterations = _positive_int(workspace.get("sim_coremark_iterations"), _COREMARK_ITERATIONS)
    total_data_size = _positive_int(
        workspace.get("sim_coremark_total_data_size"),
        _COREMARK_DEFAULT_TOTAL_DATA_SIZE,
    )
    extra_cflags = _normalize_string_list(workspace.get("sim_compile_extra_cflags", []))
    return {
        "preset": preset,
        "opt_level": opt_level,
        "march": str(workspace.get("sim_compile_march", "rv32im_zicsr") or "rv32im_zicsr").strip(),
        "mabi": str(workspace.get("sim_compile_mabi", "ilp32") or "ilp32").strip(),
        "extra_cflags": extra_cflags,
        "iterations": iterations,
        "total_data_size": total_data_size,
        "has_float": _bool_workspace_value(workspace.get("sim_coremark_has_float"), _COREMARK_DEFAULT_HAS_FLOAT),
    }


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _bool_workspace_value(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_string_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = shlex.split(value)
    else:
        raw = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _apply_coremark_build_env(workspace: dict[str, Any], env: dict[str, str], log_lines: list[str]) -> None:
    settings = _coremark_compile_settings(workspace)
    env["ECOS_SIM_OPT_LEVEL"] = str(settings["opt_level"])
    env["ECOS_SIM_MARCH"] = str(settings["march"])
    env["ECOS_SIM_MABI"] = str(settings["mabi"])
    env["ECOS_COREMARK_ITERATIONS"] = str(settings["iterations"])
    env["ECOS_COREMARK_TOTAL_DATA_SIZE"] = str(settings["total_data_size"])
    env["ECOS_COREMARK_HAS_FLOAT"] = "1" if settings["has_float"] else "0"

    extra_cflags = [str(item) for item in settings["extra_cflags"]]
    if extra_cflags:
        env["ECOS_SIM_EXTRA_CFLAGS_LINES"] = "\n".join(extra_cflags)
        env["ECOS_SIM_EXTRA_CFLAGS"] = " ".join(shlex.quote(flag) for flag in extra_cflags)
    flags_for_report = [
        str(settings["opt_level"]),
        f"-march={settings['march']}",
        f"-mabi={settings['mabi']}",
        *extra_cflags,
    ]
    env["ECOS_COREMARK_FLAGS_STR"] = ",".join(flags_for_report)
    log_lines.append(
        "[build_program] coremark compile "
        f"preset={settings['preset']} opt={settings['opt_level']} "
        f"march={settings['march']} mabi={settings['mabi']} "
        f"iterations={settings['iterations']} total_data_size={settings['total_data_size']} "
        f"has_float={int(bool(settings['has_float']))} "
        f"extra_cflags={' '.join(extra_cflags) if extra_cflags else '-'}"
    )


def _workspace_cpu_id(workspace: dict[str, Any]) -> str:
    return str(
        workspace.get("cpu_wrapper_id")
        or workspace.get("frontend_core_id")
        or workspace.get("core_id")
        or ""
    ).strip()


def _apply_cpu_program_build_env(workspace: dict[str, Any], env: dict[str, str], log_lines: list[str]) -> None:
    cpu_id = _workspace_cpu_id(workspace)
    entry_offset = _CPU_PROGRAM_ENTRY_OFFSETS.get(cpu_id, "")
    if not entry_offset:
        return
    if env.get("SOC_USE_BOOTLOADER") == "1":
        log_lines.append(f"[build_program] {cpu_id} entry offset skipped for bootloader image")
        return
    env["SOC_PROGRAM_ENTRY_OFFSET"] = entry_offset
    log_lines.append(f"[build_program] {cpu_id} program entry offset={entry_offset}")


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


def _case_terminal_output(
    *,
    suite: str,
    case_name: str,
    image: str,
    returncode: int,
    ok: bool,
    validation: dict[str, Any],
    metrics: dict[str, Any] | None,
    wave: str,
    output: str,
) -> str:
    status = "PASS" if ok else "FAIL"
    lines = [
        "ECOS Simulation Result",
        "======================",
        f"Suite       : {_suite_label(suite)}",
        f"Case        : {case_name}",
        f"Status      : {status}",
        f"Return code : {returncode}",
        f"Image       : {image or '-'}",
        f"Wave        : {wave or '-'}",
    ]
    if _is_coremark_case(case_name, image):
        compile_settings = metrics.get("compile", {}) if metrics else {}
        lines.extend([
            "",
            "CoreMark",
            "--------",
            "Benchmark   : EEMBC CoreMark",
            "Validation  : checked by CoreMark output",
            f"Compiler    : {compile_settings.get('preset', 'balanced')} {compile_settings.get('opt_level', '-O2')}",
            f"ISA/ABI     : {compile_settings.get('march', 'rv32im_zicsr')} / {compile_settings.get('mabi', 'ilp32')}",
        ])
        if metrics:
            lines.extend(_format_coremark_score_lines(metrics))
    if validation.get("type") == "rtthread_terminal":
        missing = validation.get("missing_markers", [])
        lines.extend([
            "",
            "RT-Thread terminal validation",
            "-----------------------------",
            f"Required markers: {len(validation.get('required_markers', []))}",
            f"Missing markers : {', '.join(missing) if missing else '-'}",
        ])
    if validation.get("type") == "coremark_validation":
        lines.extend([
            "",
            "CoreMark validation",
            "-------------------",
            f"Validated      : {'yes' if validation.get('validated') else 'no'}",
            f"Errors detected: {'yes' if validation.get('errors_detected') else 'no'}",
        ])

    body = output.strip()
    lines.extend(["", "Program output", "--------------"])
    if body:
        lines.append(body)
    else:
        lines.append("(program produced no stdout/stderr)")
    return "\n".join(lines) + "\n"


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


def _image_from_run_args(args: list[str]) -> str:
    image = _option_value(args, "--image")
    return str(_resolve_path(image)) if image else ""


def _wave_from_run_args(args: list[str]) -> str:
    wave = _option_value(args, "--wave")
    return str(_resolve_path(wave)) if wave else ""


def _option_value(args: list[str], option: str) -> str:
    for i, arg in enumerate(args):
        if arg == option and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(f"{option}="):
            return arg.split("=", 1)[1]
    return ""


def _apply_wave_arg(args: list[str], default_wave: Path) -> tuple[list[str], str]:
    explicit = _wave_from_run_args(args)
    if explicit:
        return list(args), explicit
    wave = str(default_wave.expanduser().resolve())
    return [*args, "--wave", wave], wave


def _apply_case_wave_arg(case_name: str, image: str, args: list[str], default_wave: Path) -> tuple[list[str], str]:
    if _is_coremark_case(case_name, image) and not _wave_from_run_args(args):
        return list(args), ""
    return _apply_wave_arg(args, default_wave)


def _run_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _build_test_script(workspace: dict[str, Any]) -> Path:
    explicit = str(workspace.get("sim_build_test_script", "")).strip()
    if explicit:
        return _resolve_path(explicit)
    soc_root = _workspace_soc_root(workspace)
    if soc_root is not None:
        return soc_root / "scripts" / "build_test.sh"
    return _invocation_root() / "scripts" / "build_test.sh"


def _program_sources_to_build(workspace: dict[str, Any]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()

    def add_source(source: Path) -> None:
        key = str(source)
        if key not in seen:
            seen.add(key)
            out.append(source)

    explicit_sources = workspace.get("sim_program_sources", []) or []
    programs_dir = _programs_dir(workspace)

    for item in explicit_sources:
        text = str(item).strip()
        if text:
            add_source(_resolve_path(text))

    if workspace.get("sim_build_all_programs"):
        for source in sorted(programs_dir.glob("*.c")):
            if source.stem in _BENCHMARK_PROGRAM_NAMES:
                continue
            add_source(source)

    names = [str(x).strip() for x in workspace.get("sim_program_names", []) or [] if str(x).strip()]
    for name in names:
        p = Path(name)
        if p.suffix != ".c":
            p = p.with_suffix(".c")
        add_source(_resolve_path(str(p), base=programs_dir))

    return out


def _programs_dir(workspace: dict[str, Any]) -> Path:
    explicit = str(workspace.get("sim_programs_dir", "")).strip()
    if explicit:
        return _resolve_path(explicit)

    soc_root = _workspace_soc_root(workspace)
    if soc_root is not None:
        return soc_root / "tests" / "programs"
    return _invocation_root() / "tests" / "programs"


def _prepare_sim_images(workspace: dict[str, Any], *,
                        build_log_path: Path | None = None,
                        case_output_root: Path | None = None) -> tuple[list[str], bool]:
    fallback_images = _sim_images(workspace)
    sources = _program_sources_to_build(workspace)
    if not sources:
        return fallback_images, True

    images: list[str] = []

    build_script = _build_test_script(workspace)
    explicit_out_dir = _explicit_soc_tests_out_dir(workspace)
    flat_out_dir = explicit_out_dir
    if flat_out_dir is None and case_output_root is None:
        flat_out_dir = _soc_tests_out_dir(workspace)
    if flat_out_dir is not None:
        flat_out_dir.mkdir(parents=True, exist_ok=True)
    if case_output_root is not None:
        case_output_root.mkdir(parents=True, exist_ok=True)

    if not build_script.exists():
        if build_log_path is not None:
            build_log_path.write_text(f"build_test.sh not found: {build_script}\n", encoding="utf-8")
        return [], False

    seen: set[str] = set()
    lines: list[str] = []
    ok = True
    env = os.environ.copy()
    if _sim_difftest_enabled(workspace):
        env["SOC_USE_BOOTLOADER"] = "1"
        env["SOC_FAST_DIFF_BOOT"] = "1"
        lines.append("[build_program] difftest fast boot env enabled")
    _apply_cpu_program_build_env(workspace, env, lines)
    if any(src.stem == "coremark" for src in sources):
        _apply_coremark_build_env(workspace, env, lines)
    link_base = str(workspace.get("sim_program_link_base", "")).strip()
    if link_base:
        env["SOC_PROGRAM_LINK_BASE"] = link_base
        lines.append(f"[build_program] program link base={link_base}")
    for src in sources:
        name = src.stem
        case_name = _safe_case_name(f"{name}.soc")
        out_dir = flat_out_dir if flat_out_dir is not None else case_output_root / case_name
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = _build_program_command(build_script, src, name, out_dir)
        preflight_errors = _rtthread_build_preflight_errors(workspace) if name == "rtthread" else []
        if preflight_errors:
            output = "\n".join(preflight_errors)
            rc = 1
        else:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, env=env)
                output = (result.stdout + result.stderr).strip()
                rc = int(result.returncode)
            except OSError as exc:
                output = str(exc)
                rc = 1
        lines.append(f"[build_program] name={name} rc={rc} src={_build_program_source_label(src, name)}")
        if output:
            lines.append(output)
        diagnosis = _build_program_failure_diagnosis(name, output)
        if rc != 0 and diagnosis:
            lines.append(f"[build_program] diagnosis: {diagnosis}")
        img = out_dir / f"{name}.soc.bin"
        if rc == 0 and img.exists():
            canonical = str(img.resolve())
            if canonical not in seen:
                seen.add(canonical)
                images.append(canonical)
        else:
            ok = False
            lines.append(f"[build_program] missing image: {img}")

    if build_log_path is not None:
        build_log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    return images, ok


def _build_program_command(build_script: Path, src: Path, name: str, out_dir: Path) -> list[str]:
    if name == "rtthread":
        return [
            str(build_script),
            "--name", "rtthread",
            "--out_dir", str(out_dir),
        ]
    return [
        str(build_script),
        "--src", str(src),
        "--name", name,
        "--out_dir", str(out_dir),
    ]


def _build_program_source_label(src: Path, name: str) -> str:
    return "rtthread-am BSP" if name == "rtthread" else str(src)


def _build_program_failure_diagnosis(name: str, output: str) -> str:
    if name != "rtthread":
        return ""
    if "scons is required to build rt-thread-am" in output:
        return "missing dependency: install scons or keep the RT-Thread fallback helper available"
    if "AM_HOME must point to an AbstractMachine repo" in output:
        return "missing dependency: set AM_HOME to an AbstractMachine repo"
    if "No RISC-V GCC toolchain found in PATH" in output:
        return "missing dependency: add a RISC-V GCC toolchain to PATH"
    if "rt-thread-am BSP not found" in output:
        return "missing dependency: initialize the rt-thread-am submodule"
    return ""


def _rtthread_build_preflight_errors(workspace: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    soc_root = _workspace_soc_root(workspace)
    rtthread_root = Path(os.getenv("RTTHREAD_AM_ROOT", "")).expanduser() if os.getenv("RTTHREAD_AM_ROOT") else None
    if rtthread_root is None and soc_root is not None:
        rtthread_root = soc_root.parent / "rt-thread-am"
    if rtthread_root is None:
        rtthread_root = _invocation_root() / "rt-thread-am"
    rtthread_bsp = rtthread_root / "bsp" / "abstract-machine"
    if not rtthread_bsp.is_dir():
        errors.append(f"rt-thread-am BSP not found: {rtthread_bsp}")

    am_home = Path(os.getenv("AM_HOME", "")).expanduser() if os.getenv("AM_HOME") else Path("/home/luyoung/ysyx-workbench/abstract-machine")
    if not (am_home / "Makefile").is_file():
        errors.append("AM_HOME must point to an AbstractMachine repo")

    cross_compile = _riscv_cross_compile_prefix()
    if not cross_compile:
        errors.append("No RISC-V GCC toolchain found in PATH")
    elif shutil.which(f"{cross_compile}gcc") is None:
        errors.append(f"Configured cross toolchain prefix is invalid: {cross_compile}")

    hexdump = os.getenv("HEXDUMP_BIN", "hexdump")
    if shutil.which(hexdump) is None:
        errors.append(f"hexdump tool not found: {hexdump}")

    if shutil.which("scons") is None and _rtthread_prepare_helper(workspace) is None:
        errors.append("scons is required to build rt-thread-am")

    return errors


def _rtthread_prepare_helper(workspace: dict[str, Any]) -> Path | None:
    soc_root = _workspace_soc_root(workspace)
    candidates: list[Path] = []
    if soc_root is not None:
        candidates.append(soc_root.parent / "rtthread_prepare.py")
    candidates.extend([
        _invocation_root() / _WORKSPACE_REL_RTTHREAD_PREPARE,
        Path(__file__).resolve().parents[2] / "thirdparty" / "rtthread_prepare.py",
    ])
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _riscv_cross_compile_prefix() -> str:
    if os.getenv("RISCV_PREFIX", "").strip():
        return os.getenv("RISCV_PREFIX", "").strip()
    for prefix in (
        "riscv32-unknown-elf-",
        "riscv64-unknown-elf-",
        "riscv64-none-elf-",
        "riscv-none-elf-",
        "riscv64-unknown-linux-gnu-",
        "riscv64-linux-gnu-",
    ):
        if shutil.which(f"{prefix}gcc") is not None:
            return prefix
    return ""


def _invocation_root() -> Path:
    """Return path root for resolving user-provided relative compile flags."""
    workspace_dir = os.getenv("BUILD_WORKSPACE_DIRECTORY", "").strip()
    if workspace_dir:
        return Path(workspace_dir).expanduser().resolve()
    return Path.cwd().expanduser().resolve()


def _normalize_sim_cflag(flag: str) -> str:
    """Normalize -I / -isystem path flags so verilator make can resolve them from obj_dir."""
    tokens = shlex.split(flag)
    if not tokens:
        return flag

    root = _invocation_root()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-I" and i + 1 < len(tokens):
            p = Path(tokens[i + 1])
            out.extend(["-I", str((root / p).resolve()) if not p.is_absolute() else str(p)])
            i += 2
            continue
        if tok.startswith("-I") and tok != "-I":
            raw = tok[2:]
            p = Path(raw)
            out.append(f"-I{(root / p).resolve()}" if raw and not p.is_absolute() else tok)
            i += 1
            continue
        if tok == "-isystem" and i + 1 < len(tokens):
            p = Path(tokens[i + 1])
            out.extend(["-isystem", str((root / p).resolve()) if not p.is_absolute() else str(p)])
            i += 2
            continue
        if tok.startswith("-isystem") and tok != "-isystem":
            raw = tok[len("-isystem"):]
            p = Path(raw)
            out.append(f"-isystem{(root / p).resolve()}" if raw and not p.is_absolute() else tok)
            i += 1
            continue
        out.append(tok)
        i += 1

    return " ".join(out)


# ── VerilatorLintStep ─────────────────────────────────────────────────────────

class VerilatorLintStep(BaseStep):
    """Run verilator --lint-only on the RTL.

    Sub-steps: lint → report
    Success condition: log.txt exists and contains no %Error.
    """

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_lint_subflow(step)
        run_info = self._run_lint(step, workspace)
        self._write_report(step, workspace, run_info)

    def check_result(self, step: WorkspaceStep) -> bool:
        summary_path = Path(step.report["dir"]) / "lint_summary.json"
        if summary_path.exists():
            summary = json_read(str(summary_path))
            if isinstance(summary, dict):
                return str(summary.get("status", "")).lower() == "pass"
        lint_path = Path(step.report["dir"]) / "log.txt"
        return lint_path.exists() and "%Error" not in lint_path.read_text(encoding="utf-8")

    def _run_lint(self, step: WorkspaceStep, workspace: dict[str, Any]) -> dict[str, Any]:
        files = rtl_files(workspace)
        top   = workspace.get("top_module", "top")
        lint_path = Path(step.report["dir"]) / "log.txt"

        cmd = [
            _verilator_cmd(),
            "--lint-only",
            "-Wall",
            "-Wno-fatal",
            "-Wno-DECLFILENAME",
            *verilator_incdir_args(workspace),
            *verilator_lint_define_args(workspace),
            "--top",
            top,
        ] + files
        lint_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            returncode = int(result.returncode)
            output = (result.stdout + result.stderr).strip() or "lint OK"
        except OSError as exc:
            returncode = 127
            output = f"%Error: failed to execute verilator: {exc}"
        lint_path.write_text(output, encoding="utf-8")
        update_substep_ok(
            step,
            LintSubFlowEnum.lint.value,
            returncode == 0,
            info={
                "top_module": top,
                "rtl_files": len(files),
                "returncode": returncode,
            },
        )
        return {
            "command": cmd,
            "returncode": returncode,
            "rtl_files": files,
            "top_module": top,
            "log_path": str(lint_path),
        }

    def _write_report(
        self,
        step: WorkspaceStep,
        workspace: dict[str, Any],
        run_info: dict[str, Any],
    ) -> None:
        lint_path = Path(step.report["dir"]) / "log.txt"
        log_content = lint_path.read_text(encoding="utf-8") if lint_path.exists() else ""
        lint_summary_path = Path(step.report["dir"]) / "lint_summary.json"
        lint_summary = build_lint_summary(
            workspace,
            run_info,
            log_content,
            summary_path=lint_summary_path,
        )
        json_write(lint_summary_path, lint_summary)
        lint_ok = lint_summary["summary"]["errors"] == 0 and int(run_info.get("returncode", 1)) == 0
        json_write(step.report["step"], {
            "lint": "pass" if lint_ok else "fail",
            "report": str(lint_path),
            "summary": str(lint_summary_path),
            "errors": lint_summary["summary"]["errors"],
            "warnings": lint_summary["summary"]["warnings"],
            "rules": lint_summary["summary"]["rules"],
            "files": lint_summary["summary"]["files"],
        })
        update_substep_ok(
            step,
            LintSubFlowEnum.report.value,
            True,
            info={
                "summary": str(lint_summary_path),
                "errors": lint_summary["summary"]["errors"],
                "warnings": lint_summary["summary"]["warnings"],
                "rules": lint_summary["summary"]["rules"],
                "files": lint_summary["summary"]["files"],
            },
        )


def build_lint_summary(
    workspace: dict[str, Any],
    run_info: dict[str, Any],
    log_content: str,
    *,
    summary_path: Path,
) -> dict[str, Any]:
    """Build a structured Verilator lint report for GUI consumption."""
    files = [str(path) for path in run_info.get("rtl_files", [])]
    diagnostics = parse_verilator_diagnostics(log_content)
    if int(run_info.get("returncode", 0)) != 0 and not diagnostics:
        diagnostics.append({
            "severity": "error",
            "code": "TOOL",
            "message": _first_nonempty_log_line(log_content) or "Verilator exited with a non-zero return code.",
            "source": "",
            "line": 0,
            "column": 0,
            "raw": _first_nonempty_log_line(log_content),
            "category": "tool",
        })
    errors = len([item for item in diagnostics if item.get("severity") == "error"])
    warnings = len([item for item in diagnostics if item.get("severity") == "warning"])
    status = "pass" if int(run_info.get("returncode", 1)) == 0 and errors == 0 else "fail"
    top_module = str(run_info.get("top_module") or workspace.get("top_module") or "top")
    rules = _lint_rule_breakdown(diagnostics)
    file_hotspots = _lint_file_hotspots(diagnostics)

    return {
        "path": str(summary_path),
        "tool": "verilator",
        "status": status,
        "returncode": int(run_info.get("returncode", 1)),
        "top_module": top_module,
        "command": [str(part) for part in run_info.get("command", [])],
        "inputs": {
            "rtl_files": files,
            "rtl_file_count": len(files),
            "incdirs": incdirs(workspace),
            "defines": verilator_lint_defines(workspace),
        },
        "summary": {
            "status": status,
            "errors": errors,
            "warnings": warnings,
            "diagnostics": len(diagnostics),
            "rules": len(rules),
            "files": len(file_hotspots),
            "rtl_files": len(files),
            "top_module": top_module,
        },
        "diagnostics": diagnostics,
        "rules": rules,
        "files": file_hotspots,
        "reports": {
            "log": str(run_info.get("log_path", "")),
            "summary": str(summary_path),
        },
    }


def parse_verilator_diagnostics(content: str) -> list[dict[str, Any]]:
    """Parse Verilator lint diagnostics into clickable records."""
    diagnostics: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int, int, str]] = set()

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _VERILATOR_DIAGNOSTIC_RE.match(line)
        if not match:
            generic = _parse_generic_verilator_diagnostic(line)
            if not generic:
                continue
            key = (
                str(generic.get("severity", "")),
                str(generic.get("code", "")),
                str(generic.get("source", "")),
                int(generic.get("line", 0) or 0),
                int(generic.get("column", 0) or 0),
                str(generic.get("message", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            diagnostics.append(generic)
            continue
        severity = "error" if match.group("severity").lower() == "error" else "warning"
        code = (match.group("code") or severity.upper()).strip()
        source = match.group("source").strip()
        line_number = int(match.group("line") or 0)
        column = int(match.group("column") or 1)
        message = match.group("message").strip()
        key = (severity, code, source, line_number, column, message)
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append({
            "severity": severity,
            "code": code,
            "message": message,
            "source": source,
            "line": line_number,
            "column": column,
            "raw": line,
            "category": _lint_category(code, message),
        })

    return diagnostics


def _parse_generic_verilator_diagnostic(line: str) -> dict[str, Any] | None:
    generic = re.match(r"^%(?P<severity>Error|Warning)(?:-(?P<code>[A-Za-z0-9_]+))?:\s*(?P<message>.*)$", line)
    if not generic:
        return None
    severity = "error" if generic.group("severity").lower() == "error" else "warning"
    code = (generic.group("code") or severity.upper()).strip()
    message = generic.group("message").strip()
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "source": "",
        "line": 0,
        "column": 0,
        "raw": line,
        "category": _lint_category(code, message),
    }


def _lint_rule_breakdown(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_code: dict[str, dict[str, Any]] = {}
    for item in diagnostics:
        code = str(item.get("code") or item.get("severity") or "UNKNOWN")
        record = by_code.setdefault(
            code,
            {
                "code": code,
                "category": _lint_category(code, str(item.get("message", ""))),
                "errors": 0,
                "warnings": 0,
                "total": 0,
                "example": str(item.get("message", "")),
            },
        )
        if item.get("severity") == "error":
            record["errors"] += 1
        elif item.get("severity") == "warning":
            record["warnings"] += 1
        record["total"] += 1

    return sorted(
        by_code.values(),
        key=lambda item: (-int(item.get("errors", 0)), -int(item.get("warnings", 0)), str(item.get("code", ""))),
    )


def _lint_file_hotspots(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source: dict[str, dict[str, Any]] = {}
    for item in diagnostics:
        source = str(item.get("source", "")).strip()
        if not source:
            continue
        record = by_source.setdefault(
            source,
            {
                "path": source,
                "errors": 0,
                "warnings": 0,
                "total": 0,
                "rules": set(),
            },
        )
        if item.get("severity") == "error":
            record["errors"] += 1
        elif item.get("severity") == "warning":
            record["warnings"] += 1
        record["total"] += 1
        record["rules"].add(str(item.get("code") or item.get("severity") or "UNKNOWN"))

    hotspots: list[dict[str, Any]] = []
    for source, record in by_source.items():
        hotspots.append({
            "path": source,
            "label": Path(source).name,
            "errors": int(record["errors"]),
            "warnings": int(record["warnings"]),
            "total": int(record["total"]),
            "rules": sorted(record["rules"]),
        })
    return sorted(
        hotspots,
        key=lambda item: (-int(item.get("errors", 0)), -int(item.get("warnings", 0)), str(item.get("path", ""))),
    )


def _lint_category(code: str, message: str) -> str:
    text = f"{code} {message}".lower()
    if "failed to execute" in text or "non-zero" in text or code.lower() == "tool":
        return "tool"
    if "syntax" in text or "unexpected" in text:
        return "syntax"
    if "width" in text or "bit" in text or "range" in text:
        return "width"
    if "unused" in text or "unuse" in text:
        return "unused"
    if "undriven" in text or "unconnected" in text or "pinmissing" in text:
        return "connectivity"
    if "multidriven" in text or "driven" in text:
        return "drivers"
    if "case" in text:
        return "case"
    if "latch" in text:
        return "latch"
    if "timing" in text or "delay" in text:
        return "timing"
    if "unsupported" in text or "unsup" in text:
        return "unsupported"
    return "lint"


def _first_nonempty_log_line(content: str) -> str:
    for line in content.splitlines():
        text = line.strip()
        if text:
            return text
    return ""


# ── VerilatorSimStep ──────────────────────────────────────────────────────────

class VerilatorSimStep(BaseStep):
    """Compile RTL to a simulation binary and run it.

    Requires workspace["testbench"] to point to a valid testbench file.
    Sub-steps: compile → simulate → report
    Success condition: simulation binary ran and returned exit code 0.
    """

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_sim_subflow(step)
        self._clear_previous_run_outputs(step)
        compiled = self._run_compile(step, workspace)
        self._run_simulate(step, workspace, compiled)
        self._write_report(step)

    def check_result(self, step: WorkspaceStep) -> bool:
        compile_state, compile_info = self._substep_status(step, SimSubFlowEnum.compile.value)
        if compile_state == "Incomplete":
            return False

        cases_json = Path(step.report["dir"]) / "cases.json"
        if cases_json.exists():
            cases = self._load_case_reports(step)
            if not cases:
                return False
            return all(self._case_report_ok(case) for case in cases)

        sim_log = Path(step.report["dir"]) / "log.txt"
        if not sim_log.exists():
            return False
        content = sim_log.read_text(encoding="utf-8")
        return _sim_output_ok(0, content)

    def _run_compile(self, step: WorkspaceStep,
                     workspace: dict[str, Any]) -> bool:
        sim_bin = Path(step.output["dir"]) / f"{workspace['design']}_sim"
        if workspace.get("sim_reuse_binary") and sim_bin.exists():
            (Path(step.log["dir"]) / "log.txt").write_text(
                f"reuse existing sim binary: {sim_bin}\n",
                encoding="utf-8",
            )
            update_substep_ok(
                step,
                SimSubFlowEnum.compile.value,
                True,
                info={"skipped": "reuse binary", "sim_bin": str(sim_bin)},
            )
            return True

        cpp_sources = _sim_cpp_sources(workspace)
        if not cpp_sources:
            message = (
                "simulation testbench is not configured; provide workspace.testbench "
                "or create a frontend SoC workspace so defaults can be inferred"
            )
            (Path(step.log["dir"]) / "log.txt").write_text(message + "\n", encoding="utf-8")
            (Path(step.report["dir"]) / "log.txt").write_text(message + "\n", encoding="utf-8")
            update_substep_ok(
                step,
                SimSubFlowEnum.compile.value,
                False,
                info={"error": "missing sim testbench"},
            )
            return False
        missing = [src for src in cpp_sources if not Path(src).exists()]
        if missing:
            update_substep_ok(
                step,
                SimSubFlowEnum.compile.value,
                False,
                info={"error": "missing sim C++ source", "missing_sources": missing},
            )
            return False

        files   = rtl_files(workspace)
        top     = workspace.get("top_module", "top")
        obj_dir = Path(step.directory) / "obj_dir"
        if obj_dir.exists():
            shutil.rmtree(obj_dir)

        cmd = [
            _verilator_cmd(), "--cc", "--exe", "--build", "-j", "8",
            "-Wno-fatal",
            "--timing",
            "--trace",
            *verilator_incdir_args(workspace),
            *verilator_define_args(workspace),
            *_sim_cflags_args(workspace),
            *_sim_ldflags_args(workspace),
            "--top", top,
            "--Mdir", str(obj_dir),
            "-o", str(sim_bin),
        ] + files + cpp_sources

        result = subprocess.run(cmd, capture_output=True, text=True)
        (Path(step.log["dir"]) / "log.txt").write_text(
            result.stdout + result.stderr, encoding="utf-8"
        )
        ok = result.returncode == 0
        update_substep_ok(step, SimSubFlowEnum.compile.value, ok)
        return ok

    @staticmethod
    def _case_report_ok(case: Any) -> bool:
        if not isinstance(case, dict) or not case.get("ok", False):
            return False
        log_path = str(case.get("log", "")).strip()
        return bool(log_path) and Path(log_path).exists()

    @staticmethod
    def _clear_previous_run_outputs(step: WorkspaceStep) -> None:
        report_dir = Path(step.report["dir"])
        for path in (
            Path(step.log["dir"]) / "log.txt",
            report_dir / "log.txt",
            report_dir / "cases.json",
            report_dir / "build_programs.log.txt",
            Path(step.report["step"]),
        ):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

    @staticmethod
    def _load_case_reports(step: WorkspaceStep) -> list[Any]:
        cases_json = Path(step.report["dir"]) / "cases.json"
        if not cases_json.exists():
            return []
        data = json_read(str(cases_json))
        cases = data.get("cases", []) if isinstance(data, dict) else []
        return cases if isinstance(cases, list) else []

    def _run_simulate(self, step: WorkspaceStep, workspace: dict[str, Any],
                      compiled: bool) -> None:
        sim_bin = Path(step.output["dir"]) / f"{workspace['design']}_sim"
        sim_log = Path(step.report["dir"]) / "log.txt"
        build_log = Path(step.report["dir"]) / "build_programs.log.txt"
        cases_json = Path(step.report["dir"]) / "cases.json"
        case_root = Path(step.report["dir"]) / "cases"
        runs_root = Path(step.report["dir"]) / "runs"
        output_cases_root = Path(step.output["dir"]) / "cases"
        suite = _sim_suite_name(workspace)

        if not compiled or not sim_bin.exists():
            previous_log = sim_log.read_text(encoding="utf-8") if sim_log.exists() else ""
            sim_log.write_text(
                previous_log
                + ("\n" if previous_log and not previous_log.endswith("\n") else "")
                + "simulation binary was not compiled; see compile sub-step\n",
                encoding="utf-8",
            )
            json_write(str(cases_json), {"suite": suite, "run_id": "", "cases": []})
            update_substep_ok(
                step,
                SimSubFlowEnum.simulate.value,
                False,
                info={"skipped": "not compiled"},
            )
            return

        images, build_ok = _prepare_sim_images(
            workspace,
            build_log_path=build_log,
            case_output_root=output_cases_root,
        )
        if not build_ok:
            sim_log.write_text(
                "build test programs failed; see build_programs.log.txt\n",
                encoding="utf-8",
            )
            json_write(str(cases_json), {"suite": suite, "run_id": "", "cases": []})
            update_substep_ok(
                step,
                SimSubFlowEnum.simulate.value,
                False,
                info={"error": "build programs failed", "log": str(build_log)},
            )
            return

        run_args = _sim_run_args(workspace)
        cases = _effective_sim_cases(images, run_args)
        runs_root.mkdir(parents=True, exist_ok=True)
        case_root.mkdir(parents=True, exist_ok=True)
        run_id = _run_tag()
        run_root = runs_root / run_id
        run_case_root = run_root / "cases"
        run_case_root.mkdir(parents=True, exist_ok=True)

        all_ok = True
        failed_cases: list[str] = []
        summary_lines: list[str] = []
        cases_report: list[dict[str, Any]] = []

        for case in cases:
            case_name = str(case["name"])
            image = str(case["image"])
            logs = _case_logs(case_root, run_case_root, output_cases_root, case_name)
            run_args, wave = _apply_case_wave_arg(
                case_name,
                image,
                list(case["args"]),
                logs["output_dir"] / "wave.vcd",
            )

            if image and not Path(image).exists():
                output = f"image not found: {image}\n"
                rc = 1
            else:
                rc, output = _run_sim_process(
                    [str(sim_bin), *run_args],
                    stream_output=case_name == "rtthread.soc",
                )

            case_ok, validation = _case_output_ok(case_name, image, rc, output)
            metrics = _coremark_metrics(workspace, output, case_ok) if _is_coremark_case(case_name, image) else {}
            terminal_output = _case_terminal_output(
                suite=suite,
                case_name=case_name,
                image=image,
                returncode=rc,
                ok=case_ok,
                validation=validation,
                metrics=metrics,
                wave=wave,
                output=output,
            )
            for log_path in (logs["latest_log"], logs["run_log"], logs["output_log"]):
                log_path.write_text(terminal_output, encoding="utf-8")
            if not case_ok:
                all_ok = False
                failed_cases.append(case_name)

            case_report = {
                "name": case_name,
                "suite": suite,
                "image": image,
                "returncode": rc,
                "ok": case_ok,
                "log": str(logs["output_log"]),
                "latest_log": str(logs["output_log"]),
                "report_log": str(logs["latest_log"]),
                "run_log": str(logs["run_log"]),
                "wave": wave,
                "run_id": run_id,
            }
            if validation:
                case_report["validation"] = validation
            if metrics:
                case_report["metrics"] = metrics
            cases_report.append(case_report)
            summary_lines.append(
                f"[{case_name}] status={'PASS' if case_ok else 'FAIL'} rc={rc} suite={suite} image={image or '-'} log={logs['output_log']} wave={wave} run_log={logs['run_log']}"
            )

        summary_text = "\n".join(summary_lines) + "\n"
        sim_log.write_text(summary_text, encoding="utf-8")
        (run_root / "log.txt").write_text(summary_text, encoding="utf-8")
        json_write(str(cases_json), {"suite": suite, "run_id": run_id, "cases": cases_report})
        json_write(str(run_root / "cases.json"), {"suite": suite, "run_id": run_id, "cases": cases_report})
        update_substep_ok(
            step,
            SimSubFlowEnum.simulate.value,
            all_ok,
            info={
                "cases": len(cases_report),
                "failed_cases": failed_cases,
                "run_id": run_id,
                "run_dir": str(run_root),
                "suite": suite,
            },
        )

    def _write_report(self, step: WorkspaceStep) -> None:
        sim_log   = Path(step.report["dir"]) / "log.txt"
        cases_json = Path(step.report["dir"]) / "cases.json"
        compile_state, compile_info = self._substep_status(step, SimSubFlowEnum.compile.value)
        cases_payload = json_read(str(cases_json)) if cases_json.exists() else {}
        if compile_state != "Success":
            sim_ok = False
            failed_cases = []
            total_cases = 0
        elif isinstance(cases_payload, dict) and cases_payload.get("cases") is not None:
            cases = cases_payload.get("cases", [])
            failed_cases = [
                str(c.get("name", ""))
                for c in cases
                if isinstance(c, dict) and not bool(c.get("ok", False))
            ]
            total_cases = len(cases) if isinstance(cases, list) else 0
            sim_ok = total_cases > 0 and len(failed_cases) == 0
        elif not sim_log.exists():
            sim_ok = False
            failed_cases = []
            total_cases = 0
        else:
            content = sim_log.read_text()
            sim_ok = _sim_output_ok(0, content)
            failed_cases = []
            total_cases = 0

        if compile_info.get("skipped"):
            compile_status = "skipped"
        elif compile_state == "Success":
            compile_status = "done"
        else:
            compile_status = "fail"

        payload: dict[str, Any] = {
            "compile":  compile_status,
            "simulate": "pass" if sim_ok else "fail",
        }
        if isinstance(cases_payload, dict) and cases_payload.get("suite"):
            payload["suite"] = str(cases_payload.get("suite"))
        if isinstance(cases_payload, dict) and cases_payload.get("run_id"):
            payload["run_id"] = str(cases_payload.get("run_id"))
        if total_cases > 0:
            payload["cases"] = total_cases
            payload["failed_cases"] = failed_cases
        json_write(step.report["step"], payload)
        update_substep_ok(step, SimSubFlowEnum.report.value, True)

    @staticmethod
    def _substep_status(step: WorkspaceStep, name: str) -> tuple[str, dict[str, Any]]:
        data = json_read(step.subflow.get("path", ""))
        for entry in data.get("steps", []):
            if entry.get("name") == name:
                return str(entry.get("state", "")), dict(entry.get("info", {}) or {})
        return "", {}


def _sim_output_ok(returncode: int, output: str) -> bool:
    return returncode == 0 and "FAILED" not in output and "%Error" not in output


def _case_logs(case_root: Path, run_case_root: Path, output_cases_root: Path,
               case_name: str) -> dict[str, Path]:
    latest_dir = _mkdir(case_root / case_name)
    run_dir = _mkdir(run_case_root / case_name)
    output_dir = _mkdir(output_cases_root / case_name)
    return {
        "latest_log": latest_dir / "log.txt",
        "run_log": run_dir / "log.txt",
        "output_log": output_dir / "log.txt",
        "output_dir": output_dir,
    }


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
