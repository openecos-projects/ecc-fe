"""CLI entry point for the ``ecc-fe`` command."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from fecompiler.resources import activate_managed_resources
from fecompiler.utility.program import invoked, program_name

_REMOVED_DIRECT_FORM = """\
Error: the direct form `ecc-fe --design <name> --top <top> ...` has been removed.

Create a project and run it instead:

  ecc-fe init <dir> --design <name> [--rtl <file> | --cpu-filelist <file>]
                 [--cpu-top-module <module>]
  cd <dir> && ecc-fe run

`ecc-fe init --help` and `ecc-fe workspace create --help` list every creation
option; `ecc-fe run --workspace <dir> --only sim --force` re-runs one step.
"""


def _is_removed_direct_form(raw_argv: Sequence[str], root_commands: set[str]) -> bool:
    return bool(raw_argv) and raw_argv[0] not in root_commands and any(
        argument == "--design" or argument.startswith("--design=")
        for argument in raw_argv
    )


def _run_root_cli(raw_argv: Sequence[str]) -> int:
    import typer

    from fecompiler.cli.app import app

    command = typer.main.get_command(app)
    try:
        result = command.main(
            args=list(raw_argv),
            prog_name=program_name(),
            standalone_mode=False,
        )
    except typer.Exit as error:
        return int(error.exit_code or 0)
    except typer.Abort:
        typer.echo("Aborted!", err=True)
        return 130
    except Exception as error:
        show = getattr(error, "show", None)
        exit_code = getattr(error, "exit_code", None)
        if callable(show) and isinstance(exit_code, int):
            show()
            return exit_code
        raise
    return int(result or 0)


def run(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    activate_managed_resources()
    if raw_argv[:1] == ["rpc"]:
        from fecompiler.cli.rpc import run as run_rpc

        return run_rpc(raw_argv[1:])
    if raw_argv[:1] == ["workspace"]:
        from fecompiler.cli.workspace import run as run_workspace

        return run_workspace(raw_argv[1:])
    root_commands = {
        "version",
        "init",
        "run",
        "check",
        "doctor",
        "status",
        "log",
        "config",
        "resource",
        "param",
        "catalog",
        "report",
        "runs",
        "handoff",
    }
    if _is_removed_direct_form(raw_argv, root_commands):
        print(invoked(_REMOVED_DIRECT_FORM), file=sys.stderr, end="")
        return 2
    return _run_root_cli(raw_argv)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
