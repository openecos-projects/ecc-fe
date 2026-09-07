from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer

from fecompiler.cli.core.inputs import CommandInput
from fecompiler.cli.core.records import error_record
from fecompiler.cli.core.types import CommandContext, CommandResult
from fecompiler.cli.rendering.render import render_result

CommandHandler = Callable[[CommandInput, CommandContext], CommandResult]


def execute_command(
    command: str,
    command_input: CommandInput,
    handler: CommandHandler,
) -> None:
    workspace = command_input.workspace or "."
    context = CommandContext(
        workspace_dir=str(Path(workspace).expanduser().resolve()),
        output_mode=command_input.output_mode,
    )
    try:
        result = handler(command_input, context)
    except Exception as error:  # noqa: BLE001 - command boundaries return stable error records.
        result = CommandResult.err([error_record(str(error), command=command)])
    render_result(command, result, context.output_mode)
    raise typer.Exit(code=result.exit_code)
