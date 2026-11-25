"""The CPU simulation step: build the simulator, build the programs, run the cases."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.common.build_policy import clean_build_requested
from fecompiler.tools.common.process import run_tool, tool_timeout
from fecompiler.tools.common.rtl_inputs import (
    rtl_files,
    verilator_define_args,
    verilator_incdir_args,
    verilator_library_args,
)
from fecompiler.tools.common.rtl_ownership import (
    rtl_ownership_map,
    rtl_source_ownership,
)
from fecompiler.tools.common.tool_config import tool_config
from fecompiler.tools.fe.base import BaseStep
from fecompiler.tools.fe.subflow import update_substep_ok
from fecompiler.tools.verilator import coverage
from fecompiler.tools.verilator.build_stamp import (
    record_build,
    same_command,
    stale_reason,
    stamp_path,
)
from fecompiler.tools.verilator.cases import (
    _apply_case_wave_arg,
    _case_logs,
    _case_output_ok,
    _case_terminal_output,
    _difftest_pass_counts,
    _effective_sim_cases,
    _sim_case_metrics,
    _sim_output_ok,
    _sim_suite_name,
    _simulation_failure,
)
from fecompiler.tools.verilator.command import verilator_cmd as _verilator_cmd
from fecompiler.tools.verilator.coremark import (
    _coremark_metrics,
    _is_coremark_case,
)
from fecompiler.tools.verilator.lint import (  # noqa: F401
    VerilatorLintStep,
    build_lint_summary,
    parse_verilator_diagnostics,
)
from fecompiler.tools.verilator.parallel import (
    _map_in_order,
)
from fecompiler.tools.verilator.programs import (
    _prepare_sim_images,
    _sim_case_program,
)
from fecompiler.tools.verilator.regression import (
    _case_image_sha256,
    _previous_simulation_run,
    _prune_run_history,
    _run_tag,
    _simulation_history,
    _simulation_regression,
)
from fecompiler.tools.verilator.sim_settings import (
    _arg_present,
    _bool_workspace_value,
    _coverage_requested,
    _setting_int,
    _sim_cflags_args,
    _sim_ldflags_args,
    _sim_run_args,
    sim_cpp_sources,
)
from fecompiler.tools.verilator.subflow import (
    SimSubFlowEnum,
    init_sim_subflow,
)
from fecompiler.utility.json import json_read, json_write


def _run_sim_process(
    cmd: list[str], *, log_path: Path, session_dir: Path
) -> tuple[int, str]:
    result = run_tool(
        cmd,
        log_path=log_path,
        timeout=tool_timeout("sim_case"),
        session_dir=session_dir,
    )
    return result.returncode, result.output


class VerilatorSimStep(BaseStep):
    """Compile RTL to a simulation binary and run it.

    Requires workspace["testbench"] to point to a valid testbench file.
    Sub-steps: compile → simulate → report
    Success condition: the selected suite reports an explicit success outcome.
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
        cpp_sources = sim_cpp_sources(workspace)
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

        # The number of jobs is not part of what gets built.
        jobs = ["-j", str(_setting_int(workspace, "sim_build_jobs", 0, minimum=0))]
        cmd = [
            _verilator_cmd(), "--cc", "--exe", "--build",
            "-Wno-fatal",
            "--timing",
            "--trace",
            *(coverage.BUILD_ARGS if _coverage_requested(workspace) else ()),
            *verilator_incdir_args(workspace),
            *verilator_library_args(workspace),
            *verilator_define_args(workspace),
            *_sim_cflags_args(workspace),
            *_sim_ldflags_args(workspace),
            "--top", top,
            "--Mdir", str(obj_dir),
            "-o", str(sim_bin),
        ] + files + cpp_sources
        build_inputs = [*files, *cpp_sources]

        rebuild_note = ""
        clean = clean_build_requested()
        if clean:
            rebuild_note = "clean build requested: rebuilding from scratch\n"
        elif workspace.get("sim_reuse_binary"):
            # A program is reused only while it is what this build would produce.
            reason = stale_reason(sim_bin, obj_dir, cmd, build_inputs)
            if reason is None:
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
            rebuild_note = f"not reusing {sim_bin}: {reason}; rebuilding\n"

        # Objects in the build directory were made with the recorded command.
        # With the same command make rebuilds only what changed; with another
        # one (flags, sources, top) nothing in there can be trusted.
        incremental = not clean and obj_dir.is_dir() and same_command(sim_bin, cmd)
        if obj_dir.exists() and not incremental:
            shutil.rmtree(obj_dir)
        stamp_path(sim_bin).unlink(missing_ok=True)
        log_path = Path(step.log["dir"]) / "log.txt"
        result = run_tool(
            [cmd[0], *cmd[1:4], *jobs, *cmd[4:]],
            log_path=log_path,
            timeout=tool_timeout("sim_compile"),
            session_dir=Path(step.log["dir"]),
        )
        ok = result.returncode == 0
        if ok:
            record_build(sim_bin, obj_dir, cmd, build_inputs)
        if rebuild_note:
            previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            log_path.write_text(rebuild_note + previous, encoding="utf-8")
        info: dict[str, Any] = {"build": "incremental" if incremental else "clean"}
        if rebuild_note:
            info["rebuilt"] = rebuild_note.strip()
        update_substep_ok(step, SimSubFlowEnum.compile.value, ok, info=info)
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
        history_json = Path(step.report["dir"]) / "history.json"
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
            json_write(
                str(cases_json),
                {"schema_version": 1, "suite": suite, "run_id": "", "cases": []},
            )
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
            json_write(
                str(cases_json),
                {"schema_version": 1, "suite": suite, "run_id": "", "cases": []},
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

        settings = tool_config(workspace)
        dump_waveform = _bool_workspace_value(settings.get("sim_dump_waveform"), True)
        collect_coverage = _coverage_requested(workspace)
        # The model only counts; the harness has to write the counts out.
        coverage_unavailable = ""
        if collect_coverage and not coverage.harness_writes_coverage(sim_cpp_sources(workspace)):
            collect_coverage = False
            coverage_unavailable = (
                "the simulation harness of this SoC does not write coverage data; "
                "update the SoC resource"
            )
        case_jobs = min(_setting_int(workspace, "sim_case_jobs", 1, minimum=1), max(len(cases), 1))

        def run_case(case: dict[str, Any]) -> tuple[dict[str, Any], str]:
            case_name = str(case["name"])
            image = str(case["image"])
            logs = _case_logs(case_root, run_case_root, output_cases_root, case_name)
            run_args, wave = _apply_case_wave_arg(
                case_name,
                image,
                list(case["args"]),
                logs["output_dir"] / "wave.vcd",
                dump=dump_waveform,
            )
            coverage_data = logs["output_dir"] / coverage.MERGED_NAME
            if collect_coverage:
                coverage_data.unlink(missing_ok=True)
                run_args = [*run_args, "--coverage", str(coverage_data)]

            if image and not Path(image).exists():
                output = f"image not found: {image}\n"
                rc = 1
            else:
                rc, output = _run_sim_process(
                    [str(sim_bin), *run_args],
                    log_path=logs["output_log"],
                    # Side by side, each case needs a session of its own.
                    session_dir=logs["output_dir"] if case_jobs > 1 else Path(step.log["dir"]),
                )

            case_ok, validation = _case_output_ok(case_name, image, rc, output)
            if (
                case_ok
                and _arg_present(run_args, "--diff")
                and _difftest_pass_counts(output) is None
            ):
                case_ok = False
            metrics = _sim_case_metrics(run_args, rc, output, case_ok)
            if _is_coremark_case(case_name, image):
                metrics.update(_coremark_metrics(workspace, output, case_ok))
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

            case_report = {
                "name": case_name,
                "suite": suite,
                "image": image,
                "image_sha256": _case_image_sha256(image),
                "program": _sim_case_program(workspace, image),
                "returncode": rc,
                "ok": case_ok,
                "log": str(logs["output_log"]),
                "latest_log": str(logs["output_log"]),
                "report_log": str(logs["latest_log"]),
                "run_log": str(logs["run_log"]),
                "wave": wave,
                "run_id": run_id,
            }
            if collect_coverage and coverage_data.is_file():
                # Until the cases are merged; the report keeps only the sum.
                case_report["coverage_data"] = str(coverage_data)
            if validation:
                case_report["validation"] = validation
            if metrics:
                case_report["metrics"] = metrics
            if not case_ok:
                case_report["failure"] = _simulation_failure(
                    returncode=rc,
                    output=output,
                    validation=validation,
                    metrics=metrics,
                    wave=wave,
                )
            return case_report, (
                f"[{case_name}] status={'PASS' if case_ok else 'FAIL'} rc={rc} suite={suite} image={image or '-'} log={logs['output_log']} wave={wave} run_log={logs['run_log']}"
            )

        for case_report, summary_line in _map_in_order(
            cases,
            run_case,
            case_jobs,
            lambda case: output_cases_root / str(case["name"]),
        ):
            cases_report.append(case_report)
            summary_lines.append(summary_line)
            if not case_report["ok"]:
                all_ok = False
                failed_cases.append(str(case_report["name"]))

        summary_text = "\n".join(summary_lines) + "\n"
        sim_log.write_text(summary_text, encoding="utf-8")
        (run_root / "log.txt").write_text(summary_text, encoding="utf-8")
        previous_run = _previous_simulation_run(runs_root, suite)
        regression = _simulation_regression(previous_run, cases_report)
        cases_payload = {
            "schema_version": 1,
            "suite": suite,
            "run_id": run_id,
            "cases": cases_report,
            "regression": regression,
        }
        report_dir = Path(step.report["dir"])
        if collect_coverage:
            ownership = rtl_ownership_map(workspace)
            cases_payload["coverage"] = coverage.brief(
                coverage.write_report(
                    [
                        case.pop("coverage_data")
                        for case in cases_report
                        if case.get("coverage_data")
                    ],
                    report_dir=report_dir,
                    merged_path=Path(step.output["dir"]) / coverage.MERGED_NAME,
                    ownership_of=lambda source: rtl_source_ownership(workspace, source, ownership),
                    cases=len(cases_report),
                ),
                report_dir,
            )
        elif coverage_unavailable:
            cases_payload["coverage"] = coverage.brief(
                coverage.write_unavailable(report_dir, coverage_unavailable), report_dir
            )
            summary_text += f"ecc-fe: coverage not collected: {coverage_unavailable}\n"
            sim_log.write_text(summary_text, encoding="utf-8")
        else:
            (report_dir / coverage.REPORT_NAME).unlink(missing_ok=True)
        json_write(str(cases_json), cases_payload)
        json_write(str(run_root / "cases.json"), cases_payload)
        _prune_run_history(runs_root, _setting_int(workspace, "sim_history_limit", 20, minimum=1))
        json_write(str(history_json), _simulation_history(runs_root, run_id))
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
                "regression": regression,
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
        if isinstance(cases_payload, dict) and cases_payload.get("regression"):
            payload["regression"] = cases_payload.get("regression")
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
