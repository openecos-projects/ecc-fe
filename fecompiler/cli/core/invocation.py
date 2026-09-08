from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import typer

from fecompiler.cli.core.inputs import CommandInput
from fecompiler.cli.core.records import error_record
from fecompiler.cli.core.types import CommandContext, CommandResult
from fecompiler.cli.project.config import ProjectConfigError, load_project_config
from fecompiler.cli.project.layout import is_workspace, resolve_run_dir, template_dir
from fecompiler.cli.rendering.render import render_result

CommandHandler = Callable[[CommandInput, CommandContext], CommandResult]


def execute_command(
    command: str,
    command_input: CommandInput,
    handler: CommandHandler,
) -> None:
    workspace = command_input.workspace
    project = command_input.project
    if workspace is not None and (
        project is not None or command_input.run_id is not None
    ):
        context = CommandContext(
            workspace_dir=str(Path(workspace).expanduser().resolve()),
            output_mode=command_input.output_mode,
            project_dir=str(Path(project or workspace).expanduser().resolve()),
            config_dir=str(Path(project or workspace).expanduser().resolve()),
            project=project,
        )
        result = CommandResult.err(
            [error_record("project_workspace_conflict")], exit_code=2
        )
        render_result(command, result, context.output_mode)
        raise typer.Exit(code=result.exit_code)

    project_dir = os.path.abspath(os.path.expanduser(project or "."))
    workspace_mode = workspace is not None
    selected_run_id = command_input.run_id
    config_dir = project_dir
    selected_template: str | None = None
    if workspace is not None:
        workspace_dir = str(Path(workspace).expanduser().resolve())
        project_dir = workspace_dir
        config_dir = workspace_dir
    else:
        config = None
        try:
            config = load_project_config(project_dir)
        except ProjectConfigError:
            pass
        project_layout = bool(
            command_input.run_id is not None
            or (config is not None and config.uses_project_layout)
            or (Path(project_dir) / ".ecc-fe" / "template").is_dir()
        )
        if is_workspace(project_dir) and not project_layout:
            workspace_dir = project_dir
            workspace_mode = True
        elif project_layout or project is not None:
            if selected_run_id is None and config is not None:
                try:
                    configured = config.run_id
                except ProjectConfigError:
                    configured = "default"
                selected_run_id = None if configured == "default" else configured
            workspace_dir, selected_run_id = resolve_run_dir(
                project_dir, selected_run_id
            )
            selected_template = template_dir(project_dir)
        else:
            workspace_dir = project_dir
            workspace_mode = True
    resolved_workspace_dir = (
        str(Path(workspace_dir).expanduser().resolve())
        if workspace_mode
        else os.path.abspath(os.path.expanduser(workspace_dir))
    )
    context = CommandContext(
        workspace_dir=resolved_workspace_dir,
        output_mode=command_input.output_mode,
        project_dir=project_dir,
        config_dir=config_dir,
        project=project,
        run_id=selected_run_id,
        workspace_mode=workspace_mode,
        template_dir=selected_template,
    )
    try:
        result = handler(command_input, context)
    except Exception as error:  # noqa: BLE001 - command boundaries return stable error records.
        result = CommandResult.err([error_record(str(error), command=command)])
    render_result(command, result, context.output_mode)
    raise typer.Exit(code=result.exit_code)
