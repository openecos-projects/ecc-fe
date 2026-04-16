"""Step info service — mirrors chipcompiler/tools/ecc/service.py in ecos-studio/ecc.

Stub implementation. Real data is provided by the registered EDA tool.
"""

from __future__ import annotations

from typing import Any

from fecompiler.data.workspace import WorkspaceStep


def get_step_info(workspace: dict[str, Any],
                  step: WorkspaceStep,
                  id: str) -> dict:  # noqa: A002
    """Return resource info for *step* identified by *id*.

    Supported IDs: views, layout, metrics, subflow, analysis, maps, checklist, sta
    """
    _ = workspace, step, id
    return {}
