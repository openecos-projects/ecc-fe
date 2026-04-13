"""ECC service implementing ecos-studio-like command workflow."""

from __future__ import annotations

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
    def __init__(self) -> None:
        self.workspace: dict | None = None
        self.engine_flow: EngineFlow | None = None

    def __build_flow(self) -> list[dict[str, str]]:
        if self.workspace is None:
            return []
        engine = EngineFlow(workspace=self.workspace)
        if not engine.has_init():
            engine.init_default_steps()
            engine.load()
        created = engine.create_step_workspaces()
        self.engine_flow = engine
        return created

    def create_workspace(self, request: ECCRequest) -> dict:
        spec = parse_create_workspace_data(request)
        created = workspace_data.create_workspace(spec)

        loaded = workspace_data.load_workspace(created["directory"])
        if loaded is None:
            return build_response(
                cmd=request.cmd,
                response=ResponseEnum.failed,
                data={},
                message=[f"create workspace failed : {spec.directory}"],
            )

        self.workspace = loaded
        step_workspaces = self.__build_flow()
        directory = self.workspace["directory"]
        return build_response(
            cmd=request.cmd,
            response=ResponseEnum.success,
            data={
                "directory": directory,
                "workspace_id": directory,
                "step_workspaces": step_workspaces,
            },
            message=[f"create workspace success : {directory}"],
        )

    def load_workspace(self, request: ECCRequest) -> dict:
        data = parse_load_workspace_data(request)
        loaded = workspace_data.load_workspace(str(data.project_dir))
        if loaded is None:
            return build_response(
                cmd=request.cmd,
                response=ResponseEnum.failed,
                data={},
                message=[f"load workspace failed : {data.directory}"],
            )

        self.workspace = loaded
        self.__build_flow()
        directory = self.workspace["directory"]
        return build_response(
            cmd=request.cmd,
            response=ResponseEnum.success,
            data={"directory": directory, "workspace_id": directory},
            message=[f"load workspace success : {directory}"],
        )

    def rtl2gds(self, request: ECCRequest) -> dict:
        if self.workspace is None or self.engine_flow is None:
            return build_response(
                cmd=request.cmd,
                response=ResponseEnum.error,
                data={"rerun": False},
                message=["rtl2gds flow not exist"],
            )

        data = parse_run_flow_data(request)
        ok, reports = self.engine_flow.run_all(rerun=data.rerun)
        return build_response(
            cmd=request.cmd,
            response=ResponseEnum.success if ok else ResponseEnum.failed,
            data={"rerun": data.rerun, "reports": reports},
            message=[
                f"run rtl2gds {'success' if ok else 'failed'} : {self.workspace['directory']}",
            ],
        )

    def run_step(self, request: ECCRequest) -> dict:
        if self.workspace is None or self.engine_flow is None:
            return build_response(
                cmd=request.cmd,
                response=ResponseEnum.error,
                data={"step": "", "state": StateEnum.Invalid.value},
                message=["workspace not loaded"],
            )

        data = parse_run_step_data(request)
        state = self.engine_flow.run_step(data.step, rerun=data.rerun)
        return build_response(
            cmd=request.cmd,
            response=ResponseEnum.success if state == StateEnum.Success else ResponseEnum.failed,
            data={"step": data.step, "state": state.value},
            message=[f"run step {data.step} {'success' if state == StateEnum.Success else 'failed'}"],
        )

    def get_home_page(self, request: ECCRequest) -> dict:
        if self.workspace is None:
            return build_response(
                cmd=request.cmd,
                response=ResponseEnum.failed,
                data={},
                message=["workspace not loaded"],
            )
        return build_response(
            cmd=request.cmd,
            response=ResponseEnum.success,
            data={"path": self.workspace["home_path"]},
            message=[f"build home page success : {self.workspace['home_path']}"],
        )
