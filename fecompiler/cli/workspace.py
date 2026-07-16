"""Compatibility facade for the ecc-fe workspace CLI.

The command engine lives in :mod:`fecompiler.application.workspace_service` so
the interactive CLI and the JSON-RPC runtime can invoke the same operations.
Private helpers remain available here while downstream users migrate.
"""

from __future__ import annotations

from typing import Any

from fecompiler.application import workspace_service as _service

CliResult = _service.CliResult
WorkspaceApplicationService = _service.WorkspaceApplicationService
WorkspaceCliError = _service.WorkspaceCliError
build_parser = _service.build_parser
build_typer_app = _service.build_typer_app
main = _service.main
run = _service.run
workspace_application = _service.workspace_application

__all__ = [
    "CliResult",
    "WorkspaceApplicationService",
    "WorkspaceCliError",
    "build_parser",
    "build_typer_app",
    "main",
    "run",
    "workspace_application",
]


def __getattr__(name: str) -> Any:
    """Preserve imports of legacy private helpers during the transition."""

    return getattr(_service, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_service)))


if __name__ == "__main__":
    main()
