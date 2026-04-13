"""Flow step spec aligned with ecos-studio style."""

from __future__ import annotations

from typing import Final

DEFAULT_FLOW_STEPS: Final[list[tuple[str, str]]] = [
    ("copyfiles", "ecc"),
    ("step2", "ecc"),
    ("step3", "ecc"),
    ("step4", "ecc"),
    ("step5", "ecc"),
    ("step6", "ecc"),
]


def sanitize_step_token(step_name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in step_name).strip("_") or "step"
