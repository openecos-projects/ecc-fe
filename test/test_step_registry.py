from __future__ import annotations

from pathlib import Path

import pytest

import fecompiler
from fecompiler.allflow.profile import CPU_CORE, GENERIC_RTL, flow_steps_for
from fecompiler.allflow.steps import (
    find_step_definition,
    is_step,
    step_definition,
    step_definitions,
    step_directory,
    step_directory_suffixes,
    step_names,
)
from fecompiler.analysis import qor
from fecompiler.cli import commands as cli_commands
from fecompiler.cli.handlers import param as param_handler
from fecompiler.cli.handlers import report as report_handler
from fecompiler.engine import provenance
from fecompiler.tools.fe import BaseStep, get_step_registry

_PACKAGE_ROOT = Path(fecompiler.__file__).resolve().parent


def test_registry_lists_the_frontend_steps_in_flow_order() -> None:
    assert step_names() == ("prepare", "review", "elab", "lint", "sim")
    assert [(step.name, step.tool) for step in step_definitions()] == [
        ("prepare", "fe"),
        ("review", "fe"),
        ("elab", "slang"),
        ("lint", "verilator"),
        ("sim", "verilator"),
    ]


def test_step_directories_keep_their_on_disk_names(tmp_path) -> None:
    assert [
        step_directory(tmp_path, name).relative_to(tmp_path).as_posix()
        for name in step_names()
    ] == ["prepare_fe", "review_fe", "elab_slang", "lint_verilator", "sim_verilator"]
    assert step_directory_suffixes() == ("_fe", "_slang", "_verilator")


def test_unknown_step_is_reported_with_the_known_names() -> None:
    assert find_step_definition("place") is None
    assert find_step_definition(None) is None
    assert not is_step("place")
    with pytest.raises(KeyError, match="known steps: prepare, review, elab, lint, sim"):
        step_definition("place")


def test_profiles_are_built_from_registered_steps() -> None:
    registered = {(step.name, step.tool) for step in step_definitions()}

    assert flow_steps_for(CPU_CORE) == [
        (step.name, step.tool) for step in step_definitions()
    ]
    assert flow_steps_for(GENERIC_RTL) == flow_steps_for(CPU_CORE)[:-1]
    assert set(flow_steps_for(GENERIC_RTL)) <= registered


def test_every_step_has_a_handler_and_a_qor_builder() -> None:
    handlers = get_step_registry()

    assert tuple(handlers) == step_names()
    for definition in step_definitions():
        handler = handlers[definition.name]
        assert isinstance(handler, BaseStep)
        assert type(handler).__name__ == definition.handler.rpartition(":")[2]
    assert set(qor._STEP_BUILDERS) == set(step_names())


def test_qor_reads_the_report_the_registry_names(tmp_path) -> None:
    class _Step:
        def __init__(self, name: str) -> None:
            self.name = name
            self.report = {
                "dir": str(tmp_path / "report"),
                "step": str(tmp_path / "report" / f"{name}.rpt"),
            }

    for definition in step_definitions():
        assert qor._step_qor_source_path(_Step(definition.name)) == (
            tmp_path / "report" / definition.qor_report
        )


def test_provenance_source_files_exist() -> None:
    for definition in step_definitions():
        assert definition.source_files
        for relative in definition.source_files:
            assert (_PACKAGE_ROOT / relative).is_file(), relative


def test_provenance_tracks_the_fields_the_registry_declares() -> None:
    merged = {
        "top_module": "top",
        "lint_profile": "strict",
        "testbench": "tb.cpp",
        "sim_images": ["a.bin"],
        "sim_all_tests": False,
        "simulator": "not tracked: no sim_ prefix",
        "review_waivers": [],
        "unrelated": 1,
    }

    def tracked(step: str) -> set[str]:
        return set(provenance._step_configuration({}, merged, step))

    assert tracked("prepare") == {"top_module"}
    assert tracked("review") == {"review_waivers"}
    assert tracked("elab") == {"top_module"}
    assert tracked("lint") == {"top_module", "lint_profile"}
    assert tracked("sim") == {"testbench", "sim_images", "sim_all_tests"}
    assert tracked("place") == set()


def test_tool_identity_follows_the_registry() -> None:
    for definition in step_definitions():
        identity = provenance._tool_identity(definition.name, definition.tool)
        assert set(identity["executables"]) == set(definition.executables)
        assert set(identity["runner_sources"]) == {
            f"fecompiler/{relative}"
            for relative in (
                *definition.source_files,
                f"analysis/qor/{definition.name}.py",
                "analysis/qor/__init__.py",
                "analysis/qor/common.py",
                "analysis/qor/fingerprint.py",
                "analysis/qor/store.py",
                "analysis/scoring.py",
                "analysis/scoring_policy.json",
            )
        }


