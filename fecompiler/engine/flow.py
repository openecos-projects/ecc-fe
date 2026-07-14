"""Flow engine — mirrors chipcompiler/engine/flow.py in ecos-studio/ecc."""

from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from fecompiler.data import workspace as workspace_data
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import WorkspaceStep
from fecompiler.allflow.builder import DEFAULT_FLOW_STEPS
from fecompiler.engine.provenance import build_step_provenance, output_fingerprint
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
                synced.append(_new_flow_step(name, tool))
                continue
            synced.append(matched)

        # Drop non-default steps to keep the flow strictly aligned with DEFAULT_FLOW_STEPS.
        if index:
            changed = True

        if [(s.get("name"), s.get("tool")) for s in existing] != [
            (s.get("name"), s.get("tool")) for s in synced
        ]:
            changed = True

        if changed:
            self.flow["steps"] = synced
            self.save()

    def init_default_steps(self) -> None:
        self.flow["steps"] = [_new_flow_step(name, tool) for name, tool in DEFAULT_FLOW_STEPS]
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
            step["info"] = {}
            step.pop("provenance", None)
        self.save()

    def refresh_stale_states(self) -> bool:
        """Invalidate successful steps whose inputs, config, tools, or upstream changed."""
        changed = False
        upstream: dict[str, Any] | None = None
        stale_from = ""
        tracked_flow = any(isinstance(step.get("provenance"), dict) for step in self.flow.get("steps", []))

        for step in self.flow.get("steps", []):
            state = str(step.get("state", ""))
            provenance = step.get("provenance") if isinstance(step.get("provenance"), dict) else None
            if stale_from:
                changed |= self._mark_step_stale(step, f"upstream step {stale_from} is stale", stale_from)
                continue

            if state != StateEnum.Success.value:
                upstream = None
                continue

            expected = build_step_provenance(
                self.workspace,
                str(step.get("name", "")),
                str(step.get("tool", "")),
                upstream,
            )
            if provenance is None:
                if tracked_flow:
                    stale_from = str(step.get("name", ""))
                    changed |= self._mark_step_stale(step, "step result has no provenance", stale_from)
                continue
            if str(provenance.get("signature", "")) != expected["signature"]:
                stale_from = str(step.get("name", ""))
                changed |= self._mark_step_stale(
                    step,
                    _provenance_change_reason(provenance, expected),
                    stale_from,
                )
                continue
            upstream = provenance

        if changed:
            self.save()
        return changed

    def clear_stale_ongoing_states(self) -> bool:
        changed = False
        for step in self.flow.get("steps", []):
            if step.get("state") != StateEnum.Ongoing.value:
                continue
            step["state"] = StateEnum.Incomplete.value
            step["runtime"] = step.get("runtime") or ""
            step["peak memory (mb)"] = step.get("peak memory (mb)", 0)
            changed = True
        if changed:
            self.save()
        return changed

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

        self.refresh_stale_states()
        if not rerun and flow_step.get("state") == StateEnum.Success.value:
            return StateEnum.Success

        self._mark_downstream_stale(step_name, "upstream step is being rerun")
        upstream = self._upstream_provenance(step_name)
        provenance = build_step_provenance(self.workspace, ws_step.name, ws_step.tool, upstream)
        provenance["started_at"] = _utc_now()
        self._clear_step_stale(flow_step)
        start = time.time()
        self.set_state(name=ws_step.name, tool=ws_step.tool, state=StateEnum.Ongoing)
        self._flow_logger.info("[START]   %-20s  tool=%s", step_name, ws_step.tool)
        restore_signal_handlers = _install_interruption_handlers()
        try:
            self._run_single_step(ws_step)
            success = self._check_step_result(ws_step)
            runtime = _format_runtime(time.time() - start)
            if success:
                provenance["finished_at"] = _utc_now()
                provenance["output_fingerprint"] = output_fingerprint(_step_result_paths(ws_step))
                flow_step["provenance"] = provenance
                self._finish_step(ws_step, StateEnum.Success, runtime)
                self._flow_logger.info("[SUCCESS] %-20s  elapsed=%s", step_name, runtime)
                return StateEnum.Success
            self._finish_step(ws_step, StateEnum.Incomplete, runtime)
            flow_step.pop("provenance", None)
            self.save()
            self._flow_logger.warning("[FAILED]  %-20s  elapsed=%s", step_name, runtime)
            return StateEnum.Incomplete
        except Exception:
            runtime = _format_runtime(time.time() - start)
            logger.exception("step %r failed unexpectedly", step_name)
            self._finish_step(ws_step, StateEnum.Incomplete, runtime)
            flow_step.pop("provenance", None)
            self.save()
            self._flow_logger.error("[ERROR]   %-20s  elapsed=%s", step_name, runtime)
            return StateEnum.Incomplete
        except BaseException as exc:
            runtime = _format_runtime(time.time() - start)
            self._finish_step(ws_step, StateEnum.Incomplete, runtime)
            flow_step.pop("provenance", None)
            self.save()
            if _is_interruption(exc):
                self._flow_logger.warning("[CANCEL]  %-20s  elapsed=%s", step_name, runtime)
            else:
                self._flow_logger.error("[ABORT]   %-20s  elapsed=%s", step_name, runtime)
            raise
        finally:
            restore_signal_handlers()

    def _finish_step(self, step: WorkspaceStep, state: StateEnum, runtime: str) -> None:
        self.set_state(
            name=step.name,
            tool=step.tool,
            state=state,
            runtime=runtime,
            peak_memory=0.0,
        )

    def _run_single_step(self, step: WorkspaceStep) -> None:
        from fecompiler.tools.fe import get_step_registry

        handler = get_step_registry().get(step.name)
        if handler is None:
            raise RuntimeError(f"no step handler registered for: {step.name}")
        handler.run(step, self.workspace)

    def _check_step_result(self, step: WorkspaceStep) -> bool:
        from fecompiler.tools.fe import get_step_registry

        handler = get_step_registry().get(step.name)
        if handler is None:
            return False
        return handler.check_result(step)

    def _upstream_provenance(self, step_name: str) -> dict[str, Any] | None:
        upstream: dict[str, Any] | None = None
        for step in self.flow.get("steps", []):
            if step.get("name") == step_name:
                break
            if step.get("state") == StateEnum.Success.value and isinstance(step.get("provenance"), dict):
                upstream = step["provenance"]
            else:
                upstream = None
        return upstream

    def _mark_downstream_stale(self, step_name: str, reason: str) -> None:
        found = False
        changed = False
        for step in self.flow.get("steps", []):
            if step.get("name") == step_name:
                found = True
                continue
            if found:
                changed |= self._mark_step_stale(step, reason, step_name)
        if changed:
            self.save()

    @staticmethod
    def _mark_step_stale(step: dict[str, Any], reason: str, stale_from: str) -> bool:
        if step.get("state") == StateEnum.Ongoing.value:
            return False
        info = step.get("info") if isinstance(step.get("info"), dict) else {}
        if (
            step.get("state") == StateEnum.Unstart.value
            and "provenance" not in step
            and info.get("stale") is not True
        ):
            return False
        already_stale = (
            step.get("state") == StateEnum.Unstart.value
            and info.get("stale") is True
            and info.get("stale_reason") == reason
            and info.get("stale_from") == stale_from
            and "provenance" not in step
        )
        if already_stale:
            return False
        step["state"] = StateEnum.Unstart.value
        step["runtime"] = ""
        step["peak memory (mb)"] = 0
        info.update({"stale": True, "stale_reason": reason, "stale_from": stale_from})
        step["info"] = info
        step.pop("provenance", None)
        return True

    @staticmethod
    def _clear_step_stale(step: dict[str, Any]) -> None:
        info = step.get("info") if isinstance(step.get("info"), dict) else {}
        for key in ("stale", "stale_reason", "stale_from"):
            info.pop(key, None)
        step["info"] = info


