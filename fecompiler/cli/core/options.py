from __future__ import annotations

from typing import Annotated

import typer

WorkspaceOption = Annotated[
    str | None,
    typer.Option(
        "--workspace", help="Frontend workspace directory (default: current directory)"
    ),
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
