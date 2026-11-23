"""Result and error types shared by every workspace operation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CliResult:
    cmd: str
    response: str
    data: dict[str, Any]
    message: list[str]


class WorkspaceCliError(Exception):
    def __init__(self, cmd: str, response: str, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.result = CliResult(cmd=cmd, response=response, data=data or {}, message=[message])


def _command_to_cmd(command: str) -> str:
    return {
        "create": "create_workspace",
        "load": "load_workspace",
        "run-flow": "rtl2gds",
        "run-step": "run_step",
        "get-info": "get_info",
        "get-home": "home_page",
    }.get(command, "workspace")


def _exit_code(response: str) -> int:
    return 0 if response in {"success", "warning"} else 1
