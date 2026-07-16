from __future__ import annotations

import json
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fecompiler.runtime.events import redirect_stdout_to_stderr
from fecompiler.runtime.workspace_api import RuntimeApiError, WorkspaceRuntimeApi

PROTOCOL_VERSION = 1
JsonObject = dict[str, Any]
NotificationSink = Callable[[JsonObject], None]

ERROR_CODES = {
    "workspace_session_not_found": -32010,
    "command_failed": -32020,
    "invalid_request": -32602,
}


class RuntimeServer:
    def __init__(
        self,
        api: WorkspaceRuntimeApi | None = None,
        *,
        notification_sink: NotificationSink | None = None,
    ) -> None:
        self.api = api or WorkspaceRuntimeApi()
        self.notification_sink = notification_sink
        self.should_exit = False
        self.api.set_event_sink(self._emit_runtime_event)
        self._handlers: dict[str, Callable[[JsonObject], Any]] = {
            "frontend.catalog": self.api.catalog_list,
            "frontend.validate_config": self.api.validate_config,
            "workspace.create": self.api.create_workspace,
            "workspace.open": self.api.open_workspace,
            "workspace.close": self.api.close_workspace,
            "workspace.home": self.api.workspace_home,
            "workspace.info": self.api.workspace_info,
            "flow.run": self.api.flow_run,
            "flow.run_step": self.api.flow_run_step,
        }

    @property
    def capabilities(self) -> tuple[str, ...]:
        return (
            "rpc.hello",
            "rpc.ping",
            "rpc.shutdown",
            *self._handlers.keys(),
        )

    def set_notification_sink(self, sink: NotificationSink | None) -> None:
        self.notification_sink = sink

    def dispatch(self, payload: bytes | str) -> str:
        request_id: Any = None
        has_request_id = False
        try:
            request = self._parse_request(payload)
            has_request_id = "id" in request
            request_id = request.get("id")
            result = self._dispatch_request(request)
            if not has_request_id:
                return ""
            return _encode_json({"jsonrpc": "2.0", "id": request_id, "result": result})
        except JsonRpcFault as exc:
            if not has_request_id and exc.code not in {-32700, -32600}:
                return ""
            return _encode_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        **({"data": exc.data} if exc.data else {}),
                    },
                },
            )

    def _dispatch_request(self, request: JsonObject) -> Any:
        if request.get("jsonrpc") != "2.0":
            raise JsonRpcFault(-32600, "invalid_request", {"message": "jsonrpc must be 2.0"})
        method = request.get("method")
        if not isinstance(method, str) or not method:
            raise JsonRpcFault(-32600, "invalid_request", {"message": "method is required"})
        params = request.get("params", {})
        if not isinstance(params, dict):
            raise JsonRpcFault(-32602, "invalid_request", {"message": "params must be an object"})

        if method == "rpc.hello":
            return self._hello(params)
        if method == "rpc.ping":
            _reject_unknown_fields(params, set())
            return {"ok": True}
        if method == "rpc.shutdown":
            _reject_unknown_fields(params, set())
            self.should_exit = True
            self.api.sessions.close_all()
            return {"ok": True}

        handler = self._handlers.get(method)
        if handler is None:
            raise JsonRpcFault(-32601, "method_not_found", {"method": method})
        normalized = _normalize_params(method, params)
        try:
            with redirect_stdout_to_stderr():
                return handler(normalized)
        except RuntimeApiError as exc:
            raise JsonRpcFault(
                ERROR_CODES.get(exc.code, -32000),
                exc.code,
                {"message": exc.message, **exc.data},
            ) from exc
        except Exception as exc:
            raise JsonRpcFault(
                ERROR_CODES["command_failed"],
                "command_failed",
                {"message": str(exc)},
            ) from exc

    def _hello(self, params: JsonObject) -> JsonObject:
        _reject_unknown_fields(params, {"version"})
        requested = params.get("version")
        if requested != PROTOCOL_VERSION:
            raise JsonRpcFault(
                -32001,
                "unsupported_version",
                {
                    "requestedVersion": requested,
                    "supportedVersion": PROTOCOL_VERSION,
                },
            )
        return {
            "version": PROTOCOL_VERSION,
            "eccFeVersion": _package_version(),
            "capabilities": list(self.capabilities),
        }

    def _parse_request(self, payload: bytes | str) -> JsonObject:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        try:
            request = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JsonRpcFault(-32700, "parse_error", {"message": str(exc)}) from exc
        if not isinstance(request, dict):
            raise JsonRpcFault(-32600, "invalid_request", {"message": "request must be an object"})
        return request

    def _emit_runtime_event(self, event: JsonObject) -> None:
        if self.notification_sink is None:
            return
        self.notification_sink(
            {
                "jsonrpc": "2.0",
                "method": "runtime.event",
                "params": event,
            },
        )


