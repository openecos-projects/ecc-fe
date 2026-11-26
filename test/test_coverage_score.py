"""Code coverage as part of the simulation score (scoring version 3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fecompiler.analysis import scoring
from fecompiler.analysis.qor import write_step_qor
from fecompiler.tools.verilator.coverage import design_coverage
from test_qor_analysis import _scored_sim_report, _step, _write


def _numbers(covered: int, total: int) -> dict[str, object]:
    return {
        "covered": covered,
        "total": total,
        "percent": round(100 * covered / total, 2) if total else None,
    }


def _coverage_report(**owners: dict[str, tuple[int, int]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "ok",
        "cases": 1,
        "cases_with_data": 1,
        "kinds": {},
        "by_ownership": {
            owner: {kind: _numbers(*pair) for kind, pair in kinds.items()}
            for owner, kinds in owners.items()
        },
        "files": [],
    }


# --- what counts as the design ---------------------------------------------


def test_the_design_is_the_cpu_not_the_harness_it_runs_in() -> None:
    report = _coverage_report(
        cpu={"line": (40, 50), "branch": (10, 50), "toggle": (300, 1000)},
        soc={"line": (1, 500), "toggle": (10, 9000)},
        adapter={"line": (5, 5), "toggle": (50, 50)},
    )

    assert design_coverage(report) == {
        # Verilator's line and branch points are both its line coverage.
        "line": _numbers(50, 100),
        "toggle": _numbers(300, 1000),
    }


def test_a_bundled_core_and_the_users_own_rtl_are_both_the_design() -> None:
    report = _coverage_report(
        third_party={"line": (10, 20), "toggle": (5, 10)},
        design={"line": (10, 20), "toggle": (5, 10)},
    )

    assert design_coverage(report) == {"line": _numbers(20, 40), "toggle": _numbers(10, 20)}


@pytest.mark.parametrize(
    "report",
    [
        {"status": "unavailable", "by_ownership": {}},
        {"status": "empty", "by_ownership": {}},
        {"status": "ok"},
        {"status": "ok", "by_ownership": []},
        _coverage_report(soc={"line": (1, 2)}),
        _coverage_report(cpu={"line": (0, 0), "toggle": (0, 0)}),
        {"status": "ok", "by_ownership": {"cpu": {"line": {"covered": "x", "total": 3}}}},
        {"status": "ok", "by_ownership": {"cpu": {"line": {"covered": 5, "total": 3}}}},
    ],
)
def test_without_usable_design_coverage_there_is_none(report) -> None:
    assert design_coverage(report) is None


# --- the score ----------------------------------------------------------------


def _scored(tmp_path: Path, coverage: dict | None, *, difftest: bool = False) -> dict:
    workspace, step = _step(tmp_path, "sim", "verilator")
    _write(Path(step.report["dir"]) / "cases.json", _scored_sim_report(difftest=difftest, has_baseline=False))
    if coverage is not None:
        _write(Path(step.report["dir"]) / "coverage.json", coverage)
    write_step_qor(step, workspace, True)
    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    metrics = json.loads(Path(step.analysis["qor_metrics"]).read_text(encoding="utf-8"))["metrics"]
    return {"summary": summary, "metrics": {item["id"]: item for item in metrics}}


def _component(result: dict) -> dict:
    return next(item for item in result["summary"]["score"]["components"] if item["id"] == "code_coverage")


def test_a_run_without_coverage_is_scored_as_before(tmp_path) -> None:
    result = _scored(tmp_path, None)

    assert _component(result) == {
        "id": "code_coverage",
        "label": "Code coverage",
        "earned": 0,
        "possible": 25,
        "evaluated": False,
        "summary": "Not evaluated: this run collected no coverage (sim.coverage).",
    }
    # Difftest off and no coverage: cases and cycle counts are all there is.
    assert result["summary"]["score"]["value"] == 100
    assert result["summary"]["score"]["evaluated_weight"] == 75
    assert result["summary"]["score"]["scoring_version"] == 3 == scoring.scoring_version()
    assert "line_coverage" not in result["metrics"]


def test_coverage_earns_in_proportion_and_in_full_from_its_target(tmp_path) -> None:
    result = _scored(
        tmp_path,
        _coverage_report(cpu={"line": (90, 100), "toggle": (300, 1000)}, soc={"line": (0, 900)}),
    )

    component = _component(result)
    # Line 90% of a 90% target is all of its 60%; toggle 30% of 75% is 0.4 of its 40%.
    assert component["earned"] == 19
    assert (component["possible"], component["evaluated"]) == (25, True)
    assert component["summary"] == (
        "Coverage of the design under test, merged over the cases: "
        "line 90.0% (90/100, full credit from 90%); "
        "toggle 30.0% (300/1000, full credit from 75%)."
    )
    score = result["summary"]["score"]
    assert score["evaluated_weight"] == 100
    assert score["value"] == 94  # 60 + 15 + 19 of 100


def test_full_coverage_keeps_a_passing_run_at_100(tmp_path) -> None:
    result = _scored(
        tmp_path, _coverage_report(cpu={"line": (95, 100), "toggle": (80, 100)}), difftest=True
    )

    assert _component(result)["earned"] == 25
    assert result["summary"]["score"]["value"] == 100
    assert result["summary"]["score"]["evaluated_weight"] == 125


def test_low_coverage_lowers_the_score_of_a_run_whose_cases_all_pass(tmp_path) -> None:
    result = _scored(tmp_path, _coverage_report(cpu={"line": (9, 100), "toggle": (0, 100)}))

    assert _component(result)["earned"] == 1.5
    assert result["summary"]["quality_status"] == "pass"
    assert result["summary"]["score"]["value"] == 76.5


def test_a_kind_the_design_has_no_point_of_leaves_its_share_to_the_other(tmp_path) -> None:
    result = _scored(tmp_path, _coverage_report(cpu={"toggle": (75, 100)}))

    component = _component(result)
    assert component["earned"] == 25
    assert "line" not in component["summary"] and "toggle 75.0%" in component["summary"]
    assert "line_coverage" not in result["metrics"]
    assert result["metrics"]["toggle_coverage"]["value"] == 0.75


@pytest.mark.parametrize(
    "report",
    [
        {"schema_version": 1, "status": "unavailable", "reason": "the harness does not write coverage", "by_ownership": {}},
        _coverage_report(soc={"line": (5, 10)}),
    ],
)
def test_coverage_that_says_nothing_about_the_design_is_not_evaluated(tmp_path, report) -> None:
    result = _scored(tmp_path, report)

    assert _component(result)["evaluated"] is False
    assert result["summary"]["score"]["value"] == 100


def test_coverage_is_also_a_metric_to_compare_runs_by(tmp_path) -> None:
    result = _scored(tmp_path, _coverage_report(cpu={"line": (45, 90), "branch": (5, 10), "toggle": (1, 4)}))

    line, toggle = result["metrics"]["line_coverage"], result["metrics"]["toggle_coverage"]
    assert (line["value"], line["unit"], line["direction"]) == (0.5, "ratio", "higher_is_better")
    assert toggle["value"] == 0.25
    assert line["rating"] == {"gate": False, "score": False, "trend": True}


def test_a_testbench_run_reports_coverage_and_stays_unscored(tmp_path) -> None:
    workspace, step = _step(tmp_path, "sim", "verilator")
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "schema_version": 1,
            "kind": "testbench",
            "suite": "testbench",
            "cases": [{"name": "tb", "ok": True, "returncode": 0, "log": "", "metrics": None, "failure": None}],
        },
    )
    _write(Path(step.report["dir"]) / "coverage.json", _coverage_report(design={"line": (3, 4), "toggle": (8, 10)}))

    write_step_qor(step, workspace, True)

    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    metrics = {
        item["id"]: item["value"]
        for item in json.loads(Path(step.analysis["qor_metrics"]).read_text(encoding="utf-8"))["metrics"]
    }
    assert summary.get("score") is None
    assert (metrics["line_coverage"], metrics["toggle_coverage"]) == (0.75, 0.8)
