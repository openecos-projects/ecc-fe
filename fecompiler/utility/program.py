"""The name this program is shown under in its own output.

``ecc-fe`` is also reached through a wrapper (``ecc fe ...``). The wrapper sets
``ECC_FE_PROGRAM`` so usage lines, message prefixes and the follow-up commands a
record suggests can be typed back exactly as printed. Only output is affected:
nothing written to a project or a run depends on the name.
"""

from __future__ import annotations

import os
import re

PROGRAM_ENV = "ECC_FE_PROGRAM"
DEFAULT_PROGRAM = "ecc-fe"

_WORD = r"[A-Za-z0-9_./][A-Za-z0-9_.+/-]*"
_VALID_PROGRAM = re.compile(rf"{_WORD}(?: {_WORD}){{0,3}}")
# `ecc-fe` as a command word: not part of a path (`/opt/ecc-fe`, `.ecc-fe/`),
# a file name (`ecc-fe.toml`) or a longer identifier (`ecc-fe-handoff`).
_PROGRAM_WORD = re.compile(r"(?<![\w./-])ecc-fe(?![\w./-])")


def program_name() -> str:
    """Return the configured name, or ``ecc-fe`` when it is unset or unusable."""
    value = " ".join(os.environ.get(PROGRAM_ENV, "").split())
    if value and _VALID_PROGRAM.fullmatch(value):
        return value
    return DEFAULT_PROGRAM


def invoked(text: str) -> str:
    """Rewrite the ``ecc-fe`` command word in *text* to the configured name.

    Apply it once, to text written with the literal ``ecc-fe``; values taken
    from the user (paths, project names) must be appended afterwards.
    """
    name = program_name()
    if name == DEFAULT_PROGRAM:
        return text
    return _PROGRAM_WORD.sub(lambda _match: name, text)


__all__ = ["DEFAULT_PROGRAM", "PROGRAM_ENV", "invoked", "program_name"]
