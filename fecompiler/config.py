"""Global configuration — default paths for the fecompiler framework."""

from __future__ import annotations

from pathlib import Path

# Root directory where all workspace projects are stored by default.
# Resolved relative to the fecompiler package root so the location is
# predictable regardless of the user's working directory.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # ecc-fe/

DEFAULT_PROJECTS_ROOT = _PACKAGE_ROOT / "workspace_projects"
