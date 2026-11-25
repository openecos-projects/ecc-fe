"""`report export`: lint diagnostics as SARIF, simulation cases as JUnit XML."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from fecompiler.analysis import ci_export
from fecompiler.cli import main as cli_main


def _diagnostic(**fields: object) -> dict[str, object]:
    return {
        "severity": "warning",
        "code": "WIDTHTRUNC",
        "message": "Operator ASSIGN expects 4 bits",
        "source": "/repo/rtl/adder.v",
        "line": 3,
        "column": 26,
        "category": "width",
        "ownership": "design",
        "actionable": True,
        **fields,
    }


def _run(diagnostics, base_dir="/repo", **options):
    log = ci_export.sarif_log(diagnostics, base_dir=Path(base_dir), **options)
    assert log["version"] == "2.1.0"
    assert log["$schema"] == ci_export.SARIF_SCHEMA
    (run,) = log["runs"]
    return run


# --- SARIF ---------------------------------------------------------------


def test_a_warning_becomes_a_result_with_its_rule_and_location():
    run = _run([_diagnostic()])

    assert run["tool"]["driver"]["name"] == "Verilator"
    assert run["tool"]["driver"]["rules"] == [
        {
            "id": "WIDTHTRUNC",
            "helpUri": "https://verilator.org/warn/WIDTHTRUNC",
            "properties": {"category": "width"},
        }
    ]
    assert run["originalUriBaseIds"] == {"SRCROOT": {"uri": "file:///repo/"}}
    assert run["results"] == [
        {
            "ruleId": "WIDTHTRUNC",
            "ruleIndex": 0,
            "level": "warning",
            "message": {"text": "Operator ASSIGN expects 4 bits"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": "rtl/adder.v", "uriBaseId": "SRCROOT"},
                        "region": {"startLine": 3, "startColumn": 26},
                    }
                }
            ],
            "properties": {"ownership": "design", "actionable": True, "category": "width"},
        }
    ]


def test_each_rule_is_listed_once_and_results_point_at_it():
    run = _run(
        [
            _diagnostic(code="WIDTHTRUNC"),
            _diagnostic(code="UNUSEDSIGNAL", category="unused"),
            _diagnostic(code="WIDTHTRUNC", line=9),
        ]
    )

    assert [rule["id"] for rule in run["tool"]["driver"]["rules"]] == ["WIDTHTRUNC", "UNUSEDSIGNAL"]
    assert [result["ruleIndex"] for result in run["results"]] == [0, 1, 0]


def test_an_error_has_the_error_level():
    (result,) = _run([_diagnostic(severity="error", code="PINMISSING")])["results"]

    assert result["level"] == "error"


def test_a_file_outside_the_base_directory_keeps_its_absolute_uri():
    (result,) = _run([_diagnostic(source="/opt/soc/top.v")])["results"]

    assert result["locations"][0]["physicalLocation"]["artifactLocation"] == {
        "uri": "file:///opt/soc/top.v"
    }


def test_paths_are_normalised_and_escaped():
    (result,) = _run([_diagnostic(source="/repo/rtl/../ip/my core/a#b.v")])["results"]

    assert result["locations"][0]["physicalLocation"]["artifactLocation"] == {
        "uri": "ip/my%20core/a%23b.v",
        "uriBaseId": "SRCROOT",
    }


def test_a_relative_source_is_taken_from_the_base_directory():
    (result,) = _run([_diagnostic(source="rtl/adder.v")])["results"]

    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "rtl/adder.v"


@pytest.mark.parametrize(
    ("line", "column", "region"),
    [
        (0, 0, None),
        (7, 0, {"startLine": 7}),
        (7, 2, {"startLine": 7, "startColumn": 2}),
        (None, None, None),
    ],
)
def test_a_region_is_written_only_for_a_known_position(line, column, region):
    (result,) = _run([_diagnostic(line=line, column=column)])["results"]

    assert result["locations"][0]["physicalLocation"].get("region") == region


def test_a_tool_failure_without_a_file_has_no_location_and_no_help_link():
    run = _run(
        [
            {
                "severity": "error",
                "code": "TOOL",
                "message": "verilator: command not found",
                "source": "",
                "line": 0,
                "column": 0,
                "category": "tool",
                "ownership": "tool",
                "actionable": False,
            }
        ]
    )

    assert run["tool"]["driver"]["rules"] == [{"id": "TOOL", "properties": {"category": "tool"}}]
    assert "locations" not in run["results"][0]


def test_a_diagnostic_without_a_code_is_filed_under_its_severity():
    run = _run([_diagnostic(code="")])

    assert run["results"][0]["ruleId"] == "WARNING"
    assert "helpUri" not in run["tool"]["driver"]["rules"][0]


@pytest.mark.parametrize(
    ("mark", "state"), [("known", "unchanged"), ("new", "new"), (None, None), ("", None)]
)
def test_the_lint_baseline_becomes_the_baseline_state(mark, state):
    (result,) = _run([_diagnostic(baseline=mark)])["results"]

    assert result.get("baselineState") == state


def test_run_properties_are_carried():
    run = _run([], properties={"design": "adder", "run": "default"})

    assert run["properties"] == {"design": "adder", "run": "default"}
    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []


def test_by_default_only_what_the_user_can_act_on_is_exported():
    own = _diagnostic()
    harness = _diagnostic(source="/opt/soc/top.v", ownership="soc", actionable=False)
    harness_error = _diagnostic(
        source="/opt/soc/top.v", ownership="soc", actionable=False, severity="error"
    )

    assert ci_export.exported_diagnostics(
        [own, harness, harness_error], everything=False
    ) == [own, harness_error]
    assert ci_export.exported_diagnostics(
        [own, harness, harness_error], everything=True
    ) == [own, harness, harness_error]


# --- JUnit ---------------------------------------------------------------


def _suite(cases, **options):
    options.setdefault("suite", "cpu-tests")
    options.setdefault("design", "picorv32")
    document, counts = ci_export.junit_xml(cases, **options)
    assert document.startswith('<?xml version="1.0" encoding="UTF-8"?>\n')
    root = ElementTree.fromstring(document.split("\n", 1)[1])
    assert root.tag == "testsuites"
    (suite,) = root
    assert {name: int(root.get(name)) for name in counts} == counts
    assert {name: int(suite.get(name)) for name in counts} == counts
    return suite, counts


def test_passing_and_failing_cases_become_testcases():
    suite, counts = _suite(
        [
            {"name": "add", "ok": True, "returncode": 0, "log": "/w/add/log.txt"},
            {
                "name": "div",
                "ok": False,
                "returncode": 1,
                "log": "/w/div/log.txt",
                "failure": {
                    "kind": "bad_trap",
                    "message": "The program terminated with a bad trap.",
                    "first_error": "HIT BAD TRAP at pc = 0x80000010",
                    "log_tail": "line a\nHIT BAD TRAP at pc = 0x80000010",
                },
            },
        ]
    )

    assert counts == {"tests": 2, "failures": 1, "errors": 0}
    assert (suite.get("name"), suite.get("skipped")) == ("cpu-tests", "0")
    add, div = suite.findall("testcase")
    assert (add.get("classname"), add.get("name")) == ("picorv32.cpu-tests", "add")
    assert add.find("failure") is None
    assert add.find("system-out").text == "log: /w/add/log.txt"
    failure = div.find("failure")
    assert failure.get("type") == "bad_trap"
    assert failure.get("message") == "The program terminated with a bad trap."
    assert failure.text == "HIT BAD TRAP at pc = 0x80000010\nline a\nHIT BAD TRAP at pc = 0x80000010"


def test_a_failure_without_details_still_says_something():
    suite, _ = _suite([{"name": "x", "ok": False, "returncode": 3}])

    failure = suite.find("testcase/failure")
    assert (failure.get("type"), failure.get("message")) == (
        "failed",
        "the simulation exited with status 3",
    )
    assert failure.text is None


def test_a_testbench_failure_carries_its_reason():
    suite, _ = _suite(
        [
            {
                "name": "tb",
                "ok": False,
                "returncode": 0,
                "failure": {"kind": "fail_pattern", "message": "the output matches FAIL"},
            }
        ],
        suite="testbench",
        design="adder",
    )

    testcase = suite.find("testcase")
    assert testcase.get("classname") == "adder.testbench"
    assert testcase.find("failure").get("message") == "the output matches FAIL"


def test_a_failed_step_without_cases_is_an_error_not_an_empty_pass():
    suite, counts = _suite([], step_error="the sim step failed")

    assert counts == {"tests": 1, "failures": 0, "errors": 1}
    testcase = suite.find("testcase")
    assert testcase.get("name") == "sim"
    error = testcase.find("error")
    assert (error.get("type"), error.get("message")) == ("step_failed", "the sim step failed")


def test_a_failed_step_adds_nothing_when_a_case_already_failed():
    _, counts = _suite([{"name": "x", "ok": False}], step_error="the sim step failed")

    assert counts == {"tests": 1, "failures": 1, "errors": 0}


def test_a_failed_step_whose_cases_all_passed_is_still_an_error():
    _, counts = _suite([{"name": "x", "ok": True}], step_error="the sim step failed")

    assert counts == {"tests": 2, "failures": 0, "errors": 1}


def test_output_that_xml_cannot_carry_is_removed():
    suite, _ = _suite(
        [
            {
                "name": "x",
                "ok": False,
                "failure": {
                    "kind": "k",
                    "message": "bad \x1b[31mred\x1b[0m \x00 <tag> & \"quoted\"",
                    "log_tail": "bell\x07 and \x1b[1mbold\x1b[0m é 中文",
                },
            }
        ]
    )

    failure = suite.find("testcase/failure")
    assert failure.get("message") == 'bad red  <tag> & "quoted"'
    assert failure.text == "bell and bold é 中文"


def test_properties_are_written_and_empty_ones_left_out():
    suite, _ = _suite([], properties={"design": "adder", "run": None, "top_module": ""})

    assert [(item.get("name"), item.get("value")) for item in suite.find("properties")] == [
        ("design", "adder")
    ]


# --- the command ---------------------------------------------------------


@pytest.fixture
def workspace(monkeypatch, tmp_path, capsys) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    monkeypatch.delenv("ECC_FE_PROGRAM", raising=False)
    directory = tmp_path / "ws"
    assert cli_main.run(["init", "--workspace", str(directory), "--json"]) == 0
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)
    return directory


def _export(capsys, workspace: Path, *args: str) -> tuple[int, dict[str, object]]:
    exit_code = cli_main.run(
        ["report", "export", "--workspace", str(workspace), *args, "--json"]
    )
    return exit_code, json.loads(capsys.readouterr().out)["records"][0]


def _write_lint(workspace: Path, diagnostics: list[dict[str, object]]) -> None:
    report = workspace / "lint_verilator" / "report"
    report.mkdir(parents=True, exist_ok=True)
    (report / "lint_summary.json").write_text(
        json.dumps({"schema_version": 1, "diagnostics": diagnostics}), encoding="utf-8"
    )


def _write_cases(workspace: Path, payload: dict[str, object]) -> None:
    report = workspace / "sim_verilator" / "report"
    report.mkdir(parents=True, exist_ok=True)
    (report / "cases.json").write_text(json.dumps(payload), encoding="utf-8")


def _set_state(workspace: Path, step: str, state: str) -> None:
    path = workspace / "home" / "flow.json"
    flow = json.loads(path.read_text(encoding="utf-8"))
    for entry in flow["steps"]:
        if entry["name"] == step:
            entry["state"] = state
    path.write_text(json.dumps(flow), encoding="utf-8")


def test_export_sarif_writes_the_users_diagnostics(workspace, tmp_path, capsys):
    _write_lint(
        workspace,
        [
            _diagnostic(source=str(tmp_path / "rtl" / "adder.v")),
            _diagnostic(source="/opt/soc/top.v", ownership="soc", actionable=False),
        ],
    )

    exit_code, record = _export(capsys, workspace, "--format", "sarif")

    assert exit_code == 0
    assert record["kind"] == "report_export"
    assert (record["format"], record["step"]) == ("sarif", "lint")
    assert (record["results"], record["warnings"], record["errors"], record["left_out"]) == (1, 1, 0, 1)
    assert record["path"] == str(tmp_path / "lint.sarif")
    assert record["base_dir"] == str(tmp_path)
    content = (tmp_path / "lint.sarif").read_text(encoding="utf-8")
    assert record["bytes"] == len(content.encode("utf-8"))
    (run,) = json.loads(content)["runs"]
    assert run["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"] == {
        "uri": "rtl/adder.v",
        "uriBaseId": "SRCROOT",
    }


def test_export_sarif_all_and_base_dir_and_output(workspace, tmp_path, capsys):
    _write_lint(
        workspace,
        [
            _diagnostic(source=str(tmp_path / "hw" / "rtl" / "adder.v")),
            _diagnostic(source="/opt/soc/top.v", ownership="soc", actionable=False),
        ],
    )

    exit_code, record = _export(
        capsys,
        workspace,
        "--format", "sarif",
        "--all",
        "--base-dir", str(tmp_path / "hw"),
        "--output", "out/scan.sarif",
    )

    assert exit_code == 0
    assert (record["results"], record["left_out"]) == (2, 0)
    assert record["path"] == str(tmp_path / "out" / "scan.sarif")
    (run,) = json.loads((tmp_path / "out" / "scan.sarif").read_text(encoding="utf-8"))["runs"]
    assert [
        result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        for result in run["results"]
    ] == ["rtl/adder.v", "file:///opt/soc/top.v"]


def test_export_sarif_of_a_clean_lint_is_a_log_without_results(workspace, tmp_path, capsys):
    _write_lint(workspace, [])

    exit_code, record = _export(capsys, workspace, "--format", "sarif")

    assert exit_code == 0
    assert record["results"] == 0
    assert json.loads((tmp_path / "lint.sarif").read_text(encoding="utf-8"))["runs"][0]["results"] == []


def test_export_sarif_before_lint_has_run_is_an_error(workspace, tmp_path, capsys):
    exit_code, record = _export(capsys, workspace, "--format", "sarif")

    assert exit_code == 1
    assert record["error"] == "lint_result_not_found"
    assert record["remediation_cmd"] == "ecc-fe run --to lint"
    assert not (tmp_path / "lint.sarif").exists()


def test_export_junit_writes_the_cases(workspace, tmp_path, capsys):
    _write_cases(
        workspace,
        {
            "schema_version": 1,
            "suite": "smoke",
            "cases": [
                {"name": "hello", "ok": True, "returncode": 0},
                {"name": "trap", "ok": False, "returncode": 1, "failure": {"kind": "bad_trap", "message": "bad trap"}},
            ],
        },
    )
    _set_state(workspace, "sim", "Incomplete")

    exit_code, record = _export(capsys, workspace, "--format", "junit")

    assert exit_code == 0
    assert (record["format"], record["step"], record["state"]) == ("junit", "sim", "incomplete")
    assert (record["tests"], record["failures"], record["errors"]) == (2, 1, 0)
    assert record["path"] == str(tmp_path / "sim.junit.xml")
    root = ElementTree.parse(tmp_path / "sim.junit.xml").getroot()
    assert [case.get("name") for case in root.iter("testcase")] == ["hello", "trap"]
    assert root.find("testsuite").get("name") == "smoke"


def test_export_junit_of_a_step_that_failed_before_any_case(workspace, tmp_path, capsys):
    _write_cases(workspace, {"schema_version": 1, "suite": "smoke", "cases": []})
    _set_state(workspace, "sim", "Incomplete")

    exit_code, record = _export(capsys, workspace, "--format", "junit", "-o", str(tmp_path / "r.xml"))

    assert exit_code == 0
    assert (record["tests"], record["failures"], record["errors"]) == (1, 0, 1)
    error = ElementTree.parse(tmp_path / "r.xml").getroot().find("testsuite/testcase/error")
    assert error.get("message") == "the sim step failed; see `ecc-fe log --step sim`"


def test_export_junit_before_sim_has_run_is_an_error(workspace, capsys):
    exit_code, record = _export(capsys, workspace, "--format", "junit")

    assert exit_code == 1
    assert record["error"] == "sim_result_not_found"
    assert record["remediation_cmd"] == "ecc-fe run --to sim"


def test_export_rejects_an_unknown_format(workspace, capsys):
    exit_code, record = _export(capsys, workspace, "--format", "html")

    assert exit_code == 2
    assert record["error"] == "unknown_export_format"
    assert record["expected"] == ["sarif", "junit"]


def test_export_rejects_a_report_that_is_not_one(workspace, capsys):
    report = workspace / "lint_verilator" / "report"
    report.mkdir(parents=True, exist_ok=True)
    (report / "lint_summary.json").write_text('{"diagnostics": "none"}', encoding="utf-8")

    exit_code, record = _export(capsys, workspace, "--format", "sarif")

    assert exit_code == 1
    assert record["error"] == "lint_result_not_found"
