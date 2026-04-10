"""ECC request/response schema helpers (ecos-studio style)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import DEFAULT_PROJECTS_ROOT


class CMDEnum(str, Enum):
    home_page = "home_page"
    create_workspace = "create_workspace"
    load_workspace = "load_workspace"
    delete_workspace = "delete_workspace"
    rtl2gds = "rtl2gds"
    run_step = "run_step"
    get_info = "get_info"


class ResponseEnum(str, Enum):
    success = "success"
    failed = "failed"
    error = "error"
    warning = "warning"


class StateEnum(str, Enum):
    Invalid = "Invalid"
    Unstart = "Unstart"
    Success = "Success"
    Ongoing = "Ongoing"
    Pending = "Pending"
    Imcomplete = "Incomplete"


@dataclass(slots=True)
class ECCRequest:
    cmd: str
    data: dict[str, Any]


@dataclass(slots=True)
class CreateWorkspaceData:
    directory: str
    pdk: str = "ics55"
    pdk_root: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    origin_def: str = ""
    origin_verilog: str = ""
    filelist: str = ""
    rtl_list: list[str] = field(default_factory=list)

    @property
    def project_dir(self) -> Path:
        return Path(self.directory).expanduser().resolve()

    @property
    def design_name(self) -> str:
        raw = str(self.parameters.get("Design", "")).strip()
        if raw:
            return raw
        return self.project_dir.name or "New_Chip_Design"


@dataclass(slots=True)
class LoadWorkspaceData:
    directory: str

    @property
    def project_dir(self) -> Path:
        return Path(self.directory).expanduser().resolve()


@dataclass(slots=True)
class RunFlowData:
    rerun: bool = False


@dataclass(slots=True)
class RunStepData:
    step: str
    rerun: bool = False


def parse_ecc_request(payload: dict[str, Any], expected: CMDEnum | None = None) -> ECCRequest:
    cmd = str(payload.get("cmd", "")).strip()
    data = payload.get("data")

    # compatibility: accept old flat payloads for create_workspace/rtl2gds
    if not cmd and expected is not None:
        cmd = expected.value
    if not isinstance(data, dict):
        data = payload if "data" not in payload else {}

    if not cmd:
        raise ValueError("`cmd` is required.")
    if expected is not None and cmd != expected.value:
        raise ValueError(f"request cmd not match: expected={expected.value}, got={cmd}")

    return ECCRequest(cmd=cmd, data=data)


def parse_create_workspace_data(req: ECCRequest) -> CreateWorkspaceData:
    data = req.data
    directory = str(data.get("directory", "")).strip()
    params = data.get("parameters")
    parameters = dict(params) if isinstance(params, dict) else {}
    if "Design" not in parameters:
        design_fallback = str(data.get("design", "")).strip() or str(data.get("projectName", "")).strip()
        if design_fallback:
            parameters["Design"] = design_fallback

    if not directory:
        design = str(parameters.get("Design", "")).strip() or "New_Chip_Design"
        directory = str((DEFAULT_PROJECTS_ROOT / design).resolve())

    rtl_list_raw = data.get("rtl_list")
    rtl_list = list(rtl_list_raw) if isinstance(rtl_list_raw, list) else []

    return CreateWorkspaceData(
        directory=directory,
        pdk=str(data.get("pdk", "ics55")).strip() or "ics55",
        pdk_root=str(data.get("pdk_root", "")).strip(),
        parameters=parameters,
        origin_def=str(data.get("origin_def", "")).strip(),
        origin_verilog=str(data.get("origin_verilog", "")).strip(),
        filelist=str(data.get("filelist", "")).strip(),
        rtl_list=rtl_list,
    )


def parse_load_workspace_data(req: ECCRequest) -> LoadWorkspaceData:
    directory = str(req.data.get("directory", "")).strip()
    if not directory:
        raise ValueError("`data.directory` is required.")
    return LoadWorkspaceData(directory=directory)


def parse_run_flow_data(req: ECCRequest) -> RunFlowData:
    return RunFlowData(rerun=bool(req.data.get("rerun", False)))


def parse_run_step_data(req: ECCRequest) -> RunStepData:
    step = str(req.data.get("step", "")).strip()
    if not step:
        raise ValueError("`data.step` is required.")
    return RunStepData(step=step, rerun=bool(req.data.get("rerun", False)))


def build_response(
    *,
    cmd: str,
    response: ResponseEnum,
    data: dict[str, Any] | None = None,
    message: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "cmd": cmd,
        "response": response.value,
        "data": data or {},
        "message": message or [],
    }
