from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import fecompiler.analysis.qor as qor
from fecompiler.analysis.qor import write_step_qor
from fecompiler.application import workspace_service
from fecompiler.tools.fe.builder import build_step, build_step_space


def _step(tmp_path: Path, name: str, tool: str):
    workspace = {
        "directory": str(tmp_path),
        "design": "cpu",
        "top_module": "ecos_sim_top",
        "test_suite_id": "cpu_tests",
        "sim_compile_preset": "balanced",
        "sim_compile_march": "rv32im_zicsr",
        "sim_compile_mabi": "ilp32",
    }
    step = build_step(
        workspace=workspace,
        step_name=name,
        tool=tool,
        input_def="",
        input_verilog="",
    )
    build_step_space(step)
    return workspace, step


def _write(path: str | Path, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


def _prepare_readiness(
    *,
    rtl_total: int = 8,
    rtl_resolved: int = 8,
    incdir_total: int = 1,
    incdir_resolved: int = 1,
    definitions: int = 1,
    source_in_inputs: bool = True,
    expected_ports: int = 2,
    matched_ports: int = 2,
    extra_ports: int = 0,
    outputs_persisted: bool = True,
) -> dict:
    return {
        "schema_version": 1,
        "sources": {
            "rtl_total": rtl_total,
            "rtl_resolved": rtl_resolved,
            "include_dir_total": incdir_total,
            "include_dir_resolved": incdir_resolved,
        },
        "top": {
            "required": True,
            "module": "cpu_top",
            "definitions": definitions,
            "source_in_inputs": source_in_inputs,
        },
        "interface": {
            "applicable": True,
            "verified": expected_ports > 0,
            "expected_ports": expected_ports,
            "matched_ports": matched_ports,
            "missing_ports": max(0, expected_ports - matched_ports),
            "extra_ports": extra_ports,
            "mismatched_ports": 0,
        },
        "reproducibility": {
            "input_fingerprint": True,
            "merged_filelist": outputs_persisted,
            "prepared_manifest": outputs_persisted,
        },
    }


@pytest.mark.parametrize(
    ("name", "tool", "report_name", "payload", "expected_metric", "expected_gate"),
    [
        (
            "prepare",
            "fe",
            "step",
            {
                "rtl_files": 8,
                "incdirs": 1,
                "defines": 2,
                "contracts": [{"id": "cpu_top", "status": "pass"}],
            },
            "rtl_file_count",
            "frontend_contracts",
        ),
        (
            "review",
            "fe",
            "rtl_review.json",
            {
                "summary": {
                    "actionable_errors": 0,
                    "actionable_warnings": 2,
                    "yosys_precheck": {"status": "success"},
                },
                "metrics": {
                    "structural": {
                        "max_fanout": 24,
                        "max_fanin": 12,
                        "max_comb_depth": 7,
                    }
                },
                "issues": [],
            },
            "max_fanout",
            "yosys_precheck",
        ),
        (
            "elab",
            "slang",
            "elab_summary.json",
            {
                "summary": {
                    "errors": 0,
                    "warnings": 1,
                    "modules": 12,
                    "unresolved_modules": 0,
                    "top_found": True,
                },
                "diagnostics": [],
            },
            "elaborated_module_count",
            "top_module_resolved",
        ),
        (
            "lint",
            "verilator",
            "lint_summary.json",
            {
                "summary": {"cpu_errors": 0, "cpu_warnings": 3, "warnings": 20},
                "diagnostics": [],
            },
            "cpu_lint_warning_count",
            "no_cpu_lint_errors",
        ),
        (
            "sim",
            "verilator",
            "cases.json",
            {
                "suite": "cpu_tests",
                "cases": [
                    {
                        "name": "add",
                        "ok": True,
                        "metrics": {
                            "cycles": 100,
                            "difftest": {
                                "enabled": True,
                                "status": "passed",
                                "commits": 100,
                                "compared": 100,
                            },
                        },
                    },
                ],
            },
            "simulation_pass_rate",
            "all_required_cases_pass",
        ),
    ],
)
def test_writes_standard_qor_triplet(
    tmp_path: Path,
    name: str,
    tool: str,
    report_name: str,
    payload: dict,
    expected_metric: str,
    expected_gate: str,
) -> None:
    workspace, step = _step(tmp_path, name, tool)
    report_path = (
        step.report["step"]
        if report_name == "step"
        else Path(step.report["dir"]) / report_name
    )
    _write(report_path, payload)

    write_step_qor(step, workspace, True)

    metrics = json.loads(Path(step.analysis["qor_metrics"]).read_text(encoding="utf-8"))
    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    hotspots = json.loads(
        Path(step.analysis["qor_hotspots"]).read_text(encoding="utf-8")
    )
    assert metrics["schema_version"] == 3
    assert summary["schema_version"] == 4
    assert hotspots["schema_version"] == 3
    assert metrics["generation"] == summary["generation"] == hotspots["generation"]
    assert expected_metric in {metric["id"] for metric in metrics["metrics"]}
    assert expected_gate in {gate["id"] for gate in summary["gates"]}
    assert summary["quality_status"] == "pass"
    assert summary["context"]["comparison"]["fingerprint"]


def test_frontend_detail_embeds_structured_qor_artifacts(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "lint", "verilator")
    workspace["home_path"] = str(tmp_path / "home" / "home.json")
    _write(
        Path(step.report["dir"]) / "lint_summary.json",
        {
            "summary": {"cpu_errors": 0, "cpu_warnings": 1, "warnings": 2},
            "diagnostics": [],
        },
    )
    write_step_qor(step, workspace, True)

    detail = workspace_service._build_frontend_step_detail(
        workspace,
        step,
        {"state": "Success", "runtime": "00:00:01"},
    )

    assert detail["qor"]["metrics"]["schema_version"] == 3
    assert detail["qor"]["summary"]["schema_version"] == 4
    assert detail["qor"]["hotspots"]["schema_version"] == 3
    assert detail["qor"]["summary"]["quality_status"] == "pass"


def test_prepare_qor_explains_full_readiness_score(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "prepare", "fe")
    _write(
        step.report["step"],
        {
            "rtl_files": 8,
            "incdirs": 1,
            "defines": 2,
            "contracts": [{"id": "cpu_top", "status": "pass"}],
            "readiness": _prepare_readiness(),
        },
    )

    write_step_qor(step, workspace, True)

    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    score = summary["score"]
    assert score == {
        "label": "Preparation readiness",
        "value": 100,
        "maximum": 100,
        "scoring_version": 1,
        "components": [
            {
                "id": "source_resolution",
                "label": "Source resolution",
                "earned": 30,
                "possible": 30,
                "summary": "8 of 8 RTL sources and 1 of 1 include directories resolved.",
            },
            {
                "id": "top_resolution",
                "label": "Top resolution",
                "earned": 20,
                "possible": 20,
                "summary": "1 matching definition found; source is in prepared inputs.",
            },
            {
                "id": "interface_contract",
                "label": "Interface contract",
                "earned": 40,
                "possible": 40,
                "summary": "2 of 2 required ports matched; 0 unexpected.",
            },
            {
                "id": "reproducibility",
                "label": "Reproducibility",
                "earned": 10,
                "possible": 10,
                "summary": "Input fingerprint recorded; normalized outputs persisted.",
            },
        ],
    }


def test_prepare_qor_keeps_partial_score_blocked_by_failed_contract(
    tmp_path: Path,
) -> None:
    workspace, step = _step(tmp_path, "prepare", "fe")
    _write(
        step.report["step"],
        {
            "rtl_files": 4,
            "incdirs": 1,
            "defines": 0,
            "contracts": [{"id": "cpu_top", "status": "failed"}],
            "readiness": _prepare_readiness(
                rtl_total=4,
                rtl_resolved=3,
                incdir_total=1,
                incdir_resolved=0,
                expected_ports=4,
                matched_ports=3,
                extra_ports=1,
                outputs_persisted=False,
            ),
        },
    )

    write_step_qor(step, workspace, False)

    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    assert summary["quality_status"] == "blocked"
    assert summary["score"]["value"] == 66.2
    assert summary["score"]["components"][2] == {
        "id": "interface_contract",
        "label": "Interface contract",
        "earned": 26.2,
        "possible": 40,
        "summary": "3 of 4 required ports matched; 1 unexpected.",
    }


def test_qor_metrics_reference_their_exact_report_fields(tmp_path: Path) -> None:
    workspace, prepare_step = _step(tmp_path, "prepare", "fe")
    _write(
        prepare_step.report["step"],
        {
            "rtl_files": 8,
            "incdirs": 1,
            "defines": 2,
            "contracts": [{"id": "cpu_top", "status": "pass"}],
        },
    )
    write_step_qor(prepare_step, workspace, True)

    prepare_metrics = json.loads(
        Path(prepare_step.analysis["qor_metrics"]).read_text(encoding="utf-8")
    )["metrics"]
    prepare_selectors = {
        metric["id"]: metric["source"]["selector"] for metric in prepare_metrics
    }
    assert prepare_selectors["rtl_file_count"] == "/rtl_files"
    assert prepare_selectors["include_dir_count"] == "/incdirs"
    assert prepare_selectors["define_count"] == "/defines"
    assert prepare_selectors["contract_failure_count"] == "/contracts"

    workspace, review_step = _step(tmp_path, "review", "fe")
    _write(
        Path(review_step.report["dir"]) / "rtl_review.json",
        {
            "summary": {
                "actionable_errors": 0,
                "actionable_warnings": 0,
                "yosys_precheck": {"status": "success"},
            },
            "metrics": {
                "structural": {
                    "max_fanout": 24,
                    "max_fanin": 12,
                    "max_comb_depth": 7,
                }
            },
            "issues": [],
        },
    )
    write_step_qor(review_step, workspace, True)

    review_metrics = json.loads(
        Path(review_step.analysis["qor_metrics"]).read_text(encoding="utf-8")
    )["metrics"]
    review_selectors = {
        metric["id"]: metric["source"]["selector"] for metric in review_metrics
    }
    assert review_selectors["max_fanout"] == "/metrics/structural/max_fanout"
    assert review_selectors["max_fanin"] == "/metrics/structural/max_fanin"
    assert (
        review_selectors["max_combinational_depth"]
        == "/metrics/structural/max_comb_depth"
    )


def test_prepare_module_only_contract_is_not_blocked(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "prepare", "fe")
    _write(
        step.report["step"],
        {
            "rtl_files": 1,
            "incdirs": 0,
            "defines": 0,
            "contracts": [{"id": "cpu_top", "status": "module_only"}],
        },
    )

    write_step_qor(step, workspace, True)

    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    assert summary["quality_status"] == "pass"
    assert summary["gates"][0]["state"] == "pass"


def test_qor_hotspots_preserve_diagnostic_severity(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "elab", "slang")
    _write(
        Path(step.report["dir"]) / "elab_summary.json",
        {
            "summary": {
                "errors": 1,
                "warnings": 1,
                "modules": 1,
                "unresolved_modules": 0,
                "top_found": True,
            },
            "diagnostics": [
                {"severity": "error", "message": "error diagnostic"},
                {"severity": "warning", "message": "warning diagnostic"},
                {"severity": "info", "message": "informational diagnostic"},
            ],
        },
    )

    write_step_qor(step, workspace, False)

    hotspots = json.loads(
        Path(step.analysis["qor_hotspots"]).read_text(encoding="utf-8")
    )["hotspots"]
    assert [item["severity"] for item in hotspots] == [
        "critical",
        "warning",
        "info",
    ]


def test_blocks_failed_simulation_and_records_hotspot(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "sim", "verilator")
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [
                {
                    "name": "add",
                    "ok": False,
                    "metrics": {
                        "cycles": 42,
                        "difftest": {
                            "enabled": True,
                            "status": "mismatch",
                            "first_mismatch": {
                                "message": "pc=0x100",
                                "pc": "0x100",
                            },
                        },
                    },
                },
            ],
        },
    )

    write_step_qor(step, workspace, False)

    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    hotspots = json.loads(
        Path(step.analysis["qor_hotspots"]).read_text(encoding="utf-8")
    )
    assert summary["quality_status"] == "blocked"
    assert {gate["id"] for gate in summary["gates"] if gate["state"] == "failed"} == {
        "all_required_cases_pass",
        "difftest_matches_reference",
    }
    assert hotspots["hotspots"][0]["display_name"] == "add"
    assert hotspots["hotspots"][0]["description"] == "pc=0x100"


