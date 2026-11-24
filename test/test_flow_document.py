"""docs/fe-flow.md has to keep up with the step registry."""

from __future__ import annotations

from pathlib import Path

import pytest

import fecompiler
from fecompiler.allflow.steps import step_definitions

_DOCUMENT = Path(fecompiler.__file__).resolve().parent.parent / "docs" / "fe-flow.md"


@pytest.mark.skipif(not _DOCUMENT.is_file(), reason="docs are not part of this checkout")
def test_flow_document_names_every_step_and_its_report() -> None:
    text = _DOCUMENT.read_text(encoding="utf-8")

    missing = [
        item
        for definition in step_definitions()
        for item in (
            f"`{definition.directory_name}`",
            definition.qor_report,
            definition.handler.partition(":")[0].replace(".", "/") + ".py",
        )
        if item not in text
    ]

    assert missing == []
