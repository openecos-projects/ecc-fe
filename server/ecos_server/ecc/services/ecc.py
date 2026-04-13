"""ECC service implementing ecos-studio-like command workflow."""

from __future__ import annotations

import threading
from typing import Any

from ..data import workspace as workspace_data
from ..engine.flow import EngineFlow
from ..schemas.ecc import (
    ECCRequest,
    ResponseEnum,
    StateEnum,
    build_response,
    parse_create_workspace_data,
    parse_load_workspace_data,
    parse_run_flow_data,
    parse_run_step_data,
)


class EccService:
    """Stateless-per-request service.

    workspace state is keyed by directory path so multiple workspaces can
    coexist. A threading.Lock per entry prevents concurrent mutation of the
    same workspace.
    """

    def __init__(self) -> None:
        self._engines: dict[str, EngineFlow] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_engine(self, directory: str) -> EngineFlow | None:
        return self._engines.get(directory)

    def _build_engine(self, ws: dict[str, Any]) -> tuple[EngineFlow, list[dict[str, str]]]:
        engine = EngineFlow(workspace=ws)
        if not engine.has_init():
            engine.init_default_steps()
            engine.load()
        created = engine.create_step_workspaces()
        with self._lock:
            self._engines[ws["directory"]] = engine
        return engine, created

    def _require_engine(self, request: ECCRequest) -> tuple[EngineFlow | None, str]:
        """Return (engine, directory) or (None, '') when workspace_id missing."""
        directory = str(request.data.get("workspace_id", "")).strip()
        if not directory:
            return None, ""
        engine = self._get_engine(directory)
        if engine is None:
            # Auto-reload from disk so run_step works after a server restart.
            ws = workspace_data.load_workspace(directory)
            if ws is None:
                return None, directory
            engine, _ = self._build_engine(ws)
        return engine, directory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_workspace(self, request: ECCRequest) -> dict:
        spec = parse_create_workspace_data(request)
        created = workspace_data.create_workspace(spec)

        ws = workspace_data.load_workspace(created["directory"])
        if ws is None:
            return build_response(
                cmd=request.cmd,
                response=ResponseEnum.failed,
                data={},
                message=[f"create workspace failed: {spec.directory}"],
            )

        engine, step_workspaces = self._build_engine(ws)
        directory = ws["directory"]
        return build_response(
            cmd=request.cmd,
            response=ResponseEnum.success,
            data={
                "directory": directory,
                "workspace_id": directory,
                "step_workspaces": step_workspaces,
            },
            message=[f"create workspace success: {directory}"],
        )

    def load_workspace(self, request: ECCRequest) -> dict:
        data = parse_load_workspace_data(request)
        ws = workspace_data.load_workspace(str(data.project_dir))
        if ws is None:
            return build_response(
                cmd=request.cmd,
                response=ResponseEnum.failed,
                data={},
                message=[f"load workspace failed: {data.directory}"],
            )

        _, step_workspaces = self._build_engine(ws)
        directory = ws["directory"]
        return build_response(
            cmd=request.cmd,
            response=ResponseEnum.success,
            data={"directory": directory, "workspace_id": directory, "step_workspaces": step_workspaces},
            message=[f"load workspace success: {directory}"],
        )

    def rtl2gds(self, request: ECCRequest) -> dict:
        engine, directory = self._require_engine(request)
        if engine is None:
            return build_response(
                cmd=request.cmd,
                response=ResponseEnum.error,
                data={"rerun": False},
                message=["workspace not loaded — pass workspace_id"],
            )

        data = parse_run_flow_data(request)
        ok, reports = engine.run_all(rerun=data.rerun)
        return build_response(
            cmd=request.cmd,
            response=ResponseEnum.success if ok else ResponseEnum.failed,
            data={"rerun": data.rerun, "reports": reports},
            message=[f"run rtl2gds {'success' if ok else 'failed'}: {directory}"],
        )

    def run_step(self, request: ECCRequest) -> dict:
        engine, _ = self._require_engine(request)
        if engine is None:
            return build_response(
                cmd=request.cmd,
                response=ResponseEnum.error,
                data={"step": "", "state": StateEnum.Invalid.value},
                message=["workspace not loaded — pass workspace_id"],
            )

        data = parse_run_step_data(request)
        state = engine.run_step(data.step, rerun=data.rerun)
        return build_response(
            cmd=request.cmd,
            response=ResponseEnum.success if state == StateEnum.Success else ResponseEnum.failed,
            data={"step": data.step, "state": state.value},
            message=[f"run step {data.step} {'success' if state == StateEnum.Success else 'failed'}"],
        )

    def get_home_page(self, request: ECCRequest) -> dict:
        engine, directory = self._require_engine(request)
        if engine is None:
            return build_response(
                cmd=request.cmd,
                response=ResponseEnum.failed,
                data={},
                message=["workspace not loaded — pass workspace_id"],
            )
        home_path = engine.workspace["home_path"]
        return build_response(
            cmd=request.cmd,
            response=ResponseEnum.success,
            data={"path": home_path},
            message=[f"build home page success: {home_path}"],
        )
