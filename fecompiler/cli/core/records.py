from __future__ import annotations

import re
import shlex

from fecompiler.utility.program import invoked

_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]*")
# Used when a failure reaches the output without a code of its own.
UNCLASSIFIED_ERROR = "command_failed"


def error_record(error: str, **fields: object) -> dict[str, object]:
    """Return an error record whose ``error`` is always a stable code.

    A free-form sentence is never a code: it is reported as ``message`` under
    :data:`UNCLASSIFIED_ERROR`, so consumers can always match on ``error``.
    """
    if _ERROR_CODE.fullmatch(error):
        return {"kind": "error", "error": error, **fields}
    return {"kind": "error", "error": UNCLASSIFIED_ERROR, "message": error, **fields}


def failure_record(
    code: str, cause: BaseException | str, **fields: object
) -> dict[str, object]:
    """Return an error record for *code* that carries *cause* as its message."""
    return {"kind": "error", "error": code, "message": str(cause), **fields}


def disclosure_cmd(
    command: str,
    workspace: str | None = None,
    *,
    project: str | None = None,
    run_id: str | None = None,
) -> str:
    parts = [invoked(command)]
    if workspace:
        parts.append(f"--workspace {shlex.quote(workspace)}")
    elif project:
        parts.append(f"--project {shlex.quote(project)}")
    if run_id is not None:
        parts.append(f"--run-id {shlex.quote(run_id)}")
    return " ".join(parts)


def normalize_state(state: object) -> str:
    return str(state or "unknown").strip().lower()


FAILED_STATES = frozenset({"incomplete", "invalid"})


def flow_state(states: list[str], *, running: bool) -> str:
    """Summarise step states as one of running/interrupted/failed/success/pending."""
    if running:
        return "running"
    if "interrupted" in states:
        return "interrupted"
    if any(state in FAILED_STATES for state in states):
        return "failed"
    if states and all(state == "success" for state in states):
        return "success"
    return "pending"