def test_simulation_hotspot_uses_failure_message(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "sim", "verilator")
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [
                {
                    "name": "add",
                    "ok": False,
                    "metrics": {"cycles": 42},
                    "failure": {
                        "kind": "bad_trap",
                        "message": "The program terminated with a bad trap.",
                    },
                },
            ],
        },
    )

    write_step_qor(step, workspace, False)

    hotspots = json.loads(
        Path(step.analysis["qor_hotspots"]).read_text(encoding="utf-8")
    )["hotspots"]
    assert hotspots[0]["description"] == "The program terminated with a bad trap."


@pytest.mark.parametrize(
    "cases",
    [
        [],
        ["invalid-case"],
        [{}],
        [{"name": "", "ok": False}],
        [{"name": "add", "ok": "false"}],
        [{"name": "add", "ok": False, "metrics": []}],
        [
            {
                "name": "add",
                "ok": True,
                "metrics": {"difftest": {"enabled": "false"}},
            }
        ],
        [
            {
                "name": "add",
                "ok": True,
                "metrics": {
                    "difftest": {
                        "enabled": True,
                        "status": "passed",
                        "commits": "100",
                        "compared": 100,
                    }
                },
            }
        ],
        [
            {
                "name": "add",
                "ok": True,
                "metrics": {
                    "difftest": {"enabled": False, "status": "passed"}
                },
            }
        ],
        [
            {
                "name": "add",
                "ok": False,
                "metrics": {
                    "difftest": {"enabled": True, "status": "mismatch"}
                },
            }
        ],
    ],
)
def test_simulation_without_valid_case_output_is_incomplete(
    tmp_path: Path,
    cases: list[object],
) -> None:
    workspace, step = _step(tmp_path, "sim", "verilator")
    _write(
        Path(step.report["dir"]) / "cases.json",
        {"suite": "cpu_tests", "cases": cases},
    )

    write_step_qor(step, workspace, False)

    metrics = json.loads(Path(step.analysis["qor_metrics"]).read_text(encoding="utf-8"))
    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    assert metrics["integrity"]["status"] == "incomplete"
    assert summary["analysis_status"] == "incomplete"
    assert summary["quality_status"] == "incomplete"


def test_missing_required_report_fields_are_incomplete_not_pass(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "lint", "verilator")
    _write(Path(step.report["dir"]) / "lint_summary.json", {"summary": {"warnings": 3}})

    write_step_qor(step, workspace, True)

    metrics = json.loads(Path(step.analysis["qor_metrics"]).read_text(encoding="utf-8"))
    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    assert metrics["integrity"]["status"] == "incomplete"
    assert summary["analysis_status"] == "incomplete"
    assert summary["quality_status"] == "incomplete"


@pytest.mark.parametrize(
    "contracts",
    [
        [],
        {"id": "cpu_top", "status": "failed"},
        ["invalid-contract"],
        [{"id": "cpu_top"}],
        [{"id": "cpu_top", "status": "unknown"}],
    ],
)
def test_malformed_prepare_contracts_are_incomplete(
    tmp_path: Path,
    contracts: object,
) -> None:
    workspace, step = _step(tmp_path, "prepare", "fe")
    _write(
        step.report["step"],
        {"rtl_files": 1, "incdirs": 0, "defines": 0, "contracts": contracts},
    )

    write_step_qor(step, workspace, True)

    metrics = json.loads(Path(step.analysis["qor_metrics"]).read_text(encoding="utf-8"))
    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    assert metrics["integrity"]["status"] == "incomplete"
    assert summary["quality_status"] == "incomplete"


