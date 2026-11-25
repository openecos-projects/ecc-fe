"""Say why a flow step failed, from the detail the step left behind.

``doctor`` answers "is the environment usable"; this answers "the tools ran,
so why is the step not a success". The input is the step's
``frontend_detail.json``, which already gathers its state, QoR gates and
normalised results; nothing is run and no log is parsed again unless the
results name no cause at all.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

FAILED_STATES = frozenset({"incomplete", "invalid"})
# How much of a log is searched for a first error when the results name none.
_LOG_SEARCH_BYTES = 256 * 1024
_ERROR_LINE = re.compile(
    # A line that opens with an error marker, or a compiler's
    # "file:line: error: ..." form.
    r"^\s*(?:%Error|\[ERROR\]|ERROR\b|error\b|Error\b|fatal\b"
    r"|Traceback \(most recent call last\)|ecc-fe: .*failed)"
    r"|:\s*(?:fatal )?error\b",
)
_LINE_LIMIT = 300
# States a prepare input contract may have without blocking the flow.
_ACCEPTED_CONTRACT_STATES = frozenset({"ok", "warning", "stub"})

Cause = dict[str, Any]


def diagnose(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``{"reason", "causes", "advice"}`` for the step *detail* describes.

    ``causes`` are ordered from what decided the failure to supporting
    evidence. A step that did not fail has no causes and a reason that says
    which state it is in.
    """
    step = str(detail.get("step") or "")
    state = str(detail.get("state") or "").strip().lower()
    if state not in FAILED_STATES:
        return {"reason": _not_failed_reason(step, state), "causes": [], "advice": []}
    causes: list[Cause] = []
    advice: list[str] = []
    finder = _STEP_CAUSES.get(step)
    if finder is not None:
        finder(detail, causes, advice)
    causes.extend(_failed_gates(detail))
    if not any(cause["category"] != "gate" for cause in causes):
        causes.extend(_log_causes(detail))
    reason = _reason(step, causes)
    return {"reason": reason, "causes": causes, "advice": list(dict.fromkeys(advice))}


def _not_failed_reason(step: str, state: str) -> str:
    if state == "success":
        return f"the {step} step succeeded"
    if state == "ongoing":
        return f"the {step} step did not finish: it is still running or was interrupted"
    if state in {"unstart", "unknown", ""}:
        return f"the {step} step has not run"
    return f"the {step} step is in state {state}"


def _reason(step: str, causes: list[Cause]) -> str:
    decisive = [cause for cause in causes if cause["category"] not in {"gate", "log"}]
    if decisive:
        counts: dict[str, int] = {}
        for cause in decisive:
            counts[cause["category"]] = counts.get(cause["category"], 0) + 1
        parts = [_COUNT_PHRASE[category](count) for category, count in counts.items()]
        return f"the {step} step failed: " + ", ".join(parts)
    if causes:
        return f"the {step} step failed: {causes[0]['message']}"
    return f"the {step} step failed and its results name no cause; read its log"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


_COUNT_PHRASE = {
    "input": lambda count: _plural(count, "input problem"),
    "review_issue": lambda count: _plural(count, "blocking review issue"),
    "tool": lambda count: "a tool could not finish",
    "elab_error": lambda count: _plural(count, "elaboration error"),
    "unresolved_module": lambda count: _plural(count, "unresolved module"),
    "lint_error": lambda count: _plural(count, "lint error"),
    "baseline": lambda count: "the lint baseline could not be used",
    "lint_new_warning": lambda count: _plural(count, "warning")
    + " not in the lint baseline",
    "sim_case": lambda count: _plural(count, "failed case"),
    "sim_build": lambda count: "the simulation did not build",
}


def _cause(category: str, message: str, **fields: Any) -> Cause:
    cause: Cause = {"category": category, "message": _clip(message)}
    cause.update(
        {key: value for key, value in fields.items() if value not in (None, "", 0, False)}
    )
    return cause


def _clip(text: object) -> str:
    return " ".join(str(text or "").split())[:_LINE_LIMIT]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _located(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": str(item.get("source") or ""),
        "line": _int(item.get("line")),
        "column": _int(item.get("column")),
    }


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


