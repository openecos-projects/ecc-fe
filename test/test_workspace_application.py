from __future__ import annotations

import argparse

from fecompiler.application.workspace_service import (
    CliResult,
    WorkspaceApplicationService,
    workspace_application,
)
from fecompiler.cli import workspace as workspace_cli


def test_cli_workspace_facade_uses_shared_application_service() -> None:
    assert workspace_cli.workspace_application is workspace_application
    assert workspace_cli.WorkspaceApplicationService is WorkspaceApplicationService


def test_application_service_executes_workspace_command_namespace() -> None:
    result = workspace_application.execute_namespace(
        "catalog-list",
        argparse.Namespace(),
    )

    assert result.cmd == "catalog_list"
    assert result.response == "success"
    assert "cores" in result.data


def test_application_service_normalizes_unexpected_errors() -> None:
    result = workspace_application.call(
        "run-step",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert result == CliResult(
        cmd="run_step",
        response="error",
        data={},
        message=["boom"],
    )
