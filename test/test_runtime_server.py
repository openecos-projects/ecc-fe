from __future__ import annotations

import json
from typing import Any

from fecompiler.application.workspace_service import CliResult
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace
from fecompiler.runtime.server import RuntimeServer
from fecompiler.runtime.workspace_api import WorkspaceRuntimeApi


class FakeApplication:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute_payload(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
        *,
        base_dir: object = None,
        event_sink=None,
    ) -> CliResult:
        del base_dir
        data = dict(payload or {})
        self.calls.append((command, data))
        if command == "catalog-list":
            return CliResult(command, "success", {"cores": []}, ["catalog"])
        if command == "validate-config":
            return CliResult(command, "failed", {"ok": False}, ["invalid config"])
        if command in {"create", "load"}:
            directory = str(data["directory"])
            return CliResult(command, "success", {"directory": directory}, ["ready"])
        if command == "get-home":
            return CliResult(command, "success", {"path": "/tmp/home.json"}, [])
        if command == "get-info":
            return CliResult(
                command,
                "success",
                {"id": data["id"], "info": {}, "step": data["step"]},
                [],
            )
        if command == "refresh-config":
            return CliResult(
                command,
                "success",
                {"directory": data["directory"], "refreshed": True},
                [],
            )
        if command == "sync-config":
            return CliResult(
                command,
                "success",
                {
                    "config_path": data["config_path"],
                    "directory": data["directory"],
                    "parameters_changed": False,
                    "refreshed": True,
                },
                [],
            )
        if command == "reset-flow":
            return CliResult(command, "success", {"directory": data["directory"]}, [])
        if command == "run-flow":
            if event_sink:
                event_sink({"type": "event", "phase": "started", "data": {}})
            return CliResult(command, "success", {"rerun": data["rerun"]}, [])
        if command == "run-step":
            if event_sink:
                event_sink(
                    {
                        "type": "event",
                        "phase": "completed",
                        "data": {"step": data["step"]},
                    },
                )
            return CliResult(
                command,
                "success",
                {"state": "Success", "step": data["step"]},
                [],
            )
        raise AssertionError(f"unexpected command: {command}")


def _request(method: str, params: dict[str, Any] | None = None, *, request_id=1):
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return json.loads(RuntimeServer().dispatch(json.dumps(payload)))


def _dispatch(server: RuntimeServer, method: str, params=None, *, request_id=1):
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return json.loads(server.dispatch(json.dumps(payload)))


def test_rpc_hello_reports_frontend_capabilities() -> None:
    response = _request("rpc.hello", {"version": 1})

    assert response["result"]["version"] == 1
    assert "frontend.catalog" in response["result"]["capabilities"]
    assert "flow.run_step" in response["result"]["capabilities"]


def test_rpc_hello_rejects_incompatible_version() -> None:
    response = _request("rpc.hello", {"version": 2})

    assert response["error"]["code"] == -32001
    assert response["error"]["message"] == "unsupported_version"


def test_workspace_session_lifecycle_and_flow_events(tmp_path) -> None:
    application = FakeApplication()
    notifications: list[dict[str, Any]] = []
    server = RuntimeServer(
        WorkspaceRuntimeApi(application=application),
        notification_sink=notifications.append,
    )
    directory = str(tmp_path / "workspace")

    created = _dispatch(server, "workspace.create", {"directory": directory})
    workspace_id = created["result"]["workspaceId"]
    assert created["result"]["directory"] == directory

    run = _dispatch(
        server,
        "flow.run_step",
        {"workspaceId": workspace_id, "step": "sim", "rerun": True},
    )
    assert run["result"]["state"] == "Success"
    assert notifications[0]["method"] == "runtime.event"
    assert notifications[0]["params"]["data"]["workspaceId"] == workspace_id
    assert notifications[0]["params"]["data"]["step"] == "sim"

    closed = _dispatch(server, "workspace.close", {"workspaceId": workspace_id})
    assert closed["result"] == {"ok": True}
    missing = _dispatch(server, "workspace.home", {"workspaceId": workspace_id})
    assert missing["error"]["code"] == -32010


