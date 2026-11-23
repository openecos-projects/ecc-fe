from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fecompiler.application import (
    create_request,
    events,
    result,
    sim_config,
    step_detail,
    values,
    workspace_service,
)
from fecompiler.cli import workspace as workspace_cli

_PACKAGE = Path(workspace_service.__file__).resolve().parent

# Each module may import only from the modules listed for it.
_ALLOWED = {
    "result": set(),
    "events": set(),
    "values": set(),
    "create_request": {"result", "values"},
    "sim_config": {"result", "values"},
    "step_detail": {"sim_config", "values"},
    "workspace_service": {
        "result",
        "events",
        "values",
        "create_request",
        "sim_config",
        "step_detail",
    },
}


def _application_imports(module: str) -> set[str]:
    tree = ast.parse((_PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
    prefix = "fecompiler.application."
    return {
        node.module[len(prefix) :]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith(prefix)
    }


@pytest.mark.parametrize("module", sorted(_ALLOWED))
def test_application_modules_depend_in_one_direction(module) -> None:
    assert _application_imports(module) <= _ALLOWED[module]


def test_every_application_module_is_covered_by_the_dependency_rule() -> None:
    modules = {path.stem for path in _PACKAGE.glob("*.py")} - {"__init__"}

    assert modules == set(_ALLOWED)


@pytest.mark.parametrize(
    ("name", "home"),
    [
        ("CliResult", result),
        ("workspace_event_sink", events),
        ("_normalize_str_list", values),
        ("_validate_create_request_paths", create_request),
        ("_update_workspace_parameters", sim_config),
        ("_cpu_supports_difftest", sim_config),
        ("DEFAULT_COREMARK_MARCH", sim_config),
        ("_build_frontend_step_detail", step_detail),
        ("_build_prepare_contracts", step_detail),
    ],
)
def test_moved_helpers_stay_reachable_through_the_old_modules(name, home) -> None:
    assert getattr(workspace_service, name) is getattr(home, name)
    assert getattr(workspace_cli, name) is getattr(home, name)


def test_unknown_attribute_is_still_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'missing_helper'"):
        workspace_service.missing_helper