# --- per step --------------------------------------------------------------


def _prepare_causes(detail: Mapping[str, Any], causes: list[Cause], advice: list[str]) -> None:
    prepare = _mapping(detail.get("prepare"))
    for contract in _items(prepare.get("contracts")):
        if str(contract.get("status") or "").strip().lower() in _ACCEPTED_CONTRACT_STATES:
            continue
        causes.append(
            _cause(
                "input",
                f"{contract.get('label')}: {contract.get('status')}",
                detail=_clip(contract.get("detail")),
            )
        )
    if causes:
        advice.append("fix the inputs the project names, then run again")


def _review_causes(detail: Mapping[str, Any], causes: list[Cause], advice: list[str]) -> None:
    review = _mapping(detail.get("review"))
    precheck = _mapping(review.get("yosys_precheck") or review.get("structural_probe"))
    if str(precheck.get("status") or "").lower() == "failed" and precheck.get("reason"):
        # What the tool said, before the issues derived from it.
        causes.append(_cause("tool", str(precheck["reason"]), tool="yosys"))
    for issue in _items(review.get("issues")):
        if issue.get("severity") != "error" or issue.get("waived") is True:
            continue
        causes.append(
            _cause(
                "review_issue",
                str(issue.get("title") or issue.get("detail") or "review issue"),
                rule=str(issue.get("category") or ""),
                advice=_clip(issue.get("recommendation")),
                ownership=str(issue.get("ownership") or ""),
                **_located(issue),
            )
        )
    if causes:
        advice.append(
            "fix the issues in your sources; one that is intended can be waived "
            "(review.waivers)"
        )


def _elab_causes(detail: Mapping[str, Any], causes: list[Cause], advice: list[str]) -> None:
    elab = _mapping(detail.get("elab"))
    for diagnostic in _items(elab.get("diagnostics")):
        if diagnostic.get("severity") == "error":
            causes.append(
                _cause("elab_error", str(diagnostic.get("message") or ""), **_located(diagnostic))
            )
    unresolved = elab.get("unresolved_modules")
    for module in unresolved if isinstance(unresolved, list) else []:
        causes.append(
            _cause("unresolved_module", f"module {module} is instantiated but not defined", module=str(module))
        )
    summary = _mapping(elab.get("summary"))
    if summary.get("top_found") is False:
        causes.append(
            _cause("elab_error", f"top module {summary.get('top_module')} was not found in the sources")
        )
    if any(cause["category"] == "unresolved_module" for cause in causes):
        advice.append("add the files that define the missing modules to the design's sources")


def _lint_causes(detail: Mapping[str, Any], causes: list[Cause], advice: list[str]) -> None:
    lint = _mapping(detail.get("lint"))
    diagnostics = _items(lint.get("diagnostics"))
    for diagnostic in diagnostics:
        if diagnostic.get("severity") == "error":
            causes.append(
                _cause(
                    "lint_error",
                    str(diagnostic.get("message") or ""),
                    rule=str(diagnostic.get("code") or ""),
                    ownership=str(diagnostic.get("ownership") or ""),
                    **_located(diagnostic),
                )
            )
    baseline = _mapping(lint.get("baseline"))
    if baseline.get("gate") != "failed":
        return
    if baseline.get("error"):
        causes.append(_cause("baseline", str(baseline["error"]), source=str(baseline.get("path") or "")))
        advice.append("write the baseline again with `ecc-fe report lint-baseline`")
        return
    for diagnostic in diagnostics:
        if diagnostic.get("baseline") == "new" and diagnostic.get("actionable") is True:
            causes.append(
                _cause(
                    "lint_new_warning",
                    str(diagnostic.get("message") or ""),
                    rule=str(diagnostic.get("code") or ""),
                    **_located(diagnostic),
                )
            )
    advice.append(
        "fix the new warnings, waive them (lint.waiver_files), or accept them with "
        "`ecc-fe report lint-baseline`"
    )


