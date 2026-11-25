"""Compare a simulation run with the previous one, and keep the run history."""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fecompiler.tools.verilator.lint import (  # noqa: F401
    VerilatorLintStep,
    build_lint_summary,
    parse_verilator_diagnostics,
)
from fecompiler.utility.json import json_read


def _previous_simulation_run(runs_root: Path, suite: str) -> dict[str, Any]:
    if not runs_root.exists():
        return {}
    for run_dir in sorted((path for path in runs_root.iterdir() if path.is_dir()), reverse=True):
        payload = json_read(run_dir / "cases.json")
        if payload.get("suite") == suite and isinstance(payload.get("cases"), list):
            return payload
    return {}


def _case_cycles(case: dict[str, Any]) -> int | None:
    metrics = case.get("metrics", {})
    cycles = metrics.get("cycles") if isinstance(metrics, dict) else None
    return cycles if isinstance(cycles, int) and not isinstance(cycles, bool) else None


def _case_image_sha256(image: str) -> str:
    path = Path(image)
    if not image or not path.is_file():
        return ""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _simulation_regression(
    previous: dict[str, Any],
    current_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    previous_cases = previous.get("cases", []) if isinstance(previous, dict) else []
    previous_by_name = {
        str(case.get("name", "")): case
        for case in previous_cases
        if isinstance(case, dict) and str(case.get("name", ""))
    }
    current_by_name = {
        str(case.get("name", "")): case
        for case in current_cases
        if str(case.get("name", ""))
    }
    if not previous:
        return {
            "has_baseline": False,
            "baseline_run_id": "",
            "new_failures": [],
            "persistent_failures": [],
            "fixed": [],
            "added": [],
            "removed": [],
            "cycle_changes": [],
        }

    new_failures = sorted(
        name
        for name, case in current_by_name.items()
        if not bool(case.get("ok"))
        and (name not in previous_by_name or bool(previous_by_name[name].get("ok")))
    )
    persistent_failures = sorted(
        name
        for name, case in current_by_name.items()
        if not bool(case.get("ok"))
        and name in previous_by_name
        and not bool(previous_by_name[name].get("ok"))
    )
    fixed = sorted(
        name
        for name, case in current_by_name.items()
        if bool(case.get("ok"))
        and name in previous_by_name
        and not bool(previous_by_name[name].get("ok"))
    )
    cycle_changes: list[dict[str, Any]] = []
    for name in sorted(current_by_name.keys() & previous_by_name.keys()):
        previous_image_sha256 = str(previous_by_name[name].get("image_sha256", ""))
        current_image_sha256 = str(current_by_name[name].get("image_sha256", ""))
        if not current_image_sha256 or current_image_sha256 != previous_image_sha256:
            continue
        previous_cycles = _case_cycles(previous_by_name[name])
        current_cycles = _case_cycles(current_by_name[name])
        if previous_cycles is None or current_cycles is None:
            continue
        delta = current_cycles - previous_cycles
        cycle_changes.append({
            "name": name,
            "image_sha256": current_image_sha256,
            "previous": previous_cycles,
            "current": current_cycles,
            "delta": delta,
            "delta_percent": (delta / previous_cycles * 100) if previous_cycles else None,
        })

    return {
        "has_baseline": bool(previous),
        "baseline_run_id": str(previous.get("run_id", "")) if previous else "",
        "new_failures": new_failures,
        "persistent_failures": persistent_failures,
        "fixed": fixed,
        "added": sorted(current_by_name.keys() - previous_by_name.keys()) if previous else [],
        "removed": sorted(previous_by_name.keys() - current_by_name.keys()) if previous else [],
        "cycle_changes": cycle_changes,
    }


def _simulation_history(runs_root: Path, latest_run_id: str) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    if runs_root.exists():
        run_dirs = sorted((path for path in runs_root.iterdir() if path.is_dir()), reverse=True)[:100]
        for run_dir in run_dirs:
            payload = json_read(run_dir / "cases.json")
            cases = payload.get("cases", []) if isinstance(payload, dict) else []
            if not isinstance(cases, list):
                continue
            failed_cases = [
                str(case.get("name", ""))
                for case in cases
                if isinstance(case, dict) and not bool(case.get("ok"))
            ]
            runs.append({
                "run_id": str(payload.get("run_id", run_dir.name)),
                "suite": str(payload.get("suite", "")),
                "ok": bool(cases) and not failed_cases,
                "cases": len(cases),
                "failed_cases": failed_cases,
                "regression": payload.get("regression", {}),
            })
    return {
        "latest_run_id": latest_run_id,
        "runs": runs,
    }


def _prune_run_history(runs_root: Path, limit: int) -> None:
    """Keep the newest *limit* runs; run directories are named by their time."""
    runs = sorted(path for path in runs_root.iterdir() if path.is_dir())
    for stale in runs[: max(len(runs) - limit, 0)]:
        shutil.rmtree(stale, ignore_errors=True)


def _run_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")
