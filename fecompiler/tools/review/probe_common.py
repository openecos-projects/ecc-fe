"""The limits the structural precheck judges by, and helpers its parts share.
"""

from __future__ import annotations

from typing import Any

_HIGH_FANOUT_THRESHOLD = 64


_HIGH_FANIN_THRESHOLD = 32


_DEEP_COMB_DEPTH_THRESHOLD = 16


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