@pytest.mark.parametrize("invalid_value", ["0", None, True, float("nan")])
def test_invalid_required_scalar_is_incomplete(
    tmp_path: Path,
    invalid_value: object,
) -> None:
    workspace, step = _step(tmp_path, "lint", "verilator")
    _write(
        Path(step.report["dir"]) / "lint_summary.json",
        {
            "summary": {
                "cpu_errors": invalid_value,
                "cpu_warnings": 0,
                "warnings": 0,
            }
        },
    )

    write_step_qor(step, workspace, True)

    metrics = json.loads(Path(step.analysis["qor_metrics"]).read_text(encoding="utf-8"))
    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    assert metrics["integrity"]["status"] == "incomplete"
    assert summary["quality_status"] == "incomplete"


def test_failed_execution_is_incomplete_even_when_quality_gates_pass(
    tmp_path: Path,
) -> None:
    workspace, step = _step(tmp_path, "lint", "verilator")
    _write(
        Path(step.report["dir"]) / "lint_summary.json",
        {
            "status": "fail",
            "summary": {"cpu_errors": 0, "cpu_warnings": 0, "warnings": 0},
            "diagnostics": [
                {
                    "severity": "error",
                    "category": "tool",
                    "ownership": "tool",
                    "message": "failed to execute verilator: not found",
                },
            ],
        },
    )

    write_step_qor(step, workspace, False)

    metrics = json.loads(Path(step.analysis["qor_metrics"]).read_text(encoding="utf-8"))
    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    assert metrics["status"] == "failed"
    assert summary["analysis_status"] == "incomplete"
    assert summary["quality_status"] == "incomplete"
    assert all(gate["state"] == "pass" for gate in summary["gates"])


@pytest.mark.parametrize(
    ("status", "quality_gate"),
    [
        ("unavailable", "unavailable"),
        ("skipped", "skipped"),
        ("tool_limited", "warnings"),
    ],
)
def test_non_blocking_yosys_precheck_is_incomplete(
    tmp_path: Path,
    status: str,
    quality_gate: str,
) -> None:
    workspace, step = _step(tmp_path, "review", "fe")
    _write(
        Path(step.report["dir"]) / "rtl_review.json",
        {
            "summary": {
                "actionable_errors": 0,
                "actionable_warnings": 0,
                "yosys_precheck": {
                    "status": status,
                    "quality": {"gate": quality_gate},
                    "diagnostics": [],
                },
            },
            "metrics": {"structural": {}},
            "issues": [],
        },
    )

    write_step_qor(step, workspace, True)

    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    precheck_gate = next(gate for gate in summary["gates"] if gate["id"] == "yosys_precheck")
    assert precheck_gate["state"] == "incomplete"
    assert summary["analysis_status"] == "incomplete"
    assert summary["quality_status"] == "incomplete"


def test_blocking_yosys_precheck_remains_blocked(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "review", "fe")
    _write(
        Path(step.report["dir"]) / "rtl_review.json",
        {
            "summary": {
                "actionable_errors": 0,
                "actionable_warnings": 0,
                "yosys_precheck": {
                    "status": "failed",
                    "quality": {"gate": "failed"},
                    "diagnostics": [
                        {
                            "severity": "error",
                            "category": "syntax",
                            "message": "unexpected token",
                        },
                    ],
                },
            },
            "metrics": {"structural": {}},
            "issues": [],
        },
    )

    write_step_qor(step, workspace, False)

    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    precheck_gate = next(gate for gate in summary["gates"] if gate["id"] == "yosys_precheck")
    assert precheck_gate["state"] == "failed"
    assert summary["analysis_status"] == "valid"
    assert summary["quality_status"] == "blocked"


def test_review_uses_full_top_level_yosys_precheck_payload(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "review", "fe")
    _write(
        Path(step.report["dir"]) / "rtl_review.json",
        {
            "summary": {
                "actionable_errors": 0,
                "actionable_warnings": 0,
                "yosys_precheck": {"status": "failed", "diagnostics": 1},
            },
            "yosys_precheck": {
                "status": "failed",
                "quality": {"gate": "failed"},
                "diagnostics": [
                    {
                        "severity": "error",
                        "category": "syntax",
                        "message": "unexpected token",
                    },
                ],
            },
            "metrics": {"structural": {}},
            "issues": [],
        },
    )

    write_step_qor(step, workspace, False)

    summary = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))
    precheck_gate = next(gate for gate in summary["gates"] if gate["id"] == "yosys_precheck")
    assert precheck_gate["state"] == "failed"
    assert precheck_gate["evidence"][0]["selector"] == "/yosys_precheck"
    assert summary["quality_status"] == "blocked"


def test_review_policy_changes_comparison_fingerprint(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "review", "fe")
    _write(
        Path(step.report["dir"]) / "rtl_review.json",
        {
            "summary": {
                "actionable_errors": 0,
                "actionable_warnings": 0,
                "yosys_precheck": {"status": "success"},
            },
            "metrics": {"structural": {}},
            "issues": [],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]

    workspace["review_waivers"] = [
        {"fingerprint": "a" * 64, "reason": "accepted for this workspace"}
    ]
    write_step_qor(step, workspace, True)
    second = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]

    assert second != first


def test_review_top_changes_comparison_fingerprint(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "review", "fe")
    report_path = Path(step.report["dir"]) / "rtl_review.json"

    def write_review(top_module: str) -> None:
        _write(
            report_path,
            {
                "summary": {
                    "actionable_errors": 0,
                    "actionable_warnings": 0,
                    "yosys_precheck": {"status": "success"},
                },
                "yosys_precheck": {
                    "status": "success",
                    "top_module": top_module,
                },
                "metrics": {"structural": {}},
                "issues": [],
            },
        )

    write_review("cpu_a")
    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]

    write_review("cpu_b")
    write_step_qor(step, workspace, True)
    second = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]

    assert first["inputs"]["top_module"] == "cpu_a"
    assert second["inputs"]["top_module"] == "cpu_b"
    assert second["fingerprint"] != first["fingerprint"]


def test_review_hotspot_preserves_original_report_index(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "review", "fe")
    _write(
        Path(step.report["dir"]) / "rtl_review.json",
        {
            "summary": {
                "actionable_errors": 0,
                "actionable_warnings": 1,
                "yosys_precheck": {"status": "success"},
            },
            "metrics": {"structural": {}},
            "issues": [
                {"title": "Waived", "ownership": "cpu", "waived": True},
                {
                    "title": "Actionable",
                    "ownership": "cpu",
                    "waived": False,
                    "severity": "warning",
                },
            ],
        },
    )

    write_step_qor(step, workspace, True)

    hotspots = json.loads(
        Path(step.analysis["qor_hotspots"]).read_text(encoding="utf-8")
    )["hotspots"]
    assert hotspots[0]["source"]["selector"] == "/issues/1"


def test_lint_hotspot_preserves_original_report_index(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "lint", "verilator")
    _write(
        Path(step.report["dir"]) / "lint_summary.json",
        {
            "summary": {"cpu_errors": 0, "cpu_warnings": 1, "warnings": 2},
            "diagnostics": [
                {"code": "SOC", "actionable": True, "ownership": "soc"},
                {
                    "code": "UNUSEDSIGNAL",
                    "actionable": True,
                    "ownership": "cpu",
                    "severity": "warning",
                },
            ],
        },
    )

    write_step_qor(step, workspace, True)

    hotspots = json.loads(
        Path(step.analysis["qor_hotspots"]).read_text(encoding="utf-8")
    )["hotspots"]
    assert hotspots[0]["source"]["selector"] == "/diagnostics/1"


def test_lint_top_changes_comparison_fingerprint(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "lint", "verilator")
    report_path = Path(step.report["dir"]) / "lint_summary.json"

    def write_lint(top_module: str) -> None:
        _write(
            report_path,
            {
                "top_module": top_module,
                "summary": {
                    "top_module": top_module,
                    "cpu_errors": 0,
                    "cpu_warnings": 0,
                    "warnings": 0,
                },
                "diagnostics": [],
            },
        )

    write_lint("soc_a")
    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]

    write_lint("soc_b")
    write_step_qor(step, workspace, True)
    second = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]

    assert first["inputs"]["top_module"] == "soc_a"
    assert second["inputs"]["top_module"] == "soc_b"
    assert second["fingerprint"] != first["fingerprint"]


