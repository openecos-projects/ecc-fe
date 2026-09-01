from __future__ import annotations

import json
from pathlib import Path

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
        workspace["sim_run_args"] = ["--diff", "--ref", str(reference)]
        workspace["sim_compile_extra_cflags"] = [
            "-I",
            str(root / "include"),
        ]
        include = root / "include"
        include.mkdir()
        (include / "difftest.h").write_text("#define CONFIG 1\n", encoding="utf-8")
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