def test_cli_step_tables_come_from_the_registry(tmp_path) -> None:
    assert cli_commands._FLOW_STEPS == step_names()
    assert report_handler._FLOW_STEPS == step_names()
    assert param_handler._FLOW_STEPS == set(step_names())
    for name in step_names():
        expected = step_directory(tmp_path, name)
        assert cli_commands._step_directory(tmp_path, name) == expected
        assert report_handler._step_directory(tmp_path, name) == expected


def test_doctor_checks_per_step() -> None:
    def checks(step: str | None) -> list[str]:
        return [item[0] for item in cli_commands._doctor_requirements(step)]

    assert checks("prepare") == []
    assert checks("review") == []
    assert checks("elab") == ["slang"]
    assert checks("lint") == ["verilator"]
    assert checks("sim") == ["verilator", "cxx", "make", "riscv"]
    assert checks(None) == ["slang", "verilator", "cxx", "make", "riscv", "yosys"]


def test_application_step_tables_name_only_registered_steps() -> None:
    from fecompiler.allflow.steps import input_preparation_step
    from fecompiler.application import sim_config, step_detail

    assert set(step_detail._STEP_DETAILS) == set(step_names())
    assert set(step_detail._STEP_ARTIFACTS) <= set(step_names())
    assert set(sim_config._STEP_RUN_SETUP) <= set(step_names())
    assert input_preparation_step().name == "prepare"
    assert [step.name for step in step_definitions() if step.prepares_inputs] == [
        "prepare"
    ]


def test_run_setup_only_applies_to_steps_that_declare_it(monkeypatch) -> None:
    from fecompiler.application import sim_config

    calls: list[tuple[str, object]] = []
    monkeypatch.setitem(
        sim_config._STEP_RUN_SETUP,
        "sim",
        lambda workspace, args: calls.append(("sim", args)) or True,
    )

    assert sim_config._prepare_step_run({}, "lint") is False
    assert sim_config._prepare_step_run({}, "sim", "arguments") is True
    assert calls == [("sim", "arguments")]


def test_sim_run_setup_forces_a_rerun_only_for_a_named_suite(monkeypatch) -> None:
    from argparse import Namespace

    from fecompiler.application import sim_config

    applied: list[object] = []
    monkeypatch.setattr(
        sim_config, "_apply_default_sim_smoke_suite", lambda ws: applied.append("smoke")
    )
    monkeypatch.setattr(
        sim_config,
        "_apply_sim_test_suite",
        lambda ws, suite, mode, cases: applied.append((suite, mode, cases)),
    )
    monkeypatch.setattr(
        sim_config,
        "_apply_coremark_compile_options",
        lambda ws, args: applied.append("coremark-options"),
    )

    def named(suite: str) -> Namespace:
        return Namespace(
            sim_test_suite=suite, sim_cpu_test_mode="selected", sim_cpu_test_case=["add"]
        )

    assert sim_config._configure_sim_run({}, None) is False
    # The run's own setting selects the whole suite without forcing a rerun.
    assert sim_config._configure_sim_run({"sim_cases": "all"}, None) is False
    assert applied.pop() == ("cpu-tests", "all", None)
    assert sim_config._configure_sim_run({}, named("")) is False
    assert sim_config._configure_sim_run({}, named("Default")) is False
    assert sim_config._configure_sim_run({}, named("cpu_tests")) is True
    assert sim_config._configure_sim_run({}, named("CoreMark")) is True
    assert applied == [
        "smoke",
        "smoke",
        "smoke",
        ("cpu_tests", "selected", ["add"]),
        "coremark-options",
        ("CoreMark", "selected", ["add"]),
    ]


def test_every_step_has_a_qor_module_and_the_package_has_no_other() -> None:
    package = Path(provenance.__file__).parents[1] / "analysis" / "qor"
    shared = {"__init__", "common", "fingerprint", "store"}

    assert {path.stem for path in package.glob("*.py")} == shared | set(step_names())
    for name in step_names():
        assert (package.parent.parent / provenance.step_qor_source_file(name)).is_file()


def test_no_source_named_in_a_signature_is_missing() -> None:
    root = Path(provenance.__file__).parents[1]
    for definition in step_definitions():
        identity = provenance._tool_identity(definition.name, definition.tool)
        missing = [name for name, digest in identity["runner_sources"].items() if not digest]
        assert missing == [], f"{definition.name}: {missing}"
        assert all((root.parent / name).is_file() for name in identity["runner_sources"])