def test_failed_qor_publication_rolls_back_the_complete_triplet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, step = _step(tmp_path, "lint", "verilator")
    report_path = Path(step.report["dir"]) / "lint_summary.json"
    _write(
        report_path,
        {
            "summary": {"cpu_errors": 0, "cpu_warnings": 0, "warnings": 0},
            "diagnostics": [],
        },
    )
    write_step_qor(step, workspace, True)
    previous_summary = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )

    _write(
        report_path,
        {
            "summary": {"cpu_errors": 0, "cpu_warnings": 1, "warnings": 1},
            "diagnostics": [],
        },
    )
    summary_path = Path(step.analysis["qor_summary"])
    real_replace = qor.os.replace

    def fail_summary_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == summary_path:
            raise OSError("simulated summary publication failure")
        real_replace(source, destination)

    monkeypatch.setattr(qor.os, "replace", fail_summary_replace)

    with pytest.raises(OSError, match="failed to persist QoR artifact triplet"):
        write_step_qor(step, workspace, True)

    metrics = json.loads(Path(step.analysis["qor_metrics"]).read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    hotspots = json.loads(
        Path(step.analysis["qor_hotspots"]).read_text(encoding="utf-8")
    )
    assert {
        metrics["generation"],
        summary["generation"],
        hotspots["generation"],
    } == {previous_summary["generation"]}
    assert not list(Path(step.analysis["dir"]).glob(".*.tmp"))
    assert not list(Path(step.analysis["dir"]).glob(".*.rollback"))


def test_sim_comparison_fingerprint_changes_with_workload(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "sim", "verilator")
    report_path = Path(step.report["dir"]) / "cases.json"
    _write(
        report_path,
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )
    write_step_qor(step, workspace, True)
    first = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))[
        "context"
    ]["comparison"]["fingerprint"]

    _write(
        report_path,
        {
            "suite": "cpu_tests",
            "cases": [{"name": "mul", "ok": True, "metrics": {"cycles": 10}}],
        },
    )
    write_step_qor(step, workspace, True)
    second = json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))[
        "context"
    ]["comparison"]["fingerprint"]

    assert first != second


def test_sim_comparison_fingerprint_tracks_harness_and_link_configuration(
    tmp_path: Path,
) -> None:
    workspace, step = _step(tmp_path, "sim", "verilator")
    testbench = tmp_path / "sim" / "main.cpp"
    testbench.parent.mkdir()
    testbench.write_text("int main() { return 0; }\n", encoding="utf-8")
    workspace["testbench"] = str(testbench)
    report_path = Path(step.report["dir"]) / "cases.json"
    _write(
        report_path,
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    def fingerprint() -> str:
        write_step_qor(step, workspace, True)
        return json.loads(
            Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
        )["context"]["comparison"]["fingerprint"]

    baseline = fingerprint()
    testbench.write_text("int main() { return 1; }\n", encoding="utf-8")
    assert fingerprint() != baseline

    testbench.write_text("int main() { return 0; }\n", encoding="utf-8")
    workspace["top_module"] = "alternate_sim_top"
    assert fingerprint() != baseline

    workspace["top_module"] = "ecos_sim_top"
    workspace["sim_cflags"] = ["-DSIM_MODE=1"]
    assert fingerprint() != baseline

    workspace["sim_cflags"] = []
    workspace["sim_ldflags"] = ["-lcustom_runtime"]
    assert fingerprint() != baseline


def test_sim_comparison_fingerprint_uses_effective_relative_include_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    include = checkout / "include"
    testbench = checkout / "sim" / "main.cpp"
    include.mkdir(parents=True)
    testbench.parent.mkdir(parents=True)
    header = include / "config.h"
    header.write_text("#define RESULT 1\n", encoding="utf-8")
    testbench.write_text(
        '#include "config.h"\nint main() { return RESULT; }\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(checkout))

    workspace, step = _step(tmp_path / "ws", "sim", "verilator")
    workspace["testbench"] = str(testbench)
    workspace["sim_cflags"] = ["-Iinclude"]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first_comparison = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    include_flag = next(
        item
        for item in first_comparison["inputs"]["sim_cflags"]
        if isinstance(item, dict) and item.get("option") == "-I"
    )
    assert include_flag["value"] == {"kind": "directory"}

    generated = include / "build" / "generated.o"
    generated.parent.mkdir()
    generated.write_bytes(b"first build output")
    write_step_qor(step, workspace, True)
    generated_fingerprint = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    generated.write_bytes(b"different build output")
    write_step_qor(step, workspace, True)
    assert (
        json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))[
            "context"
        ]["comparison"]["fingerprint"]
        == generated_fingerprint
    )

    header.write_text("#define RESULT 2\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    second_fingerprint = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert second_fingerprint != first_comparison["fingerprint"]


def test_sim_comparison_fingerprint_tracks_angle_include_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    include = checkout / "include"
    testbench = checkout / "sim" / "main.cpp"
    include.mkdir(parents=True)
    testbench.parent.mkdir(parents=True)
    header = include / "config.h"
    header.write_text("#define RESULT 1\n", encoding="utf-8")
    testbench.write_text(
        "#include <config.h>\nint main() { return RESULT; }\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(checkout))

    workspace, step = _step(tmp_path / "ws", "sim", "verilator")
    workspace["testbench"] = str(testbench)
    workspace["sim_cflags"] = ["-Iinclude"]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    assert first["inputs"]["harness_sources"][0]["local_headers"] == [
        {"include": "config.h", "sha256": qor._file_sha256(header)}
    ]

    header.write_text("#define RESULT 2\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    second = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert second != first["fingerprint"]


def test_sim_comparison_fingerprint_preserves_cpp_include_search_classes(
    tmp_path: Path,
) -> None:
    workspace, step = _step(tmp_path / "ws", "sim", "verilator")
    testbench = tmp_path / "main.cpp"
    quote_dir = tmp_path / "quote"
    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    for directory in (quote_dir, user_dir, system_dir):
        directory.mkdir()
    quote_config = quote_dir / "config.h"
    user_config = user_dir / "config.h"
    quote_angle = quote_dir / "angle.h"
    user_angle = user_dir / "angle.h"
    quote_config.write_text("#define CONFIG 1\n", encoding="utf-8")
    user_config.write_text("#define CONFIG 2\n", encoding="utf-8")
    quote_angle.write_text("#define ANGLE 1\n", encoding="utf-8")
    user_angle.write_text("#define ANGLE 2\n", encoding="utf-8")
    testbench.write_text(
        '#include "config.h"\n#include <angle.h>\nint main() { return CONFIG + ANGLE; }\n',
        encoding="utf-8",
    )
    workspace["testbench"] = str(testbench)
    workspace["sim_cflags"] = [
        f"-I{user_dir}",
        f"-isystem {system_dir}",
        f"-iquote {quote_dir}",
    ]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    assert first["inputs"]["harness_sources"][0]["local_headers"] == sorted(
        [
            {"include": "config.h", "sha256": qor._file_sha256(quote_config)},
            {"include": "angle.h", "sha256": qor._file_sha256(user_angle)},
        ],
        key=lambda item: (item["include"], item["sha256"]),
    )

    user_config.write_text("#define CONFIG 3\n", encoding="utf-8")
    quote_angle.write_text("#define ANGLE 3\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    assert (
        json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))[
            "context"
        ]["comparison"]["fingerprint"]
        == first["fingerprint"]
    )

    user_angle.write_text("#define ANGLE 4\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    assert (
        json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))[
            "context"
        ]["comparison"]["fingerprint"]
        != first["fingerprint"]
    )


