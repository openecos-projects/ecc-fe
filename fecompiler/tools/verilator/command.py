"""Locate the Verilator executable; shared by the lint and sim steps."""

from __future__ import annotations

import os
import shutil


def verilator_cmd() -> str:
    """Return the Resource Manager/PATH-provided verilator executable."""
    for env_name in ("ECOS_VERILATOR", "VERILATOR"):
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return shutil.which("verilator") or "verilator"
