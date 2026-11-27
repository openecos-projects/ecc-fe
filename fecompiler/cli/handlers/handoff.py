"""`ecc-fe handoff`: describe a finished run for whoever takes the design next.

The handoff manifest is the one contract between the frontend and a consumer
such as ECC. It names the user's design only (a CPU project hands off the CPU,
not the SoC harness it is simulated in), with everything needed to compile it
the way the frontend did, and records what the frontend concluded about it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fecompiler.allflow.profile import GENERIC_RTL, user_top_module
from fecompiler.allflow.steps import step_definitions
from fecompiler.cli.core.inputs import CommandInput, HandoffInput
from fecompiler.cli.core.records import (
    FAILED_STATES,
    error_record,
    failure_record,
    flow_state,
    normalize_state,
)
from fecompiler.cli.core.types import CommandContext, CommandResult
from fecompiler.cli.core.version_info import ecc_fe_version
from fecompiler.cli.workspace_access import load_existing_workspace, read_json_object
from fecompiler.tools.common.filelist import Filelist, parse_filelist
from fecompiler.tools.common.sv_module import (
    macro_names,
    module_port_contract_from_files,
    strip_sv_comments,
)
from fecompiler.tools.common.tool_config import config_list, config_text, resolve_config_path
from fecompiler.utility.program import invoked

HANDOFF_SCHEMA_VERSION = 1
HANDOFF_KIND = "ecc-fe-handoff"
MANIFEST_NAME = "handoff.json"
FILELIST_NAME = "design.f"
# Where a bundled handoff keeps the timing constraints.
CONSTRAINTS_DIR = "constraints"
# Defines the frontend adds itself when it elaborates and lints.
FRONTEND_DEFINES = ("SYNTHESIS",)
# Time scale the frontend gives modules that do not declare one.
FRONTEND_TIMESCALE = "1ns/1ps"

_INCLUDE = re.compile(r'^\s*`include\s+"([^"]+)"', re.MULTILINE)
_CLOCK_PORT = re.compile(r"^(?:clk|clock)(?:_i|_in)?$|^(?:i_|in_)?(?:clk|clock)$", re.IGNORECASE)


def handoff(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, HandoffInput)
    try:
        workspace = load_existing_workspace(context.workspace_dir)
        if workspace is None:
            return CommandResult.err(
                [
                    error_record(
                        "workspace_not_found",
                        workspace=context.workspace_dir,
                        remediation_cmd=invoked("ecc-fe run"),
                    )
                ]
            )
        flow = read_json_object(Path(workspace["flow_path"]))
        parameters = read_json_object(Path(workspace["parameters_path"]))
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [failure_record("workspace_unreadable", error, workspace=context.workspace_dir)]
        )

    steps = _flow_records(workspace, flow)
    blockers = [
        {"step": step["step"], "reason": reason}
        for step in steps
        if (reason := _blocker(step)) is not None
    ]
    partial = None if blockers else _partial_suite_blocker(parameters, steps)
    if partial is not None:
        blockers.append(partial)
    if blockers and not command_input.allow_incomplete:
        advice = (
            {
                "reason": "the CPU passed a selection of its test suite, not all of it",
                # A project run keeps the settings it was created with.
                **(
                    {
                        "remediation_cmd": invoked("ecc-fe param set sim.cases all"),
                        "then_cmd": invoked("ecc-fe run"),
                    }
                    if context.workspace_mode
                    else {
                        "new_run_cmd": invoked(
                            "ecc-fe run --run-id <new-run> --set sim.cases=all"
                        ),
                        "then_cmd": invoked("ecc-fe handoff --run-id <new-run>"),
                    }
                ),
            }
            if partial is not None
            else {
                "reason": "every step must have succeeded on the current inputs",
                "remediation_cmd": invoked("ecc-fe run --resume"),
            }
        )
        return CommandResult.err(
            [
                error_record(
                    "handoff_not_ready",
                    workspace=context.workspace_dir,
                    blockers=blockers,
                    override="--allow-incomplete",
                    **advice,
                )
            ]
        )

    try:
        design_inputs = _design_inputs(parameters)
    except ValueError as error:
        return CommandResult.err(
            [failure_record("handoff_inputs_unavailable", error, workspace=context.workspace_dir)]
        )

    constraints = _constraints_file(command_input, workspace)
    if constraints is not None and not constraints.is_file():
        return CommandResult.err(
            [
                error_record(
                    "handoff_sdc_not_found",
                    path=str(constraints),
                    reason="named by --sdc"
                    if command_input.sdc
                    else "named by design.sdc of this run",
                )
            ]
        )

    destination = _destination(command_input, context)
    try:
        destination.mkdir(parents=True, exist_ok=True)
        # A manifest from an earlier call must not outlive a failed one.
        (destination / MANIFEST_NAME).unlink(missing_ok=True)
        rtl = (
            _bundle(design_inputs, destination)
            if command_input.bundle
            else _reference(design_inputs)
        )
        (destination / FILELIST_NAME).write_text(_filelist_text(rtl), encoding="utf-8")
        rtl["filelist"] = FILELIST_NAME
        design = _design(parameters, workspace, design_inputs)
        handed_constraints = {
            "sdc": _handed_off_constraints(
                constraints, destination, bundle=command_input.bundle
            )
        }
        check = (
            {"status": "skipped", "reason": "--no-check"}
            if not command_input.check
            else _standalone_check(rtl, design, destination)
        )
        if check["status"] == "fail" and not command_input.allow_incomplete:
            return CommandResult.err(
                [
                    error_record(
                        "handoff_design_incomplete",
                        path=str(destination),
                        reason="the design as described does not elaborate on its own",
                        top=design["top"],
                        errors=check.get("errors"),
                        log=check.get("log"),
                        override="--allow-incomplete",
                    )
                ]
            )
        if check["status"] == "fail":
            blockers.append({"step": "handoff", "reason": "standalone elaboration failed"})
        manifest = {
            "schema_version": HANDOFF_SCHEMA_VERSION,
            "kind": HANDOFF_KIND,
            "generator": {"name": "ecc-fe", "version": ecc_fe_version()},
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "source": {
                "project": None if context.workspace_mode else context.project_dir,
                "run": None if context.workspace_mode else (context.run_id or "default"),
                "workspace": context.workspace_dir,
            },
            "design": design,
            # Added within schema version 1; `design.sdc` stays null there.
            "constraints": handed_constraints,
            "rtl": rtl,
            "standalone_check": check,
            "excluded": design_inputs["excluded"],
            "flow": {
                "status": "incomplete" if blockers else "complete",
                "state": flow_state(
                    [str(step["state"]) for step in steps], running=False
                ),
                "blockers": blockers,
                "steps": steps,
                "lint": _lint_conclusion(Path(context.workspace_dir)),
            },
            "tools": _tools(steps),
            "conventions": {
                "frontend_defines": list(FRONTEND_DEFINES),
                "default_timescale": FRONTEND_TIMESCALE,
            },
        }
        path = destination / MANIFEST_NAME
        path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError as error:
        return CommandResult.err(
            [failure_record("handoff_write_failed", error, path=str(destination))]
        )
    return CommandResult.ok(
        [
            {
                "kind": "handoff",
                "status": manifest["flow"]["status"],
                "path": str(path),
                "filelist": str(destination / FILELIST_NAME),
                "bundled": bool(command_input.bundle),
                "top": manifest["design"]["top"],
                "rtl_files": len(rtl["files"]),
                "headers": len(rtl["headers"]),
                "unresolved_includes": len(rtl["unresolved_includes"]),
                "standalone_check": check["status"],
                "sdc": (handed_constraints["sdc"] or {}).get("path"),
                "view_cmd": f"cat {shlex.quote(str(path))}",
            }
        ]
    )


# ── readiness ─────────────────────────────────────────────────────────────────


def _flow_records(workspace: dict[str, Any], flow: dict[str, Any]) -> list[dict[str, Any]]:
    from fecompiler.cli.commands import _stale_step_reasons

    raw_steps = flow.get("steps", [])
    stale = _stale_step_reasons(workspace, raw_steps)
    records: list[dict[str, Any]] = []
    for step in raw_steps if isinstance(raw_steps, list) else []:
        if not isinstance(step, dict):
            continue
        name = str(step.get("name", ""))
        provenance = step.get("provenance") if isinstance(step.get("provenance"), dict) else {}
        summary = _qor_summary(Path(str(workspace["directory"])), name)
        score = summary.get("score") if isinstance(summary.get("score"), dict) else {}
        records.append(
            {
                "step": name,
                "tool": str(step.get("tool", "")),
                "state": normalize_state(step.get("state")),
                "stale": name in stale,
                "stale_reason": stale.get(name),
                "quality": summary.get("quality_status"),
                "score": score.get("value"),
                "signature": provenance.get("signature"),
                "input_fingerprint": provenance.get("input_fingerprint"),
                "finished_at": provenance.get("finished_at"),
            }
        )
    return records


def _blocker(step: dict[str, Any]) -> str | None:
    state = str(step["state"])
    if state in FAILED_STATES:
        return "failed"
    if state != "success":
        return f"not finished ({state})"
    if step["stale"]:
        return f"stale: {step['stale_reason']}"
    return None


def _partial_suite_blocker(
    parameters: dict[str, Any], steps: list[dict[str, Any]]
) -> dict[str, str] | None:
    """What stands in the way when a CPU was simulated on part of its suite.

    A run simulates a quick selection of the CPU test suite unless it is set
    to run all of it (``sim.cases``). The selection says little about the
    CPU, so it is not what a design is handed off on. A design with its own
    testbench has no suite to complete.
    """
    if str(parameters.get("frontend_design_kind", "")).strip() == "generic_rtl":
        return None
    if not any(step["step"] == "sim" for step in steps):
        return None
    if parameters.get("sim_build_all_programs") or parameters.get("sim_all_tests"):
        return None
    return {"step": "sim", "reason": "ran a selection of the CPU test suite, not all of it"}


def _qor_summary(workspace: Path, step: str) -> dict[str, Any]:
    from fecompiler.allflow.steps import step_directory

    try:
        return read_json_object(step_directory(workspace, step) / "analysis" / "qor_summary.json")
    except (OSError, TypeError, ValueError, KeyError):
        return {}


def _lint_conclusion(workspace: Path) -> dict[str, Any] | None:
    from fecompiler.allflow.steps import step_directory

    try:
        summary = read_json_object(
            step_directory(workspace, "lint") / "report" / "lint_summary.json"
        )
    except (OSError, TypeError, ValueError, KeyError):
        return None
    counts = summary.get("summary") if isinstance(summary.get("summary"), dict) else {}
    baseline = summary.get("baseline") if isinstance(summary.get("baseline"), dict) else {}
    return {
        "status": summary.get("status"),
        "errors": counts.get("errors"),
        "warnings": counts.get("warnings"),
        "actionable_diagnostics": counts.get("actionable_diagnostics"),
        "baseline_gate": baseline.get("gate", "off"),
        "new_warnings": baseline.get("new", 0),
    }


def _tools(steps: list[dict[str, Any]]) -> dict[str, Any]:
    from fecompiler.cli.commands import _tool_version

    used = {step["step"] for step in steps}
    names = dict.fromkeys(
        executable
        for definition in step_definitions()
        if definition.name in used
        for executable in definition.executables
    )
    tools: dict[str, Any] = {}
    for name in names:
        path = shutil.which(name)
        tools[name] = {
            "path": str(Path(path).resolve()) if path else None,
            "version": _tool_version(path) if path else None,
        }
    return tools


# ── what is handed off ────────────────────────────────────────────────────────


def _design_inputs(parameters: dict[str, Any]) -> dict[str, Any]:
    """Collect the user's design from the filelist it was created with.

    A CPU project hands off the CPU filelist; the SoC harness around it is a
    simulation environment and is only counted. An RTL project hands off its
    one filelist.
    """
    is_cpu = str(parameters.get("frontend_design_kind", "")).strip() != "generic_rtl"
    cpu_filelist = str(parameters.get("cpu_filelist", "") or "").strip()
    if is_cpu and cpu_filelist:
        source, field = cpu_filelist, "cpu_filelist"
    else:
        source, field = str(parameters.get("input_filelist", "") or "").strip(), "input_filelist"
    origin = str(parameters.get("origin_verilog", "") or "").strip()
    if source and field == "input_filelist" and is_cpu:
        # A CPU workspace points input_filelist at the merged CPU+SoC list.
        raise ValueError("this workspace has no CPU filelist to hand off")
    if source:
        parsed = parse_filelist(source)
        if not parsed.filelists:
            raise ValueError(f"filelist not found: {parsed.path}")
        if parsed.missing:
            raise ValueError(
                "files listed in the filelist were not found: "
                + ", ".join(str(path) for path in parsed.missing)
            )
    elif origin and Path(origin).is_file():
        path = Path(origin).resolve()
        parsed = Filelist(
            path=path, filelists=(), files=(path,), missing=(), incdirs=(), defines=()
        )
    else:
        raise ValueError("the workspace records no RTL input")
    if not parsed.sources:
        raise ValueError("the filelist lists no .v or .sv source file")

    # The frontend also searches the directory of every source and listed
    # header. That is written out here, so a consumer needs no such rule.
    incdirs = list(
        dict.fromkeys(
            [
                *parsed.incdirs,
                *(path.parent for path in parsed.headers),
                *(path.parent for path in parsed.sources),
            ]
        )
    )
    headers, unresolved = _include_closure([*parsed.sources, *parsed.headers], incdirs)
    headers = list(dict.fromkeys([*parsed.headers, *headers]))
    soc_filelist = str(parameters.get("soc_filelist", "") or "").strip()
    excluded: dict[str, Any] = {}
    if is_cpu and soc_filelist:
        excluded["soc_harness"] = {
            "filelist": soc_filelist,
            "rtl_files": len(parse_filelist(soc_filelist).sources),
            "reason": "simulation environment of a CPU project, not part of the design",
        }
    return {
        "is_cpu": is_cpu,
        "source_filelist": source or None,
        "sources": list(parsed.sources),
        "headers": headers,
        "incdirs": incdirs,
        "libdirs": list(parsed.libdirs),
        "libexts": list(parsed.libexts),
        "defines": list(parsed.defines),
        "unresolved_includes": unresolved,
        "not_passed_on": list(parsed.unsupported),
        "excluded": excluded,
    }


def _include_closure(
    files: list[Path], incdirs: list[Path]
) -> tuple[list[Path], list[dict[str, str]]]:
    """Follow `include directives to every header the design reads."""
    found: dict[Path, None] = {}
    unresolved: list[dict[str, str]] = []
    pending = list(files)
    visited: set[Path] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        try:
            text = strip_sv_comments(current.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        for name in _INCLUDE.findall(text):
            target = next(
                (
                    candidate.resolve()
                    for candidate in (current.parent / name, *(base / name for base in incdirs))
                    if candidate.is_file()
                ),
                None,
            )
            if target is None:
                unresolved.append({"include": name, "from": str(current)})
            elif target not in visited:
                found[target] = None
                pending.append(target)
    return list(found), unresolved


def _design(
    parameters: dict[str, Any], workspace: dict[str, Any], inputs: dict[str, Any]
) -> dict[str, Any]:
    top = user_top_module(
        {**parameters, "frontend_design_kind": "cpu_core" if inputs["is_cpu"] else GENERIC_RTL}
    )
    clock, clock_source = _clock_port(parameters, inputs, top)
    frequency = parameters.get("Frequency max [MHz]")
    try:
        frequency = float(frequency) if frequency not in (None, "") else None
    except (TypeError, ValueError):
        frequency = None
    return {
        "name": str(parameters.get("Design", "") or ""),
        "kind": "cpu" if inputs["is_cpu"] else "rtl",
        "top": top,
        "clock_port": clock,
        # "parameter": set for the project; "detected": the top's only
        # clock-named input; None: not known, the consumer must be told.
        "clock_port_source": clock_source,
        "frequency_mhz": frequency,
        "parameters": config_list(workspace, "design_parameters"),
        # Always null in schema version 1; see the manifest's `constraints`.
        "sdc": None,
    }


def _constraints_file(command_input: HandoffInput, workspace: dict[str, Any]) -> Path | None:
    """The SDC to hand off: the one given now, else the run's ``design.sdc``."""
    if command_input.sdc:
        return Path(command_input.sdc).expanduser().resolve()
    configured = config_text(workspace, "design_sdc").strip()
    return resolve_config_path(workspace, configured) if configured else None


def _handed_off_constraints(
    constraints: Path | None, destination: Path, *, bundle: bool
) -> dict[str, str] | None:
    """Describe the constraints file; the flow passes it on without reading it."""
    directory = destination / CONSTRAINTS_DIR
    if directory.exists():
        shutil.rmtree(directory)
    if constraints is None:
        return None
    if not bundle:
        return {"path": str(constraints), "sha256": _sha256(constraints)}
    directory.mkdir(parents=True)
    target = directory / constraints.name
    shutil.copy2(constraints, target)
    return {"path": (Path(CONSTRAINTS_DIR) / constraints.name).as_posix(), "sha256": _sha256(target)}


def _clock_port(
    parameters: dict[str, Any], inputs: dict[str, Any], top: str
) -> tuple[str | None, str | None]:
    _, ports = module_port_contract_from_files(
        inputs["sources"], top, defined_macros=macro_names(inputs["defines"])
    )
    if not ports:
        _, ports = module_port_contract_from_files(inputs["sources"], top)
    inputs_by_name = {
        str(port["name"]): port for port in ports if port.get("direction") == "input"
    }
    # In a CPU project the project clock names the SoC harness's port.
    configured = "" if inputs["is_cpu"] else str(parameters.get("Clock", "") or "").strip()
    if configured and (not ports or configured in inputs_by_name):
        return configured, "parameter"
    candidates = [name for name in inputs_by_name if _CLOCK_PORT.fullmatch(name)]
    if len(candidates) == 1:
        return candidates[0], "detected"
    return None, None


# ── output ────────────────────────────────────────────────────────────────────


def _destination(command_input: HandoffInput, context: CommandContext) -> Path:
    if command_input.output_dir:
        return Path(command_input.output_dir).expanduser().resolve()
    if context.workspace_mode:
        return Path(context.workspace_dir) / "handoff"
    return Path(context.project_dir) / "handoff" / (context.run_id or "default")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reference(inputs: dict[str, Any]) -> dict[str, Any]:
    """Describe the design by absolute paths, where it is."""
    return {
        "bundled": False,
        "root": None,
        "files": [{"path": str(path), "sha256": _sha256(path)} for path in inputs["sources"]],
        "headers": [{"path": str(path), "sha256": _sha256(path)} for path in inputs["headers"]],
        "incdirs": [str(path) for path in inputs["incdirs"]],
        "libdirs": [str(path) for path in inputs["libdirs"]],
        "libexts": inputs["libexts"],
        "defines": inputs["defines"],
        "unresolved_includes": inputs["unresolved_includes"],
        "not_passed_on": inputs["not_passed_on"],
        "source_filelist": inputs["source_filelist"],
    }


def _bundle(inputs: dict[str, Any], destination: Path) -> dict[str, Any]:
    """Copy the design below ``rtl/`` and describe it by relative paths.

    The layout below the common directory of all inputs is kept, so files
    with the same name do not collide and relative includes keep working.
    """
    everything = [*inputs["sources"], *inputs["headers"], *inputs["incdirs"], *inputs["libdirs"]]
    common = Path(os.path.commonpath([str(path) for path in everything]))
    if common.is_file():
        common = common.parent
    root = destination / "rtl"
    if root.exists():
        shutil.rmtree(root)

    def placed(path: Path) -> str:
        relative = path.relative_to(common)
        return (Path("rtl") / relative).as_posix() if str(relative) != "." else "rtl"

    def copy(path: Path) -> dict[str, str]:
        target = destination / placed(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return {"path": placed(path), "sha256": _sha256(target)}

    root.mkdir(parents=True)
    files = [copy(path) for path in inputs["sources"]]
    headers = [copy(path) for path in inputs["headers"]]
    for directory in inputs["libdirs"]:
        # A library directory is searched by module name: take its RTL along.
        for candidate in sorted(directory.iterdir()) if directory.is_dir() else []:
            if candidate.is_file() and candidate.suffix.lower() in {".v", ".sv", ".vh", ".svh"}:
                copy(candidate)
    for directory in (*inputs["incdirs"], *inputs["libdirs"]):
        (destination / placed(directory)).mkdir(parents=True, exist_ok=True)
    return {
        "bundled": True,
        "root": "rtl",
        "files": files,
        "headers": headers,
        "incdirs": [placed(path) for path in inputs["incdirs"]],
        "libdirs": [placed(path) for path in inputs["libdirs"]],
        "libexts": inputs["libexts"],
        "defines": inputs["defines"],
        "unresolved_includes": inputs["unresolved_includes"],
        "not_passed_on": inputs["not_passed_on"],
        "source_filelist": inputs["source_filelist"],
    }


def _standalone_check(
    rtl: dict[str, Any], design: dict[str, Any], destination: Path
) -> dict[str, Any]:
    """Elaborate exactly what the manifest describes, and nothing else.

    Inside a flow the design is compiled together with its surroundings and
    with include directories the frontend adds by itself. This compiles it
    from the manifest alone, the way a consumer will.
    """
    slang = os.getenv("ECOS_SLANG", "").strip() or shutil.which("slang")
    if not slang:
        return {"status": "skipped", "reason": "slang is not installed"}
    command = [
        slang,
        "--top", str(design["top"]),
        "--allow-use-before-declare",
        "--timescale", FRONTEND_TIMESCALE,
    ]
    for define in (*FRONTEND_DEFINES, *rtl["defines"]):
        command.extend(["-D", str(define)])
    for incdir in rtl["incdirs"]:
        command.extend(["-I", str(incdir)])
    for libdir in rtl["libdirs"]:
        command.extend(["-y", str(libdir)])
    for extension in rtl["libexts"]:
        command.extend(["-Y", str(extension)])
    for override in design["parameters"]:
        command.extend(["-G", str(override)])
    command.extend(str(entry["path"]) for entry in rtl["files"])
    log_path = destination / "standalone_check.log"
    try:
        result = subprocess.run(
            command,
            cwd=destination,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "skipped", "reason": f"slang could not be run: {error}"}
    output = (result.stdout or "") + (result.stderr or "")
    log_path.write_text(output, encoding="utf-8")
    errors = len(re.findall(r"(?m): error: ", output))
    return {
        "status": "pass" if result.returncode == 0 else "fail",
        "tool": "slang",
        "errors": errors,
        "log": log_path.name,
        "defines": [*FRONTEND_DEFINES, *rtl["defines"]],
    }


def _filelist_text(rtl: dict[str, Any]) -> str:
    """Render the design as a filelist; paths are relative to it when bundled."""
    lines = ["# Generated by ecc-fe handoff. Paths are "
             + ("relative to this file." if rtl["bundled"] else "absolute.")]
    lines.extend(f"+incdir+{path}" for path in rtl["incdirs"])
    lines.extend(f"+define+{define}" for define in rtl["defines"])
    lines.extend(f"-y {path}" for path in rtl["libdirs"])
    if rtl["libexts"]:
        lines.append("+libext+" + "+".join(rtl["libexts"]))
    lines.extend(entry["path"] for entry in rtl["files"])
    return "\n".join(lines) + "\n"
