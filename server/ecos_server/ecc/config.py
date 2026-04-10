"""Project-wide runtime config."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROJECTS_ROOT = Path(
    os.environ.get("ECC_FE_PROJECTS_ROOT", str(REPO_ROOT / "workspace_projects")),
).expanduser().resolve()
