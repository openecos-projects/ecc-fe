"""Verilator step implementations — VerilatorLintStep and VerilatorSimStep."""

from __future__ import annotations

import os
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fecompiler.tools.fe.base import BaseStep
from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.common.rtl_inputs import (
    rtl_files,
    verilator_define_args,
    verilator_incdir_args,
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

_LOCAL_VERILATOR_BIN = Path(__file__).parent / "bin" / "verilator"
_SYSTEM_VERILATOR_BIN = Path("/usr/local/bin/verilator")
_LOCAL_VERILATOR_INCLUDE = Path(__file__).parent / "include"
_SYSTEM_VERILATOR_INCLUDE = Path("/usr/local/share/verilator/include")
_WORKSPACE_REL_VERILATOR_BIN = Path("fecompiler/tools/verilator/bin/verilator")
_WORKSPACE_REL_VERILATOR_INCLUDE = Path("fecompiler/tools/verilator/include")
_WORKSPACE_REL_SOC_ROOT = Path("fecompiler/thirdparty/SoC")


def _verilator_cmd() -> str:
    """Return verilator executable path (repo-local first, then system)."""
    workspace_dir = os.getenv("BUILD_WORKSPACE_DIRECTORY", "").strip()
    workspace_bin = (
        Path(workspace_dir) / _WORKSPACE_REL_VERILATOR_BIN if workspace_dir else None
    )
    cwd_bin = Path.cwd() / _WORKSPACE_REL_VERILATOR_BIN

    if _LOCAL_VERILATOR_BIN.exists():
        return str(_LOCAL_VERILATOR_BIN)
    if workspace_bin is not None and workspace_bin.exists():
        return str(workspace_bin)
    if cwd_bin.exists():
        return str(cwd_bin)
    if _SYSTEM_VERILATOR_BIN.exists():
        return str(_SYSTEM_VERILATOR_BIN)
    return "verilator"


def _verilator_include_args() -> list[str]:
    """Return include arg for verilator runtime headers if present."""
    workspace_dir = os.getenv("BUILD_WORKSPACE_DIRECTORY", "").strip()
    workspace_include = (
        Path(workspace_dir) / _WORKSPACE_REL_VERILATOR_INCLUDE if workspace_dir else None
    )
    cwd_include = Path.cwd() / _WORKSPACE_REL_VERILATOR_INCLUDE

    if _LOCAL_VERILATOR_INCLUDE.exists():
        return [f"-I{_LOCAL_VERILATOR_INCLUDE}"]
    if workspace_include is not None and workspace_include.exists():
        return [f"-I{workspace_include}"]
    if cwd_include.exists():
        return [f"-I{cwd_include}"]
    if _SYSTEM_VERILATOR_INCLUDE.exists():
        return [f"-I{_SYSTEM_VERILATOR_INCLUDE}"]
    return []


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
    has_std = any(flag.startswith("-std=") for flag in user_flags)
    flags = ([] if has_std else ["-std=c++20"]) + user_flags
    if not flags:
        return []
    return ["-CFLAGS", " ".join(flags)]


def _sim_ldflags_args(workspace: dict[str, Any]) -> list[str]:
    flags = [str(f).strip() for f in workspace.get("sim_ldflags", []) or [] if str(f).strip()]
    if not flags:
        return []
    return ["-LDFLAGS", " ".join(flags)]


def _sim_run_args(workspace: dict[str, Any]) -> list[str]:
    return [str(arg) for arg in workspace.get("sim_run_args", []) or []]


def _sim_difftest_enabled(workspace: dict[str, Any]) -> bool:
    return "--diff" in _sim_run_args(workspace)


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
    out: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--image":
            i += 2
            continue
        if arg.startswith("--image="):
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
        cases.append(
            {
                "name": case_name,
                "image": image,
                "args": ["--image", image, *base_args],
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


def _image_from_run_args(args: list[str]) -> str:
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--image" and i + 1 < len(args):
            return str(_resolve_path(args[i + 1]))
        if arg.startswith("--image="):
            return str(_resolve_path(arg.split("=", 1)[1]))
        i += 1
    return ""


def _wave_from_run_args(args: list[str]) -> str:
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--wave" and i + 1 < len(args):
            return str(_resolve_path(args[i + 1]))
        if arg.startswith("--wave="):
            return str(_resolve_path(arg.split("=", 1)[1]))
        i += 1
    return ""


def _apply_wave_arg(args: list[str], default_wave: Path) -> tuple[list[str], str]:
    explicit = _wave_from_run_args(args)
    if explicit:
        return list(args), explicit
    wave = str(default_wave.expanduser().resolve())
    return [*args, "--wave", wave], wave


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
    explicit_sources = workspace.get("sim_program_sources", []) or []
    if explicit_sources:
        out: list[Path] = []
        seen: set[str] = set()
        for item in explicit_sources:
            text = str(item).strip()
            if not text:
                continue
            p = _resolve_path(text)
            key = str(p)
            if key not in seen:
                seen.add(key)
                out.append(p)
        return out

    explicit_dir = str(workspace.get("sim_programs_dir", "")).strip()
    if explicit_dir:
        programs_dir = _resolve_path(explicit_dir)
    else:
        soc_root = _workspace_soc_root(workspace)
        programs_dir = soc_root / "tests" / "programs" if soc_root is not None else (_invocation_root() / "tests" / "programs")

    names = [str(x).strip() for x in workspace.get("sim_program_names", []) or [] if str(x).strip()]
    if names:
        out = []
        seen = set()
        for name in names:
            p = Path(name)
            if p.suffix != ".c":
                p = p.with_suffix(".c")
            source = _resolve_path(str(p), base=programs_dir)
            key = str(source)
            if key not in seen:
                seen.add(key)
                out.append(source)
        return out

    if workspace.get("sim_build_all_programs"):
        return sorted(programs_dir.glob("*.c"))
    return []


def _prepare_sim_images(workspace: dict[str, Any], *,
                        build_log_path: Path | None = None) -> tuple[list[str], bool]:
    images = _sim_images(workspace)
    sources = _program_sources_to_build(workspace)
    if not sources:
        return images, True

    build_script = _build_test_script(workspace)
    out_dir = _soc_tests_out_dir(workspace)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not build_script.exists():
        if build_log_path is not None:
            build_log_path.write_text(f"build_test.sh not found: {build_script}\n", encoding="utf-8")
        return images, False

    seen: set[str] = set(images)
    lines: list[str] = []
    ok = True
    env = os.environ.copy()
    if _sim_difftest_enabled(workspace):
        env["SOC_USE_BOOTLOADER"] = "1"
        env["SOC_FAST_DIFF_BOOT"] = "1"
        lines.append("[build_program] difftest fast boot env enabled")
    for src in sources:
        name = src.stem
        cmd = [
            str(build_script),
            "--src", str(src),
            "--name", name,
            "--out_dir", str(out_dir),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=env)
            output = (result.stdout + result.stderr).strip()
            rc = int(result.returncode)
        except OSError as exc:
            output = str(exc)
            rc = 1
        lines.append(f"[build_program] name={name} rc={rc} src={src}")
        if output:
            lines.append(output)
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
        self._run_lint(step, workspace)
        self._write_report(step)

    def check_result(self, step: WorkspaceStep) -> bool:
        lint_path = Path(step.report["dir"]) / "log.txt"
        return lint_path.exists() and "%Error" not in lint_path.read_text(encoding="utf-8")

    def _run_lint(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        files = rtl_files(workspace)
        top   = workspace.get("top_module", "top")
        lint_path = Path(step.report["dir"]) / "log.txt"

        cmd = [
            _verilator_cmd(),
            "--lint-only",
            "-Wno-fatal",
            *_verilator_include_args(),
            *verilator_incdir_args(workspace),
            *verilator_define_args(workspace),
            "--top",
            top,
        ] + files
        result = subprocess.run(cmd, capture_output=True, text=True)
        lint_path.write_text(
            (result.stdout + result.stderr).strip() or "lint OK",
            encoding="utf-8",
        )
        update_substep_ok(step, LintSubFlowEnum.lint.value, result.returncode == 0)

    def _write_report(self, step: WorkspaceStep) -> None:
        lint_path = Path(step.report["dir"]) / "log.txt"
        lint_ok   = lint_path.exists() and "%Error" not in lint_path.read_text()
        json_write(step.report["step"], {
            "lint": "pass" if lint_ok else "fail",
        })
        update_substep_ok(step, LintSubFlowEnum.report.value, True)


# ── VerilatorSimStep ──────────────────────────────────────────────────────────

class VerilatorSimStep(BaseStep):
    """Compile RTL to a simulation binary and run it.

    Requires workspace["testbench"] to point to a valid testbench file.
    Sub-steps: compile → simulate → report
    Success condition: simulation binary ran and returned exit code 0.
    """

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_sim_subflow(step)
        compiled = self._run_compile(step, workspace)
        self._run_simulate(step, workspace, compiled)
        self._write_report(step)

    def check_result(self, step: WorkspaceStep) -> bool:
        compile_state, compile_info = self._substep_status(step, SimSubFlowEnum.compile.value)
        if compile_state == "Incomplete":
            return False
        if compile_info.get("skipped") == "no testbench":
            return True

        cases_json = Path(step.report["dir"]) / "cases.json"
        if cases_json.exists():
            data = json_read(str(cases_json))
            cases = data.get("cases", []) if isinstance(data, dict) else []
            if not isinstance(cases, list) or not cases:
                return False
            for case in cases:
                if not isinstance(case, dict):
                    return False
                if not case.get("ok", False):
                    return False
                log_path = str(case.get("log", "")).strip()
                if not log_path or not Path(log_path).exists():
                    return False
            return True

        sim_log = Path(step.report["dir"]) / "log.txt"
        if not sim_log.exists():
            return False
        content = sim_log.read_text(encoding="utf-8")
        return "FAILED" not in content and "%Error" not in content

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
            update_substep_ok(
                step,
                SimSubFlowEnum.compile.value,
                True,
                info={"skipped": "no testbench"},
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

        cmd = [
            _verilator_cmd(), "--binary", "-j", "8",
            "-Wno-fatal",
            "--trace",
            *_verilator_include_args(),
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

    def _run_simulate(self, step: WorkspaceStep, workspace: dict[str, Any],
                      compiled: bool) -> None:
        sim_bin = Path(step.output["dir"]) / f"{workspace['design']}_sim"
        sim_log = Path(step.report["dir"]) / "log.txt"
        build_log = Path(step.report["dir"]) / "build_programs.log.txt"
        cases_json = Path(step.report["dir"]) / "cases.json"
        case_root = Path(step.report["dir"]) / "cases"
        runs_root = Path(step.report["dir"]) / "runs"

        if not compiled or not sim_bin.exists():
            update_substep_ok(
                step,
                SimSubFlowEnum.simulate.value,
                True,
                info={"skipped": "not compiled"},
            )
            return

        images, build_ok = _prepare_sim_images(workspace, build_log_path=build_log)
        if not build_ok:
            sim_log.write_text(
                "build test programs failed; see build_programs.log.txt\n",
                encoding="utf-8",
            )
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
            latest_case_dir = case_root / case_name
            latest_case_dir.mkdir(parents=True, exist_ok=True)
            latest_case_log = latest_case_dir / "log.txt"
            run_case_dir = run_case_root / case_name
            run_case_dir.mkdir(parents=True, exist_ok=True)
            run_case_log = run_case_dir / "log.txt"
            output_case_dir = Path(step.output["dir"]) / "cases" / case_name
            output_case_dir.mkdir(parents=True, exist_ok=True)
            run_args, wave = _apply_wave_arg(
                list(case["args"]),
                output_case_dir / "wave.vcd",
            )

            if image and not Path(image).exists():
                output = f"image not found: {image}\n"
                rc = 1
            else:
                result = subprocess.run(
                    [str(sim_bin), *run_args],
                    capture_output=True,
                    text=True,
                )
                output = result.stdout + result.stderr
                rc = int(result.returncode)

            latest_case_log.write_text(output, encoding="utf-8")
            run_case_log.write_text(output, encoding="utf-8")
            case_ok = rc == 0 and "FAILED" not in output and "%Error" not in output
            if not case_ok:
                all_ok = False
                failed_cases.append(case_name)

            cases_report.append(
                {
                    "name": case_name,
                    "image": image,
                    "returncode": rc,
                    "ok": case_ok,
                    "log": str(run_case_log),
                    "latest_log": str(latest_case_log),
                    "wave": wave,
                    "run_id": run_id,
                }
            )
            summary_lines.append(
                f"[{case_name}] rc={rc} image={image or '-'} wave={wave} latest_log={latest_case_log} run_log={run_case_log}"
            )

        summary_text = "\n".join(summary_lines) + "\n"
        sim_log.write_text(summary_text, encoding="utf-8")
        (run_root / "log.txt").write_text(summary_text, encoding="utf-8")
        json_write(str(cases_json), {"run_id": run_id, "cases": cases_report})
        json_write(str(run_root / "cases.json"), {"run_id": run_id, "cases": cases_report})
        update_substep_ok(
            step,
            SimSubFlowEnum.simulate.value,
            all_ok,
            info={
                "cases": len(cases_report),
                "failed_cases": failed_cases,
                "run_id": run_id,
                "run_dir": str(run_root),
            },
        )

    def _write_report(self, step: WorkspaceStep) -> None:
        sim_log   = Path(step.report["dir"]) / "log.txt"
        cases_json = Path(step.report["dir"]) / "cases.json"
        compile_state, compile_info = self._substep_status(step, SimSubFlowEnum.compile.value)
        if compile_info.get("skipped") == "no testbench":
            sim_ok = True
            failed_cases: list[str] = []
            total_cases = 0
        elif cases_json.exists():
            data = json_read(str(cases_json))
            cases = data.get("cases", []) if isinstance(data, dict) else []
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
            sim_ok = (
                "FAILED" not in content and
                "%Error" not in content
            )
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
