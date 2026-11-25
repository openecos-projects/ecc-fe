"""Write a step's QoR records, three files that are replaced together."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fecompiler.allflow.steps import find_step_definition
from fecompiler.analysis.qor.common import (
    SCHEMA_REVISION,
    _QorResult,
    _unique_sources,
)
from fecompiler.analysis.qor.elab import (
    _elab_qor,
)
from fecompiler.analysis.qor.fingerprint import (
    _comparison_fingerprint,
    _file_sha256,
)
from fecompiler.analysis.qor.lint import (
    _lint_qor,
)
from fecompiler.analysis.qor.prepare import (
    _prepare_qor,
)
from fecompiler.analysis.qor.review import (
    _review_qor,
)
from fecompiler.analysis.qor.sim import (
    _sim_qor,
)


def write_step_qor(step: Any, workspace: dict[str, Any], success: bool) -> None:
    """Write the standard QoR triplet for one completed frontend step."""
    builder = _STEP_BUILDERS.get(str(step.name))
    result = builder(step, workspace) if builder else _QorResult()
    analysis = step.analysis
    design = str(workspace.get("design", ""))
    context = {
        "comparison": {
            "fingerprint": _comparison_fingerprint(step, workspace, result.comparison),
            "inputs": result.comparison,
        },
    }
    metrics_payload = {
        "schema_version": 3,
        "analysis_revision": SCHEMA_REVISION,
        "tool": str(step.tool),
        "step": str(step.name),
        "design": design,
        "status": "success" if success else "failed",
        "metrics": result.metrics,
        "details": result.details,
        "sources": _unique_sources(result.metrics),
        "integrity": {
            "status": "pass" if result.source_available else "incomplete",
            "invalid_metric_source_ids": [],
            "invalid_detail_ids": [],
        },
        "context": context,
    }
    gates = result.gates
    has_failed_gate = any(gate["state"] == "failed" for gate in gates)
    has_incomplete_gate = any(
        gate["state"] in {"incomplete", "unavailable"} for gate in gates
    )
    if not result.source_available:
        quality_status = "incomplete"
        analysis_status = "incomplete"
    elif has_failed_gate:
        quality_status = "blocked"
        analysis_status = "valid"
    elif has_incomplete_gate or not success:
        quality_status = "incomplete"
        analysis_status = "incomplete"
    else:
        quality_status = "pass"
        analysis_status = "valid"
    summary_payload = {
        "schema_version": 4,
        "analysis_revision": SCHEMA_REVISION,
        "tool": str(step.tool),
        "step": str(step.name),
        "design": design,
        "analysis_status": analysis_status,
        "quality_status": quality_status,
        "metric_count": len(result.metrics),
        "dimensions": sorted({str(metric["category"]) for metric in result.metrics}),
        "gates": gates,
        "missing_metrics": result.missing_metrics,
        "metrics_file": "qor_metrics.json",
        "context": context,
    }
    if result.score is not None:
        summary_payload["score"] = result.score
    hotspots_payload = {
        "schema_version": 3,
        "analysis_revision": SCHEMA_REVISION,
        "tool": str(step.tool),
        "step": str(step.name),
        "design": design,
        "hotspots": result.hotspots,
    }
    generation = _qor_generation(metrics_payload, summary_payload, hotspots_payload)
    metrics_payload["generation"] = generation
    summary_payload["generation"] = generation
    hotspots_payload["generation"] = generation
    _write_qor_triplet_atomic(
        (
            (analysis["qor_metrics"], metrics_payload),
            (analysis["qor_summary"], summary_payload),
            (analysis["qor_hotspots"], hotspots_payload),
        ),
    )


def clear_step_qor(step_directory: str | Path) -> None:
    """Remove QoR and its embedded frontend snapshot before a new run."""
    root = Path(step_directory)
    for path in (
        root / "analysis" / "qor_metrics.json",
        root / "analysis" / "qor_summary.json",
        root / "analysis" / "qor_hotspots.json",
        root / "report" / "frontend_detail.json",
    ):
        path.unlink(missing_ok=True)


def step_qor_source_revision(step: Any) -> str:
    """Return a revision token for the report consumed by a step's QoR builder."""
    path = _step_qor_source_path(step)
    if path is None:
        return "unsupported"
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    identity = {
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
        "changed_ns": stat.st_ctime_ns,
        "sha256": _file_sha256(path),
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _qor_generation(*payloads: dict[str, Any]) -> str:
    content = json.dumps(
        payloads,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _write_qor_triplet_atomic(
    entries: tuple[tuple[str | Path, dict[str, Any]], ...],
) -> None:
    staged: list[tuple[Path, Path]] = []
    originals: dict[Path, bytes | None] = {}
    published: list[Path] = []
    try:
        for file_path, payload in entries:
            destination = Path(file_path)
            originals[destination] = (
                destination.read_bytes() if destination.is_file() else None
            )
            staged.append((destination, _stage_json(destination, payload)))
        for destination, temporary_path in staged:
            os.replace(temporary_path, destination)
            published.append(destination)
    except Exception as exc:
        rollback_errors: list[OSError] = []
        for destination in reversed(published):
            try:
                original = originals[destination]
                if original is None:
                    destination.unlink(missing_ok=True)
                else:
                    _restore_bytes(destination, original)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        message = "failed to persist QoR artifact triplet"
        if rollback_errors:
            message += "; rollback also failed"
        raise OSError(message) from exc
    finally:
        for _, temporary_path in staged:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _stage_json(destination: Path, payload: dict[str, Any]) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        return Path(temporary_name)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _stage_bytes(destination: Path, content: bytes) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".rollback",
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return Path(temporary_name)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _restore_bytes(destination: Path, content: bytes) -> None:
    temporary_path = _stage_bytes(destination, content)
    try:
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


_STEP_BUILDERS: dict[str, Callable[[Any, dict[str, Any]], _QorResult]] = {
    "prepare": _prepare_qor,
    "review": _review_qor,
    "elab": _elab_qor,
    "lint": _lint_qor,
    "sim": _sim_qor,
}


def _step_qor_source_path(step: Any) -> Path | None:
    definition = find_step_definition(str(step.name))
    if definition is None:
        return None
    return Path(step.report["dir"]) / definition.qor_report