def test_real_run_step_emits_each_saved_subflow_stage(tmp_path) -> None:
    rtl = tmp_path / "chip_top.v"
    rtl.write_text("module chip_top(); endmodule\n", encoding="utf-8")
    directory = tmp_path / "workspace"
    create_workspace(
        CreateWorkspaceData(
            directory=str(directory),
            origin_verilog=str(rtl),
            parameters={"Design": "demo", "Top module": "chip_top"},
        ),
    )
    notifications: list[dict[str, Any]] = []
    server = RuntimeServer(notification_sink=notifications.append)
    opened = _dispatch(server, "workspace.open", {"directory": str(directory)})

    result = _dispatch(
        server,
        "flow.run_step",
        {
            "workspaceId": opened["result"]["workspaceId"],
            "step": "prepare",
            "rerun": True,
        },
    )

    assert result["result"]["state"] == "Success"
    events = [notification["params"] for notification in notifications]
    subflow_events = [event for event in events if event["phase"] == "subflow.stage"]
    pending_events = [
        event for event in subflow_events if event["data"]["state"] == "Unstart"
    ]
    completed_events = [
        event for event in subflow_events if event["data"]["state"] == "Success"
    ]
    expected_steps = [
        "collect inputs",
        "merge filelist",
        "persist state",
        "report",
    ]
    assert [event["data"]["subflow_step"] for event in pending_events] == expected_steps
    assert [event["data"]["subflow_step"] for event in completed_events] == expected_steps
    assert all(event["data"]["step"] == "prepare" for event in subflow_events)
    assert events.index(subflow_events[0]) < next(
        index for index, event in enumerate(events) if event["phase"] == "completed"
    )

    persisted = json.loads(
        (directory / "prepare_fe" / "subflow.json").read_text(encoding="utf-8"),
    )
    assert persisted["steps"][-1]["state"] == "Success"


def test_frontend_validation_returns_structured_failed_result() -> None:
    application = FakeApplication()
    server = RuntimeServer(WorkspaceRuntimeApi(application=application))

    response = _dispatch(
        server,
        "frontend.validate_config",
        {"core_id": "custom-filelist"},
    )

    assert response["result"]["ok"] is False
    assert response["result"]["response"] == "failed"
    assert response["result"]["message"] == ["invalid config"]


def test_runtime_passes_cpu_top_module_to_validation_and_create(tmp_path) -> None:
    application = FakeApplication()
    server = RuntimeServer(WorkspaceRuntimeApi(application=application))
    module_name = "ysyx_00000000"

    _dispatch(
        server,
        "frontend.validate_config",
        {"core_id": "custom-filelist", "cpu_top_module": module_name},
    )
    _dispatch(
        server,
        "workspace.create",
        {"directory": str(tmp_path / "workspace"), "cpu_top_module": module_name},
    )

    assert application.calls[0] == (
        "validate-config",
        {"core_id": "custom-filelist", "cpu_top_module": module_name},
    )
    assert application.calls[1] == (
        "create",
        {"directory": str(tmp_path / "workspace"), "cpu_top_module": module_name},
    )


def test_runtime_rejects_unknown_and_duplicate_fields() -> None:
    server = RuntimeServer(WorkspaceRuntimeApi(application=FakeApplication()))

    unknown = _dispatch(server, "workspace.open", {"directory": "/tmp/ws", "x": 1})
    duplicate = _dispatch(
        server,
        "workspace.close",
        {"workspaceId": "one", "workspace_id": "two"},
    )

    assert unknown["error"]["code"] == -32602
    assert unknown["error"]["data"]["message"] == "unknown field: x"
    assert duplicate["error"]["code"] == -32602
    assert duplicate["error"]["data"]["message"] == "duplicate field: workspace_id"


def test_notification_has_no_response() -> None:
    server = RuntimeServer(WorkspaceRuntimeApi(application=FakeApplication()))
    payload = json.dumps({"jsonrpc": "2.0", "method": "rpc.ping"})

    assert server.dispatch(payload) == ""


def test_real_application_opens_workspace_and_returns_home(tmp_path) -> None:
    directory = tmp_path / "workspace"
    create_workspace(
        CreateWorkspaceData(
            directory=str(directory),
            parameters={"Design": "demo", "Top module": "chip_top"},
        ),
    )
    server = RuntimeServer()

    opened = _dispatch(server, "workspace.open", {"directory": str(directory)})
    workspace_id = opened["result"]["workspaceId"]
    home = _dispatch(server, "workspace.home", {"workspaceId": workspace_id})

    assert home["result"]["path"] == str(directory / "home" / "home.json")
    assert home["result"]["response"] == "success"


def test_workspace_config_lifecycle_uses_open_session(tmp_path) -> None:
    application = FakeApplication()
    server = RuntimeServer(WorkspaceRuntimeApi(application=application))
    directory = str(tmp_path / "workspace")
    opened = _dispatch(server, "workspace.open", {"directory": directory})
    workspace_id = opened["result"]["workspaceId"]
    config_path = str(tmp_path / "workspace" / "config" / "sim.json")

    refreshed = _dispatch(
        server,
        "workspace.refresh_config",
        {"workspaceId": workspace_id},
    )
    synced = _dispatch(
        server,
        "workspace.sync_config",
        {"workspaceId": workspace_id, "configPath": config_path},
    )
    reset = _dispatch(
        server,
        "workspace.reset_flow",
        {"workspaceId": workspace_id},
    )

    assert refreshed["result"]["refreshed"] is True
    assert synced["result"]["config_path"] == config_path
    assert reset["result"]["directory"] == directory
