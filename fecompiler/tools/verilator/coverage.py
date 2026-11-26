"""Line and toggle coverage of a simulation, merged over its cases.

Verilator writes one coverage data file per run of a simulation built with
coverage. Each line of it is one coverage point with a count:

    C '<fields>' <count>

where ``<fields>`` is a sequence of ``\\x01key\\x02value`` pairs: ``f`` the
source file, ``l`` the line, ``t`` the kind of point (``line``, ``toggle``,
...), ``o`` what it observes, ``h`` the instance. Files of several runs are
merged by adding the counts of equal points; a point is covered when its
count is above zero. The merged file is written in the same format, so
``verilator_coverage --annotate`` can read it.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

REPORT_SCHEMA_VERSION = 1
REPORT_NAME = "coverage.json"
MERGED_NAME = "coverage.dat"
# The kinds asked of Verilator, and reported even when a design has no point.
KINDS = ("line", "toggle")
BUILD_ARGS = ("--coverage-line", "--coverage-toggle")
_HEADER = "# SystemC::Coverage-3"
_POINT = re.compile(r"^C '(.*)' (\d+)\s*$")

Points = dict[str, int]


def read_points(path: str | Path) -> Points:
    """Return the points of one coverage data file; a missing file has none."""
    points: Points = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            for line in stream:
                match = _POINT.match(line)
                if match is not None:
                    key = match.group(1)
                    points[key] = points.get(key, 0) + int(match.group(2))
    except OSError:
        return {}
    return points


def merge_points(files: Iterable[str | Path]) -> Points:
    """Add up the counts of the points of several coverage data files."""
    merged: Points = {}
    for path in files:
        for key, count in read_points(path).items():
            merged[key] = merged.get(key, 0) + count
    return merged


def write_points(path: str | Path, points: Mapping[str, int]) -> None:
    lines = [_HEADER, *(f"C '{key}' {count}" for key, count in sorted(points.items()))]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def point_fields(key: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in key.split("\x01"):
        name, separator, value = part.partition("\x02")
        if separator:
            fields[name] = value
    return fields


def _tally() -> dict[str, dict[str, int]]:
    return {kind: {"covered": 0, "total": 0} for kind in KINDS}


def _finish(tally: Mapping[str, Mapping[str, int]]) -> dict[str, dict[str, Any]]:
    return {
        kind: {
            "covered": numbers["covered"],
            "total": numbers["total"],
            # No point of a kind is "nothing to cover", not 0 %.
            "percent": (
                round(100.0 * numbers["covered"] / numbers["total"], 2)
                if numbers["total"]
                else None
            ),
        }
        for kind, numbers in tally.items()
    }


def summarise(
    points: Mapping[str, int],
    *,
    ownership_of: Callable[[str], str] | None = None,
    exclude: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Count covered points by kind: in total, per ownership and per file.

    Files in *exclude* (a testbench is not the design) are left out
    everywhere. Kinds other than line and toggle are counted when present.
    """
    excluded = {os.path.abspath(str(path)) for path in exclude}
    total = _tally()
    by_file: dict[str, dict[str, dict[str, int]]] = {}
    for key, count in points.items():
        fields = point_fields(key)
        kind, source = fields.get("t", ""), fields.get("f", "")
        if not kind or not source or os.path.abspath(source) in excluded:
            continue
        for tally in (total, by_file.setdefault(source, _tally())):
            numbers = tally.setdefault(kind, {"covered": 0, "total": 0})
            numbers["total"] += 1
            numbers["covered"] += count > 0
    owners: dict[str, dict[str, dict[str, int]]] = {}
    files: list[dict[str, Any]] = []
    for source in sorted(by_file):
        ownership = ownership_of(source) if ownership_of is not None else ""
        if ownership:
            owner = owners.setdefault(ownership, _tally())
            for kind, numbers in by_file[source].items():
                slot = owner.setdefault(kind, {"covered": 0, "total": 0})
                slot["covered"] += numbers["covered"]
                slot["total"] += numbers["total"]
        files.append({"file": source, "ownership": ownership, **_finish(by_file[source])})
    return {
        "kinds": _finish(total),
        "by_ownership": {name: _finish(owners[name]) for name in sorted(owners)},
        "files": files,
    }


