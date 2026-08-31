from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from fecompiler.application.workspace_service import (
    CliResult,
    WorkspaceApplicationService,
    workspace_application,
)
from fecompiler.runtime.sessions import (
    WorkspaceSession,
    WorkspaceSessionNotFound,
    WorkspaceSessionRegistry,
)

_T = TypeVar("_T")
RuntimeEventSink = Callable[[dict[str, Any]], None]


class RuntimeApiError(RuntimeError):
    def __init__(self, code: str, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


class WorkspaceRuntimeApi:
    def __init__(
        self,
        application: WorkspaceApplicationService | None = None,
        sessions: WorkspaceSessionRegistry | None = None,
        *,
        event_sink: RuntimeEventSink | None = None,
    ) -> None:
        self.application = application or workspace_application
        self.sessions = sessions or WorkspaceSessionRegistry()
        self.event_sink = event_sink
        self.runtime_instance_id = f"runtime-{uuid4().hex}"
        self.active_operation_ids: set[str] = set()

    def set_event_sink(self, sink: RuntimeEventSink | None) -> None:
        self.event_sink = sink

    def catalog_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        return self._result_data(self.application.execute_payload("catalog-list"))

    def validate_config(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._result_data(
            self.application.execute_payload("validate-config", params),
            allow_failed=True,
        )

    def create_workspace(self, params: dict[str, Any]) -> dict[str, Any]:
        directory = _required_text(params, "directory")
        result = self.application.execute_payload("create", params)
        data = self._result_data(result)
        resolved_directory = str(data.get("directory") or directory)
        session = self.sessions.create_session(resolved_directory)
        return _session_result(session)

    def open_workspace(self, params: dict[str, Any]) -> dict[str, Any]:
        directory = _required_text(params, "directory")
        result = self.application.execute_payload(
            "load",
            {"directory": directory, "recover_stale_ongoing": False},
        )
        data = self._result_data(result)
        resolved_directory = str(data.get("directory") or directory)
        session = self.sessions.open_session(resolved_directory)
        return _session_result(session)

    def recover_interrupted(self, params: dict[str, Any]) -> dict[str, Any]:
        operation_id = _optional_text(params, "operation_id")

        def recover(session: WorkspaceSession) -> dict[str, Any]:
            from fecompiler.runtime.recovery import recover_interrupted_operations

            return recover_interrupted_operations(
                session.directory,
                active_operation_ids=set(self.active_operation_ids),
                operation_id=operation_id,
            )

        return self._with_session(params, recover)

    def close_workspace(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace_id = _required_text(params, "workspace_id")
        try:
            self.sessions.close_session(workspace_id)
        except WorkspaceSessionNotFound as exc:
            raise self._session_not_found(workspace_id) from exc
        return {"ok": True}

    def workspace_home(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._with_session(
            params,
            lambda session: self._result_data(
                self.application.execute_payload(
                    "get-home",
                    {"directory": str(session.directory)},
                ),
            ),
        )

    def workspace_info(self, params: dict[str, Any]) -> dict[str, Any]:
        step = _required_text(params, "step")
        info_id = _required_text(params, "info_id")
        return self._with_session(
            params,
            lambda session: self._result_data(
                self.application.execute_payload(
                    "get-info",
                    {
                        "directory": str(session.directory),
                        "id": info_id,
                        "step": step,
                    },
                ),
            ),
        )

    def refresh_config(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._with_session(
            params,
            lambda session: self._result_data(
                self.application.execute_payload(
                    "refresh-config",
                    {"directory": str(session.directory)},
                ),
            ),
        )

    def sync_config(self, params: dict[str, Any]) -> dict[str, Any]:
        config_path = _required_text(params, "config_path")
        return self._with_session(
            params,
            lambda session: self._result_data(
                self.application.execute_payload(
                    "sync-config",
                    {
                        "config_path": config_path,
                        "directory": str(session.directory),
                    },
                ),
            ),
        )

    def reset_flow(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._with_session(
            params,
            lambda session: self._result_data(
                self.application.execute_payload(
                    "reset-flow",
                    {"directory": str(session.directory)},
                ),
            ),
        )

    def flow_run(self, params: dict[str, Any]) -> dict[str, Any]:
        rerun = _optional_bool(params, "rerun")
        operation_id = _optional_text(params, "operation_id")
        return self._with_session(
            params,
            lambda session: self._execute_for_session(
                session,
                "run-flow",
                {"rerun": rerun},
                operation_id=operation_id,
            ),
        )

    def flow_run_step(self, params: dict[str, Any]) -> dict[str, Any]:
        step = _required_text(params, "step")
        rerun = _optional_bool(params, "rerun")
        operation_id = _optional_text(params, "operation_id")
        options = {
            key: value
            for key, value in params.items()
            if key not in {"workspace_id", "step", "rerun", "operation_id"}
        }
        return self._with_session(
            params,
            lambda session: self._execute_for_session(
                session,
                "run-step",
                {"step": step, "rerun": rerun, **options},
                operation_id=operation_id,
            ),
        )

    def _execute_for_session(
        self,
        session: WorkspaceSession,
        command: str,
        payload: dict[str, Any],
        *,
        operation_id: str = "",
    ) -> dict[str, Any]:
        def emit(event: dict[str, Any]) -> None:
            if self.event_sink is None:
                return
            data = dict(event.get("data") or {})
            data.setdefault("directory", str(session.directory))
            data["workspaceId"] = session.workspace_id
            self.event_sink({**event, "data": data})

        marker = (
            {
                "schema": 1,
                "operation_id": operation_id,
                "runtime_instance_id": self.runtime_instance_id,
            }
            if operation_id
            else None
        )
        if operation_id:
            self.active_operation_ids.add(operation_id)
        try:
            result = self.application.execute_payload(
                command,
                {"directory": str(session.directory), **payload},
                event_sink=emit,
                runtime_operation=marker,
            )
        finally:
            if operation_id:
                self.active_operation_ids.discard(operation_id)
        return self._result_data(result)

    def _with_session(
        self,
        params: dict[str, Any],
        operation: Callable[[WorkspaceSession], _T],
    ) -> _T:
        workspace_id = _required_text(params, "workspace_id")
        try:
            session = self.sessions.get_session(workspace_id)
        except WorkspaceSessionNotFound as exc:
            raise self._session_not_found(workspace_id) from exc
        with session.mutation_lock:
            return operation(session)

    @staticmethod
    def _result_data(result: CliResult, *, allow_failed: bool = False) -> dict[str, Any]:
        if result.response in {"success", "warning"} or allow_failed:
            return {
                **result.data,
                "message": list(result.message),
                "response": result.response,
            }
        message = result.message[0] if result.message else f"{result.cmd} failed"
        raise RuntimeApiError(
            "command_failed",
            message,
            {
                "cmd": result.cmd,
                "data": result.data,
                "message": result.message,
                "response": result.response,
            },
        )

    @staticmethod
    def _session_not_found(workspace_id: str) -> RuntimeApiError:
        return RuntimeApiError(
            "workspace_session_not_found",
            f"workspace session not found: {workspace_id}",
        )


def _session_result(session: WorkspaceSession) -> dict[str, Any]:
    return {
        "directory": str(session.directory),
        "workspaceId": session.workspace_id,
    }


def _required_text(params: dict[str, Any], field: str) -> str:
    value = params.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeApiError("invalid_request", f"missing required field: {field}")
    return value.strip()


def _optional_bool(params: dict[str, Any], field: str) -> bool:
    value = params.get(field, False)
    if not isinstance(value, bool):
        raise RuntimeApiError("invalid_request", f"{field} must be a boolean")
    return value


def _optional_text(params: dict[str, Any], field: str) -> str:
    value = params.get(field, "")
    if not isinstance(value, str):
        raise RuntimeApiError("invalid_request", f"{field} must be a string")
    return value.strip()
