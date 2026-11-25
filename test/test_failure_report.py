"""`report failure`: why a step failed, from the detail it left behind."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fecompiler.analysis.failure import diagnose
from fecompiler.cli import main as cli_main


def _categories(result: dict) -> list[str]:
    return [cause["category"] for cause in result["causes"]]


# --- states that are not failures -----------------------------------------


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("Success", "the lint step succeeded"),
        ("Ongoing", "the lint step did not finish: it is still running or was interrupted"),
        ("Unstart", "the lint step has not run"),
        ("", "the lint step has not run"),
        ("Pending", "the lint step is in state pending"),
    ],
)
def test_a_step_that_did_not_fail_has_no_causes(state, reason) -> None:
    assert diagnose({"step": "lint", "state": state}) == {
        "reason": reason,
        "causes": [],
        "advice": [],
    }


# --- lint -------------------------------------------------------------------


def _lint_detail(diagnostics: list[dict], baseline: dict | None = None) -> dict:
    return {
        "step": "lint",
        "state": "Incomplete",
        "lint": {
            "diagnostics": diagnostics,
            "baseline": baseline or {"gate": "off", "error": ""},
        },
    }


def test_lint_errors_are_the_cause_and_warnings_are_not() -> None:
    result = diagnose(
        _lint_detail(
            [
                {"severity": "warning", "code": "WIDTH", "message": "width", "source": "/r/a.v", "line": 1},
                {"severity": "error", "code": "PINMISSING", "message": "Cell has missing pin: 'x'", "source": "/r/a.v", "line": 7, "column": 3, "ownership": "design"},
                {"severity": "error", "code": "", "message": "syntax error", "source": "/r/b.v", "line": 2, "column": 0},
            ]
        )
    )

    assert result["reason"] == "the lint step failed: 2 lint errors"
    assert result["causes"] == [
        {
            "category": "lint_error",
            "message": "Cell has missing pin: 'x'",
            "rule": "PINMISSING",
            "ownership": "design",
            "source": "/r/a.v",
            "line": 7,
            "column": 3,
        },
        {"category": "lint_error", "message": "syntax error", "source": "/r/b.v", "line": 2},
    ]


def test_new_warnings_fail_the_baseline_gate() -> None:
    result = diagnose(
        _lint_detail(
            [
                {"severity": "warning", "code": "WIDTH", "message": "known", "source": "/r/a.v", "line": 1, "baseline": "known", "actionable": True},
                {"severity": "warning", "code": "UNUSED", "message": "new one", "source": "/r/a.v", "line": 9, "baseline": "new", "actionable": True},
                {"severity": "warning", "code": "UNUSED", "message": "new in the harness", "source": "/soc/t.v", "line": 3, "baseline": "new", "actionable": False},
            ],
            {"gate": "failed", "error": "", "path": "/p/base.json", "new_actionable": 1},
        )
    )

    assert result["reason"] == "the lint step failed: 1 warning not in the lint baseline"
    assert [(cause["category"], cause["message"]) for cause in result["causes"]] == [
        ("lint_new_warning", "new one")
    ]
    assert result["advice"] == [
        "fix the new warnings, waive them (lint.waiver_files), or accept them with "
        "`ecc-fe report lint-baseline`"
    ]


def test_a_baseline_that_cannot_be_read_is_the_cause() -> None:
    result = diagnose(
        _lint_detail(
            [{"severity": "warning", "message": "w", "baseline": "new", "actionable": True}],
            {"gate": "failed", "error": "cannot read lint baseline /p/base.json: bad", "path": "/p/base.json"},
        )
    )

    assert result["reason"] == "the lint step failed: the lint baseline could not be used"
    assert result["causes"] == [
        {
            "category": "baseline",
            "message": "cannot read lint baseline /p/base.json: bad",
            "source": "/p/base.json",
        }
    ]


# --- review, elab, prepare --------------------------------------------------


def test_review_names_what_the_tool_said_then_the_blocking_issues() -> None:
    result = diagnose(
        {
            "step": "review",
            "state": "Incomplete",
            "review": {
                "yosys_precheck": {"status": "failed", "reason": "ERROR: Module `\\m' is not part of the design."},
                "issues": [
                    {"severity": "error", "category": "hierarchy", "title": "Yosys could not resolve the top hierarchy", "recommendation": "Confirm the top module exists.", "source": "", "line": 0, "ownership": "design"},
                    {"severity": "warning", "category": "style", "title": "long line"},
                    {"severity": "error", "category": "latch", "title": "waived latch", "waived": True},
                ],
            },
            "qor": {
                "summary": {
                    "gates": [
                        {"id": "no_actionable_errors", "title": "No actionable RTL errors", "state": "failed", "metrics": [{"actual": 1, "operator": "==", "expected": 0}]},
                        {"id": "other", "title": "Other", "state": "pass"},
                    ]
                }
            },
        }
    )

    assert _categories(result) == ["tool", "review_issue", "gate"]
    assert result["reason"] == (
        "the review step failed: a tool could not finish, 1 blocking review issue"
    )
    assert result["causes"][1] == {
        "category": "review_issue",
        "message": "Yosys could not resolve the top hierarchy",
        "rule": "hierarchy",
        "advice": "Confirm the top module exists.",
        "ownership": "design",
    }
    assert result["causes"][2] == {
        "category": "gate",
        "message": "No actionable RTL errors (is 1, must be == 0)",
        "gate": "no_actionable_errors",
    }


def test_elab_names_errors_unresolved_modules_and_a_missing_top() -> None:
    result = diagnose(
        {
            "step": "elab",
            "state": "Incomplete",
            "elab": {
                "diagnostics": [
                    {"severity": "error", "message": "unknown module 'fifo'", "source": "/r/top.sv", "line": 4, "column": 3},
                    {"severity": "warning", "message": "unused"},
                ],
                "unresolved_modules": ["fifo"],
                "summary": {"top_found": False, "top_module": "chip"},
            },
        }
    )

    assert _categories(result) == ["elab_error", "unresolved_module", "elab_error"]
    assert result["reason"] == "the elab step failed: 2 elaboration errors, 1 unresolved module"
    assert result["causes"][1]["module"] == "fifo"
    assert result["causes"][2]["message"] == "top module chip was not found in the sources"
    assert result["advice"] == [
        "add the files that define the missing modules to the design's sources"
    ]


def test_prepare_names_the_inputs_that_are_not_usable() -> None:
    result = diagnose(
        {
            "step": "prepare",
            "state": "Incomplete",
            "prepare": {
                "contracts": [
                    {"label": "Custom RTL Input", "status": "Missing", "detail": "No custom input found"},
                    {"label": "Top Module", "status": "Warning", "detail": "x"},
                    {"label": "Difftest", "status": "Stub", "detail": "y"},
                    {"label": "CPU Filelist", "status": "OK", "detail": "/a.f"},
                ]
            },
        }
    )

    assert result["causes"] == [
        {"category": "input", "message": "Custom RTL Input: Missing", "detail": "No custom input found"}
    ]
    assert result["reason"] == "the prepare step failed: 1 input problem"


# --- sim --------------------------------------------------------------------


def test_sim_lists_the_failed_cases_with_their_evidence() -> None:
    result = diagnose(
        {
            "step": "sim",
            "state": "Incomplete",
            "cases": [
                {"name": "add", "ok": True},
                {
                    "name": "div",
                    "ok": False,
                    "returncode": 1,
                    "log": "/w/div/log.txt",
                    "wave": "/w/div/wave.vcd",
                    "failure": {"kind": "bad_trap", "message": "The program terminated with a bad trap.", "first_error": "HIT BAD TRAP at pc = 0x80000010"},
                },
                {"name": "mul", "ok": False, "returncode": 3},
                {"name": "loop", "ok": False, "failure": {"kind": "timeout", "message": "The simulation reached its cycle limit."}},
            ],
            "sim": {"regression": {"new_failures": ["div"]}},
        }
    )

    assert result["reason"] == "the sim step failed: 3 failed cases"
    assert result["causes"][0] == {
        "category": "sim_case",
        "message": "The program terminated with a bad trap.",
        "case": "div",
        "failure": "bad_trap",
        "evidence": "HIT BAD TRAP at pc = 0x80000010",
        "log": "/w/div/log.txt",
        "wave": "/w/div/wave.vcd",
        "newly_failing": True,
    }
    assert result["causes"][1] == {
        "category": "sim_case",
        "message": "the simulation exited with status 3",
        "case": "mul",
    }
    assert len(result["advice"]) == 2


def test_a_testbench_that_does_not_build_is_a_build_failure() -> None:
    result = diagnose(
        {
            "step": "sim",
            "state": "Incomplete",
            "cases": [
                {"name": "tb", "ok": False, "failure": {"kind": "compile", "message": "%Error: tb.sv:3: syntax error"}}
            ],
        }
    )

    assert result["reason"] == "the sim step failed: the simulation did not build"
    assert result["causes"][0]["category"] == "sim_build"


def test_sim_without_cases_reads_the_build_log(tmp_path) -> None:
    build_log = tmp_path / "build_programs.log.txt"
    build_log.write_text(
        "[build_program] name=add rc=2 src=/s/add.c\nadd.c:3:5: note: x\nadd.c:4:1: error: expected ';'\nerror: later\n",
        encoding="utf-8",
    )
    step_log = tmp_path / "log.txt"
    step_log.write_text("%Error: not this one\n", encoding="utf-8")

    result = diagnose(
        {
            "step": "sim",
            "state": "Incomplete",
            "cases": [],
            "logs": [
                {"label": "Step log", "path": str(step_log)},
                {"label": "Build programs log", "path": str(build_log)},
            ],
        }
    )

    assert result["causes"] == [
        {"category": "sim_build", "message": "add.c:4:1: error: expected ';'", "log": str(build_log)}
    ]


# --- when the results name nothing ------------------------------------------


def test_the_first_error_line_of_the_log_is_the_last_resort(tmp_path) -> None:
    log = tmp_path / "log.txt"
    log.write_text("starting\n  [ERROR] RuntimeError: verilator not found\nmore\n", encoding="utf-8")

    result = diagnose({"step": "elab", "state": "Incomplete", "log": str(log)})

    assert result["causes"] == [
        {"category": "log", "message": "[ERROR] RuntimeError: verilator not found", "log": str(log)}
    ]
    assert result["reason"] == "the elab step failed: [ERROR] RuntimeError: verilator not found"


def test_a_failed_gate_alone_still_looks_in_the_log(tmp_path) -> None:
    log = tmp_path / "log.txt"
    log.write_text("%Error: tool crashed\n", encoding="utf-8")

    result = diagnose(
        {
            "step": "lint",
            "state": "Incomplete",
            "log": str(log),
            "qor": {"summary": {"gates": [{"id": "g", "title": "Gate", "state": "failed"}]}},
        }
    )

    assert _categories(result) == ["gate", "log"]
    assert result["reason"] == "the lint step failed: Gate"


def test_nothing_to_go_on_says_so() -> None:
    assert diagnose({"step": "lint", "state": "Invalid"}) == {
        "reason": "the lint step failed and its results name no cause; read its log",
        "causes": [],
        "advice": [],
    }


def test_malformed_detail_does_not_break_the_diagnosis() -> None:
    result = diagnose(
        {
            "step": "sim",
            "state": "Incomplete",
            "cases": "none",
            "sim": [],
            "qor": {"summary": {"gates": ["x", None]}},
            "logs": [None, {"label": "Step log"}],
        }
    )

    assert result["causes"] == []


def test_long_messages_are_one_clipped_line() -> None:
    result = diagnose(
        _lint_detail([{"severity": "error", "message": "a\n  b " + "x" * 600}])
    )

    message = result["causes"][0]["message"]
    assert message.startswith("a b x") and len(message) == 300


# --- the command ------------------------------------------------------------


@pytest.fixture
def workspace(monkeypatch, tmp_path, capsys) -> Path:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    monkeypatch.delenv("ECC_FE_PROGRAM", raising=False)
    directory = tmp_path / "ws"
    assert cli_main.run(["init", "--workspace", str(directory), "--json"]) == 0
    capsys.readouterr()
    return directory


def _set_states(workspace: Path, **states: str) -> None:
    path = workspace / "home" / "flow.json"
    flow = json.loads(path.read_text(encoding="utf-8"))
    for entry in flow["steps"]:
        if entry["name"] in states:
            entry["state"] = states[entry["name"]]
    path.write_text(json.dumps(flow), encoding="utf-8")


def _write_detail(workspace: Path, directory: str, detail: dict) -> Path:
    report = workspace / directory / "report"
    report.mkdir(parents=True, exist_ok=True)
    (report / "frontend_detail.json").write_text(json.dumps(detail), encoding="utf-8")
    return report / "frontend_detail.json"


def _failure(capsys, workspace: Path, *args: str) -> tuple[int, list[dict[str, object]]]:
    exit_code = cli_main.run(["report", "failure", "--workspace", str(workspace), *args, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)["records"]


def _lint_failure(workspace: Path, errors: int = 2) -> Path:
    _set_states(workspace, prepare="Success", review="Success", elab="Success", lint="Incomplete")
    return _write_detail(
        workspace,
        "lint_verilator",
        {
            "step": "lint",
            "tool": "verilator",
            # The flow state decides, not what the detail last recorded.
            "state": "Success",
            "lint": {
                "diagnostics": [
                    {"severity": "error", "code": "PINMISSING", "message": f"missing pin {index}", "source": "/r/a.v", "line": index + 1, "column": 2}
                    for index in range(errors)
                ],
                "baseline": {"gate": "off"},
            },
        },
    )


def test_the_first_failed_step_is_explained(workspace, capsys) -> None:
    detail = _lint_failure(workspace)

    exit_code, (summary, *causes) = _failure(capsys, workspace)

    assert exit_code == 0
    assert summary == {
        "kind": "failure",
        "failed": True,
        "step": "lint",
        "tool": "verilator",
        "state": "incomplete",
        "reason": "the lint step failed: 2 lint errors",
        "causes": 2,
        "shown": 2,
        "advice": [],
        "detail": str(detail),
        "log_cmd": f"ecc-fe log --step lint --workspace {workspace}",
        "rerun_cmd": f"ecc-fe run --from lint --workspace {workspace}",
    }
    assert causes[0] == {
        "kind": "failure_cause",
        "step": "lint",
        "category": "lint_error",
        "message": "missing pin 0",
        "rule": "PINMISSING",
        "source": "/r/a.v",
        "line": 1,
        "column": 2,
    }


def test_the_list_is_limited_and_says_how_to_see_the_rest(workspace, capsys) -> None:
    _lint_failure(workspace, errors=5)

    _, (summary, *causes) = _failure(capsys, workspace, "--limit", "2")

    assert (summary["causes"], summary["shown"], len(causes)) == (5, 2, 2)
    assert summary["reason"] == "the lint step failed: 5 lint errors"
    assert summary["more_cmd"] == (
        f"ecc-fe report failure --step lint --workspace {workspace} --limit 5"
    )


def test_a_run_without_a_failed_step_says_so(workspace, capsys) -> None:
    _set_states(workspace, prepare="Success")

    exit_code, (record,) = _failure(capsys, workspace)

    assert exit_code == 0
    assert (record["failed"], record["step"]) == (False, None)
    assert record["reason"] == "no step of this run has failed"
    assert record["states"]["prepare"] == "success"
    assert record["status_cmd"] == f"ecc-fe status --workspace {workspace}"


def test_a_named_step_that_succeeded_is_reported_as_such(workspace, capsys) -> None:
    _lint_failure(workspace)

    exit_code, (record,) = _failure(capsys, workspace, "--step", "elab")

    assert exit_code == 0
    assert (record["failed"], record["state"]) == (False, "success")
    assert record["reason"] == "the elab step succeeded"
    assert "rerun_cmd" not in record


def test_a_failed_step_without_a_detail_file_falls_back(workspace, capsys) -> None:
    _set_states(workspace, prepare="Incomplete")

    exit_code, (record,) = _failure(capsys, workspace)

    assert exit_code == 0
    assert (record["failed"], record["step"], record["detail"]) == (True, "prepare", None)
    assert record["reason"] == (
        "the prepare step failed and its results name no cause; read its log"
    )


def test_a_detail_file_that_is_not_json_falls_back(workspace, capsys) -> None:
    _set_states(workspace, prepare="Success", review="Incomplete")
    report = workspace / "review_fe" / "report"
    report.mkdir(parents=True, exist_ok=True)
    (report / "frontend_detail.json").write_text("{not json", encoding="utf-8")

    exit_code, (record,) = _failure(capsys, workspace)

    assert exit_code == 0
    assert (record["step"], record["causes"]) == ("review", 0)


@pytest.mark.parametrize(
    ("arguments", "error"),
    [(["--step", "route"], "unknown_step"), (["--limit", "0"], "invalid_limit")],
)
def test_usage_errors(workspace, capsys, arguments, error) -> None:
    exit_code, records = _failure(capsys, workspace, *arguments)

    assert exit_code == 2
    assert records[0]["error"] == error


def test_text_output_is_one_block_per_cause(workspace, capsys) -> None:
    _lint_failure(workspace, errors=1)

    assert cli_main.run(["report", "failure", "--workspace", str(workspace)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "[report.failure]"
    assert "  reason: the lint step failed: 1 lint error" in lines
    assert lines[-1] == "  [lint_error]  /r/a.v:1:2  PINMISSING  missing pin 0"


def test_status_points_at_the_failure_report(workspace, capsys) -> None:
    _lint_failure(workspace)

    assert cli_main.run(["status", "--workspace", str(workspace), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)["records"][0]

    assert summary["failed_step"] == "lint"
    assert summary["failure_cmd"] == f"ecc-fe report failure --workspace {workspace}"


def test_status_of_a_run_without_failures_has_no_failure_command(workspace, capsys) -> None:
    assert cli_main.run(["status", "--workspace", str(workspace), "--json"]) == 0

    assert "failure_cmd" not in json.loads(capsys.readouterr().out)["records"][0]
