"""Line and toggle coverage of a simulation, and `report coverage`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fecompiler.cli import main as cli_main
from fecompiler.tools.common.process import ToolResult
from fecompiler.tools.fe.builder import build_step, build_step_space
from fecompiler.tools.verilator import coverage, testbench
from fecompiler.tools.verilator.sim import SimStep


def _key(file: str, kind: str, what: str, line: int = 1) -> str:
    return (
        f"\x01f\x02{file}\x01l\x02{line}\x01n\x021\x01t\x02{kind}"
        f"\x01page\x02v_{kind}/m\x01o\x02{what}\x01h\x02top.m"
    )


def _data(path: Path, points: dict[str, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    coverage.write_points(path, points)
    return path


# --- data files -------------------------------------------------------------


def test_points_are_read_back_as_written(tmp_path) -> None:
    points = {_key("/r/a.v", "line", "block"): 3, _key("/r/a.v", "toggle", "q:0->1"): 0}

    path = _data(tmp_path / "c.dat", points)

    assert path.read_text(encoding="utf-8").startswith("# SystemC::Coverage-3\nC '")
    assert coverage.read_points(path) == points


def test_a_missing_or_foreign_file_has_no_points(tmp_path) -> None:
    (tmp_path / "other.txt").write_text("hello\nC 'broken\n", encoding="utf-8")

    assert coverage.read_points(tmp_path / "gone.dat") == {}
    assert coverage.read_points(tmp_path / "other.txt") == {}


def test_merging_adds_the_counts_of_equal_points(tmp_path) -> None:
    line, toggle, other = _key("/r/a.v", "line", "block"), _key("/r/a.v", "toggle", "q"), _key("/r/b.v", "line", "block")
    first = _data(tmp_path / "1.dat", {line: 2, toggle: 0})
    second = _data(tmp_path / "2.dat", {line: 1, toggle: 0, other: 4})

    assert coverage.merge_points([first, second, tmp_path / "gone.dat"]) == {
        line: 3,
        toggle: 0,
        other: 4,
    }


def test_point_fields() -> None:
    assert coverage.point_fields(_key("/r/a.v", "toggle", "q[0]:0->1", line=7)) == {
        "f": "/r/a.v",
        "l": "7",
        "n": "1",
        "t": "toggle",
        "page": "v_toggle/m",
        "o": "q[0]:0->1",
        "h": "top.m",
    }


# --- the summary ------------------------------------------------------------


def test_summary_counts_covered_points_by_kind_file_and_ownership() -> None:
    points = {
        _key("/r/a.v", "line", "b1"): 2,
        _key("/r/a.v", "line", "b2"): 0,
        _key("/r/a.v", "toggle", "q:0->1"): 1,
        _key("/soc/t.v", "toggle", "x:0->1"): 0,
        _key("/soc/t.v", "toggle", "x:1->0"): 0,
        _key("/r/a.v", "branch", "if"): 5,
    }

    summary = coverage.summarise(
        points, ownership_of=lambda source: "cpu" if source.startswith("/r/") else "soc"
    )

    assert summary["kinds"] == {
        "line": {"covered": 1, "total": 2, "percent": 50.0},
        "toggle": {"covered": 1, "total": 3, "percent": 33.33},
        "branch": {"covered": 1, "total": 1, "percent": 100.0},
    }
    assert summary["by_ownership"]["soc"] == {
        "line": {"covered": 0, "total": 0, "percent": None},
        "toggle": {"covered": 0, "total": 2, "percent": 0.0},
    }
    assert summary["by_ownership"]["cpu"]["line"] == {"covered": 1, "total": 2, "percent": 50.0}
    assert [(item["file"], item["ownership"]) for item in summary["files"]] == [
        ("/r/a.v", "cpu"),
        ("/soc/t.v", "soc"),
    ]


def test_excluded_files_are_left_out_everywhere() -> None:
    points = {_key("/r/a.v", "line", "b"): 1, _key("/tb/tb.sv", "line", "b"): 0}

    summary = coverage.summarise(points, exclude=["/tb/tb.sv"])

    assert summary["kinds"]["line"] == {"covered": 1, "total": 1, "percent": 100.0}
    assert [item["file"] for item in summary["files"]] == ["/r/a.v"]
    assert summary["by_ownership"] == {}


def test_nothing_to_cover_is_not_zero_percent() -> None:
    assert coverage.summarise({})["kinds"] == {
        "line": {"covered": 0, "total": 0, "percent": None},
        "toggle": {"covered": 0, "total": 0, "percent": None},
    }


# --- the report -------------------------------------------------------------


def test_report_merges_the_cases_and_keeps_the_merged_data(tmp_path) -> None:
    line = _key("/r/a.v", "line", "b")
    first = _data(tmp_path / "c1" / "coverage.dat", {line: 0})
    second = _data(tmp_path / "c2" / "coverage.dat", {line: 1})

    report = coverage.write_report(
        [first, second, tmp_path / "c3" / "coverage.dat"],
        report_dir=tmp_path / "report",
        merged_path=tmp_path / "out" / "coverage.dat",
        cases=3,
    )

    assert (report["status"], report["cases"], report["cases_with_data"]) == ("ok", 3, 2)
    assert report["kinds"]["line"] == {"covered": 1, "total": 1, "percent": 100.0}
    assert coverage.read_points(report["data"]) == {line: 1}
    # Only the sum is kept.
    assert not first.exists() and not second.exists()
    assert json.loads((tmp_path / "report" / "coverage.json").read_text(encoding="utf-8")) == report
    assert coverage.brief(report, tmp_path / "report") == {
        "status": "ok",
        "report": str(tmp_path / "report" / "coverage.json"),
        "kinds": report["kinds"],
        "data": str(tmp_path / "out" / "coverage.dat"),
    }


def test_a_run_that_wrote_no_data_is_reported_as_empty(tmp_path) -> None:
    report = coverage.write_report(
        [tmp_path / "gone.dat"],
        report_dir=tmp_path / "report",
        merged_path=tmp_path / "out" / "coverage.dat",
        cases=1,
    )

    assert (report["status"], report["data"]) == ("empty", "")
    assert report["reason"] == "the simulation wrote no coverage data"
    assert not (tmp_path / "out" / "coverage.dat").exists()


def test_unavailable_coverage_is_recorded_with_its_reason(tmp_path) -> None:
    report = coverage.write_unavailable(tmp_path, "the harness does not write coverage")

    assert report["status"] == "unavailable"
    assert coverage.brief(report, tmp_path)["reason"] == "the harness does not write coverage"
    assert json.loads((tmp_path / "coverage.json").read_text(encoding="utf-8"))["files"] == []


def test_a_harness_writes_coverage_when_it_names_the_interface(tmp_path) -> None:
    old = tmp_path / "old.cpp"
    new = tmp_path / "new.cpp"
    old.write_text("int main() { top->final(); }\n", encoding="utf-8")
    new.write_text("int main() { contextp->coveragep()->write(path); }\n", encoding="utf-8")

    assert coverage.harness_writes_coverage([old, tmp_path / "gone.cpp"]) is False
    assert coverage.harness_writes_coverage([old, new]) is True


# --- testbench simulation ---------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    monkeypatch.delenv("ECC_FE_PROGRAM", raising=False)


def _testbench_workspace(tmp_path: Path, **parameters) -> tuple[dict, Path]:
    bench = tmp_path / "tb.sv"
    bench.write_text("module tb; endmodule\n", encoding="utf-8")
    directory = tmp_path / "ws"
    (directory / "home").mkdir(parents=True, exist_ok=True)
    path = directory / "home" / "parameters.json"
    path.write_text(
        json.dumps(
            {
                "frontend_design_kind": "generic_rtl",
                "testbench_top": "tb",
                "testbench_files": [str(bench)],
                **parameters,
            }
        ),
        encoding="utf-8",
    )
    return {"directory": str(directory), "design": "demo", "parameters_path": str(path)}, bench


def _fake_tools(monkeypatch, bench: Path, *, write_data: bool = True) -> list[list[str]]:
    commands: list[list[str]] = []

    def run_tool(command, **_options):
        commands.append([str(item) for item in command])
        if "--binary" in command:
            Path(command[command.index("-o") + 1]).write_text("#!/bin/sh\n", encoding="utf-8")
            return ToolResult(0, "", False)
        for argument in command:
            if str(argument).startswith("+verilator+coverage+file+") and write_data:
                _data(
                    Path(str(argument).removeprefix("+verilator+coverage+file+")),
                    {
                        _key("/design/dut.sv", "line", "b"): 1,
                        _key("/design/dut.sv", "toggle", "q"): 0,
                        _key(str(bench), "line", "b"): 0,
                    },
                )
        return ToolResult(0, "done\n", False)

    monkeypatch.setattr(testbench, "run_tool", run_tool)
    monkeypatch.setattr(testbench, "rtl_files", lambda _workspace: ["/design/dut.sv"])
    for name in ("verilator_incdir_args", "verilator_library_args", "verilator_define_args"):
        monkeypatch.setattr(testbench, name, lambda _workspace: [])
    return commands


def _run_sim(workspace: dict):
    step = build_step(workspace=workspace, step_name="sim", tool="verilator")
    build_step_space(step)
    SimStep().run(step, workspace)
    report_dir = Path(step.report["dir"])
    return step, json.loads((report_dir / "cases.json").read_text(encoding="utf-8"))


def test_a_testbench_simulation_collects_coverage_of_the_design(tmp_path, monkeypatch) -> None:
    workspace, bench = _testbench_workspace(tmp_path, testbench_coverage=True)
    commands = _fake_tools(monkeypatch, bench)

    step, cases = _run_sim(workspace)

    build, run = commands
    assert "--coverage-line" in build and "--coverage-toggle" in build
    data = Path(step.output["dir"]) / "tb_sim.coverage.dat"
    assert run[-1] == f"+verilator+coverage+file+{data}"
    assert not data.exists() and "coverage" not in cases["cases"][0]
    # The testbench's own points are not the design's.
    assert cases["coverage"]["kinds"] == {
        "line": {"covered": 1, "total": 1, "percent": 100.0},
        "toggle": {"covered": 0, "total": 1, "percent": 0.0},
    }
    report = json.loads((Path(step.report["dir"]) / "coverage.json").read_text(encoding="utf-8"))
    assert [item["file"] for item in report["files"]] == ["/design/dut.sv"]
    assert cases["coverage"]["data"] == str(Path(step.output["dir"]) / "coverage.dat")


def test_without_the_setting_nothing_about_coverage_is_done(tmp_path, monkeypatch) -> None:
    workspace, bench = _testbench_workspace(tmp_path)
    commands = _fake_tools(monkeypatch, bench)

    step, cases = _run_sim(workspace)

    assert not [item for command in commands for item in command if "coverage" in item]
    assert "coverage" not in cases and "coverage" not in cases["cases"][0]
    assert not (Path(step.report["dir"]) / "coverage.json").exists()


def test_a_report_of_an_earlier_run_does_not_outlive_the_setting(tmp_path, monkeypatch) -> None:
    workspace, bench = _testbench_workspace(tmp_path, testbench_coverage=True)
    _fake_tools(monkeypatch, bench)
    step, _ = _run_sim(workspace)
    assert (Path(step.report["dir"]) / "coverage.json").is_file()

    Path(workspace["parameters_path"]).write_text(
        json.dumps({"frontend_design_kind": "generic_rtl", "testbench_top": "tb", "testbench_files": [str(bench)]}),
        encoding="utf-8",
    )
    step, _ = _run_sim(workspace)

    assert not (Path(step.report["dir"]) / "coverage.json").exists()


def test_a_simulation_that_wrote_no_data_says_so(tmp_path, monkeypatch) -> None:
    workspace, bench = _testbench_workspace(tmp_path, testbench_coverage=True)
    _fake_tools(monkeypatch, bench, write_data=False)

    _, cases = _run_sim(workspace)

    assert cases["cases"][0]["ok"] is True
    assert cases["coverage"]["status"] == "empty"
    assert cases["coverage"]["reason"] == "the simulation wrote no coverage data"


# --- the command ------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path, capsys) -> Path:
    directory = tmp_path / "cli-ws"
    assert cli_main.run(["init", "--workspace", str(directory), "--json"]) == 0
    capsys.readouterr()
    return directory


def _numbers(covered: int, total: int) -> dict[str, object]:
    return {"covered": covered, "total": total, "percent": round(100 * covered / total, 2) if total else None}


def _write_report(workspace: Path, **overrides) -> Path:
    report = {
        "schema_version": 1,
        "status": "ok",
        "cases": 3,
        "cases_with_data": 3,
        "data": "/w/out/coverage.dat",
        "kinds": {"line": _numbers(30, 60), "toggle": _numbers(10, 100)},
        "by_ownership": {
            "cpu": {"line": _numbers(25, 40), "toggle": _numbers(8, 40)},
            "soc": {"line": _numbers(5, 20), "toggle": _numbers(2, 60)},
        },
        "files": [
            {"file": "/r/alu.v", "ownership": "cpu", "line": _numbers(20, 20), "toggle": _numbers(6, 20)},
            {"file": "/r/csr.v", "ownership": "cpu", "line": _numbers(5, 20), "toggle": _numbers(2, 20)},
            {"file": "/soc/uart.v", "ownership": "soc", "line": _numbers(5, 20), "toggle": _numbers(1, 30)},
            {"file": "/soc/top.v", "ownership": "soc", "line": _numbers(0, 0), "toggle": _numbers(1, 30)},
        ],
        **overrides,
    }
    path = workspace / "sim_verilator" / "report" / "coverage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _coverage(capsys, workspace: Path, *args: str) -> tuple[int, list[dict[str, object]]]:
    exit_code = cli_main.run(["report", "coverage", "--workspace", str(workspace), *args, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)["records"]


def test_report_coverage_lists_the_least_covered_files_first(workspace, capsys) -> None:
    path = _write_report(workspace)

    exit_code, (summary, *files) = _coverage(capsys, workspace)

    assert exit_code == 0
    assert summary["kind"] == "coverage"
    assert (summary["status"], summary["cases"], summary["files"], summary["shown"]) == ("ok", 3, 4, 4)
    assert summary["kinds"]["line"] == _numbers(30, 60)
    assert set(summary["by_ownership"]) == {"cpu", "soc"}
    assert summary["report"] == str(path)
    assert summary["annotate_cmd"] == "verilator_coverage --annotate coverage-annotated /w/out/coverage.dat"
    # Equal line coverage is ordered by toggle coverage; no line points sorts last.
    assert [item["file"] for item in files] == ["/soc/uart.v", "/r/csr.v", "/r/alu.v", "/soc/top.v"]
    assert files[0]["kind"] == "coverage_file"


def test_report_coverage_of_one_owner(workspace, capsys) -> None:
    _write_report(workspace)

    _, (summary, *files) = _coverage(capsys, workspace, "--ownership", "cpu", "--limit", "1")

    assert summary["ownership"] == "cpu"
    assert summary["kinds"] == {"line": _numbers(25, 40), "toggle": _numbers(8, 40)}
    assert summary["by_ownership"] is None
    assert (summary["files"], summary["shown"]) == (2, 1)
    assert [item["file"] for item in files] == ["/r/csr.v"]


def test_report_coverage_that_could_not_be_collected_says_why(workspace, capsys) -> None:
    _write_report(
        workspace,
        status="unavailable",
        reason="the simulation harness of this SoC does not write coverage data",
        data="",
        files=[],
        by_ownership={},
        kinds={"line": _numbers(0, 0), "toggle": _numbers(0, 0)},
    )

    exit_code, (summary,) = _coverage(capsys, workspace)

    assert exit_code == 0
    assert summary["status"] == "unavailable"
    assert summary["reason"].startswith("the simulation harness")
    assert "annotate_cmd" not in summary


def test_report_coverage_without_a_report_is_an_error(workspace, capsys) -> None:
    exit_code, (record,) = _coverage(capsys, workspace)

    assert exit_code == 1
    assert record["error"] == "coverage_not_found"
    assert "sim.coverage" in record["reason"] and "testbench.coverage" in record["reason"]


@pytest.mark.parametrize(
    ("arguments", "error"),
    [(["--ownership", "nobody"], "unknown_ownership"), (["--limit", "-1"], "invalid_limit")],
)
def test_report_coverage_usage_errors(workspace, capsys, arguments, error) -> None:
    _write_report(workspace)

    exit_code, records = _coverage(capsys, workspace, *arguments)

    assert exit_code == 2
    assert records[0]["error"] == error


def test_report_coverage_text_is_one_line_per_file(workspace, capsys) -> None:
    _write_report(workspace)

    assert cli_main.run(["report", "coverage", "--workspace", str(workspace), "--limit", "1"]) == 0

    assert capsys.readouterr().out.splitlines()[-1] == (
        "  line 25.0% (5/20)  toggle 3.3% (1/30)  /soc/uart.v  [soc]"
    )
