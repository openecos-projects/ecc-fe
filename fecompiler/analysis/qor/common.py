"""What every step's QoR builder shares: record shapes, gates, hotspots, validation."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fecompiler.tools.common.rtl_ownership import rtl_ownership_map, user_rtl_ownership

SCHEMA_REVISION = "frontend-quality-gates-v1"


class _QorResult:
    def __init__(self, *, source_available: bool = False) -> None:
        self.source_available = source_available
        self.metrics: list[dict[str, Any]] = []
        self.gates: list[dict[str, Any]] = []
        self.hotspots: list[dict[str, Any]] = []
        self.details: list[dict[str, Any]] = []
        self.missing_metrics: list[dict[str, Any]] = []
        self.comparison: dict[str, Any] = {}
        self.score: dict[str, Any] | None = None


def _rtl_lines(step: Any, workspace: dict[str, Any], *, user_owned: bool) -> int:
    """Count the lines of the prepared RTL, all of it or the part the user owns.

    A bundled core is third-party code: its user owns none of it and the
    count is 0.
    """
    ownership = user_rtl_ownership(workspace)
    owners = rtl_ownership_map(workspace)
    if not owners:
        # The record handed to analysis does not always name the manifest.
        manifest = Path(step.directory).parent / "prepare_fe" / "output" / "prepared_inputs.json"
        owners = rtl_ownership_map({**workspace, "prepared_manifest": str(manifest)})
    lines = 0
    for path, owner in owners.items():
        if user_owned and owner != ownership:
            continue
        try:
            with open(path, "rb") as stream:
                lines += sum(1 for _ in stream)
        except OSError:
            continue
    return lines


def _metric(
    metric_id: str,
    display_name: str,
    value: float,
    unit: str,
    category: str,
    direction: str,
    scope: str,
    source: dict[str, str],
    *,
    gate: bool = False,
) -> dict[str, Any]:
    return {
        "id": metric_id,
        "display_name": display_name,
        "value": value,
        "unit": unit,
        "category": category,
        "direction": direction,
        "scope": scope,
        "corner": None,
        "project_role": "gate" if gate else "trend",
        "step_role": "primary" if gate else "secondary",
        "analysis_group": category,
        "rating": {"gate": gate, "score": False, "trend": not gate},
        "confidence": "high",
        "source": source,
    }


def _append_optional_metric(
    result: _QorResult,
    metric_id: str,
    display_name: str,
    value: Any,
    unit: str,
    category: str,
    direction: str,
    scope: str,
    source: dict[str, str],
) -> None:
    numeric_value = _optional_number(value)
    if numeric_value is None:
        return
    result.metrics.append(
        _metric(
            metric_id,
            display_name,
            numeric_value,
            unit,
            category,
            direction,
            scope,
            source,
        )
    )


def _gate(
    gate_id: str,
    title: str,
    actual: float,
    operator: str,
    expected: float,
    source: dict[str, str],
    *,
    state: str | None = None,
) -> dict[str, Any]:
    passed = _compare(actual, operator, expected)
    return {
        "id": gate_id,
        "title": title,
        "state": state or ("pass" if passed else "failed"),
        "metrics": [
            {
                "id": gate_id,
                "actual": actual,
                "operator": operator,
                "expected": expected,
                "source": source,
            }
        ],
        "evidence": [source],
    }


def _hotspot(
    metric_id: str,
    display_name: str,
    severity: str,
    value: Any,
    source: dict[str, str],
    description: str,
) -> dict[str, Any]:
    return {
        "kind": "frontend_quality",
        "severity": severity,
        "metric_id": metric_id,
        "display_name": display_name,
        "value": value,
        "source": source,
        "description": description,
    }


def _hotspot_severity(value: Any) -> str:
    severity = str(value or "").strip().lower()
    if severity in {"error", "fatal", "critical"}:
        return "critical"
    if severity in {"warning", "warn"}:
        return "warning"
    return "info"


def _diagnostic_hotspots(
    report: dict[str, Any], metric_id: str, path: str
) -> list[dict[str, Any]]:
    return [
        _hotspot(
            metric_id,
            str(item.get("code") or item.get("severity") or "Diagnostic"),
            _hotspot_severity(item.get("severity")),
            None,
            _source(
                _relative_report_source(item.get("source"), path),
                f"/diagnostics/{index}",
            ),
            str(item.get("message") or item.get("text") or "Elaboration diagnostic."),
        )
        for index, item in enumerate(_records(report.get("diagnostics"))[:50])
    ]


def _unique_sources(metrics: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    sources: list[dict[str, str]] = []
    for metric in metrics:
        source = _record(metric.get("source"))
        key = (str(source.get("kind", "")), str(source.get("path", "")))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        sources.append({"kind": key[0], "path": key[1]})
    return sources


def _source(path: str, selector: str) -> dict[str, str]:
    return {"kind": "report", "path": path, "selector": selector}


def _relative_report_source(value: Any, fallback: str) -> str:
    path = str(value or "").strip()
    return (
        path
        if path and not Path(path).is_absolute() and ".." not in Path(path).parts
        else fallback
    )


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _records(value: Any) -> list[dict[str, Any]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _valid_count_fields(value: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return all(_is_count(value.get(field)) for field in fields)


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]


def _number(value: Any) -> int | float:
    return (
        value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
    )


def _optional_number(value: Any) -> int | float | None:
    return (
        value
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _compare(actual: float, operator: str, expected: float) -> bool:
    comparators: dict[str, Callable[[int | float, int | float], bool]] = {
        "==": lambda left, right: left == right,
        ">": lambda left, right: left > right,
        ">=": lambda left, right: left >= right,
        "<=": lambda left, right: left <= right,
    }
    return comparators[operator](actual, expected)