def _sim_causes(detail: Mapping[str, Any], causes: list[Cause], advice: list[str]) -> None:
    cases = _items(detail.get("cases"))
    regression = _mapping(_mapping(detail.get("sim")).get("regression"))
    new_failures = regression.get("new_failures")
    newly_failing = set(map(str, new_failures)) if isinstance(new_failures, list) else set()
    for case in cases:
        if case.get("ok") is True:
            continue
        failure = _mapping(case.get("failure"))
        kind = str(failure.get("kind") or "")
        returncode = case.get("returncode")
        message = str(failure.get("message") or "") or (
            f"the simulation exited with status {returncode}"
            if returncode not in (None, 0)
            else "the case did not pass"
        )
        causes.append(
            _cause(
                # A testbench that does not build never became a case that ran.
                "sim_build" if kind in {"compile", "settings"} else "sim_case",
                message,
                case=str(case.get("name") or ""),
                failure=kind,
                evidence=_clip(failure.get("first_error")),
                log=str(case.get("log") or ""),
                wave=str(failure.get("wave") or case.get("wave") or ""),
                # It passed in the previous run of the same suite.
                newly_failing=str(case.get("name") or "") in newly_failing,
            )
        )
    if not causes:
        # No case failed, so the step stopped before or while building.
        for log in _named_logs(detail, ("Build programs log", "Step log", "Tool log")):
            line = _first_error_line(log["path"])
            if line:
                causes.append(_cause("sim_build", line, log=log["path"]))
                break
    kinds = {cause.get("failure") for cause in causes}
    if "timeout" in kinds:
        advice.append("a case that needs more cycles or time has limits to raise (sim.* / ECOS_FE_TIMEOUT_SIM_CASE)")
    if kinds & {"difftest_mismatch", "bad_trap"}:
        advice.append("open the case log and its waveform at the first reported error")
    if kinds & {"fail_pattern", "pass_pattern_missing"}:
        advice.append("the verdict comes from testbench.pass_pattern / testbench.fail_pattern; read the case log for what was printed")


_STEP_CAUSES = {
    "prepare": _prepare_causes,
    "review": _review_causes,
    "elab": _elab_causes,
    "lint": _lint_causes,
    "sim": _sim_causes,
}


# --- shared ----------------------------------------------------------------


def _failed_gates(detail: Mapping[str, Any]) -> list[Cause]:
    summary = _mapping(_mapping(detail.get("qor")).get("summary"))
    causes: list[Cause] = []
    for gate in _items(summary.get("gates")):
        if gate.get("state") != "failed":
            continue
        metric = next(iter(_items(gate.get("metrics"))), {})
        measured = (
            f" (is {metric.get('actual')}, must be {metric.get('operator')} {metric.get('expected')})"
            if metric
            else ""
        )
        causes.append(
            _cause("gate", f"{gate.get('title') or gate.get('id')}{measured}", gate=str(gate.get("id") or ""))
        )
    return causes


def _named_logs(detail: Mapping[str, Any], order: Iterable[str]) -> list[dict[str, str]]:
    logs = {
        str(item.get("label") or ""): str(item.get("path") or "")
        for item in _items(detail.get("logs"))
        if item.get("path")
    }
    ordered = [name for name in order if name in logs]
    ordered += [name for name in logs if name not in ordered]
    return [{"label": name, "path": logs[name]} for name in ordered]


def _log_causes(detail: Mapping[str, Any]) -> list[Cause]:
    logs = _named_logs(detail, ("Tool log", "Step log"))
    if detail.get("log"):
        logs.append({"label": "Step log", "path": str(detail["log"])})
    for log in logs:
        line = _first_error_line(log["path"])
        if line:
            return [_cause("log", line, log=log["path"])]
    return []


def _first_error_line(path: str) -> str:
    """Return the first line of a log that reads as an error, or ``""``."""
    try:
        with open(path, "rb") as stream:
            text = stream.read(_LOG_SEARCH_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        if _ERROR_LINE.search(line):
            return _clip(line)
    return ""


__all__ = ["FAILED_STATES", "diagnose"]
