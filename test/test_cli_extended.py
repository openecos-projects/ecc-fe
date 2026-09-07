from __future__ import annotations

import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

from fecompiler.cli import commands as cli_commands
from fecompiler.cli import main as cli_main
from fecompiler.cli.handlers import param as param_handlers


def _isolate_xdg(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)


def _json_call(capsys, *args: str) -> tuple[int, dict[str, object]]:
    exit_code = cli_main.run([*args, "--json"])
    captured = capsys.readouterr()
    return exit_code, json.loads(captured.out)


def _init_workspace(monkeypatch, tmp_path: Path, capsys) -> Path:
    _isolate_xdg(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    exit_code, payload = _json_call(
        capsys,
        "init",
        "--workspace",
        str(workspace),
        "--design",
        "cli-extended",
    )
    assert exit_code == 0
    assert payload["records"][0]["status"] == "success"
    return workspace


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_qor_triplet(workspace: Path, step_directory: str) -> None:
    analysis = workspace / step_directory / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    generation = "test-generation"
    payloads = {
        "qor_metrics.json": {
            "schema_version": 3,
            "generation": generation,
            "metrics": [{"id": "rtl_file_count", "value": 4}],
        },
        "qor_summary.json": {
            "schema_version": 4,
            "generation": generation,
            "analysis_status": "valid",
            "quality_status": "pass",
            "score": {"label": "Preparation readiness", "value": 100},
            "gates": [],
        },
        "qor_hotspots.json": {
            "schema_version": 3,
            "generation": generation,
            "hotspots": [],
        },
    }
    for filename, payload in payloads.items():
        (analysis / filename).write_text(json.dumps(payload), encoding="utf-8")


def test_init_creates_project_config_and_rejects_existing_workspace(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)

    with (workspace / "ecc-fe.toml").open("rb") as stream:
        config = tomllib.load(stream)
    assert config["schema_version"] == 1
    assert config["design"]["name"] == "cli-extended"
    assert config["defaults"]["design.frequency_mhz"] == 100.0
    assert config["params"] == {}

    exit_code, payload = _json_call(capsys, "init", "--workspace", str(workspace))
    assert exit_code == 1
    assert payload["records"][0]["error"] == "workspace_already_exists"


def test_param_set_diff_and_unset_sync_workspace_and_reset_flow(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    flow_path = workspace / "home" / "flow.json"
    flow = _read_json(flow_path)
    flow["steps"][0]["state"] = "Success"
    flow_path.write_text(json.dumps(flow), encoding="utf-8")

    exit_code, payload = _json_call(
        capsys,
        "param",
        "set",
        "design.frequency_mhz",
        "150",
        "--workspace",
        str(workspace),
    )
    assert exit_code == 0
    assert payload["records"][0]["flow_reset"] is True
    parameters = _read_json(workspace / "home" / "parameters.json")
    assert parameters["Frequency max [MHz]"] == 150.0
    assert {item["state"] for item in _read_json(flow_path)["steps"]} == {"Unstart"}
    with (workspace / "ecc-fe.toml").open("rb") as stream:
        config = tomllib.load(stream)
    assert config["params"] == {"design.frequency_mhz": 150.0}

    exit_code, payload = _json_call(
        capsys, "param", "diff", "--workspace", str(workspace)
    )
    assert exit_code == 0
    assert payload["records"][0] == {
        "kind": "param_diff",
        "param": "design.frequency_mhz",
        "value": 150.0,
        "default": 100.0,
        "source": "ecc-fe.toml",
    }

    exit_code, payload = _json_call(
        capsys,
        "param",
        "unset",
        "design.frequency_mhz",
        "--workspace",
        str(workspace),
    )
    assert exit_code == 0
    assert payload["records"][0]["status"] == "unset"
    parameters = _read_json(workspace / "home" / "parameters.json")
    assert parameters["Frequency max [MHz]"] == 100.0
    with (workspace / "ecc-fe.toml").open("rb") as stream:
        config = tomllib.load(stream)
    assert config["params"] == {}

    exit_code, payload = _json_call(
        capsys, "param", "diff", "--workspace", str(workspace)
    )
    assert exit_code == 0
    assert payload["records"] == [{"kind": "param_diff", "diff_status": "clean"}]

    exit_code, payload = _json_call(
        capsys, "config", "--resolved", "--workspace", str(workspace)
    )
    assert exit_code == 0
    frequency = next(
        item
        for item in payload["records"]
        if item.get("param") == "design.frequency_mhz"
    )
    assert frequency["value"] == 100.0
    assert frequency["source"] == "default"


def test_param_list_types_validation_and_dash_prefixed_value(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)

    exit_code, payload = _json_call(
        capsys, "param", "list", "--workspace", str(workspace)
    )
    assert exit_code == 0
    assert {item["param"] for item in payload["records"]} == {
        "design.frequency_mhz",
        "sim.compile_preset",
    }

    exit_code, payload = _json_call(
        capsys, "param", "list", "--all", "--workspace", str(workspace)
    )
    assert exit_code == 0
    assert len(payload["records"]) == 11

    original = (workspace / "ecc-fe.toml").read_text(encoding="utf-8")
    exit_code, payload = _json_call(
        capsys,
        "param",
        "set",
        "design.frequency_mhz",
        "not-a-number",
        "--workspace",
        str(workspace),
    )
    assert exit_code == 1
    assert payload["records"][0]["error"] == "invalid_value"
    assert (workspace / "ecc-fe.toml").read_text(encoding="utf-8") == original

    exit_code, payload = _json_call(
        capsys,
        "param",
        "set",
        "sim.compile_opt_level",
        "--value=-O3",
        "--workspace",
        str(workspace),
    )
    assert exit_code == 0
    assert payload["records"][0]["value"] == "-O3"
    assert (
        _read_json(workspace / "home" / "parameters.json")["sim_compile_opt_level"]
        == "-O3"
    )

    exit_code, payload = _json_call(
        capsys,
        "param",
        "list",
        "--step",
        "unknown",
        "--workspace",
        str(workspace),
    )
    assert exit_code == 2
    assert payload["records"][0]["error"] == "unknown_step"


def test_param_set_lazily_adds_config_to_legacy_workspace(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    config_path = workspace / "ecc-fe.toml"
    config_path.unlink()

    exit_code, _ = _json_call(capsys, "param", "list", "--workspace", str(workspace))
    assert exit_code == 0
    assert not config_path.exists()

    exit_code, payload = _json_call(
        capsys,
        "param",
        "set",
        "sim.compile_preset",
        "speed",
        "--workspace",
        str(workspace),
    )
    assert exit_code == 0
    assert payload["records"][0]["status"] == "set"
    assert config_path.is_file()


def test_param_set_does_not_write_when_flow_reset_fails(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    config_path = workspace / "ecc-fe.toml"
    parameters_path = workspace / "home" / "parameters.json"
    original_config = config_path.read_bytes()
    original_parameters = parameters_path.read_bytes()

    def fail_reset(command, payload, *, base_dir):
        assert command == "reset-flow"
        return SimpleNamespace(response="failed", data={}, message=["reset failed"])

    monkeypatch.setattr(
        param_handlers.workspace_application, "execute_payload", fail_reset
    )

    exit_code, payload = _json_call(
        capsys,
        "param",
        "set",
        "design.frequency_mhz",
        "180",
        "--workspace",
        str(workspace),
    )
    assert exit_code == 1
    assert payload["records"][0]["error"] == (
        "Unable to reset frontend flow after parameter change"
    )
    assert config_path.read_bytes() == original_config
    assert parameters_path.read_bytes() == original_parameters


def test_invalid_project_config_blocks_run_and_fails_doctor(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    config_path = workspace / "ecc-fe.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\n[unexpected]\nvalue = 1\n",
        encoding="utf-8",
    )

    exit_code, payload = _json_call(capsys, "run", "--workspace", str(workspace))
    assert exit_code == 1
    assert payload["records"][0]["error"] == "invalid_project_config"

    exit_code, payload = _json_call(capsys, "doctor", "--workspace", str(workspace))
    assert exit_code == 1
    config_check = next(
        item for item in payload["records"] if item.get("check") == "project_config"
    )
    assert config_check["status"] == "fail"
    assert config_check["required"] is True


def test_run_synchronizes_manual_toml_overrides_before_execution(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    config_path = workspace / "ecc-fe.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "[params]\n", '[params]\n"design.frequency_mhz" = 175\n'
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_execute(command, payload, *, base_dir):
        calls.append(command)
        return SimpleNamespace(response="success", data={}, message=[])

    monkeypatch.setattr(
        cli_commands.workspace_application, "execute_payload", fake_execute
    )

    exit_code, payload = _json_call(capsys, "run", "--workspace", str(workspace))
    assert exit_code == 0
    assert calls == ["reset-flow", "run-flow"]
    assert payload["records"][0]["config_changes"] == ["design.frequency_mhz"]
    assert (
        _read_json(workspace / "home" / "parameters.json")["Frequency max [MHz]"]
        == 175.0
    )


def test_run_restores_baseline_after_manual_override_removal(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    exit_code, _ = _json_call(
        capsys,
        "param",
        "set",
        "design.frequency_mhz",
        "175",
        "--workspace",
        str(workspace),
    )
    assert exit_code == 0
    config_path = workspace / "ecc-fe.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            '[params]\n"design.frequency_mhz" = 175.0\n', "[params]\n"
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_execute(command, payload, *, base_dir):
        calls.append(command)
        return SimpleNamespace(response="success", data={}, message=[])

    monkeypatch.setattr(
        cli_commands.workspace_application, "execute_payload", fake_execute
    )

    exit_code, payload = _json_call(capsys, "run", "--workspace", str(workspace))

    assert exit_code == 0
    assert calls == ["reset-flow", "run-flow"]
    assert payload["records"][0]["config_changes"] == ["design.frequency_mhz"]
    assert (
        _read_json(workspace / "home" / "parameters.json")["Frequency max [MHz]"]
        == 100.0
    )


def test_catalog_commands_expose_and_validate_existing_contracts(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)

    exit_code, payload = _json_call(
        capsys, "catalog", "list", "--kind", "core", "--status", "experimental"
    )
    assert exit_code == 0
    assert any(item["id"] == "picorv32" for item in payload["records"])
    assert {item["catalog_kind"] for item in payload["records"]} == {"core"}
    assert all("data" not in item for item in payload["records"])

    exit_code, payload = _json_call(capsys, "catalog", "show", "picorv32")
    assert exit_code == 0
    assert payload["records"][0]["data"]["id"] == "picorv32"

    exit_code, payload = _json_call(capsys, "catalog", "check")
    assert exit_code == 0
    assert payload["records"][0]["status"] == "pass"

    exit_code, payload = _json_call(capsys, "catalog", "list", "--status", "unknown")
    assert exit_code == 2
    assert payload["records"][0]["error"] == "invalid_catalog_status"

    exit_code, payload = _json_call(
        capsys,
        "catalog",
        "validate",
        "--core-id",
        "picorv32",
        "--test-suite-id",
        "smoke",
    )
    assert exit_code == 0
    assert payload["records"][0]["status"] == "pass"

    exit_code, payload = _json_call(capsys, "catalog", "show", "does-not-exist")
    assert exit_code == 1
    assert payload["records"][0]["error"] == "catalog_entry_not_found"


def test_report_qor_aggregates_frontend_details_and_lists_files(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    detail_path = workspace / "prepare_fe" / "report" / "frontend_detail.json"
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    detail_path.write_text(
        json.dumps({"summary": {"status": "Success", "source_files": 4}}),
        encoding="utf-8",
    )
    _write_qor_triplet(workspace, "prepare_fe")
    flow_path = workspace / "home" / "flow.json"
    flow = _read_json(flow_path)
    flow["steps"][0]["state"] = "Success"
    flow_path.write_text(json.dumps(flow), encoding="utf-8")
    output_path = tmp_path / "qor.json"

    exit_code, payload = _json_call(
        capsys,
        "report",
        "qor",
        "--workspace",
        str(workspace),
        "--output",
        str(output_path),
    )
    assert exit_code == 0
    record = payload["records"][0]
    assert record["status"] == "partial"
    assert record["steps"]["prepare"] == "success"
    assert record["summaries"]["prepare"]["source_files"] == 4
    assert record["qor"]["prepare"]["summary"]["quality_status"] == "pass"
    assert record["qor"]["prepare"]["summary"]["score"]["value"] == 100
    assert record["qor_generated_steps"] == 1
    assert record["path"] == str(output_path)
    assert _read_json(output_path)["kind"] == "frontend_qor"

    exit_code, payload = _json_call(
        capsys,
        "report",
        "files",
        "--step",
        "prepare",
        "--workspace",
        str(workspace),
    )
    assert exit_code == 0
    assert any(
        item["relative_path"] == "prepare_fe/report/frontend_detail.json"
        for item in payload["records"]
    )
    assert any(
        item["relative_path"] == "prepare_fe/analysis/qor_summary.json"
        and item["section"] == "analysis"
        for item in payload["records"]
    )

    assert cli_main.run(["report", "qor", "--workspace", str(workspace)]) == 0
    text = capsys.readouterr().out
    assert "prepare: success quality=pass score=100/100" in text
    assert '"metrics"' not in text

    exit_code, payload = _json_call(
        capsys,
        "report",
        "files",
        "--step",
        "unknown",
        "--workspace",
        str(workspace),
    )
    assert exit_code == 2
    assert payload["records"][0]["error"] == "unknown_step"


def test_report_qor_rejects_malformed_frontend_detail(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    detail_path = workspace / "review_fe" / "report" / "frontend_detail.json"
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    detail_path.write_text("[]", encoding="utf-8")

    exit_code, payload = _json_call(
        capsys, "report", "qor", "--workspace", str(workspace)
    )
    assert exit_code == 1
    assert payload["records"][0]["kind"] == "frontend_qor"
    assert payload["records"][1]["error"] == "invalid_report"


def test_report_qor_returns_failure_for_failed_flow(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    flow_path = workspace / "home" / "flow.json"
    flow = _read_json(flow_path)
    flow["steps"][0]["state"] = "Incomplete"
    flow_path.write_text(json.dumps(flow), encoding="utf-8")

    exit_code, payload = _json_call(
        capsys, "report", "qor", "--workspace", str(workspace)
    )
    assert exit_code == 1
    assert payload["records"][0]["status"] == "failed"


def test_report_qor_rejects_incomplete_qor_triplet(
    monkeypatch, tmp_path, capsys
) -> None:
    workspace = _init_workspace(monkeypatch, tmp_path, capsys)
    analysis = workspace / "lint_verilator" / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "qor_summary.json").write_text(
        json.dumps({"schema_version": 4, "generation": "partial"}),
        encoding="utf-8",
    )

    exit_code, payload = _json_call(
        capsys, "report", "qor", "--workspace", str(workspace)
    )
    assert exit_code == 1
    assert payload["records"][1]["error"] == "incomplete_qor_artifacts"
