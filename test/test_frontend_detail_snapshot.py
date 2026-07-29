from __future__ import annotations

import json
from pathlib import Path

from fecompiler.application import workspace_service
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import WorkspaceStep


class FakeEngine:
    def get_step(self, name: str, tool: str):
        return {
            "name": name,
            "tool": tool,
            "state": StateEnum.Success.value,
            "runtime": "00:00:01",
            "peak memory (mb)": 0.0,
            "info": {},
        }


def test_write_frontend_step_detail_persists_review_snapshot(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    report_dir = workspace_dir / "review_fe" / "report"
    log_dir = workspace_dir / "review_fe" / "log"
    output_dir = workspace_dir / "review_fe" / "output"
    report_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    review_path = report_dir / "rtl_review.json"
    review_path.write_text(
        json.dumps(
            {
                "scope": "cpu",
                "summary": {"status": "needs_attention", "warnings": 2},
                "issues": [{"severity": "warning", "title": "Review warning"}],
                "source_files": [],
            }
        ),
        encoding="utf-8",
    )
    step_report = report_dir / "review.rpt"
    step_report.write_text(json.dumps({"review": "pass"}), encoding="utf-8")

    step = WorkspaceStep(
        name="review",
        tool="fe",
        version="",
        directory=str(workspace_dir / "review_fe"),
        config={},
        input={},
        output={"dir": str(output_dir)},
        data={},
        feature={},
        report={"dir": str(report_dir), "step": str(step_report)},
        log={"file": str(log_dir / "log.txt")},
        script={},
        analysis={},
        subflow={"path": str(workspace_dir / "review_fe" / "subflow.json")},
        checklist={},
    )
    workspace = {
        "directory": str(workspace_dir),
        "home_path": str(workspace_dir / "home" / "home.json"),
    }

    detail_path = workspace_service._write_frontend_step_detail(
        workspace,
        FakeEngine(),
        step,
        StateEnum.Success,
    )

    snapshot = json.loads(Path(detail_path).read_text(encoding="utf-8"))
    assert snapshot["state"] == "Success"
    assert snapshot["review"]["summary"]["warnings"] == 2
    assert snapshot["review"]["path"] == str(review_path)
    assert snapshot["summary"]["status"] == "Success"

    workspace_service._remove_frontend_step_detail(step)
    assert not Path(detail_path).exists()


def test_write_frontend_step_detail_preserves_sim_program_artifacts(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    report_dir = workspace_dir / "sim_verilator" / "report"
    log_dir = workspace_dir / "sim_verilator" / "log"
    output_dir = workspace_dir / "sim_verilator" / "output"
    case_dir = output_dir / "cases" / "add.soc"
    report_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    case_dir.mkdir(parents=True)

    source = workspace_dir / "programs" / "add.c"
    source.parent.mkdir(parents=True)
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    program_paths = {
        "source": source,
        "elf": case_dir / "add.elf",
        "binary": case_dir / "add.bin",
        "image": case_dir / "add.soc.bin",
        "disassembly": case_dir / "add.txt",
    }
    for key, path in program_paths.items():
        if key != "source":
            path.write_bytes(b"artifact")

    cases_path = report_dir / "cases.json"
    cases_path.write_text(
        json.dumps({
            "suite": "cpu_tests",
            "run_id": "run-1",
            "cases": [{
                "name": "add.soc",
                "suite": "cpu_tests",
                "ok": True,
                "returncode": 0,
                "image": str(program_paths["image"]),
                "program": {key: str(path) for key, path in program_paths.items()},
            }],
        }),
        encoding="utf-8",
    )

    step = WorkspaceStep(
        name="sim",
        tool="verilator",
        version="",
        directory=str(workspace_dir / "sim_verilator"),
        config={},
        input={},
        output={"dir": str(output_dir)},
        data={},
        feature={},
        report={"dir": str(report_dir), "step": ""},
        log={"file": str(log_dir / "log.txt")},
        script={},
        analysis={},
        subflow={"path": str(workspace_dir / "sim_verilator" / "subflow.json")},
        checklist={},
    )
    workspace = {
        "directory": str(workspace_dir),
        "home_path": str(workspace_dir / "home" / "home.json"),
    }

    detail = workspace_service._build_frontend_step_detail(
        workspace,
        step,
        FakeEngine().get_step("sim", "verilator"),
    )

    assert detail["cases"][0]["program"] == {
        key: str(path) for key, path in program_paths.items()
    }
    artifact_labels = {item["label"] for item in detail["artifacts"]}
    assert "add.soc ELF" in artifact_labels
    assert "add.soc disassembly" in artifact_labels
    assert "add.soc source" in artifact_labels
