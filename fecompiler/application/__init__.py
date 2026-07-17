"""Application services shared by the ecc-fe CLI and runtime transports."""

from fecompiler.application.workspace_service import (
    CliResult,
    WorkspaceApplicationService,
    WorkspaceCliError,
    workspace_application,
)

__all__ = [
    "CliResult",
    "WorkspaceApplicationService",
    "WorkspaceCliError",
    "workspace_application",
]