def test_sim_comparison_fingerprint_uses_active_compiler_dependencies(
    tmp_path: Path,
) -> None:
    workspace, step = _step(tmp_path / "ws", "sim", "verilator")
    testbench = tmp_path / "main.cpp"
    active_header = tmp_path / "active.h"
    inactive_header = tmp_path / "inactive.h"
    active_header.write_text("#define ACTIVE 1\n", encoding="utf-8")
    inactive_header.write_text("#define INACTIVE 1\n", encoding="utf-8")
    testbench.write_text(
        '#include "active.h"\n#if 0\n#include "inactive.h"\n#endif\n'
        "int main() { return ACTIVE; }\n",
        encoding="utf-8",
    )
    workspace["testbench"] = str(testbench)
    build_directory = Path(step.directory) / "obj_dir"
    build_directory.mkdir()
    (build_directory / "main.d").write_text(
        f"main.o: {testbench} {active_header}\n",
        encoding="utf-8",
    )
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    assert first["inputs"]["harness_sources"][0]["local_headers"] == [
        {"include": "active.h", "sha256": qor._file_sha256(active_header)}
    ]

    inactive_header.write_text("#define INACTIVE 2\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    assert (
        json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))[
            "context"
        ]["comparison"]["fingerprint"]
        == first["fingerprint"]
    )

    active_header.write_text("#define ACTIVE 2\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    assert (
        json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))[
            "context"
        ]["comparison"]["fingerprint"]
        != first["fingerprint"]
    )


def test_sim_comparison_fingerprint_tracks_forced_include_dependencies(
    tmp_path: Path,
) -> None:
    workspace, step = _step(tmp_path / "ws", "sim", "verilator")
    testbench = tmp_path / "main.cpp"
    root_header = tmp_path / "forced.h"
    child_header = tmp_path / "child.h"
    testbench.write_text("int main() { return CHILD; }\n", encoding="utf-8")
    root_header.write_text('#include "child.h"\n', encoding="utf-8")
    child_header.write_text("#define CHILD 1\n", encoding="utf-8")
    workspace["testbench"] = str(testbench)
    workspace["sim_cflags"] = [f"-include {root_header}"]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    assert first["inputs"]["forced_headers"] == [
        {
            "include": root_header.name,
            "sha256": qor._file_sha256(root_header),
            "local_headers": [
                {"include": "child.h", "sha256": qor._file_sha256(child_header)}
            ],
        }
    ]

    child_header.write_text("#define CHILD 2\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    assert (
        json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))[
            "context"
        ]["comparison"]["fingerprint"]
        != first["fingerprint"]
    )


