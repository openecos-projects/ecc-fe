"""Plan the cases of a simulation run and judge the output of each."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fecompiler.tools.verilator.coremark import (
    _coremark_requested,
    _format_coremark_score_lines,
    _is_coremark_case,
)
from fecompiler.tools.verilator.lint import (  # noqa: F401
    VerilatorLintStep,
    build_lint_summary,
    parse_verilator_diagnostics,
)
from fecompiler.tools.verilator.programs import (
    _safe_case_name,
)
from fecompiler.tools.verilator.sim_settings import (
    _arg_present,
    _option_value,
    _resolve_path,
    _sim_cycles_from_output,
    _strip_option_with_value,
)

_SIM_BAD_TRAP_RE = re.compile(
    r"HIT BAD TRAP(?:,\s*code=(?P<code>0x[0-9a-fA-F]+|[0-9]+))?",
    re.IGNORECASE,
)


_DIFFTEST_MISMATCH_RE = re.compile(
    r"^\s*\[DIFFTEST\]\s+(?P<message>.*?mismatch.*?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


_DIFFTEST_PROGRESS_RE = re.compile(
    r"\[soc-sim\]\[difftest\]\s+progress:.*?\blast_pc=(?P<last_pc>0x[0-9a-fA-F]+)"
    r"(?:.*?\blast_npc=(?P<last_npc>0x[0-9a-fA-F]+))?",
    re.IGNORECASE,
)


_DIFFTEST_PASSED_RE = re.compile(
    r"\[soc-sim\]\[difftest\]\s+passed:\s+commits=(?P<commits>[0-9]+)\s+compared=(?P<compared>[0-9]+)",
    re.IGNORECASE,
)


def _sim_suite_name(workspace: dict[str, Any]) -> str:
    if _coremark_requested(workspace):
        return "coremark"
    return "cpu_tests"


def _suite_label(suite: str) -> str:
    if suite == "coremark":
        return "CoreMark"
    return "CPU Tests"


def _strip_image_args(args: list[str]) -> list[str]:
    return _strip_option_with_value(args, "--image")


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

    return _sim_output_ok(returncode, output), {}


def _sim_int_option(args: list[str], option: str) -> int | None:
    value = _option_value(args, option)
    if not value:
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def _difftest_pass_counts(output: str) -> tuple[int, int] | None:
    match = _DIFFTEST_PASSED_RE.search(output)
    if match is None:
        return None
    try:
        commits = int(match.group("commits"))
        compared = int(match.group("compared"))
    except ValueError:
        return None
    return (commits, compared) if compared > 0 else None


def _sim_case_metrics(
    args: list[str],
    returncode: int,
    output: str,
    ok: bool,
) -> dict[str, Any]:
    mismatch = _DIFFTEST_MISMATCH_RE.search(output)
    bad_trap = _SIM_BAD_TRAP_RE.search(output)
    timed_out = "timeout after" in output
    has_good_trap = "HIT GOOD TRAP" in output
    difftest_pass_counts = _difftest_pass_counts(output)
    difftest_enabled = _arg_present(args, "--diff") or "[difftest] enabled" in output.lower()

    if mismatch:
        termination = "difftest_mismatch"
    elif difftest_enabled and not difftest_pass_counts and returncode == 0 and has_good_trap:
        termination = "difftest_incomplete"
    elif bad_trap:
        termination = "bad_trap"
    elif timed_out:
        termination = "timeout"
    elif returncode != 0:
        termination = "process_error"
    elif has_good_trap or ok:
        termination = "good_trap"
    else:
        termination = "unknown"

    trap_code: int | None = 0 if termination == "good_trap" else None
    if bad_trap and bad_trap.group("code"):
        try:
            trap_code = int(bad_trap.group("code"), 0)
        except ValueError:
            trap_code = None

    if mismatch:
        difftest_status = "mismatch"
    elif not difftest_enabled:
        difftest_status = "disabled"
    elif ok and difftest_pass_counts:
        difftest_status = "passed"
    else:
        difftest_status = "incomplete"

    progress_matches = list(_DIFFTEST_PROGRESS_RE.finditer(output))
    progress = progress_matches[-1] if progress_matches else None
    first_mismatch: dict[str, Any] | None = None
    if mismatch:
        message = mismatch.group("message").strip()
        pc_match = re.search(r"\bpc=(0x[0-9a-fA-F]+)", message, re.IGNORECASE)
        first_mismatch = {
            "message": message,
            "pc": pc_match.group(1) if pc_match else None,
        }

    return {
        "cycles": _sim_cycles_from_output(output),
        "max_cycles": _sim_int_option(args, "--max-cycles"),
        "termination": termination,
        "trap_code": trap_code,
        "timeout_accepted": timed_out and _arg_present(args, "--timeout-ok"),
        "difftest": {
            "enabled": difftest_enabled,
            "status": difftest_status,
            "commits": difftest_pass_counts[0] if difftest_pass_counts else None,
            "compared": difftest_pass_counts[1] if difftest_pass_counts else None,
            "last_pc": progress.group("last_pc") if progress else None,
            "last_npc": progress.group("last_npc") if progress else None,
            "first_mismatch": first_mismatch,
        },
    }


def _first_sim_error(output: str) -> str:
    markers = (
        "mismatch",
        "difftest] incomplete",
        "HIT BAD TRAP",
        "timeout after",
        "image not found",
        "%Error",
        "ERROR!",
        "FAILED",
    )
    for line in output.splitlines():
        text = line.strip()
        if text and any(marker.lower() in text.lower() for marker in markers):
            return text
    return ""


def _simulation_failure(
    *,
    returncode: int,
    output: str,
    validation: dict[str, Any],
    metrics: dict[str, Any],
    wave: str,
) -> dict[str, Any]:
    termination = str(metrics.get("termination", "unknown"))
    if termination == "difftest_mismatch":
        kind = "difftest_mismatch"
        message = "Difftest found an architectural mismatch."
    elif termination == "difftest_incomplete":
        kind = "difftest_incomplete"
        message = "Difftest ended without completing an architectural comparison."
    elif termination == "bad_trap":
        kind = "bad_trap"
        message = "The program terminated with a bad trap."
    elif termination == "timeout":
        kind = "timeout"
        message = "The simulation reached its cycle limit."
    elif termination == "process_error":
        kind = "process_error"
        message = f"The simulator process exited with return code {returncode}."
    elif validation:
        kind = "validation_failed"
        message = "The simulator completed, but suite validation failed."
    else:
        kind = "simulation_failed"
        message = "The simulation did not produce an accepted success result."

    nonempty_lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    return {
        "kind": kind,
        "message": message,
        "first_error": _first_sim_error(output),
        "log_tail": "\n".join(nonempty_lines[-20:]),
        "wave": wave,
    }


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


def _image_from_run_args(args: list[str]) -> str:
    image = _option_value(args, "--image")
    return str(_resolve_path(image)) if image else ""


def _wave_from_run_args(args: list[str]) -> str:
    wave = _option_value(args, "--wave")
    return str(_resolve_path(wave)) if wave else ""


def _apply_wave_arg(args: list[str], default_wave: Path) -> tuple[list[str], str]:
    explicit = _wave_from_run_args(args)
    if explicit:
        return list(args), explicit
    wave = str(default_wave.expanduser().resolve())
    return [*args, "--wave", wave], wave


def _apply_case_wave_arg(
    case_name: str, image: str, args: list[str], default_wave: Path, *, dump: bool = True
) -> tuple[list[str], str]:
    """Add the waveform argument of a case; one named in *args* always wins."""
    wanted = dump and not _is_coremark_case(case_name, image)
    if not wanted and not _wave_from_run_args(args):
        return list(args), ""
    return _apply_wave_arg(args, default_wave)


def _sim_output_ok(returncode: int, output: str) -> bool:
    return (
        returncode == 0
        and "HIT GOOD TRAP" in output
        and "HIT BAD TRAP" not in output
        and "FAILED" not in output
        and "%Error" not in output
        and "timeout after" not in output
    )


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
