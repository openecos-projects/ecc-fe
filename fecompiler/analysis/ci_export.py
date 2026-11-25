"""Results in the formats CI systems read.

* lint diagnostics as SARIF 2.1.0, for code-scanning views;
* simulation cases as JUnit XML, for test reports.

Both are built from reports the flow has already written
(``lint_summary.json``, ``cases.json``); nothing is run again.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
# The name the base directory has inside a SARIF log.
SARIF_BASE_ID = "SRCROOT"

_VERILATOR_RULE = re.compile(r"[A-Z][A-Z0-9_]+")
# Diagnostics without a rule of their own are filed under their severity.
_NOT_A_RULE = frozenset({"ERROR", "WARNING", "TOOL"})
_BASELINE_STATE = {"known": "unchanged", "new": "new"}
# Characters XML 1.0 cannot carry, and terminal colour sequences.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_NOT_XML = re.compile("[^\t\n\r\x20-퟿-�\U00010000-\U0010ffff]")


def exported_diagnostics(
    diagnostics: Iterable[Mapping[str, Any]], *, everything: bool
) -> list[Mapping[str, Any]]:
    """Return the diagnostics an export carries.

    By default these are the ones the user can act on: diagnostics in the
    user's own sources, and every error. *everything* adds warnings in
    sources the user does not own, such as the SoC harness of a CPU.
    """
    return [
        item
        for item in diagnostics
        if everything or item.get("actionable") is True or item.get("severity") == "error"
    ]


def sarif_log(
    diagnostics: Iterable[Mapping[str, Any]],
    *,
    base_dir: Path,
    tool_name: str = "Verilator",
    tool_uri: str = "https://verilator.org",
    properties: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a SARIF log of lint *diagnostics*.

    A file below *base_dir* is written relative to it, which is what a
    code-scanning service needs to find the file in the repository.
    """
    # Not resolved through symlinks, like the paths it is compared with.
    base = Path(os.path.abspath(base_dir))
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for item in diagnostics:
        code = str(item.get("code") or "").strip() or str(item.get("severity", "")).upper()
        if code not in rules:
            rules[code] = _sarif_rule(code, str(item.get("category") or ""))
        result: dict[str, Any] = {
            "ruleId": code,
            "ruleIndex": list(rules).index(code),
            "level": "error" if item.get("severity") == "error" else "warning",
            "message": {"text": str(item.get("message") or code)},
        }
        location = _sarif_location(item, base)
        if location is not None:
            result["locations"] = [location]
        baseline = _BASELINE_STATE.get(str(item.get("baseline") or ""))
        if baseline is not None:
            result["baselineState"] = baseline
        result["properties"] = {
            key: item[key] for key in ("ownership", "actionable", "category") if key in item
        }
        results.append(result)
    run: dict[str, Any] = {
        "tool": {
            "driver": {
                "name": tool_name,
                "informationUri": tool_uri,
                "rules": list(rules.values()),
            }
        },
        "originalUriBaseIds": {SARIF_BASE_ID: {"uri": base.as_uri() + "/"}},
        "columnKind": "utf16CodeUnits",
        "results": results,
    }
    if properties:
        run["properties"] = dict(properties)
    return {"$schema": SARIF_SCHEMA, "version": SARIF_VERSION, "runs": [run]}


def _sarif_rule(code: str, category: str) -> dict[str, Any]:
    rule: dict[str, Any] = {"id": code}
    if _VERILATOR_RULE.fullmatch(code) and code not in _NOT_A_RULE:
        rule["helpUri"] = f"https://verilator.org/warn/{code}"
    if category:
        rule["properties"] = {"category": category}
    return rule