# The owners whose code a simulation is there to exercise. The SoC harness a
# CPU is simulated in, the adapter that fits it there and generated code are
# the fixture, not the design.
DESIGN_OWNERSHIPS = ("cpu", "design", "third_party")


def design_coverage(report: Mapping[str, Any]) -> dict[str, dict[str, Any]] | None:
    """Return line and toggle coverage of the design under test, or ``None``.

    ``line`` adds up Verilator's line and branch points, which are both its
    line coverage. ``None`` when the report holds no usable data or the
    design has no point at all.
    """
    if report.get("status") != "ok":
        return None
    owners = report.get("by_ownership")
    if not isinstance(owners, Mapping):
        return None
    totals = {"line": [0, 0], "toggle": [0, 0]}
    for owner in DESIGN_OWNERSHIPS:
        kinds = owners.get(owner)
        if not isinstance(kinds, Mapping):
            continue
        for kind, target in (("line", "line"), ("branch", "line"), ("toggle", "toggle")):
            numbers = kinds.get(kind)
            if not isinstance(numbers, Mapping):
                continue
            try:
                covered, total = int(numbers["covered"]), int(numbers["total"])
            except (KeyError, TypeError, ValueError):
                return None
            if covered < 0 or total < covered:
                return None
            totals[target][0] += covered
            totals[target][1] += total
    if not any(total for _, total in totals.values()):
        return None
    return {
        kind: {
            "covered": covered,
            "total": total,
            "percent": round(100.0 * covered / total, 2) if total else None,
        }
        for kind, (covered, total) in totals.items()
    }


def write_report(
    data_files: Iterable[str | Path],
    *,
    report_dir: Path,
    merged_path: Path,
    ownership_of: Callable[[str], str] | None = None,
    exclude: Iterable[str | Path] = (),
    cases: int,
) -> dict[str, Any]:
    """Merge *data_files*, write the merged data and the report; return the report.

    The data of the single cases is removed once it is merged: a design of
    some size writes megabytes per case, and the merged file holds the sum.
    """
    present = [str(path) for path in data_files if Path(path).is_file()]
    points = merge_points(present)
    for path in present:
        if Path(path) != merged_path:
            Path(path).unlink(missing_ok=True)
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "ok" if points else "empty",
        "cases": cases,
        "cases_with_data": len(present),
        "data": str(merged_path) if points else "",
        **summarise(points, ownership_of=ownership_of, exclude=exclude),
    }
    if points:
        merged_path.parent.mkdir(parents=True, exist_ok=True)
        write_points(merged_path, points)
    else:
        report["reason"] = "the simulation wrote no coverage data"
    _write_json(report_dir / REPORT_NAME, report)
    return report


def write_unavailable(report_dir: Path, reason: str) -> dict[str, Any]:
    """Record that coverage was asked for and could not be collected."""
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "unavailable",
        "reason": reason,
        "kinds": _finish(_tally()),
        "by_ownership": {},
        "files": [],
    }
    _write_json(report_dir / REPORT_NAME, report)
    return report


def brief(report: Mapping[str, Any], report_dir: Path) -> dict[str, Any]:
    """What the case report carries: the totals and where the rest is."""
    summary: dict[str, Any] = {
        "status": report.get("status"),
        "report": str(report_dir / REPORT_NAME),
        "kinds": report.get("kinds"),
    }
    if report.get("reason"):
        summary["reason"] = report["reason"]
    if report.get("data"):
        summary["data"] = report["data"]
    return summary


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def harness_writes_coverage(sources: Iterable[str | Path]) -> bool:
    """Whether a C++ simulation harness saves coverage when asked to.

    The model only counts; the program that drives it has to write the
    counts out. A harness that does names the coverage interface.
    """
    for source in sources:
        try:
            if "coveragep()" in Path(source).read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return False


__all__ = [
    "BUILD_ARGS",
    "DESIGN_OWNERSHIPS",
    "KINDS",
    "MERGED_NAME",
    "REPORT_NAME",
    "brief",
    "design_coverage",
    "harness_writes_coverage",
    "merge_points",
    "point_fields",
    "read_points",
    "summarise",
    "write_points",
    "write_report",
    "write_unavailable",
]
