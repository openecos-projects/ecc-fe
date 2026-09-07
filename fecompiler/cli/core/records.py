from __future__ import annotations

import shlex


def error_record(error: str, **fields: object) -> dict[str, object]:
    return {"kind": "error", "error": error, **fields}


def disclosure_cmd(command: str, workspace: str | None = None) -> str:
    if not workspace:
        return command
    return f"{command} --workspace {shlex.quote(workspace)}"


def normalize_state(state: object) -> str:
    return str(state or "unknown").strip().lower()
