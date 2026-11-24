"""`runs list`, `runs delete`, `report compare`, and lock file hygiene."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from fecompiler.allflow.steps import step_directory
from fecompiler.cli import commands as cli_commands
from fecompiler.cli import main as cli_main
from fecompiler.data.step import StateEnum
from fecompiler.utility.workspace_lock import (
    discard_workspace_lock,
    workspace_lock,
    workspace_lock_path,
)


def _call(capsys, *args: str) -> tuple[int, list[dict[str, object]]]:
    exit_code = cli_main.run([*args, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)["records"]


@pytest.fixture
def project(monkeypatch, tmp_path, capsys) -> Path:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    directory = tmp_path / "project"
    assert _call(capsys, "init", str(directory))[0] == 0
    # Runs are created for real; only executing the flow is faked.
    real = cli_commands.workspace_application.execute_payload

    def execute(command, payload, *, base_dir, **kwargs):
        if command in {"run-flow", "run-step"}:
            return SimpleNamespace(response="success", data={}, message=[])
        return real(command, payload, base_dir=base_dir, **kwargs)

    monkeypatch.setattr(cli_commands.workspace_application, "execute_payload", execute)
    return directory


def _create_run(capsys, project: Path, run_id: str, *extra: str) -> Path:
    exit_code, _ = _call(
        capsys, "run", "--project", str(project), "--run-id", run_id, *extra
    )
    assert exit_code == 0
    return project / "runs" / run_id


def _set_states(run_dir: Path, **states: StateEnum) -> None:
    path = run_dir / "home" / "flow.json"
    flow = json.loads(path.read_text(encoding="utf-8"))
    for step in flow["steps"]:
        if step["name"] in states:
            step["state"] = states[step["name"]].value
    path.write_text(json.dumps(flow), encoding="utf-8")


def _write_qor(run_dir: Path, step: str, *, score: float, fingerprint: str, **metrics) -> None:
    analysis = step_directory(run_dir, step) / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    payloads = {
        "qor_metrics.json": {
            "schema_version": 3,
            "metrics": [
                {"id": name, "value": value, "unit": "count", "direction": direction}
                for name, (value, direction) in metrics.items()
            ],
        },
        "qor_summary.json": {
            "schema_version": 4,
            "quality_status": "pass",
            "score": {"value": score, "maximum": 100},
            "context": {"comparison": {"fingerprint": fingerprint}},
        },
        "qor_hotspots.json": {"schema_version": 3},
    }
    for name, payload in payloads.items():
        (analysis / name).write_text(
            json.dumps({**payload, "generation": "g1"}), encoding="utf-8"
        )


def test_runs_list_reports_every_run_with_its_state(project, capsys) -> None:
    _create_run(capsys, project, "default")
    fast = _create_run(capsys, project, "fast", "--set", "design.frequency_mhz=200")
    _set_states(fast, prepare=StateEnum.Success, review=StateEnum.Incomplete)
    (project / "runs" / ".hidden").mkdir()

    exit_code, records = _call(capsys, "runs", "list", "--project", str(project))

    assert exit_code == 0
    assert records[0] == {
        "kind": "run_list",
        "project": str(project),
        "count": 2,
        "selected": "default",
    }
    runs = {record["run"]: record for record in records[1:]}
    assert list(runs) == ["default", "fast"]
    assert runs["default"]["selected"] is True
    assert runs["default"]["state"] == "pending"
    assert runs["fast"]["state"] == "failed"
    assert runs["fast"]["failed_step"] == "review"
    assert runs["fast"]["steps_done"] == 1
    assert runs["fast"]["overrides"] == {"design.frequency_mhz": 200.0}
    assert runs["fast"]["modified"].endswith("Z")


def test_runs_list_on_a_new_project_is_empty_and_says_what_to_do(project, capsys) -> None:
    exit_code, records = _call(capsys, "runs", "list", "--project", str(project))

    assert exit_code == 0
    assert len(records) == 1
    assert records[0]["count"] == 0
    assert records[0]["next_cmd"].startswith("ecc-fe run")


def test_runs_list_marks_a_broken_run_instead_of_failing(project, capsys) -> None:
    run_dir = _create_run(capsys, project, "default")
    (run_dir / "home" / "flow.json").write_text("{", encoding="utf-8")

    exit_code, records = _call(capsys, "runs", "list", "--project", str(project))

    assert exit_code == 0
    assert records[1]["state"] == "unreadable"


def test_runs_commands_need_a_project(tmp_path, capsys) -> None:
    exit_code, records = _call(capsys, "runs", "list", "--project", str(tmp_path))

    assert exit_code == 1
    assert records[0]["error"] == "project_not_found"


def test_runs_delete_removes_the_run_and_its_lock_file(project, capsys) -> None:
    keep = _create_run(capsys, project, "default")
    gone = _create_run(capsys, project, "old")

    exit_code, records = _call(capsys, "runs", "delete", "old", "--project", str(project))

    assert exit_code == 0
    assert records == [{"kind": "run_deleted", "run": "old", "workspace": str(gone)}]
    assert sorted(path.name for path in (project / "runs").iterdir() if "old" in path.name) == []
    assert keep.is_dir()
    assert (project / ".ecc-fe" / "template").is_dir()


def test_runs_delete_reports_each_run_and_fails_if_any_is_missing(project, capsys) -> None:
    _create_run(capsys, project, "a")

    exit_code, records = _call(
        capsys, "runs", "delete", "a", "ghost", "--project", str(project)
    )

    assert exit_code == 1
    assert records[0]["kind"] == "run_deleted"
    assert records[1]["error"] == "run_not_found"
    assert not (project / "runs" / "a").exists()


@pytest.mark.parametrize("run_id", ["../template", ".hidden", "a/b"])
def test_runs_delete_rejects_anything_but_a_plain_run_id(project, capsys, run_id) -> None:
    exit_code, records = _call(
        capsys, "runs", "delete", run_id, "--project", str(project)
    )

    assert exit_code == 2
    assert records[0]["error"] == "invalid_run_id"
    assert (project / ".ecc-fe" / "template").is_dir()


def test_runs_delete_refuses_a_symlinked_run(project, tmp_path, capsys) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x", encoding="utf-8")
    (project / "runs").mkdir(exist_ok=True)
    os.symlink(outside, project / "runs" / "link")

    exit_code, records = _call(capsys, "runs", "delete", "link", "--project", str(project))

    assert exit_code == 1
    assert records[0]["error"] == "run_path_refused"
    assert (outside / "keep.txt").is_file()


def test_runs_delete_refuses_a_run_that_is_in_use(project, capsys) -> None:
    run_dir = _create_run(capsys, project, "busy")

    import threading

    result: list[tuple[int, list[dict[str, object]]]] = []
    with workspace_lock(run_dir):
        # Another thread stands in for another command.
        worker = threading.Thread(
            target=lambda: result.append(
                (cli_main.run(["runs", "delete", "busy", "--project", str(project), "--json"]), [])
            )
        )
        worker.start()
        worker.join()

    assert result[0][0] == 1
    assert json.loads(capsys.readouterr().out)["records"][0]["error"] == "workspace_busy"
    assert run_dir.is_dir()


def test_selector_on_a_missing_run_leaves_no_lock_file(project, capsys) -> None:
    exit_code, records = _call(
        capsys, "run", "--project", str(project), "--run-id", "ghost", "--resume"
    )

    assert exit_code == 1
    assert records[0]["error"] == "run_not_found"
    assert not workspace_lock_path(project / "runs" / "ghost").exists()


def test_lock_file_of_an_existing_workspace_is_kept(project, capsys) -> None:
    run_dir = _create_run(capsys, project, "default")

    assert discard_workspace_lock(run_dir) is False
    assert run_dir.is_dir()


def test_runs_delete_sweeps_orphan_lock_files(project, capsys) -> None:
    _create_run(capsys, project, "a")
    orphan = workspace_lock_path(project / "runs" / "ghost")
    orphan.write_text("", encoding="utf-8")

    assert _call(capsys, "runs", "delete", "a", "--project", str(project))[0] == 0

    assert not orphan.exists()


def _two_runs(capsys, project: Path) -> tuple[Path, Path]:
    base = _create_run(capsys, project, "base")
    cand = _create_run(capsys, project, "cand", "--set", "design.frequency_mhz=200")
    for run_dir in (base, cand):
        _set_states(run_dir, sim=StateEnum.Success)
    _write_qor(
        base, "sim", score=80, fingerprint="f1",
        total_cycles=(1000, "lower_is_better"),
        passed=(3, "higher_is_better"),
        files=(10, "trend_only"),
    )
    _write_qor(
        cand, "sim", score=85.5, fingerprint="f2",
        total_cycles=(900, "lower_is_better"),
        passed=(2, "higher_is_better"),
        files=(10, "trend_only"),
    )
    return base, cand


def test_compare_gives_no_score_delta_across_scoring_versions(project, capsys) -> None:
    _two_runs(capsys, project)
    summary_path = project / "runs" / "cand" / "sim_verilator" / "analysis" / "qor_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["score"]["scoring_version"] = 2
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    _, records = _call(capsys, "report", "compare", "base", "cand", "--project", str(project))

    sim = next(r for r in records if r["kind"] == "qor_step" and r["step"] == "sim")
    assert (sim["base_score"], sim["run_score"], sim["score_delta"]) == (80, 85.5, None)


def test_compare_reports_changed_metrics_with_a_verdict(project, capsys) -> None:
    _two_runs(capsys, project)

    exit_code, records = _call(
        capsys, "report", "compare", "base", "cand", "--project", str(project)
    )

    assert exit_code == 0
    summary = records[0]
    assert (summary["base"], summary["run"]) == ("base", "cand")
    assert summary["status"] == "regressed"
    assert (summary["regressed"], summary["improved"], summary["changed_metrics"]) == (1, 1, 2)
    assert summary["parameter_changes"]["Frequency max [MHz]"] == [100.0, 200.0] or any(
        200.0 in change for change in summary["parameter_changes"].values()
    )
    sim = next(r for r in records if r["kind"] == "qor_step" and r["step"] == "sim")
    assert (sim["base_score"], sim["run_score"], sim["score_delta"]) == (80, 85.5, 5.5)
    assert sim["same_inputs"] is False
    metrics = {r["metric"]: r for r in records if r["kind"] == "qor_metric"}
    assert set(metrics) == {"total_cycles", "passed"}
    assert metrics["total_cycles"]["verdict"] == "improved"
    assert metrics["total_cycles"]["delta"] == -100
    assert metrics["passed"]["verdict"] == "regressed"


def test_compare_all_includes_unchanged_metrics(project, capsys) -> None:
    _two_runs(capsys, project)

    _, records = _call(
        capsys, "report", "compare", "base", "cand", "--all", "--project", str(project)
    )

    metrics = {r["metric"]: r for r in records if r["kind"] == "qor_metric"}
    assert metrics["files"]["verdict"] == "same"


def test_compare_fail_on_regression_sets_the_exit_status(project, capsys) -> None:
    _two_runs(capsys, project)
    args = ("report", "compare", "--project", str(project), "--fail-on-regression")

    assert _call(capsys, *args, "base", "cand")[0] == 1
    assert _call(capsys, *args, "base", "base")[0] == 0


def test_compare_counts_a_step_that_no_longer_succeeds(project, capsys) -> None:
    base, cand = _two_runs(capsys, project)
    _set_states(base, lint=StateEnum.Success)
    _set_states(cand, lint=StateEnum.Incomplete)

    _, records = _call(
        capsys, "report", "compare", "base", "cand", "--project", str(project)
    )

    assert records[0]["regressed"] == 2


def test_compare_a_run_with_itself_is_same(project, capsys) -> None:
    _two_runs(capsys, project)

    exit_code, records = _call(
        capsys, "report", "compare", "base", "base", "--project", str(project)
    )

    assert exit_code == 0
    assert records[0]["status"] == "same"
    assert records[0]["parameter_changes"] == {}
    assert not [r for r in records if r["kind"] == "qor_metric"]


def test_compare_names_the_missing_run(project, capsys) -> None:
    _create_run(capsys, project, "base")

    exit_code, records = _call(
        capsys, "report", "compare", "base", "nope", "--project", str(project)
    )

    assert exit_code == 1
    assert records[0]["error"] == "run_not_found"
    assert records[0]["run"] == "nope"


def test_compact_text_output(project, capsys) -> None:
    _two_runs(capsys, project)

    assert cli_main.run(["runs", "list", "--project", str(project)]) == 0
    listing = capsys.readouterr().out
    assert cli_main.run(["report", "compare", "base", "cand", "--project", str(project)]) == 0
    comparison = capsys.readouterr().out

    assert "  cand:  pending  1/5 steps" in listing
    assert "design.frequency_mhz=200.0" in listing
    assert "  sim:  success  quality pass  score 80 -> 85.5  (inputs or settings differ)" in comparison
    assert "  sim.total_cycles: 1000 -> 900 (-100)  improved" in comparison
