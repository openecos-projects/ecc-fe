"""Build the test programs a CPU simulation runs, and find their images."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from fecompiler.tools.common.build_policy import clean_build_requested
from fecompiler.tools.common.process import run_tool, tool_timeout
from fecompiler.tools.verilator import program_build
from fecompiler.tools.verilator.coremark import (
    _coremark_compile_settings,
    _program_compile_settings,
)
from fecompiler.tools.verilator.lint import (  # noqa: F401
    VerilatorLintStep,
    build_lint_summary,
    parse_verilator_diagnostics,
)
from fecompiler.tools.verilator.parallel import (
    _map_in_order,
)
from fecompiler.tools.verilator.sim_settings import (
    _invocation_root,
    _resolve_path,
    _setting_int,
    _sim_difftest_enabled,
    _workspace_cpu_id,
    _workspace_soc_root,
)

_BENCHMARK_PROGRAM_NAMES = {"coremark"}


_CPU_PROGRAM_ENTRY_OFFSETS = {
    "ibex": "0x80",
}


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


def _safe_case_name(name: str) -> str:
    token = "".join(ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_" for ch in name)
    token = token.strip("._")
    return token or "case"


def _apply_program_compile_env(
    settings: dict[str, Any],
    env: dict[str, str],
) -> list[str]:
    env["ECOS_SIM_OPT_LEVEL"] = str(settings["opt_level"])
    env["ECOS_SIM_MARCH"] = str(settings["march"])
    env["ECOS_SIM_MABI"] = str(settings["mabi"])

    extra_cflags = [str(item) for item in settings["extra_cflags"]]
    if extra_cflags:
        env["ECOS_SIM_EXTRA_CFLAGS_LINES"] = "\n".join(extra_cflags)
        env["ECOS_SIM_EXTRA_CFLAGS"] = " ".join(shlex.quote(flag) for flag in extra_cflags)
    return extra_cflags


def _apply_program_build_env(workspace: dict[str, Any], env: dict[str, str], log_lines: list[str]) -> None:
    settings = _program_compile_settings(workspace)
    extra_cflags = _apply_program_compile_env(settings, env)
    log_lines.append(
        "[build_program] compile "
        f"preset={settings['preset']} opt={settings['opt_level']} "
        f"march={settings['march']} mabi={settings['mabi']} "
        f"extra_cflags={' '.join(extra_cflags) if extra_cflags else '-'}"
    )


def _apply_coremark_build_env(workspace: dict[str, Any], env: dict[str, str], log_lines: list[str]) -> None:
    settings = _coremark_compile_settings(workspace)
    extra_cflags = _apply_program_compile_env(settings, env)
    env["ECOS_COREMARK_ITERATIONS"] = str(settings["iterations"])
    env["ECOS_COREMARK_TOTAL_DATA_SIZE"] = str(settings["total_data_size"])
    env["ECOS_COREMARK_HAS_FLOAT"] = "1" if settings["has_float"] else "0"

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


def _sim_case_program(workspace: dict[str, Any], image: str) -> dict[str, str]:
    """Describe the build artifacts associated with a simulation image."""
    image_text = str(image).strip()
    if not image_text:
        return {
            "source": "",
            "elf": "",
            "binary": "",
            "image": "",
            "disassembly": "",
        }

    image_path = _resolve_path(image_text)
    image_name = image_path.name
    program_name = (
        image_name.removesuffix(".soc.bin")
        if image_name.endswith(".soc.bin")
        else image_path.stem
    )
    output_prefix = image_path.parent / program_name

    source_path: Path | None = None
    for candidate in _program_sources_to_build(workspace):
        if candidate.stem == program_name and candidate.is_file():
            source_path = candidate.resolve()
            break
    if source_path is None:
        candidate = _programs_dir(workspace) / f"{program_name}.c"
        if candidate.is_file():
            source_path = candidate.resolve()

    def existing(path: Path) -> str:
        return str(path.resolve()) if path.is_file() else ""

    return {
        "source": str(source_path) if source_path is not None else "",
        "elf": existing(output_prefix.with_suffix(".elf")),
        "binary": existing(output_prefix.with_suffix(".bin")),
        "image": str(image_path),
        "disassembly": existing(output_prefix.with_suffix(".txt")),
    }


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
    else:
        _apply_program_build_env(workspace, env, lines)
    link_base = str(workspace.get("sim_program_link_base", "")).strip()
    if link_base:
        env["SOC_PROGRAM_LINK_BASE"] = link_base
        lines.append(f"[build_program] program link base={link_base}")
    jobs = _setting_int(workspace, "sim_build_jobs", 0, minimum=0) or os.cpu_count() or 1
    jobs = min(jobs, len(sources))

    def out_dir_of(src: Path) -> Path:
        case_name = _safe_case_name(f"{src.stem}.soc")
        return flat_out_dir if flat_out_dir is not None else case_output_root / case_name

    def session_of(src: Path) -> Path:
        # Programs built side by side may share an output directory; each
        # still needs a session of its own.
        out_dir = out_dir_of(src)
        return out_dir / f".session_{src.stem}" if jobs > 1 else out_dir

    # What every program depends on is the same for all of them: hash it once.
    shared = program_build.shared_inputs(build_script, env)
    # Read here: the builds below may run in worker threads.
    rebuild_all = clean_build_requested()
    if rebuild_all:
        lines.append("[build_program] clean build requested: rebuilding every program")

    def build(src: Path) -> tuple[list[str], Path, bool]:
        name = src.stem
        out_dir = out_dir_of(src)
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = _build_program_command(build_script, src, name, out_dir)
        img = out_dir / f"{name}.soc.bin"
        if not rebuild_all and program_build.is_current(img, cmd, src, shared):
            return [f"[build_program] name={name} up to date src={src}"], img, True
        try:
            result = run_tool(
                cmd,
                env=env,
                timeout=tool_timeout("program_build"),
                session_dir=session_of(src),
            )
            output = result.output.strip()
            rc = result.returncode
        except OSError as exc:
            output = str(exc)
            rc = 1
        if jobs > 1:
            # The per-program session directory is empty once the build is over.
            try:
                session_of(src).rmdir()
            except OSError:
                pass
        report = [f"[build_program] name={name} rc={rc} src={src}"]
        if output:
            report.append(output)
        built = rc == 0 and img.exists()
        if built:
            program_build.record(img, cmd, src, shared)
        else:
            program_build.record_path(img).unlink(missing_ok=True)
            report.append(f"[build_program] missing image: {img}")
        return report, img, built

    # Each build writes only files named after its program, so they can run
    # side by side; the log keeps the order of the sources.
    for report, img, built in _map_in_order(sources, build, jobs, session_of):
        lines.extend(report)
        if not built:
            ok = False
            continue
        canonical = str(img.resolve())
        if canonical not in seen:
            seen.add(canonical)
            images.append(canonical)

    if build_log_path is not None:
        build_log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    return images, ok


def _build_program_command(build_script: Path, src: Path, name: str, out_dir: Path) -> list[str]:
    return [
        str(build_script),
        "--src", str(src),
        "--name", name,
        "--out_dir", str(out_dir),
    ]
