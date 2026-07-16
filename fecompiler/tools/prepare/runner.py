"""Prepare step implementation: normalize / merge RTL inputs for later steps."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.fe.base import BaseStep
from fecompiler.tools.common.rtl_inputs import workspace_input_fingerprint
from fecompiler.tools.common.rtl_ownership import classify_rtl_source, ownership_summary
from fecompiler.tools.common.sv_module import (
    compare_port_contracts,
    module_definitions,
    module_port_contract_from_files,
)
from fecompiler.tools.prepare.subflow import PrepareSubFlowEnum, init_prepare_subflow
from fecompiler.resources import resolve_thirdparty_path
from fecompiler.utility.json import json_read, json_write


ECOS_CPU_TOP = "cpu_top"


class PrepareStep(BaseStep):
    """Build a normalized merged RTL filelist for elab/lint/sim."""

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_prepare_subflow(step)
        self.write_standard_outputs(step)

        prepared, source_info = self._collect_inputs(step, workspace)
        cpu_top_contract = self._validate_frontend_cpu_top(step, workspace, prepared["rtl_files"])
        prepared["cpu_top_contract"] = cpu_top_contract
        merged_path = self._write_merged_filelist(step, prepared["rtl_files"])
        manifest_path = self._write_prepared_manifest(step, prepared)
        self._persist_workspace_input_filelist(workspace, merged_path, manifest_path)
        self._update_substep(
            step,
            PrepareSubFlowEnum.persist_state.value,
            ok=True,
            info={
                "input_filelist": str(merged_path),
                "prepared_manifest": str(manifest_path),
            },
        )

        report = {
            "prepare": "pass",
            "merged_filelist": str(merged_path),
            "prepared_manifest": str(manifest_path),
            "rtl_files": len(prepared["rtl_files"]),
            "incdirs": len(prepared["incdirs"]),
            "defines": len(prepared["defines"]),
            "inputs": source_info,
            "contracts": [cpu_top_contract],
            "ownership": prepared["ownership"],
        }
        json_write(step.output["json"], report)
        json_write(step.report["step"], report)
        self._update_substep(step, PrepareSubFlowEnum.report.value, ok=True)

    def check_result(self, step: WorkspaceStep) -> bool:
        merged_path = self._merged_filelist_path(step)
        if not merged_path.exists():
            return False
        lines = [
            l.strip() for l in merged_path.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        manifest = self._manifest_path(step)
        return len(lines) > 0 and manifest.exists()

    def _collect_inputs(self, step: WorkspaceStep, workspace: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        cpu_filelist = str(workspace.get("cpu_filelist", "")).strip()
        soc_filelist = str(workspace.get("soc_filelist", "")).strip()
        filelist = str(workspace.get("input_filelist", "")).strip()
        origin_verilog = str(workspace.get("origin_verilog", "")).strip()

        inputs: dict[str, Any] = {}
        merged: list[Path] = []
        seen_rtl: set[str] = set()
        seen_incdir: set[str] = set()
        seen_define: set[str] = set()
        rtl_source_by_path: dict[str, dict[str, str]] = {}
        incdirs: list[Path] = []
        defines: list[str] = []
        def _add_unique(items: list[Any], target: list[Any], seen: set[str]) -> None:
            for item in items:
                key = str(item)
                if key not in seen:
                    seen.add(key)
                    target.append(item)

        def _add_filelist(label: str, path: str) -> None:
            data = self._parse_sv_filelist(path)
            _add_unique(data["rtl_files"], merged, seen_rtl)
            _add_unique(data["incdirs"], incdirs, seen_incdir)
            _add_unique(data["defines"], defines, seen_define)
            inputs[label] = self._filelist_info(path, data)
            for rtl_path in data["rtl_files"]:
                key = str(rtl_path)
                rtl_source_by_path.setdefault(key, {
                    "path": key,
                    "ownership": classify_rtl_source(rtl_path, label, workspace),
                    "source": label,
                })

        # Frontend integration path: explicit CPU + SoC filelists.
        if cpu_filelist or soc_filelist:
            if cpu_filelist:
                _add_filelist("cpu_filelist", cpu_filelist)
            else:
                inputs["cpu_filelist"] = {"path": "", "rtl_files": 0, "skipped": "not provided"}

            if soc_filelist:
                _add_filelist("soc_filelist", soc_filelist)
            else:
                inputs["soc_filelist"] = {"path": "", "rtl_files": 0, "skipped": "not provided"}

        # Legacy single-filelist path.
        elif filelist and Path(filelist).exists():
            _add_filelist("input_filelist", filelist)

        # Last fallback: one source RTL.
        elif origin_verilog and Path(origin_verilog).exists():
            p = Path(origin_verilog).resolve()
            _add_unique([p], merged, seen_rtl)
            inputs["origin_verilog"] = {"path": str(p), "rtl_files": 1}
            rtl_source_by_path[str(p)] = {
                "path": str(p),
                "ownership": classify_rtl_source(p, "origin_verilog", workspace),
                "source": "origin_verilog",
            }

        if not merged:
            self._update_substep(
                step,
                PrepareSubFlowEnum.collect_inputs.value,
                ok=False,
                info={"error": "No RTL inputs found. Provide cpu/soc filelist or input_filelist."},
            )
            raise RuntimeError("prepare failed: no RTL inputs found")

        self._update_substep(
            step,
            PrepareSubFlowEnum.collect_inputs.value,
            ok=True,
            info={
                "total_rtl_files": len(merged),
                "total_incdirs": len(incdirs),
                "total_defines": len(defines),
            },
        )
        rtl_sources = [
            rtl_source_by_path.get(str(path), {
                "path": str(path),
                "ownership": classify_rtl_source(path, workspace=workspace),
                "source": "unknown",
            })
            for path in merged
        ]
        prepared = {
            "rtl_files": [str(p) for p in merged],
            "rtl_sources": rtl_sources,
            "ownership": ownership_summary(rtl_sources),
            "incdirs": [str(p) for p in incdirs],
            "defines": defines,
            "source_fingerprint": workspace_input_fingerprint(workspace),
        }
        return prepared, inputs

    def _validate_frontend_cpu_top(
        self,
        step: WorkspaceStep,
        workspace: dict[str, Any],
        rtl_files: list[str],
    ) -> dict[str, Any]:
        if not self._requires_frontend_cpu_top(workspace):
            return {"id": "cpu_top", "status": "not_required"}

        required_top = str(workspace.get("required_cpu_top_module", "")).strip() or ECOS_CPU_TOP

        matches = module_definitions(rtl_files, required_top)
        if len(matches) != 1:
            self._fail_cpu_top_contract(
                step,
                f"frontend prepare requires exactly one {required_top} module, found {len(matches)}",
                {"module": required_top, "count": len(matches), "files": [str(path) for path in matches]},
            )

        expected = self._expected_cpu_top_contract(workspace)
        source, actual = module_port_contract_from_files(matches, required_top)
        result: dict[str, Any] = {
            "id": "cpu_top",
            "status": "pass" if expected else "module_only",
            "module": required_top,
            "source": str(source or matches[0]),
            "ports": actual,
            "expected_ports": len(expected),
        }
        if not expected:
            return result

        differences = compare_port_contracts(expected, actual)
        result["differences"] = differences
        if not any(differences.values()):
            return result

        details: list[str] = []
        if differences["missing"]:
            details.append(f"missing ports: {', '.join(differences['missing'])}")
        if differences["extra"]:
            details.append(f"extra ports: {', '.join(differences['extra'])}")
        for mismatch in differences["mismatches"]:
            expected_port = mismatch["expected"]
            actual_port = mismatch["actual"]
            details.append(
                f"{mismatch['name']}: expected {expected_port['direction']}[{expected_port['width']}], "
                f"found {actual_port.get('direction') or 'unknown'}[{actual_port.get('width') or 'unknown'}]"
            )
        self._fail_cpu_top_contract(
            step,
            f"{required_top} port contract mismatch ({'; '.join(details)})",
            result,
        )
        return result

    @staticmethod
    def _expected_cpu_top_contract(workspace: dict[str, Any]) -> list[dict[str, Any]]:
        raw_contract = workspace.get("required_cpu_top_port_contract", [])
        if not isinstance(raw_contract, list):
            return []
        contract: list[dict[str, Any]] = []
        for raw_port in raw_contract:
            if not isinstance(raw_port, dict):
                continue
            name = str(raw_port.get("name", "")).strip()
            direction = str(raw_port.get("direction", "")).strip().lower()
            try:
                width = int(raw_port.get("width", 0))
            except (TypeError, ValueError):
                width = 0
            if name and direction in {"input", "output", "inout"} and width > 0:
                contract.append({"name": name, "direction": direction, "width": width})
        return contract

    def _fail_cpu_top_contract(
        self,
        step: WorkspaceStep,
        message: str,
        info: dict[str, Any],
    ) -> None:
        self._update_substep(
            step,
            PrepareSubFlowEnum.collect_inputs.value,
            ok=False,
            info={"error": message, **info},
        )
        raise RuntimeError(f"prepare failed: {message}")

    @staticmethod
    def _requires_frontend_cpu_top(workspace: dict[str, Any]) -> bool:
        return any(
            str(workspace.get(field, "")).strip() == value
            for field, value in (
                ("top_module", "ecos_sim_top"),
                ("soc_wrapper_top", "ecos_sim_top"),
                ("cpu_socket_contract", "ysyx-axi-cpu-socket-v1"),
            )
        ) or any(
            str(workspace.get(field, "")).strip()
            for field in ("soc_wrapper_id", "soc_harness_id", "required_cpu_top_module")
        )

    @staticmethod
    def _filelist_info(path: str, data: dict[str, list[Any]]) -> dict[str, Any]:
        return {
            "path": path,
            "rtl_files": len(data["rtl_files"]),
            "incdirs": len(data["incdirs"]),
            "defines": len(data["defines"]),
        }

    @staticmethod
    def _file_defines_module(path: Path, module_name: str) -> bool:
        return bool(module_definitions([path], module_name))

    def _write_merged_filelist(self, step: WorkspaceStep, files: list[str]) -> Path:
        merged_path = self._merged_filelist_path(step)
        merged_path.write_text(
            "\n".join(files) + "\n",
            encoding="utf-8",
        )
        self._update_substep(
            step,
            PrepareSubFlowEnum.merge_filelist.value,
            ok=True,
            info={"merged_filelist": str(merged_path), "rtl_files": len(files)},
        )
        return merged_path

    def _write_prepared_manifest(self, step: WorkspaceStep, prepared: dict[str, Any]) -> Path:
        manifest_path = self._manifest_path(step)
        json_write(manifest_path, prepared)
        return manifest_path

    def _persist_workspace_input_filelist(
        self,
        workspace: dict[str, Any],
        merged_path: Path,
        manifest_path: Path,
    ) -> None:
        workspace["prepared_filelist"] = str(merged_path)
        workspace["prepared_manifest"] = str(manifest_path)
        workspace["input_filelist"] = str(merged_path)

        params_path = str(workspace.get("parameters_path", "")).strip()
        if params_path:
            params = json_read(params_path)
            params["input_filelist"] = str(merged_path)
            params["prepared_manifest"] = str(manifest_path)
            cpu_filelist = str(workspace.get("cpu_filelist", "")).strip()
            soc_filelist = str(workspace.get("soc_filelist", "")).strip()
            if cpu_filelist:
                params["cpu_filelist"] = cpu_filelist
            if soc_filelist:
                params["soc_filelist"] = soc_filelist
            json_write(params_path, params)


    @staticmethod
    def _parse_sv_filelist(filelist_path: str, visited: set[str] | None = None) -> dict[str, list[Any]]:
        path = Path(filelist_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"filelist not found: {path}")

        if visited is None:
            visited = set()
        canonical = str(path)
        if canonical in visited:
            return {
                "rtl_files": [],
                "incdirs": [],
                "defines": [],
            }
        visited.add(canonical)

        base = path.parent
        resolved: list[Path] = []
        incdirs: list[Path] = []
        defines: list[str] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "//", "`")):
                continue
            try:
                parts = shlex.split(line, comments=True, posix=True)
            except ValueError as exc:
                raise ValueError(f"invalid filelist entry in {path}: {line}") from exc
            option = parts[0] if parts else ""
            if option in ("-f", "-F"):
                nested_ref = parts[1].strip() if len(parts) > 1 else ""
                if not nested_ref:
                    continue
                nested_ref = nested_ref.strip("\"'")
                nested_path = Path(nested_ref) if Path(nested_ref).is_absolute() else base / nested_ref
                nested_path = resolve_thirdparty_path(nested_path)
                nested = PrepareStep._parse_sv_filelist(str(nested_path), visited)
                resolved.extend(nested["rtl_files"])
                incdirs.extend(nested["incdirs"])
                defines.extend(nested["defines"])
                continue
            if line.startswith("+incdir+"):
                payload = line.removeprefix("+incdir+").strip()
                if not payload:
                    continue
                for inc in [x for x in payload.split("+") if x]:
                    inc = inc.strip("\"'")
                    if not inc:
                        continue
                    inc_path = Path(inc) if Path(inc).is_absolute() else base / inc
                    incdirs.append(resolve_thirdparty_path(inc_path))
                continue
            if line.startswith("+define+"):
                payload = line.removeprefix("+define+").strip()
                if not payload:
                    continue
                for define in [x for x in payload.split("+") if x]:
                    define = define.strip()
                    if define:
                        defines.append(define)
                continue
            if line.startswith("+") or line.startswith("-"):
                continue
            token = option.strip("\"'")
            if not (token.endswith(".v") or token.endswith(".sv")):
                continue

            src = Path(token) if Path(token).is_absolute() else base / token
            src = resolve_thirdparty_path(src)
            if not src.exists():
                raise FileNotFoundError(f"RTL source not found in {path}: {line}")
            resolved.append(src)

        return {
            "rtl_files": resolved,
            "incdirs": incdirs,
            "defines": defines,
        }

    @staticmethod
    def _merged_filelist_path(step: WorkspaceStep) -> Path:
        return Path(step.output["dir"]) / "merged_rtl.f"

    @staticmethod
    def _manifest_path(step: WorkspaceStep) -> Path:
        return Path(step.output["dir"]) / "prepared_inputs.json"

    @staticmethod
    def _update_substep(step: WorkspaceStep, name: str, ok: bool,
                        info: dict | None = None) -> None:
        from fecompiler.data.step import StateEnum

        state = StateEnum.Success.value if ok else StateEnum.Incomplete.value
        for entry in step.subflow.get("steps", []):
            if entry["name"] == name:
                entry["state"] = state
                entry["info"] = info or {}
                break
        path = step.subflow.get("path", "")
        if path:
            json_write(path, step.subflow)