def _sarif_location(item: Mapping[str, Any], base: Path) -> dict[str, Any] | None:
    source = str(item.get("source") or "").strip()
    if not source:
        return None
    path = Path(source)
    if not path.is_absolute():
        path = base / path
    path = Path(_normalized(path))
    try:
        artifact: dict[str, Any] = {
            "uri": quote(path.relative_to(base).as_posix()),
            "uriBaseId": SARIF_BASE_ID,
        }
    except ValueError:
        artifact = {"uri": path.as_uri()}
    physical: dict[str, Any] = {"artifactLocation": artifact}
    line = _positive(item.get("line"))
    if line:
        region = {"startLine": line}
        column = _positive(item.get("column"))
        if column:
            region["startColumn"] = column
        physical["region"] = region
    return {"physicalLocation": physical}


def _normalized(path: Path) -> str:
    # Remove "." and ".." without following symlinks: the path the user
    # wrote is the one their repository knows.
    return os.path.normpath(str(path))


def _positive(value: object) -> int:
    try:
        number = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def junit_xml(
    cases: Iterable[Mapping[str, Any]],
    *,
    suite: str,
    design: str,
    properties: Mapping[str, Any] | None = None,
    step_error: str | None = None,
) -> tuple[str, dict[str, int]]:
    """Return a JUnit XML document of simulation *cases* and its counts.

    *step_error* is why the step produced no verdict of its own, for example
    a build that failed before any case ran. It becomes one errored test, so
    a report without cases is never read as a pass.
    """
    listed = list(cases)
    counts = {"tests": len(listed), "failures": 0, "errors": 0}
    classname = ".".join(_xml_text(part) for part in (design, suite) if part)
    testsuite = ElementTree.Element("testsuite", {"name": _xml_text(suite or "sim")})
    if properties:
        holder = ElementTree.SubElement(testsuite, "properties")
        for name, value in properties.items():
            if value not in (None, ""):
                ElementTree.SubElement(
                    holder, "property", {"name": name, "value": _xml_text(str(value))}
                )
    for case in listed:
        testcase = ElementTree.SubElement(
            testsuite,
            "testcase",
            {"classname": classname, "name": _xml_text(str(case.get("name") or "case"))},
        )
        if not case.get("ok"):
            counts["failures"] += 1
            _junit_failure(testcase, case)
        log = str(case.get("log") or "").strip()
        if log:
            ElementTree.SubElement(testcase, "system-out").text = f"log: {_xml_text(log)}"
    if step_error and not counts["failures"]:
        counts["tests"] += 1
        counts["errors"] += 1
        testcase = ElementTree.SubElement(
            testsuite, "testcase", {"classname": classname, "name": "sim"}
        )
        ElementTree.SubElement(
            testcase, "error", {"type": "step_failed", "message": _xml_text(step_error)}
        )
    totals = {name: str(value) for name, value in counts.items()}
    testsuite.attrib.update({**totals, "skipped": "0"})
    root = ElementTree.Element("testsuites", {"name": "ecc-fe sim", **totals})
    root.append(testsuite)
    ElementTree.indent(root)
    document = ElementTree.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{document}\n', counts


def _junit_failure(testcase: ElementTree.Element, case: Mapping[str, Any]) -> None:
    failure = case.get("failure")
    details = failure if isinstance(failure, Mapping) else {}
    returncode = case.get("returncode")
    message = str(details.get("message") or "").strip() or (
        f"the simulation exited with status {returncode}"
        if returncode not in (None, 0)
        else "the case did not pass"
    )
    element = ElementTree.SubElement(
        testcase,
        "failure",
        {
            "type": _xml_text(str(details.get("kind") or "failed")),
            "message": _xml_text(message),
        },
    )
    body = [
        str(details[key]).strip()
        for key in ("first_error", "log_tail")
        if str(details.get(key) or "").strip()
    ]
    if body:
        element.text = _xml_text("\n".join(dict.fromkeys(body)))


def _xml_text(value: str) -> str:
    return _NOT_XML.sub("", _ANSI.sub("", value))


__all__ = [
    "SARIF_BASE_ID",
    "SARIF_SCHEMA",
    "SARIF_VERSION",
    "exported_diagnostics",
    "junit_xml",
    "sarif_log",
]
