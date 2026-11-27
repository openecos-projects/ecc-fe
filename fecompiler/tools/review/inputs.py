"""Which sources RTL review looks at.

Review inspects the user's design: the CPU of a CPU project, the whole
design of an RTL project. It takes that set from the prepare result, the
one place a filelist is turned into sources, include directories, library
directories, and defines. Only when there is no current prepare result does
it fall back to reading the filelist itself.

Every source of the design is read, whoever wrote it (a CPU vendored from a
third party, the adapter around it): the hierarchy below the top has to be
complete. Only the SoC harness is left out. Which findings count against the
user is decided by ownership afterwards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.tools.common.rtl_inputs import prepared_inputs

# The ownership of the SoC harness sources, which are not the user's design.
_HARNESS = "soc"


def prepared_review_inputs(workspace: dict[str, Any]) -> dict[str, Any] | None:
    """Return the user's sources from the current prepare result, or ``None``."""
    prepared = prepared_inputs(workspace)
    if not prepared:
        return None
    files = [
        str(record.get("path", ""))
        for record in prepared.get("rtl_sources", [])
        if isinstance(record, dict)
        and record.get("ownership") != _HARNESS
        and str(record.get("path", "")).strip()
    ]
    if not files:
        return None
    directories: list[str] = []
    for raw in (
        *prepared.get("incdirs", []),
        *(str(Path(str(header)).parent) for header in prepared.get("header_files", [])),
        *(str(Path(path).parent) for path in files),
    ):
        text = str(raw).strip()
        if text and text not in directories:
            directories.append(text)
    return {
        "rtl_files": files,
        "incdirs": directories,
        "defines": [str(item) for item in prepared.get("defines", [])],
        # Searched for modules the listed sources instantiate but do not define.
        "libdirs": [str(item) for item in prepared.get("libdirs", [])],
        "libexts": [str(item) for item in prepared.get("libexts", [])],
    }