def test_sim_comparison_fingerprint_includes_automatic_soc_include(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    soc_root = checkout / "soc"
    testbench = checkout / "sim" / "main.cpp"
    soc_root.mkdir(parents=True)
    testbench.parent.mkdir(parents=True)
    header = soc_root / "soc_config.h"
    header.write_text("#define SOC_RESULT 1\n", encoding="utf-8")
    testbench.write_text(
        '#include "soc_config.h"\nint main() { return SOC_RESULT; }\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(checkout))

    workspace, step = _step(tmp_path / "ws", "sim", "verilator")
    workspace.update(
        {
            "testbench": str(testbench),
            "sim_cflags": [],
            "sim_soc_root": str(soc_root),
        }
    )
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first_comparison = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    assert any(
        isinstance(item, dict)
        and item.get("option") == "-I"
        and item.get("value", {}).get("kind") == "directory"
        for item in first_comparison["inputs"]["sim_cflags"]
    )

    header.write_text("#define SOC_RESULT 2\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    second_fingerprint = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert second_fingerprint != first_comparison["fingerprint"]


def test_sim_comparison_fingerprint_tracks_harness_local_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    testbench = tmp_path / "harness" / "main.cpp"
    testbench.parent.mkdir()
    header = testbench.parent / "config.h"
    header.write_text("#define RESULT 1\n", encoding="utf-8")
    testbench.write_text(
        '#include "config.h"\nint main() { return RESULT; }\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(checkout))

    workspace, step = _step(tmp_path / "ws", "sim", "verilator")
    workspace["testbench"] = str(testbench)
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    assert first["inputs"]["harness_sources"][0]["local_headers"] == [
        {
            "include": "config.h",
            "sha256": qor._file_sha256(header),
        }
    ]

    header.write_text("#define RESULT 2\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    second_fingerprint = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert second_fingerprint != first["fingerprint"]


def test_sim_comparison_fingerprint_tracks_compiler_response_file_inputs(
    tmp_path: Path,
) -> None:
    workspace, step = _step(tmp_path / "ws", "sim", "verilator")
    build_directory = Path(step.directory) / "obj_dir"
    build_directory.mkdir()
    testbench = tmp_path / "main.cpp"
    forced_header = Path(step.directory) / "forced.h"
    response_file = build_directory / "compile.rsp"
    testbench.write_text("int main() { return MODE; }\n", encoding="utf-8")
    forced_header.write_text("#define MODE 1\n", encoding="utf-8")
    response_file.write_text(
        "-DMODE=1 -include ../forced.h\n", encoding="utf-8"
    )
    workspace["testbench"] = str(testbench)
    workspace["sim_cflags"] = ["@compile.rsp"]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    response_identity = next(
        item
        for item in first["inputs"]["sim_cflags"]
        if isinstance(item, dict) and item.get("option") == "@"
    )
    assert response_identity["value"] == {
        "kind": "file",
        "sha256": qor._file_sha256(response_file),
    }
    assert response_identity["arguments"][1] == {
        "option": "-include",
        "value": {"kind": "file", "sha256": qor._file_sha256(forced_header)},
    }

    forced_header.write_text("#define MODE 2\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    header_changed = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert header_changed != first["fingerprint"]

    forced_header.write_text("#define MODE 1\n", encoding="utf-8")
    response_file.write_text(
        "-DMODE=2 -include ../forced.h\n", encoding="utf-8"
    )
    write_step_qor(step, workspace, True)
    response_changed = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert response_changed != first["fingerprint"]


def test_sim_comparison_fingerprint_hashes_direct_linker_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    testbench = tmp_path / "main.cpp"
    testbench.write_text("int main() { return 0; }\n", encoding="utf-8")
    first_library = tmp_path / "first" / "libmodel.a"
    second_library = tmp_path / "second" / "libmodel.a"
    first_library.parent.mkdir()
    second_library.parent.mkdir()
    first_library.write_bytes(b"same-library")
    second_library.write_bytes(b"same-library")
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(checkout))

    workspace, step = _step(tmp_path / "ws", "sim", "verilator")
    workspace["testbench"] = str(testbench)
    workspace["sim_ldflags"] = [str(first_library)]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    assert first["inputs"]["sim_ldflags"] == [
        {"kind": "file", "sha256": qor._file_sha256(first_library)}
    ]

    workspace["sim_ldflags"] = [str(second_library)]
    write_step_qor(step, workspace, True)
    relocated = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert relocated == first["fingerprint"]

    second_library.write_bytes(b"changed-library")
    write_step_qor(step, workspace, True)
    changed = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert changed != first["fingerprint"]


def test_sim_comparison_fingerprint_resolves_library_path_libraries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, step = _step(tmp_path, "sim", "verilator")
    testbench = tmp_path / "main.cpp"
    library_directory = tmp_path / "environment-lib"
    library_directory.mkdir()
    selected_library = library_directory / "libsim.so"
    selected_library.write_bytes(b"environment-library-v1")
    testbench.write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setenv("LIBRARY_PATH", str(library_directory))
    workspace["testbench"] = str(testbench)
    workspace["sim_ldflags"] = ["-lsim"]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    assert first["inputs"]["sim_ldflags"][0]["value"] == {
        "kind": "file",
        "sha256": qor._file_sha256(selected_library),
    }

    selected_library.write_bytes(b"environment-library-v2")
    write_step_qor(step, workspace, True)
    changed = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert changed != first["fingerprint"]


def test_sim_comparison_fingerprint_resolves_compiler_default_library(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, step = _step(tmp_path, "sim", "verilator")
    testbench = tmp_path / "main.cpp"
    selected_library = tmp_path / "compiler-default" / "libsim.a"
    selected_library.parent.mkdir()
    selected_library.write_bytes(b"compiler-library")
    testbench.write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.delenv("LIBRARY_PATH", raising=False)
    monkeypatch.setattr(
        qor,
        "_compiler_library_file",
        lambda filenames, base_directory=None, compiler_command=None: selected_library
        if "libsim.a" in filenames
        else None,
    )
    workspace["testbench"] = str(testbench)
    workspace["sim_ldflags"] = ["-lsim"]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    comparison = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["inputs"]
    assert comparison["sim_ldflags"][0]["value"] == {
        "kind": "file",
        "sha256": qor._file_sha256(selected_library),
    }


def test_compiler_default_library_probe_uses_verilator_link_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_directory = tmp_path / "obj_dir"
    build_directory.mkdir()
    (build_directory / "Vcpu.mk").write_text("all:\n\t@true\n", encoding="utf-8")
    selected_library = tmp_path / "toolchain" / "libsim.a"
    selected_library.parent.mkdir()
    selected_library.write_bytes(b"configured-link-library")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        if command[0] == "make":
            assert "$(LINK)" in kwargs["input"]
            return SimpleNamespace(
                returncode=0,
                stdout="__ECC_FE_LINK_DRIVER__=configured-c++\n",
            )
        assert command == [
            "configured-c++",
            "-m32",
            "--sysroot=/sdk",
            "-lsim",
            "-print-file-name=libsim.so",
        ]
        return SimpleNamespace(returncode=0, stdout=f"{selected_library}\n")

    monkeypatch.setenv("CXX", "wrong-environment-c++")
    monkeypatch.setattr(qor, "run_subprocess", fake_run)

    normalized = qor._normalized_shell_link_flags(
        ["-m32", "--sysroot=/sdk", "-lsim"],
        build_directory,
    )

    assert commands[0][:4] == [
        "make",
        "--no-print-directory",
        "-f",
        "Vcpu.mk",
    ]
    assert normalized[-1]["value"] == {
        "kind": "file",
        "sha256": qor._file_sha256(selected_library),
    }


def test_verilator_link_driver_expands_recursive_make_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_directory = tmp_path / "obj_dir"
    build_directory.mkdir()
    (build_directory / "Vcpu.mk").write_text(
        "LINK = $(CXX)\nCXX = configured-c++\nall:\n\t@true\n",
        encoding="utf-8",
    )

    def fake_run(command, **kwargs):
        assert command[-3:] == ["-", "-n"] or command[-2:] == ["-", "-n"]
        assert "$(LINK)" in kwargs["input"]
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "__ECC_FE_LINK_DRIVER__=configured-c++\n"
                "LINK = $(CXX)\n"
                "CXX = configured-c++\n"
            ),
        )

    monkeypatch.setattr(qor, "run_subprocess", fake_run)

    assert qor._verilator_link_driver(build_directory) == ["configured-c++"]


def test_linker_rpath_inputs_are_portable_across_workspaces(tmp_path: Path) -> None:
    normalized: list[list[object]] = []
    for workspace_name in ("ws_a", "ws_b"):
        build_directory = tmp_path / workspace_name / "obj_dir"
        runtime_directory = tmp_path / workspace_name / "runtime"
        transitive_directory = tmp_path / workspace_name / "transitive"
        build_directory.mkdir(parents=True)
        runtime_directory.mkdir()
        transitive_directory.mkdir()
        normalized.append(
            qor._normalized_shell_link_flags(
                [
                    f"-Wl,-rpath,{runtime_directory}",
                    f"-Wl,-rpath-link={transitive_directory}",
                ],
                build_directory,
            )
        )

    assert normalized[0] == normalized[1]
    assert normalized[0] == [
        {
            "option": "-Wl",
            "arguments": [
                {"option": "-rpath", "value": {"kind": "directory"}}
            ],
        },
        {
            "option": "-Wl",
            "arguments": [
                {"option": "-rpath-link", "value": {"kind": "directory"}}
            ],
        },
    ]


@pytest.mark.parametrize(
    "flag_template",
    [
        "-Wl,-T,{path}",
        "-Wl,--script={path}",
        "-Wl,--version-script,{path}",
    ],
)
def test_sim_comparison_fingerprint_hashes_wl_linker_inputs(
    tmp_path: Path,
    flag_template: str,
) -> None:
    workspace, step = _step(tmp_path, "sim", "verilator")
    testbench = tmp_path / "main.cpp"
    linker_script = tmp_path / "layout.ld"
    testbench.write_text("int main() { return 0; }\n", encoding="utf-8")
    linker_script.write_text("SECTIONS {}\n", encoding="utf-8")
    workspace["testbench"] = str(testbench)
    workspace["sim_ldflags"] = [flag_template.format(path=linker_script)]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    wl_flag = first["inputs"]["sim_ldflags"][0]
    file_identity = next(
        item["value"]
        for item in wl_flag["arguments"]
        if isinstance(item, dict) and "value" in item
    )
    assert file_identity == {
        "kind": "file",
        "sha256": qor._file_sha256(linker_script),
    }

    linker_script.write_text("SECTIONS { .text : { *(.text) } }\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    second = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert second != first["fingerprint"]


@pytest.mark.parametrize("prefix", ["-Wl,", "-Wl,@"])
def test_sim_comparison_fingerprint_hashes_wl_nested_file_inputs(
    tmp_path: Path,
    prefix: str,
) -> None:
    workspace, step = _step(tmp_path, "sim", "verilator")
    testbench = tmp_path / "main.cpp"
    linker_input = tmp_path / "link-input.rsp"
    testbench.write_text("int main() { return 0; }\n", encoding="utf-8")
    linker_input.write_text("--gc-sections\n", encoding="utf-8")
    workspace["testbench"] = str(testbench)
    workspace["sim_ldflags"] = [f"{prefix}{linker_input}"]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    nested = first["inputs"]["sim_ldflags"][0]["arguments"][0]
    expected_identity = {
        "kind": "file",
        "sha256": qor._file_sha256(linker_input),
    }
    assert nested == (
        {
            "option": "@",
            "value": expected_identity,
            "arguments": ["--gc-sections"],
        }
        if prefix.endswith("@")
        else expected_identity
    )

    linker_input.write_text("--no-gc-sections\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    assert (
        json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))[
            "context"
        ]["comparison"]["fingerprint"]
        != first["fingerprint"]
    )


def test_sim_comparison_fingerprint_tracks_linker_response_file_inputs(
    tmp_path: Path,
) -> None:
    workspace, step = _step(tmp_path / "ws", "sim", "verilator")
    build_directory = Path(step.directory) / "obj_dir"
    library_directory = Path(step.directory) / "lib"
    build_directory.mkdir()
    library_directory.mkdir()
    testbench = tmp_path / "main.cpp"
    selected_library = library_directory / "libmodel.a"
    response_file = build_directory / "link.rsp"
    testbench.write_text("int main() { return 0; }\n", encoding="utf-8")
    selected_library.write_bytes(b"response-library-v1")
    response_file.write_text("../lib/libmodel.a\n", encoding="utf-8")
    workspace["testbench"] = str(testbench)
    workspace["sim_ldflags"] = ["-Wl,@link.rsp"]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    response_identity = first["inputs"]["sim_ldflags"][0]["arguments"][0]
    assert response_identity["value"] == {
        "kind": "file",
        "sha256": qor._file_sha256(response_file),
    }
    assert response_identity["arguments"] == [
        {"kind": "file", "sha256": qor._file_sha256(selected_library)}
    ]

    selected_library.write_bytes(b"response-library-v2")
    write_step_qor(step, workspace, True)
    changed = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert changed != first["fingerprint"]


def test_sim_comparison_fingerprint_ignores_linker_map_output(
    tmp_path: Path,
) -> None:
    workspace, step = _step(tmp_path / "ws", "sim", "verilator")
    build_directory = Path(step.directory) / "obj_dir"
    build_directory.mkdir()
    testbench = tmp_path / "main.cpp"
    map_file = build_directory / "sim.map"
    testbench.write_text("int main() { return 0; }\n", encoding="utf-8")
    map_file.write_text("first generated map\n", encoding="utf-8")
    workspace["testbench"] = str(testbench)
    workspace["sim_ldflags"] = ["-Wl,-Map,sim.map"]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    assert first["inputs"]["sim_ldflags"] == [
        {
            "option": "-Wl",
            "arguments": [
                {"option": "-Map", "value": {"kind": "output"}},
            ],
        }
    ]

    map_file.write_text("second generated map\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    changed = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert changed == first["fingerprint"]


def test_sim_comparison_fingerprint_hashes_only_selected_search_library(
    tmp_path: Path,
) -> None:
    workspace, step = _step(tmp_path, "sim", "verilator")
    testbench = tmp_path / "main.cpp"
    library_dir = tmp_path / "lib"
    library_dir.mkdir()
    selected_library = library_dir / "libsim.a"
    unrelated_library = library_dir / "libunused.a"
    selected_library.write_bytes(b"selected-v1")
    unrelated_library.write_bytes(b"unrelated-v1")
    testbench.write_text("int main() { return 0; }\n", encoding="utf-8")
    workspace["testbench"] = str(testbench)
    workspace["sim_ldflags"] = [f"-L{library_dir}", "-lsim"]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    assert first["inputs"]["sim_ldflags"] == [
        {"option": "-L", "value": {"kind": "directory"}},
        {
            "option": "-l",
            "name": "sim",
            "mode": "dynamic-preferred",
            "value": {
                "kind": "file",
                "sha256": qor._file_sha256(selected_library),
            },
        },
    ]

    unrelated_library.write_bytes(b"unrelated-v2")
    write_step_qor(step, workspace, True)
    assert (
        json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))[
            "context"
        ]["comparison"]["fingerprint"]
        == first["fingerprint"]
    )

    selected_library.write_bytes(b"selected-v2")
    write_step_qor(step, workspace, True)
    assert (
        json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))[
            "context"
        ]["comparison"]["fingerprint"]
        != first["fingerprint"]
    )