def _new_flow_step(name: str, tool: str) -> dict[str, Any]:
    return {
        "name": name,
        "tool": tool,
        "state": StateEnum.Unstart.value,
        "runtime": "",
        "peak memory (mb)": 0,
        "info": {},
    }


def _step_result_paths(step: WorkspaceStep) -> list[str]:
    return [
        step.output.get("json", ""),
        step.report.get("step", ""),
        step.analysis.get("metrics", ""),
        step.subflow.get("path", ""),
    ]


def _provenance_change_reason(previous: dict[str, Any], current: dict[str, Any]) -> str:
    changed: list[str] = []
    for key, label in (
        ("input_fingerprint", "inputs"),
        ("config_fingerprint", "configuration"),
        ("tool_fingerprint", "tools or resources"),
    ):
        if str(previous.get(key, "")) != str(current.get(key, "")):
            changed.append(label)
    return f"step {'/'.join(changed) if changed else 'provenance'} changed"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _is_interruption(exc: BaseException) -> bool:
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return True
    return isinstance(exc, OSError) and getattr(exc, "errno", None) in {
        getattr(signal, "SIGINT", 2),
        getattr(signal, "SIGTERM", 15),
    }


def _install_interruption_handlers():
    previous: dict[int, Any] = {}

    def handle_interruption(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, handle_interruption)
        except (ValueError, OSError):
            continue

    def restore() -> None:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                continue

    return restore
