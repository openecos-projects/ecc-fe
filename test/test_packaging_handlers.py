"""The packaged runtime must contain every step handler.

Handlers are imported by name when a step runs, so PyInstaller cannot find
them by following imports: ``ecc-fe.spec`` has to list each module.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import fecompiler
from fecompiler.allflow.steps import step_definitions

_SPEC = Path(fecompiler.__file__).resolve().parent.parent / "ecc-fe.spec"


@pytest.mark.skipif(not _SPEC.is_file(), reason="ecc-fe.spec is not part of this checkout")
def test_spec_lists_the_module_of_every_step_handler() -> None:
    tree = ast.parse(_SPEC.read_text(encoding="utf-8"))
    hidden = next(
        ast.literal_eval(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(target, "id", "") == "HIDDENIMPORTS" for target in node.targets)
    )

    handlers = {definition.handler.partition(":")[0] for definition in step_definitions()}

    assert handlers <= set(hidden)