def test_sim_comparison_fingerprint_resolves_flags_from_verilator_obj_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, step = _step(tmp_path / "ws", "sim", "verilator")
    build_directory = Path(step.directory) / "obj_dir"
    actual_include = Path(step.directory) / "headers"
    actual_library_dir = Path(step.directory) / "lib"
    checkout = tmp_path / "checkout"
    decoy_include = checkout / "headers"
    decoy_library_dir = checkout / "lib"
    for directory in (
        build_directory,
        actual_include,
        actual_library_dir,
        decoy_include,
        decoy_library_dir,
    ):
        directory.mkdir(parents=True)
    testbench = tmp_path / "main.cpp"
    actual_header = actual_include / "config.h"
    actual_library = actual_library_dir / "libsim.a"
    decoy_header = decoy_include / "config.h"
    decoy_library = decoy_library_dir / "libsim.a"
    testbench.write_text(
        '#include "config.h"\nint main() { return CONFIG; }\n', encoding="utf-8"
    )
    actual_header.write_text("#define CONFIG 1\n", encoding="utf-8")
    actual_library.write_bytes(b"actual-library")
    decoy_header.write_text("#define CONFIG 99\n", encoding="utf-8")
    decoy_library.write_bytes(b"decoy-library")
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(checkout))
    workspace["testbench"] = str(testbench)
    workspace["sim_cflags"] = ["-iquote../headers"]
    workspace["sim_ldflags"] = ["-L../lib", "-lsim"]
    (build_directory / "main.d").write_text(
        f"main.o: {testbench} {actual_header}\n", encoding="utf-8"
    )
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    comparison = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["inputs"]
    assert comparison["harness_sources"][0]["local_headers"] == [
        {"include": "config.h", "sha256": qor._file_sha256(actual_header)}
    ]
    assert comparison["sim_ldflags"][1]["value"] == {
        "kind": "file",
        "sha256": qor._file_sha256(actual_library),
    }


@pytest.mark.parametrize(
    "link_flags",
    [
        ["-static", "-lsim"],
        ["-lsim", "-static"],
        ["--static", "-lsim"],
        ["-lsim", "--static"],
        ["-Wl,-Bstatic", "-lsim"],
        ["-Wl,--static", "-lsim"],
    ],
    ids=[
        "driver-before",
        "driver-after",
        "driver-long-before",
        "driver-long-after",
        "linker-state",
        "linker-long-state",
    ],
)
def test_sim_comparison_fingerprint_honors_static_library_selection(
    tmp_path: Path,
    link_flags: list[str],
) -> None:
    workspace, step = _step(tmp_path, "sim", "verilator")
    testbench = tmp_path / "main.cpp"
    library_dir = tmp_path / "lib"
    library_dir.mkdir()
    shared_library = library_dir / "libsim.so"
    static_library = library_dir / "libsim.a"
    shared_library.write_bytes(b"shared-v1")
    static_library.write_bytes(b"static-v1")
    testbench.write_text("int main() { return 0; }\n", encoding="utf-8")
    workspace["testbench"] = str(testbench)
    workspace["sim_ldflags"] = [f"-L{library_dir}", *link_flags]
    _write(
        Path(step.report["dir"]) / "cases.json",
        {
            "suite": "cpu_tests",
            "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]
    library_identity = next(
        item
        for item in first["inputs"]["sim_ldflags"]
        if isinstance(item, dict) and item.get("option") == "-l"
    )
    assert library_identity == {
        "option": "-l",
        "name": "sim",
        "mode": "static",
        "value": {"kind": "file", "sha256": qor._file_sha256(static_library)},
    }

    shared_library.write_bytes(b"shared-v2")
    write_step_qor(step, workspace, True)
    assert (
        json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))[
            "context"
        ]["comparison"]["fingerprint"]
        == first["fingerprint"]
    )

    static_library.write_bytes(b"static-v2")
    write_step_qor(step, workspace, True)
    assert (
        json.loads(Path(step.analysis["qor_summary"]).read_text(encoding="utf-8"))[
            "context"
        ]["comparison"]["fingerprint"]
        != first["fingerprint"]
    )


@pytest.mark.parametrize(
    "link_flags",
    [
        ["-lsim", "-Wl,--static"],
        ["-Wl,-Bdynamic", "-static", "-lsim"],
    ],
    ids=["after-library", "driver-global-before-forwarded-dynamic"],
)
def test_sim_comparison_fingerprint_preserves_positional_linker_static_state(
    tmp_path: Path,
    link_flags: list[str],
) -> None:
    library_dir = tmp_path / "lib"
    library_dir.mkdir()
    shared_library = library_dir / "libsim.so"
    static_library = library_dir / "libsim.a"
    shared_library.write_bytes(b"shared")
    static_library.write_bytes(b"static")

    normalized = qor._normalized_shell_link_flags(
        [f"-L{library_dir}", *link_flags],
        tmp_path,
    )

    library_identity = next(
        item
        for item in normalized
        if isinstance(item, dict) and item.get("option") == "-l"
    )
    assert library_identity == {
        "option": "-l",
        "name": "sim",
        "mode": "dynamic-preferred",
        "value": {"kind": "file", "sha256": qor._file_sha256(shared_library)},
    }


def test_sim_comparison_fingerprint_normalizes_path_inputs(tmp_path: Path) -> None:
    fingerprints: list[str] = []
    references: list[Path] = []
    for workspace_name in ("ws_a", "ws_b"):
        root = tmp_path / workspace_name
        workspace, step = _step(root, "sim", "verilator")
        reference = root / "resources" / "riscv32-nemu-interpreter-so"
        reference.parent.mkdir(parents=True)
        reference.write_bytes(b"same-reference")
        references.append(reference)
        testbench = root / "sim" / "main.cpp"
        testbench.parent.mkdir()
        testbench.write_text("int main() { return 0; }\n", encoding="utf-8")
        workspace["testbench"] = str(testbench)
        workspace["sim_run_args"] = ["--diff", "--ref", str(reference)]
        workspace["sim_compile_extra_cflags"] = [
            "-I",
            str(root / "include"),
        ]
        include = root / "include"
        include.mkdir()
        (include / "difftest.h").write_text("#define CONFIG 1\n", encoding="utf-8")
        library_dir = root / "lib"
        library_dir.mkdir()
        (library_dir / "libsim.a").write_bytes(b"same-library")
        workspace["sim_cflags"] = [f"-I {include}"]
        workspace["sim_ldflags"] = [f"-L {library_dir}", "-lsim"]
        _write(
            Path(step.report["dir"]) / "cases.json",
            {
                "suite": "cpu_tests",
                "cases": [{"name": "add", "ok": True, "metrics": {"cycles": 10}}],
            },
        )

        write_step_qor(step, workspace, True)
        fingerprints.append(
            json.loads(
                Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
            )["context"]["comparison"]["fingerprint"]
        )

    assert fingerprints[0] == fingerprints[1]

    references[1].write_bytes(b"changed-reference")
    workspace, step = _step(tmp_path / "ws_b", "sim", "verilator")
    workspace["sim_run_args"] = ["--diff", "--ref", str(references[1])]
    workspace["sim_compile_extra_cflags"] = [
        "-I",
        str(tmp_path / "ws_b" / "include"),
    ]
    write_step_qor(step, workspace, True)
    changed = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert changed != fingerprints[0]


