"""The rules every step score follows (scoring version 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fecompiler.analysis import scoring
from fecompiler.analysis.qor import write_step_qor
from fecompiler.tools.fe.builder import build_step, build_step_space
from fecompiler.tools.review.netlist_graph import _module_graph_summary


def _step(tmp_path: Path, name: str, tool: str):
    workspace = {"directory": str(tmp_path), "design": "demo", "top_module": "demo"}
    step = build_step(workspace=workspace, step_name=name, tool=tool)
    build_step_space(step)
    return workspace, step


def _score(step) -> dict:
    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    return summary["score"]


# --- the three rules ------------------------------------------------------


def test_a_component_that_was_not_evaluated_is_left_out_of_the_total() -> None:
    components = [
        scoring.component("a", "A", 30, 60, "half of it"),
        scoring.component("b", "B", 0, 40, "does not apply", evaluated=False),
    ]

    assert scoring.score("Demo", components) == {
        "label": "Demo",
        "value": 50,
        "maximum": 100,
        "scoring_version": 2,
        "evaluated_weight": 60,
        "components": components,
    }


def test_nothing_evaluated_is_no_score() -> None:
    assert scoring.score("Demo", [scoring.component("a", "A", 0, 10, "", evaluated=False)]) is None


@pytest.mark.parametrize(
    ("count", "lines", "expected"),
    [
        (0, 1000, 25),
        (10, 1000, 12.5),
        (20, 1000, 0),
        (40, 1000, 0),
        # The same ten warnings weigh less in a design ten times the size.
        (10, 10000, 23.75),
        # A tiny or unreadable design is scored as 200 lines, not as zero lines.
        (2, 50, 12.5),
        (2, 0, 12.5),
    ],
)
def test_warnings_are_judged_by_density(count, lines, expected) -> None:
    assert scoring.density_credit(count, lines, 25, 20) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("lines", "text"),
    [
        (1000, "3 lint warnings in 1000 lines (3.0 per 1000 lines)."),
        (50, "3 lint warnings in 50 lines, scored as 200 (15.0 per 1000 lines)."),
        (0, "3 lint warnings in no counted lines, scored as 200 lines (15.0 per 1000 lines)."),
    ],
)
def test_the_summary_says_what_size_was_used(lines, text) -> None:
    assert scoring.density_summary(3, lines, "lint warning") == text


def test_policy_weights_add_up_to_one_hundred_per_step() -> None:
    policy = scoring.scoring_policy()

    def total(step: str, names: tuple[str, ...]) -> float:
        return sum(
            sum(value.values()) if isinstance(value, dict) else value
            for value in (policy[step][name] for name in names)
        )

    assert total(
        "prepare",
        (
            "rtl_sources",
            "include_directories",
            "top_defined_once",
            "top_in_inputs",
            "interface_ports",
            "interface_no_extra_ports",
            "input_fingerprint",
            "merged_filelist",
            "prepared_manifest",
        ),
    ) == 100
    assert total(
        "review",
        ("structural_precheck", "actionable_errors", "warning_hygiene", "structural_headroom"),
    ) == 100
    assert total(
        "elab",
        (
            "compiler_execution",
            "diagnostic_errors",
            "hierarchy_closure",
            "top_resolution",
            "warning_hygiene",
        ),
    ) == 100
    assert total("lint", ("analysis_execution", "errors", "warnings", "rule_breadth")) == 100
    assert total("sim", ("required_cases", "difftest_confidence", "cycle_telemetry")) == 100


# --- what that means for the steps ---------------------------------------


def _readiness(*, top_required: bool, interface: str) -> dict:
    verified = interface == "verified"
    return {
        "schema_version": 1,
        "sources": {
            "rtl_total": 2,
            "rtl_resolved": 2,
            "include_dir_total": 0,
            "include_dir_resolved": 0,
        },
        "top": {"required": top_required, "definitions": 1, "source_in_inputs": True},
        "interface": {
            "applicable": interface != "not_applicable",
            "verified": verified,
            "expected_ports": 2 if verified else 0,
            "matched_ports": 2 if verified else 0,
            "missing_ports": 0,
            "extra_ports": 0,
            "mismatched_ports": 0,
        },
        "reproducibility": {
            "input_fingerprint": True,
            "merged_filelist": True,
            "prepared_manifest": True,
        },
    }


@pytest.mark.parametrize(
    ("interface", "weight", "evaluated"),
    [
        ("verified", 100, True),
        # An RTL design has no CPU interface contract: nothing is given away.
        ("not_applicable", 60, False),
        # A CPU without a known contract is not marked down for it either.
        ("unverified", 60, False),
    ],
)
def test_prepare_scores_only_the_checks_that_apply(tmp_path, interface, weight, evaluated) -> None:
    workspace, step = _step(tmp_path, "prepare", "fe")
    Path(step.report["step"]).write_text(
        json.dumps(
            {
                "rtl_files": 2,
                "incdirs": 0,
                "defines": 0,
                "contracts": [{"id": "top", "status": "pass"}],
                "readiness": _readiness(top_required=True, interface=interface),
            }
        ),
        encoding="utf-8",
    )

    write_step_qor(step, workspace, True)

    score = _score(step)
    assert (score["value"], score["evaluated_weight"]) == (100, weight)
    assert score["components"][2]["evaluated"] is evaluated


def _review_report(precheck_status: str) -> dict:
    return {
        "summary": {"actionable_errors": 0, "actionable_warnings": 0, "total_lines": 500},
        "metrics": {"structural": {}},
        "yosys_precheck": {"status": precheck_status, "diagnostics": []},
        "issues": [],
    }


def test_review_without_yosys_is_not_scored_down(tmp_path) -> None:
    workspace, step = _step(tmp_path, "review", "fe")
    (Path(step.report["dir"]) / "rtl_review.json").write_text(
        json.dumps(_review_report("unavailable")), encoding="utf-8"
    )

    write_step_qor(step, workspace, True)

    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    score = summary["score"]
    # What Yosys would have measured is not evaluated, and says so; the
    # quality stays incomplete because the precheck gate is.
    assert summary["quality_status"] == "incomplete"
    assert (score["value"], score["evaluated_weight"]) == (100, 45)
    assert [(item["id"], item["evaluated"]) for item in score["components"]] == [
        ("structural_precheck", False),
        ("actionable_errors", True),
        ("warning_hygiene", True),
        ("structural_headroom", False),
    ]


# --- structural measurements ---------------------------------------------


def _register(clock: int, data: int, output: int) -> dict:
    return {
        "type": "$dff",
        "port_directions": {"CLK": "input", "D": "input", "Q": "output"},
        "connections": {"CLK": [clock], "D": [data], "Q": [output]},
    }


def test_clock_pins_do_not_count_as_fanout() -> None:
    # Bit 2 clocks eight registers; bit 3 feeds the data input of all of them.
    module = {
        "ports": {
            "clk": {"direction": "input", "bits": [2]},
            "d": {"direction": "input", "bits": [3]},
        },
        "cells": {f"r{index}": _register(2, 3, 10 + index) for index in range(8)},
    }

    summary = _module_graph_summary("demo", module)

    assert summary["max_fanout"] == 8
    module["cells"] = {f"r{index}": _register(2, 20 + index, 10 + index) for index in range(8)}
    assert _module_graph_summary("demo", module)["max_fanout"] == 1


def test_a_path_through_a_submodule_instance_is_not_a_combinational_cycle() -> None:
    def gate(a: int, y: int) -> dict:
        return {
            "type": "$not",
            "port_directions": {"A": "input", "Y": "output"},
            "connections": {"A": [a], "Y": [y]},
        }

    instance = {
        "type": "core",
        "port_directions": {"i": "input", "o": "output"},
        "connections": {"i": [5], "o": [4]},
    }
    # gate drives the instance, the instance drives the gate: whether that is a
    # loop depends on what is inside the instance, which this module cannot say.
    through_instance = {"ports": {}, "cells": {"g": gate(4, 5), "u_core": instance}}
    real_loop = {"ports": {}, "cells": {"g1": gate(4, 5), "g2": gate(5, 4)}}

    assert _module_graph_summary("wrapper", through_instance)["comb_cycle"] is False
    assert _module_graph_summary("loop", real_loop)["comb_cycle"] is True
