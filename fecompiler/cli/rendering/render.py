from __future__ import annotations

import json
import sys
from typing import TextIO

from fecompiler.cli.core.types import CommandResult, OutputMode
from fecompiler.utility.program import program_name


def render_result(
    command: str,
    result: CommandResult,
    mode: OutputMode,
    *,
    file: TextIO | None = None,
    error_file: TextIO | None = None,
) -> None:
    """Write *result* in *mode*.

    Structured modes write every record, errors included, to *file* so a
    consumer reads one stream. Text mode is for people: error records go to
    *error_file* (stderr) and everything else to *file* (stdout).
    """
    target = file or sys.stdout
    if mode == OutputMode.JSON:
        print(
            json.dumps({"records": list(result.records)}, ensure_ascii=False),
            file=target,
        )
        return
    if mode == OutputMode.JSONL:
        for record in result.records:
            print(json.dumps(record, ensure_ascii=False), file=target)
        return
    if mode == OutputMode.PLAIN:
        for record in result.records:
            print(_plain_record(record), file=target)
        return
    _render_text(command, result, target, error_file or file or sys.stderr)


def _render_text(
    command: str, result: CommandResult, target: TextIO, error_target: TextIO
) -> None:
    errors = [record for record in result.records if record.get("kind") == "error"]
    records = [record for record in result.records if record.get("kind") != "error"]
    if command == "resource.env" and len(records) == 1 and "content" in records[0]:
        print(str(records[0]["content"]), file=target)
    elif command == "report.qor" and records:
        _render_qor_text(records[0], target)
    elif command in _COMPACT_TEXT and records:
        print(f"[{command}]", file=target)
        _render_fields(records[0], target)
        for record in records[1:]:
            print(f"  {_COMPACT_TEXT[command](record)}", file=target)
    elif records:
        print(f"[{command}]", file=target)
        for index, record in enumerate(records):
            if index:
                print(file=target)
            _render_fields(record, target)
    for record in errors:
        print(f"{program_name()}: error: {record.get('error')}", file=error_target)
        _render_fields(
            {key: value for key, value in record.items() if key not in {"kind", "error"}},
            error_target,
        )


def _run_line(record: dict[str, object]) -> str:
    parts = [
        f"{record.get('run')}:",
        str(record.get("state")),
        f"{record.get('steps_done', 0)}/{record.get('steps_total', 0)} steps",
    ]
    if record.get("failed_step"):
        parts.append(f"failed in {record['failed_step']}")
    if record.get("modified"):
        parts.append(str(record["modified"]))
    overrides = record.get("overrides")
    if isinstance(overrides, dict) and overrides:
        parts.append(" ".join(f"{key}={value}" for key, value in overrides.items()))
    if record.get("selected"):
        parts.append("(selected)")
    if record.get("message"):
        parts.append(str(record["message"]))
    return "  ".join(parts)


def _arrow(base: object, run: object) -> str:
    def show(value: object) -> str:
        return "-" if value is None else _scalar(value)

    return show(base) if base == run else f"{show(base)} -> {show(run)}"


def _compare_line(record: dict[str, object]) -> str:
    if record.get("kind") == "qor_metric":
        delta = record.get("delta")
        change = f" ({delta:+g})" if isinstance(delta, (int, float)) else ""
        return (
            f"{record.get('step')}.{record.get('metric')}: "
            f"{_arrow(record.get('base'), record.get('run'))}{change}"
            f"  {record.get('verdict')}"
        )
    if record.get("kind") != "qor_step":
        return " ".join(f"{key}={value}" for key, value in record.items())
    parts = [
        f"{record.get('step')}:",
        _arrow(record.get("base_state"), record.get("run_state")),
        f"quality {_arrow(record.get('base_quality'), record.get('run_quality'))}",
        f"score {_arrow(record.get('base_score'), record.get('run_score'))}",
    ]
    if record.get("same_inputs") is False:
        parts.append("(inputs or settings differ)")
    return "  ".join(parts)


def _flow_line(record: dict[str, object]) -> str:
    if record.get("in_flow"):
        return (
            f"{int(record.get('index', 0)) + 1}  {str(record.get('step')):<8}  "
            f"{str(record.get('tool')):<10}  {record.get('directory')}"
        )
    return f"-  {str(record.get('step')):<8}  not in this flow: {record.get('reason')}"


def _failure_line(record: dict[str, object]) -> str:
    where = str(record.get("source") or "")
    if where and record.get("line"):
        where += f":{record['line']}"
        if record.get("column"):
            where += f":{record['column']}"
    subject = where or str(record.get("case") or record.get("gate") or record.get("module") or "")
    parts = [f"[{record.get('category')}]"]
    if subject:
        parts.append(subject)
    if record.get("rule"):
        parts.append(str(record["rule"]))
    if record.get("failure"):
        parts.append(str(record["failure"]))
    parts.append(str(record.get("message")))
    line = "  ".join(parts)
    for key in ("detail", "evidence", "advice", "log"):
        if record.get(key):
            line += f"\n      {key}: {record[key]}"
    return line


_COMPACT_TEXT = {
    "report.failure": _failure_line,
    "runs.list": _run_line,
    "report.compare": _compare_line,
    "flow.list": _flow_line,
}


def _render_fields(record: dict[str, object], target: TextIO) -> None:
    # Field names are printed as they are in the structured modes, so a
    # follow-up command (`status_cmd`) never collides with a value (`status`).
    for key, value in record.items():
        if value is None:
            continue
        if key == "content":
            print(str(value), file=target)
        elif isinstance(value, (dict, list, tuple)):
            encoded = json.dumps(value, ensure_ascii=False)
            print(f"  {key}: {encoded}", file=target)
        else:
            print(f"  {key}: {_scalar(value)}", file=target)


def _scalar(value: object) -> str:
    # Booleans read the same in every mode: `true`/`false`, as in JSON.
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _render_qor_text(report: dict[str, object], target: TextIO) -> None:
    print("[report.qor]", file=target)
    for key in ("workspace", "design", "top_module", "status"):
        value = report.get(key)
        if value is not None:
            print(f"  {key}: {value}", file=target)
    steps = report.get("steps", {})
    qor_steps = report.get("qor", {})
    if isinstance(steps, dict) and isinstance(qor_steps, dict):
        for step, state in steps.items():
            quality = "unavailable"
            score_text = ""
            qor = qor_steps.get(step)
            if isinstance(qor, dict):
                summary = qor.get("summary")
                if isinstance(summary, dict):
                    quality = str(summary.get("quality_status", quality))
                    score = summary.get("score")
                    if isinstance(score, dict) and score.get("value") is not None:
                        maximum = score.get("maximum", 100)
                        score_text = f" score={score['value']}/{maximum}"
            print(
                f"  {step}: {state} quality={quality}{score_text}",
                file=target,
            )
    if report.get("path"):
        print(f"  path: {report['path']}", file=target)


def _plain_record(record: dict[str, object]) -> str:
    fields: list[str] = []
    for key, value in record.items():
        if value is None:
            continue
        if isinstance(value, (dict, list, tuple)):
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            raw = _scalar(value)
        fields.append(f"{key}={_quote_plain(raw)}")
    return " ".join(fields)


def _quote_plain(value: str) -> str:
    if any(char.isspace() for char in value) or any(char in value for char in '\\"='):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return value