def test_comparison_fingerprint_ignores_workspace_paths_but_tracks_rtl_content(
    tmp_path: Path,
) -> None:
    fingerprints: list[str] = []
    source_paths: list[Path] = []
    header_paths: list[Path] = []
    for workspace_name in ("ws_a", "ws_b"):
        root = tmp_path / workspace_name
        workspace, step = _step(root, "prepare", "fe")
        source = root / "origin" / "cpu.sv"
        include_dir = root / "include"
        header = include_dir / "config.inc"
        source.parent.mkdir(parents=True, exist_ok=True)
        include_dir.mkdir(parents=True, exist_ok=True)
        source.write_text(
            '`include "config.inc"\nmodule cpu; endmodule\n', encoding="utf-8"
        )
        (source.parent / "sources.f").write_text(
            f"{source.resolve()}\n",
            encoding="utf-8",
        )
        header.write_text("`define CPU_WIDTH 32\n", encoding="utf-8")
        source_paths.append(source)
        header_paths.append(header)
        _write(
            step.report["step"],
            {
                "rtl_files": 1,
                "incdirs": 1,
                "defines": 1,
                "contracts": [{"id": "cpu_top", "status": "not_required"}],
            },
        )
        _write(
            Path(step.output["dir"]) / "prepared_inputs.json",
            {
                "rtl_files": [str(source)],
                "rtl_sources": [
                    {"path": str(source), "ownership": "cpu", "source": "cpu_filelist"}
                ],
                "incdirs": [str(include_dir)],
                "defines": ["SYNTHESIS"],
            },
        )
        write_step_qor(step, workspace, True)
        summary = json.loads(
            Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
        )
        fingerprints.append(summary["context"]["comparison"]["fingerprint"])

    assert fingerprints[0] == fingerprints[1]

    header_paths[1].write_text("`define CPU_WIDTH 64\n", encoding="utf-8")
    workspace, step = _step(tmp_path / "ws_b", "prepare", "fe")
    write_step_qor(step, workspace, True)
    header_changed = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert header_changed != fingerprints[0]

    header_paths[1].write_text("`define CPU_WIDTH 32\n", encoding="utf-8")
    source_paths[1].write_text(
        '`include "config.inc"\nmodule cpu; wire changed; endmodule\n',
        encoding="utf-8",
    )
    workspace, step = _step(tmp_path / "ws_b", "prepare", "fe")
    write_step_qor(step, workspace, True)
    changed = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert changed != fingerprints[0]


def test_comparison_fingerprint_preserves_rtl_and_define_order(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "prepare", "fe")
    first_source = tmp_path / "rtl" / "first.sv"
    second_source = tmp_path / "rtl" / "second.sv"
    first_source.parent.mkdir()
    first_source.write_text("`define VALUE 1\n", encoding="utf-8")
    second_source.write_text("module cpu; localparam V = `VALUE; endmodule\n", encoding="utf-8")
    _write(
        step.report["step"],
        {
            "rtl_files": 2,
            "incdirs": 0,
            "defines": 2,
            "contracts": [{"id": "cpu_top", "status": "not_required"}],
        },
    )
    manifest = Path(step.output["dir"]) / "prepared_inputs.json"
    payload = {
        "rtl_files": [str(first_source), str(second_source)],
        "rtl_sources": [{"path": str(first_source)}, {"path": str(second_source)}],
        "incdirs": [],
        "defines": ["FIRST=1", "SECOND=2"],
    }
    _write(manifest, payload)

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]

    payload["rtl_files"].reverse()
    payload["rtl_sources"].reverse()
    _write(manifest, payload)
    write_step_qor(step, workspace, True)
    rtl_reordered = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert rtl_reordered != first

    payload["rtl_files"].reverse()
    payload["rtl_sources"].reverse()
    payload["defines"].reverse()
    _write(manifest, payload)
    write_step_qor(step, workspace, True)
    defines_reordered = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]
    assert defines_reordered != first


def test_comparison_fingerprint_tracks_implicit_rtl_include_directories(
    tmp_path: Path,
) -> None:
    workspace, step = _step(tmp_path, "prepare", "fe")
    source = tmp_path / "rtl" / "cpu.sv"
    header = source.parent / "defines"
    source.parent.mkdir(parents=True)
    source.write_text(
        '`include "defines"\nmodule cpu; endmodule\n', encoding="utf-8"
    )
    header.write_text("`define CPU_WIDTH 32\n", encoding="utf-8")
    _write(
        step.report["step"],
        {
            "rtl_files": 1,
            "incdirs": 0,
            "defines": 0,
            "contracts": [{"id": "cpu_top", "status": "not_required"}],
        },
    )
    _write(
        Path(step.output["dir"]) / "prepared_inputs.json",
        {
            "rtl_files": [str(source)],
            "rtl_sources": [{"path": str(source)}],
            "incdirs": [],
            "defines": [],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]

    header.write_text("`define CPU_WIDTH 64\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    second = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]

    assert second != first


def test_comparison_fingerprint_preserves_include_search_order(tmp_path: Path) -> None:
    workspace, step = _step(tmp_path, "prepare", "fe")
    first_include = tmp_path / "include_a"
    second_include = tmp_path / "include_b"
    first_include.mkdir()
    second_include.mkdir()
    (first_include / "config.svh").write_text("`define VALUE 1\n", encoding="utf-8")
    (second_include / "config.svh").write_text("`define VALUE 2\n", encoding="utf-8")
    _write(
        step.report["step"],
        {
            "rtl_files": 1,
            "incdirs": 2,
            "defines": 0,
            "contracts": [{"id": "cpu_top", "status": "not_required"}],
        },
    )
    manifest = Path(step.output["dir"]) / "prepared_inputs.json"
    payload = {
        "rtl_files": [],
        "rtl_sources": [],
        "incdirs": [str(first_include), str(second_include)],
        "defines": [],
    }
    _write(manifest, payload)

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]

    payload["incdirs"] = [str(second_include), str(first_include)]
    _write(manifest, payload)
    write_step_qor(step, workspace, True)
    second = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]

    assert second != first


def test_comparison_fingerprint_tracks_symlinked_include_directories(
    tmp_path: Path,
) -> None:
    workspace, step = _step(tmp_path, "prepare", "fe")
    source = tmp_path / "rtl" / "cpu.sv"
    include_target = tmp_path / "include_target"
    source.parent.mkdir()
    include_target.mkdir()
    source.write_text(
        '`include "vendor/config.svh"\nmodule cpu; endmodule\n',
        encoding="utf-8",
    )
    header = include_target / "config.svh"
    header.write_text("`define CPU_WIDTH 32\n", encoding="utf-8")
    try:
        (source.parent / "vendor").symlink_to(include_target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")
    _write(
        step.report["step"],
        {
            "rtl_files": 1,
            "incdirs": 0,
            "defines": 0,
            "contracts": [{"id": "cpu_top", "status": "not_required"}],
        },
    )
    _write(
        Path(step.output["dir"]) / "prepared_inputs.json",
        {
            "rtl_files": [str(source)],
            "rtl_sources": [{"path": str(source)}],
            "incdirs": [],
            "defines": [],
        },
    )

    write_step_qor(step, workspace, True)
    first = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]

    header.write_text("`define CPU_WIDTH 64\n", encoding="utf-8")
    write_step_qor(step, workspace, True)
    second = json.loads(
        Path(step.analysis["qor_summary"]).read_text(encoding="utf-8")
    )["context"]["comparison"]["fingerprint"]

    assert second != first