class JsonRpcFault(RuntimeError):
    def __init__(self, code: int, message: str, data: JsonObject | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


_COMMON_ALIASES = {
    "cpuRtlFiles": "cpu_rtl_files",
    "id": "info_id",
    "infoId": "info_id",
    "simCompileExtraCflags": "sim_compile_extra_cflags",
    "simCpuTestCases": "sim_cpu_test_cases",
    "workspaceId": "workspace_id",
}

_METHOD_FIELDS: dict[str, set[str] | None] = {
    "frontend.catalog": set(),
    "frontend.validate_config": {
        "core_id",
        "soc_harness_id",
        "toolchain_id",
        "test_suite_id",
        "cpu_filelist",
        "cpu_rtl_files",
    },
    "workspace.create": {
        "core_id",
        "cpu_filelist",
        "cpu_rtl_files",
        "directory",
        "designTool",
        "filelist",
        "origin_def",
        "origin_verilog",
        "parameters",
        "pdk",
        "pdk_root",
        "rtl_list",
        "sim_all_tests",
        "sim_build_all_programs",
        "sim_build_test_script",
        "sim_cflags",
        "sim_compile_extra_cflags",
        "sim_compile_mabi",
        "sim_compile_march",
        "sim_compile_opt_level",
        "sim_compile_preset",
        "sim_coremark_has_float",
        "sim_coremark_iterations",
        "sim_coremark_max_cycles",
        "sim_coremark_total_data_size",
        "sim_cpp_sources",
        "sim_images",
        "sim_ldflags",
        "sim_program_link_base",
        "sim_program_names",
        "sim_program_sources",
        "sim_programs_dir",
        "sim_run_args",
        "sim_soc_root",
        "sim_tests_dir",
        "sim_tests_out_dir",
        "soc_filelist",
        "soc_harness_id",
        "soc_variant",
        "test_suite_id",
        "testbench",
        "toolchain_id",
        "top_module",
    },
    "workspace.open": {"directory"},
    "workspace.close": {"workspace_id"},
    "workspace.home": {"workspace_id"},
    "workspace.info": {"workspace_id", "step", "info_id"},
    "flow.run": {"workspace_id", "rerun"},
    "flow.run_step": {
        "workspace_id",
        "step",
        "rerun",
        "sim_test_suite",
        "sim_cpu_test_mode",
        "sim_cpu_test_cases",
        "sim_compile_preset",
        "sim_compile_opt_level",
        "sim_compile_march",
        "sim_compile_mabi",
        "sim_compile_extra_cflags",
        "sim_coremark_iterations",
        "sim_coremark_total_data_size",
        "sim_coremark_max_cycles",
        "sim_coremark_has_float",
    },
}


def _normalize_params(method: str, params: JsonObject) -> JsonObject:
    normalized: JsonObject = {}
    for key, value in params.items():
        normalized_key = _COMMON_ALIASES.get(str(key), str(key))
        if normalized_key in normalized:
            raise JsonRpcFault(
                -32602,
                "invalid_request",
                {"message": f"duplicate field: {normalized_key}"},
            )
        normalized[normalized_key] = value
    allowed = _METHOD_FIELDS.get(method)
    if allowed is not None:
        _reject_unknown_fields(normalized, allowed)
    return normalized


def _reject_unknown_fields(params: JsonObject, allowed: set[str]) -> None:
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise JsonRpcFault(
            -32602,
            "invalid_request",
            {"message": f"unknown field: {unknown[0]}"},
        )


def _encode_json(value: JsonObject) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _package_version() -> str:
    try:
        return version("ecc-fe")
    except PackageNotFoundError:
        return "unknown"
