"""Which sources RTL review looks at.

Review inspects the user's own RTL: the CPU of a CPU project, the whole
design of an RTL project. It takes that set from the prepare result, the
one place a filelist is turned into sources, include directories, library
directories, and defines. Only when there is no current prepare result does
it fall back to reading the filelist itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.tools.common.rtl_inputs import prepared_inputs
from fecompiler.tools.common.rtl_ownership import user_rtl_ownership


def prepared_review_inputs(workspace: dict[str, Any]) -> dict[str, Any] | None:
    """Return the user's sources from the current prepare result, or ``None``."""
    prepared = prepared_inputs(workspace)
    if not prepared:
        return None
    owner = user_rtl_ownership(workspace)
    files = [
        str(record.get("path", ""))
        for record in prepared.get("rtl_sources", [])
        if isinstance(record, dict)
        and record.get("ownership") == owner
        and str(record.get("path", "")).strip()
    ]
    if not files:
        return None
    directories: list[str] = []
    for raw in (
        *prepared.get("incdirs", []),
        *(str(Path(str(header)).parent) for header in prepared.get("header_files", [])),
        *(str(Path(path).parent) for path in files),
        # Library directories are searched for modules the listed sources
        # instantiate but do not define.
        *prepared.get("libdirs", []),
    ):
        text = str(raw).strip()
        if text and text not in directories:
            directories.append(text)
    return {
        "rtl_files": files,
        "incdirs": directories,
        "defines": [str(item) for item in prepared.get("defines", [])],
    }
