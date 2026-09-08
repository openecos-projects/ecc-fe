from __future__ import annotations

import shlex


def error_record(error: str, **fields: object) -> dict[str, object]:
    return {"kind": "error", "error": error, **fields}


def disclosure_cmd(
    command: str,
    workspace: str | None = None,
    *,
    project: str | None = None,
    run_id: str | None = None,
) -> str:
    parts = [command]
    if workspace:
        parts.append(f"--workspace {shlex.quote(workspace)}")
    elif project:
        parts.append(f"--project {shlex.quote(project)}")
    if run_id is not None:
        parts.append(f"--run-id {shlex.quote(run_id)}")
    return " ".join(parts)


def normalize_state(state: object) -> str:
    return str(state or "unknown").strip().lower()
