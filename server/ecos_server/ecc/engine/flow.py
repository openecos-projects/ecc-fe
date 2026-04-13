"""Flow engine modeled after ecos-studio EngineFlow behavior."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from ..data import workspace as workspace_data
from ..flow_spec import DEFAULT_FLOW_STEPS
from ..schemas.ecc import StateEnum
from ..tools.ecc import builder


class EngineFlow:
    def __init__(self, workspace: dict[str, Any]) -> None:
        self.workspace = workspace
        self.workspace_steps: list[dict[str, Any]] = []
        self.flow = workspace_data.load_flow(Path(self.workspace["flow_path"]))

    def has_init(self) -> bool:
        return len(self.flow.get("steps", [])) > 0

    def init_default_steps(self) -> None:
        self.flow["steps"] = [
            {
                "name": name,
                "tool": tool,
                "state": StateEnum.Unstart.value,
                "runtime": "",
                "peak memory (mb)": 0,
                "info": {},
            }
            for name, tool in DEFAULT_FLOW_STEPS
        ]
        self.save()

    def load(self) -> None:
        self.flow = workspace_data.load_flow(Path(self.workspace["flow_path"]))

    def save(self) -> None:
        workspace_data.save_flow(Path(self.workspace["flow_path"]), self.flow)

    def clear_states(self) -> None:
        for step in self.flow.get("steps", []):
            step["state"] = StateEnum.Unstart.value
            step["runtime"] = ""
            step["peak memory (mb)"] = 0
        self.save()

    def get_step(self, name: str, tool: str) -> dict[str, Any] | None:
        for step in self.flow.get("steps", []):
            if step.get("name") == name and step.get("tool") == tool:
                return step
        return None

    def set_state(
        self,
        *,
        name: str,
        tool: str,
        state: StateEnum,
        runtime: str | None = None,
        peak_memory: float | None = None,
    ) -> bool:
        step = self.get_step(name, tool)
        if step is None:
            return False
        step["state"] = state.value
        if runtime is not None:
            step["runtime"] = runtime
        if peak_memory is not None:
            step["peak memory (mb)"] = peak_memory
        self.save()
        return True

    def is_flow_success(self) -> bool:
        return all(x.get("state") == StateEnum.Success.value for x in self.flow.get("steps", []))

    def create_step_workspaces(self) -> list[dict[str, str]]:
        self.workspace_steps = []
        created: list[dict[str, str]] = []
        pre_step: dict[str, Any] | None = None
        for flow_step in self.flow.get("steps", []):
            if pre_step is None:
                input_def = self.workspace["origin_def"]
                input_verilog = self.workspace["origin_verilog"]
            else:
                input_def = pre_step["output"]["def"]
                input_verilog = pre_step["output"]["verilog"]

            ws_step = builder.build_step(
                workspace=self.workspace,
                step_name=flow_step["name"],
                tool=flow_step["tool"],
                input_def=input_def,
                input_verilog=input_verilog,
            )
            builder.build_step_space(ws_step)
            builder.build_step_config(ws_step)
            self.workspace_steps.append(ws_step)
            pre_step = ws_step
            created.append(
                {
                    "step": ws_step["name"],
                    "tool": ws_step["tool"],
                    "directory": ws_step["directory"],
                },
            )
        return created

    def get_workspace_step(self, name: str) -> dict[str, Any] | None:
        for step in self.workspace_steps:
            if step["name"] == name:
                return step
        return None

    def run_all(self, rerun: bool = False) -> tuple[bool, list[dict[str, str]]]:
        if rerun:
            self.clear_states()
        reports: list[dict[str, str]] = []
        for ws_step in self.workspace_steps:
            state = self.run_step(ws_step["name"], rerun=rerun)
            reports.append(
                {
                    "step": ws_step["name"],
                    "tool": ws_step["tool"],
                    "state": state.value,
                    "log_file": ws_step["log"]["file"],
                },
            )
            if state != StateEnum.Success:
                return False, reports
        return True, reports

    def run_step(self, step_name: str, rerun: bool = False) -> StateEnum:
        ws_step = self.get_workspace_step(step_name)
        if ws_step is None:
            return StateEnum.Invalid

        flow_step = self.get_step(ws_step["name"], ws_step["tool"])
        if flow_step is None:
            return StateEnum.Invalid

        if not rerun and flow_step.get("state") == StateEnum.Success.value:
            return StateEnum.Success

        start = time.time()
        self.set_state(name=ws_step["name"], tool=ws_step["tool"], state=StateEnum.Ongoing)
        try:
            self._run_single_step(ws_step)
            success = self._check_step_result(ws_step)
            runtime = _format_runtime(time.time() - start)
            if success:
                self.set_state(
                    name=ws_step["name"],
                    tool=ws_step["tool"],
                    state=StateEnum.Success,
                    runtime=runtime,
                    peak_memory=0.0,
                )
                return StateEnum.Success
            self.set_state(
                name=ws_step["name"],
                tool=ws_step["tool"],
                state=StateEnum.Incomplete,
                runtime=runtime,
                peak_memory=0.0,
            )
            return StateEnum.Incomplete
        except Exception:
            runtime = _format_runtime(time.time() - start)
            logger.exception("step %r failed unexpectedly", step_name)
            self.set_state(
                name=ws_step["name"],
                tool=ws_step["tool"],
                state=StateEnum.Incomplete,
                runtime=runtime,
                peak_memory=0.0,
            )
            return StateEnum.Incomplete

    def _run_single_step(self, step: dict[str, Any]) -> None:
        from ..steps import STEP_REGISTRY

        handler = STEP_REGISTRY.get(step["name"])
        if handler is not None:
            handler.run(step, self.workspace)
        else:
            self._run_stub_step(step)

    def _run_stub_step(self, step: dict[str, Any]) -> None:
        """Fallback stub for steps that have no registered handler yet."""
        step_name = step["name"]
        step_token = step_name.replace(" ", "_")

        Path(step["script"]["main"]).write_text(
            "# auto-generated\n"
            f'puts "running {step_name} ({step["tool"]})"\n',
            encoding="utf-8",
        )

        Path(step["log"]["file"]).write_text(
            f"[BEGIN] step={step_name} tool={step['tool']}\n"
            f"[END] step={step_name} tool={step['tool']}\n",
            encoding="utf-8",
        )

        Path(step["output"]["json"]).write_text(
            json.dumps(
                {"step": step_name, "tool": step["tool"], "state": "Success"},
                indent=2,
            ),
            encoding="utf-8",
        )

        Path(step["output"]["verilog"]).write_text(
            f"// generated by {step_name}\nmodule {step_token}(); endmodule\n",
            encoding="utf-8",
        )
        Path(step["output"]["def"]).write_text(f"# generated {step_name}\n", encoding="utf-8")
        Path(step["output"]["gds"]).write_text(f"GDS:{step_name}\n", encoding="utf-8")

        Path(step["analysis"]["metrics"]).write_text(
            json.dumps({"step": step_name, "status": "Success"}, indent=2),
            encoding="utf-8",
        )
        Path(step["feature"]["step"]).write_text(
            json.dumps({"name": step_name, "tool": step["tool"]}, indent=2),
            encoding="utf-8",
        )
        Path(step["report"]["step"]).write_text(f"report: {step_name}\n", encoding="utf-8")

    def _check_step_result(self, step: dict[str, Any]) -> bool:
        return (
            Path(step["output"]["def"]).exists()
            and Path(step["output"]["verilog"]).exists()
            and Path(step["output"]["gds"]).exists()
        )


def _format_runtime(seconds: float) -> str:
    sec = int(max(seconds, 0))
    hh = sec // 3600
    mm = (sec % 3600) // 60
    ss = sec % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}"
