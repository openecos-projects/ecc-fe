from __future__ import annotations

from collections.abc import Callable

from fecompiler.cli.core.records import error_record
from fecompiler.cli.core.types import CommandResult
from fecompiler.utility.workspace_lock import WorkspaceBusyError, workspace_lock


def with_workspace_lock(
    workspace_dir: str, action: Callable[[], CommandResult]
) -> CommandResult:
    """Run a mutating command while holding the workspace lock.

    A workspace owned by another live command is reported as a
    ``workspace_busy`` record instead of being modified underneath it.
    """
    try:
        with workspace_lock(workspace_dir):
            return action()
    except WorkspaceBusyError as error:
        return CommandResult.err(
            [
                error_record(
                    "workspace_busy",
                    workspace=str(error.directory),
                    owner_pid=error.owner.get("pid"),
                )
            ]
        )
