"""Flow engine — mirrors chipcompiler/engine/flow.py in ecos-studio/ecc."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from fecompiler.data import workspace as workspace_data
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import WorkspaceStep
from fecompiler.allflow.builder import DEFAULT_FLOW_STEPS
from fecompiler.tools.fe import builder


class EngineFlow:
    def __init__(self, workspace: dict[str, Any]) -> None:
        self.workspace = workspace
        self.workspace_steps: list[WorkspaceStep] = []
        self.flow = workspace_data.load_flow(Path(self.workspace["flow_path"]))
        self._sync_flow_steps()
        self._flow_logger = _build_flow_logger(workspace["directory"])

    def has_init(self) -> bool:
        return len(self.flow.get("steps", [])) > 0

    def _sync_flow_steps(self) -> None:
        """Make existing flow.json compatible with current DEFAULT_FLOW_STEPS.

        Preserves state/runtime/info for matched steps and appends any newly added
        default steps with Unstart state.
        """
        existing = self.flow.get("steps", [])
        if not existing:
            return

        index = {(s.get("name"), s.get("tool")): s for s in existing}
        synced: list[dict[str, Any]] = []
        changed = False

        for name, tool in DEFAULT_FLOW_STEPS:
            matched = index.pop((name, tool), None)
            if matched is None:
                changed = True
                synced.append(
                    {
                        "name": name,
                        "tool": tool,
                        "state": StateEnum.Unstart.value,
                        "runtime": "",
                        "peak memory (mb)": 0,
                        "info": {},
                    }
                )
                continue
            synced.append(matched)

        # Keep unknown historical steps at the tail to avoid destructive migration.
        if index:
            changed = True
            synced.extend(index.values())

        if [(s.get("name"), s.get("tool")) for s in existing] != [
            (s.get("name"), s.get("tool")) for s in synced
        ]:
            changed = True

        if changed:
            self.flow["steps"] = synced
            self.save()

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
        self._rebuild_workspace_steps()

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
        """Build step workspace dirs on disk and return a summary list."""
        self._rebuild_workspace_steps()
        for ws_step in self.workspace_steps:
            builder.build_step_space(ws_step)
            builder.build_step_config(ws_step)
        return [
            {
                "step": ws_step["name"],
                "tool": ws_step["tool"],
                "directory": ws_step["directory"],
            }
            for ws_step in self.workspace_steps
        ]

    def _rebuild_workspace_steps(self) -> None:
        """Reconstruct workspace_steps from flow + workspace (no disk writes).
        Called on load() so workspace_steps survives a server restart.
        """
        self.workspace_steps = []
        pre_step: dict[str, Any] | None = None
        for flow_step in self.flow.get("steps", []):
            if pre_step is None:
                input_def = self.workspace.get("origin_def", "")
                input_verilog = self.workspace.get("origin_verilog", "")
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
            self.workspace_steps.append(ws_step)
            pre_step = ws_step

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
        self.set_state(name=ws_step.name, tool=ws_step.tool, state=StateEnum.Ongoing)
        self._flow_logger.info("[START]   %-20s  tool=%s", step_name, ws_step.tool)
        try:
            self._run_single_step(ws_step)
            success = self._check_step_result(ws_step)
            runtime = _format_runtime(time.time() - start)
            if success:
                self.set_state(
                    name=ws_step.name,
                    tool=ws_step.tool,
                    state=StateEnum.Success,
                    runtime=runtime,
                    peak_memory=0.0,
                )
                self._flow_logger.info("[SUCCESS] %-20s  elapsed=%s", step_name, runtime)
                return StateEnum.Success
            self.set_state(
                name=ws_step.name,
                tool=ws_step.tool,
                state=StateEnum.Incomplete,
                runtime=runtime,
                peak_memory=0.0,
            )
            self._flow_logger.warning("[FAILED]  %-20s  elapsed=%s", step_name, runtime)
            return StateEnum.Incomplete
        except Exception:
            runtime = _format_runtime(time.time() - start)
            logger.exception("step %r failed unexpectedly", step_name)
            self.set_state(
                name=ws_step.name,
                tool=ws_step.tool,
                state=StateEnum.Incomplete,
                runtime=runtime,
                peak_memory=0.0,
            )
            self._flow_logger.error("[ERROR]   %-20s  elapsed=%s", step_name, runtime)
            return StateEnum.Incomplete

    def _run_single_step(self, step: WorkspaceStep) -> None:
        from fecompiler.tools.fe import STEP_REGISTRY

        handler = STEP_REGISTRY.get(step.name)
        if handler is not None:
            handler.run(step, self.workspace)
        else:
            self._run_stub_step(step)

    def _run_stub_step(self, step: WorkspaceStep) -> None:
        """Fallback stub for steps that have no registered handler yet."""
        from fecompiler.tools.fe.subflow import init_subflow, update_substep
        from fecompiler.data.step import StateEnum as SE

        step_name = step.name
        step_token = step_name.replace(" ", "_")

        # initialise subflow.json with the canonical sub-step list
        init_subflow(step)

        # mark every sub-step Success (stub: nothing actually runs)
        for sub in step.subflow.get("steps", []):
            update_substep(step, sub["name"], SE.Success, runtime="00:00:00")

        Path(step.script["main"]).write_text(
            "# auto-generated\n"
            f'puts "running {step_name} ({step.tool})"\n',
            encoding="utf-8",
        )

        Path(step.log["file"]).write_text(
            f"[BEGIN] step={step_name} tool={step.tool}\n"
            f"[END] step={step_name} tool={step.tool}\n",
            encoding="utf-8",
        )

        Path(step.output["json"]).write_text(
            json.dumps(
                {"step": step_name, "tool": step.tool, "state": "Success"},
                indent=2,
            ),
            encoding="utf-8",
        )

        Path(step.output["verilog"]).write_text(
            f"// generated by {step_name}\nmodule {step_token}(); endmodule\n",
            encoding="utf-8",
        )
        Path(step.output["def"]).write_text(f"# generated {step_name}\n", encoding="utf-8")
        Path(step.output["gds"]).write_text(f"GDS:{step_name}\n", encoding="utf-8")

        Path(step.analysis["metrics"]).write_text(
            json.dumps({"step": step_name, "status": "Success"}, indent=2),
            encoding="utf-8",
        )
        Path(step.feature["step"]).write_text(
            json.dumps({"name": step_name, "tool": step.tool}, indent=2),
            encoding="utf-8",
        )
        Path(step.report["step"]).write_text(f"report: {step_name}\n", encoding="utf-8")

    def _check_step_result(self, step: WorkspaceStep) -> bool:
        from fecompiler.tools.fe import STEP_REGISTRY
        handler = STEP_REGISTRY.get(step.name)
        if handler is not None:
            return handler.check_result(step)
        # default: check DEF / verilog / GDS
        return (
            Path(step.output["def"]).exists()
            and Path(step.output["verilog"]).exists()
            and Path(step.output["gds"]).exists()
        )


def _format_runtime(seconds: float) -> str:
    sec = int(max(seconds, 0))
    hh = sec // 3600
    mm = (sec % 3600) // 60
    ss = sec % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def _build_flow_logger(workspace_dir: str) -> logging.Logger:
    """Return a logger that writes to <workspace>/log/log.txt."""
    import logging.handlers

    log_path = Path(workspace_dir) / "log" / "log.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log = logging.getLogger(f"fecompiler.flow.{Path(workspace_dir).name}")
    log.setLevel(logging.DEBUG)
    log.propagate = False

    # avoid adding duplicate handlers if EngineFlow is recreated
    if not log.handlers:
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        log.addHandler(handler)

    return log
