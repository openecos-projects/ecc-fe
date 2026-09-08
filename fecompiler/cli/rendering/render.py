from __future__ import annotations

import json
import sys
from typing import TextIO

from fecompiler.cli.core.types import CommandResult, OutputMode


def render_result(
    command: str,
    result: CommandResult,
    mode: OutputMode,
    *,
    file: TextIO | None = None,
) -> None:
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
    _render_text(command, result, target)


def _render_text(command: str, result: CommandResult, target: TextIO) -> None:
    if command == "resource.env" and len(result.records) == 1:
        content = result.records[0].get("content")
        if content is not None:
            print(str(content), file=target)
            return
    if command == "report.qor" and result.records:
        _render_qor_text(result, target)
        return
    print(f"[{command}]", file=target)
    for index, record in enumerate(result.records):
        if index:
            print(file=target)
        for key, value in record.items():
            if value is None:
                continue
            label = key.removesuffix("_cmd")
            if key == "content":
                print(str(value), file=target)
            elif isinstance(value, (dict, list, tuple)):
                encoded = json.dumps(value, ensure_ascii=False)
                print(f"  {label}: {encoded}", file=target)
            else:
                print(f"  {label}: {value}", file=target)


def _render_qor_text(result: CommandResult, target: TextIO) -> None:
    report = result.records[0]
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
    for error in result.records[1:]:
        if error.get("error"):
            print(f"  error: {error['error']}", file=target)


def _plain_record(record: dict[str, object]) -> str:
    fields: list[str] = []
    for key, value in record.items():
        if value is None:
            continue
        if isinstance(value, (dict, list, tuple)):
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            raw = str(value)
        fields.append(f"{key}={_quote_plain(raw)}")
    return " ".join(fields)


def _quote_plain(value: str) -> str:
    if any(char.isspace() for char in value) or any(char in value for char in '\\"='):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return value
