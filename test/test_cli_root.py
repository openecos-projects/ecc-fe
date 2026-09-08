from __future__ import annotations

import json
from pathlib import Path

from fecompiler.catalog.registry import catalog_payload
from fecompiler.cli import commands as cli_commands
from fecompiler.cli import main as cli_main
from fecompiler.resources import empty_resource_manifest, resource_manifest_path


def _isolate_xdg(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)


def _custom_cpu_source(module_name: str = "top") -> str:
    core = next(
        item for item in catalog_payload()["cores"] if item["id"] == "custom-filelist"
    )
    ports = []
    for port in core["required_cpu_top_port_contract"]:
        width = int(port["width"])
        packed = f" [{width - 1}:0]" if width > 1 else ""
        ports.append(f"  {port['direction']}{packed} {port['name']}")
    return f"module {module_name}(\n" + ",\n".join(ports) + "\n);\nendmodule\n"


def test_root_version_json_matches_ecc_schema(monkeypatch, tmp_path, capsys) -> None:
    _isolate_xdg(monkeypatch, tmp_path)

    exit_code = cli_main.run(["version", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["runtime"] == "ECC-FE CLI"
    assert "ecc_fe" in payload


def test_init_defaults_to_runnable_catalog_core(monkeypatch, tmp_path, capsys) -> None:
    _isolate_xdg(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"

    exit_code = cli_main.run(
        [
            "init",
            "--workspace",
            str(workspace),
            "--design",
            "smoke",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["records"][0]["status"] == "success"
    parameters = json.loads((workspace / "home" / "parameters.json").read_text())
    assert parameters["frontend_core_id"] == "picorv32"


def test_init_rtl_selects_custom_core_and_materializes_cpu_source(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    rtl = tmp_path / "top.sv"
    rtl.write_text(_custom_cpu_source(), encoding="utf-8")

    exit_code = cli_main.run(
        [
            "init",
            "--workspace",
            str(workspace),
            "--rtl",
            str(rtl),
            "--top",
            "top",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["records"][0]["status"] == "success"
    parameters = json.loads((workspace / "home" / "parameters.json").read_text())
    assert parameters["frontend_core_id"] == "custom-filelist"
    filelist = Path(parameters["cpu_filelist"])
    assert filelist.name == ".cpu_sources.f"
    assert str(rtl.resolve()) in filelist.read_text(encoding="utf-8")


def test_status_reports_stable_step_records(monkeypatch, tmp_path, capsys) -> None:
    _isolate_xdg(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    assert cli_main.run(["init", "--workspace", str(workspace), "--json"]) == 0
    capsys.readouterr()

    exit_code = cli_main.run(["status", "--workspace", str(workspace), "--jsonl"])

    assert exit_code == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[0]["kind"] == "workspace"
    assert [record["step"] for record in records[1:]] == [
        "prepare",
        "review",
        "elab",
        "lint",
        "sim",
    ]


def test_legacy_flag_shape_keeps_argparse_entry(monkeypatch, tmp_path) -> None:
    _isolate_xdg(monkeypatch, tmp_path)
    received: list[str] = []

    def fake_legacy(argv):
        received.extend(argv)
        return 7

    monkeypatch.setattr(cli_main, "_run_legacy", fake_legacy)

    assert cli_main.run(["--design", "legacy", "--top", "top"]) == 7
    assert received == ["--design", "legacy", "--top", "top"]


def test_init_design_option_is_not_misrouted_to_legacy(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli_main,
        "_run_legacy",
        lambda argv: (_ for _ in ()).throw(AssertionError("legacy route used")),
    )

    assert (
        cli_main.run(
            [
                "init",
                "--workspace",
                str(tmp_path / "workspace"),
                "--design",
                "new-cli",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["records"][0]["status"] == "success"


def test_resource_env_default_output_is_directly_evaluable(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)

    assert cli_main.run(["resource", "env", "--shell", "zsh"]) == 0

    output = capsys.readouterr().out
    assert output.startswith("export ")
    assert all(line.startswith("export ") for line in output.splitlines())
    assert "[resource.env]" not in output


def test_invalid_root_option_returns_usage_exit_without_traceback(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)

    assert cli_main.run(["status", "--does-not-exist"]) == 2

    captured = capsys.readouterr()
    assert "No such option" in captured.err
    assert "Traceback" not in captured.err


def test_required_install_rejects_specific_version_before_registry_access(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)

    assert (
        cli_main.run(
            [
                "resource",
                "install",
                "--required",
                "--version",
                "1",
                "--json",
            ]
        )
        == 2
    )

    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["records"][0]["error"] == "--version cannot be combined with --required"
    )


def test_resource_status_reports_missing_required_resource_after_partial_install(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)
    slang_root = tmp_path / "data" / "ecos-studio" / "tools" / "slang" / "1"
    slang = slang_root / "bin" / "slang"
    slang.parent.mkdir(parents=True)
    slang.write_text("#!/bin/sh\n", encoding="utf-8")
    slang.chmod(0o755)
    manifest = empty_resource_manifest()
    manifest["required_resources"] = ["tool:slang", "tool:riscv-toolchain"]
    manifest["installed"] = {
        "tool:slang": {
            "type": "tool",
            "name": "slang",
            "version": "1",
            "path": str(slang_root),
            "active": True,
            "managed": True,
        }
    }
    manifest_path = resource_manifest_path()
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert cli_main.run(["resource", "status", "--json"]) == 1

    records = json.loads(capsys.readouterr().out)["records"]
    installed, missing = records
    assert installed["resource_id"] == "tool:slang"
    assert installed["status"] == "ok"
    assert installed["required"] is True
    assert missing == {
        "kind": "resource_status",
        "resource_id": "tool:riscv-toolchain",
        "status": "missing",
        "required": True,
        "version": None,
        "active": False,
        "managed": True,
        "path": None,
        "missing_markers": [],
        "remediation_cmd": "ecc-fe resource install riscv-toolchain",
    }


def test_resource_status_fails_when_required_resource_is_inactive(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)
    slang_root = tmp_path / "data" / "ecos-studio" / "tools" / "slang" / "1"
    slang = slang_root / "bin" / "slang"
    slang.parent.mkdir(parents=True)
    slang.write_text("#!/bin/sh\n", encoding="utf-8")
    slang.chmod(0o755)
    manifest = empty_resource_manifest()
    manifest["required_resources"] = ["tool:slang"]
    manifest["installed"] = {
        "tool:slang": {
            "type": "tool",
            "name": "slang",
            "version": "1",
            "path": str(slang_root),
            "active": False,
            "managed": True,
        }
    }
    manifest_path = resource_manifest_path()
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert cli_main.run(["resource", "status", "--json"]) == 1

    record = json.loads(capsys.readouterr().out)["records"][0]
    assert record["resource_id"] == "tool:slang"
    assert record["status"] == "inactive"
    assert record["active"] is False


def test_log_reads_only_a_bounded_tail(monkeypatch, tmp_path, capsys) -> None:
    _isolate_xdg(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    assert cli_main.run(["init", "--workspace", str(workspace), "--json"]) == 0
    capsys.readouterr()
    log_path = workspace / "log" / "log.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as stream:
        stream.seek(cli_commands._LOG_TAIL_MAX_BYTES + 1024)
        stream.write(b"discarded\nlast-one\nlast-two\n")
    original_read_text = Path.read_text

    def reject_complete_log_read(path, *args, **kwargs):
        if path == log_path:
            raise AssertionError("log command loaded the complete file")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(
        Path,
        "read_text",
        reject_complete_log_read,
    )

    assert (
        cli_main.run(["log", "--workspace", str(workspace), "--lines", "2", "--json"])
        == 0
    )

    record = json.loads(capsys.readouterr().out)["records"][0]
    assert record["content"] == "last-one\nlast-two"


def test_doctor_uses_actionable_tool_specific_remediation(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_commands, "_find_executable", lambda candidates: None)

    assert cli_main.run(["doctor", "--json"]) == 1

    records = json.loads(capsys.readouterr().out)["records"]
    checks = {record.get("check"): record for record in records}
    assert checks["yosys"]["required"] is False
    assert checks["yosys"]["remediation_cmd"] == "ecc-fe resource install yosys"
    assert checks["riscv"]["remediation_cmd"] == (
        "ecc-fe resource install riscv-toolchain"
    )
    assert checks["cxx"]["remediation"] == (
        "Install a host C++ compiler and ensure c++ is on PATH."
    )
    assert checks["cxx"]["remediation_cmd"] is None


def test_existing_non_workspace_directory_is_rejected(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)

    assert cli_main.run(["status", "--workspace", str(tmp_path), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["records"][0]["error"] == "Frontend workspace was not found"


def test_run_does_not_mutate_existing_non_workspace_directory(
    monkeypatch, tmp_path, capsys
) -> None:
    _isolate_xdg(monkeypatch, tmp_path)

    assert cli_main.run(["run", "--workspace", str(tmp_path), "--json"]) == 1

    capsys.readouterr()
    assert not (tmp_path / "home").exists()
    assert not (tmp_path / "prepare_fe").exists()


def test_status_rejects_malformed_workspace_json(monkeypatch, tmp_path, capsys) -> None:
    _isolate_xdg(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    assert cli_main.run(["init", "--workspace", str(workspace), "--json"]) == 0
    capsys.readouterr()
    (workspace / "home" / "flow.json").write_text("{", encoding="utf-8")

    assert cli_main.run(["status", "--workspace", str(workspace), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert "Expecting property name" in payload["records"][0]["error"]
