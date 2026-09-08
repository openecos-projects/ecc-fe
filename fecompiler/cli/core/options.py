from __future__ import annotations

from typing import Annotated

import typer

WorkspaceOption = Annotated[
    str | None,
    typer.Option(
        "--workspace", help="Reuse an existing frontend workspace in place"
    ),
]
ProjectOption = Annotated[
    str | None,
    typer.Option(
        "--project", help="ECC-FE project directory (default: current directory)"
    ),
]
RunIdOption = Annotated[
    str | None,
    typer.Option("--run-id", help="Project run id (default: flow.run or default)"),
]
JsonOption = Annotated[
    bool, typer.Option("--json", help="Emit one JSON records envelope")
]
JsonlOption = Annotated[
    bool, typer.Option("--jsonl", help="Emit one JSON object per line")
]
PlainOption = Annotated[
    bool, typer.Option("--plain", help="Emit stable key=value records")
]
