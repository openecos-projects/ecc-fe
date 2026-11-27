"""Prepare step implementation: normalize / merge RTL inputs for later steps."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.allflow.profile import GENERIC_RTL, normalize_frontend_design_kind
from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.fe.base import BaseStep
from fecompiler.tools.common.filelist import read_complete_filelist
from fecompiler.tools.common.rtl_inputs import workspace_input_fingerprint
from fecompiler.tools.common.rtl_ownership import classify_rtl_source, ownership_summary
from fecompiler.tools.common.sv_module import (
    UNDRIVEN_PORTS_TEXT,
    compare_port_contracts,
    is_simple_sv_identifier,
    macro_names,
    module_definitions,
    module_port_contract_from_files,
    outputs_beyond,
)
from fecompiler.tools.fe.subflow import update_substep_ok
from fecompiler.tools.prepare.subflow import PrepareSubFlowEnum, init_prepare_subflow
from fecompiler.utility.json import json_read, json_write


ECOS_CPU_TOP = "cpu_top"
ECOS_CUSTOM_CPU_TOP_DEFINE = "ECOS_CUSTOM_CPU_TOP"


class PrepareStep(BaseStep):
    """Build a normalized merged RTL filelist for elab/lint/sim."""

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_prepare_subflow(step)
        self.write_standard_outputs(step)

        prepared, source_info = self._collect_inputs(step, workspace)
        top_contract = self._validate_frontend_top(step, workspace, prepared)
        if normalize_frontend_design_kind(workspace.get("frontend_design_kind")) != GENERIC_RTL:
            self._inject_custom_cpu_top_define(workspace, prepared)
            prepared["cpu_top_contract"] = top_contract
        else:
            prepared["design_top_contract"] = top_contract
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
            "input_notes": prepared["input_notes"],
            "contracts": [top_contract],
            "ownership": prepared["ownership"],
            "readiness": self._build_readiness_facts(
                workspace,
                prepared,
                top_contract,
                outputs_persisted=True,
            ),
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

        header_files: list[Path] = []
        seen_header: set[str] = set()
        libdirs: list[Path] = []
        seen_libdir: set[str] = set()
        libexts: list[str] = []
        seen_libext: set[str] = set()
        notes: list[dict[str, str]] = []

        def _add_filelist(label: str, path: str) -> None:
            data = self._parse_sv_filelist(path)
            _add_unique(data["rtl_files"], merged, seen_rtl)
            _add_unique(data["incdirs"], incdirs, seen_incdir)
            # A listed header is included, not compiled: make its directory
            # searchable so the `include that names it resolves.
            _add_unique(data["header_files"], header_files, seen_header)
            _add_unique(
                [header.parent for header in data["header_files"]], incdirs, seen_incdir
            )
            _add_unique(data["defines"], defines, seen_define)
            _add_unique(data["libdirs"], libdirs, seen_libdir)
            _add_unique(data["libexts"], libexts, seen_libext)
            inputs[label] = self._filelist_info(path, data)
            notes.extend(self._filelist_notes(label, data))
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
            "header_files": [str(p) for p in header_files],
            "incdirs": [str(p) for p in incdirs],
            "defines": defines,
            "libdirs": [str(p) for p in libdirs],
            "libexts": libexts,
            # What the filelists contained that prepare did not act on.
            "input_notes": notes,
            "source_fingerprint": workspace_input_fingerprint(workspace),
        }
        return prepared, inputs

    def _validate_frontend_top(
        self,
        step: WorkspaceStep,
        workspace: dict[str, Any],
        prepared: dict[str, Any],
    ) -> dict[str, Any]:
        if normalize_frontend_design_kind(workspace.get("frontend_design_kind")) == GENERIC_RTL:
            return self._validate_generic_design_top(step, workspace, prepared)
        return self._validate_frontend_cpu_top(step, workspace, prepared)

    def _validate_generic_design_top(
        self,
        step: WorkspaceStep,
        workspace: dict[str, Any],
        prepared: dict[str, Any],
    ) -> dict[str, Any]:
        top_module = str(workspace.get("top_module", "")).strip()
        if not is_simple_sv_identifier(top_module):
            self._fail_generic_top_contract(
                step,
                workspace,
                f"frontend prepare requires a valid SystemVerilog top identifier: {top_module}",
                {"module": top_module},
                prepared,
            )

        matches = self._top_definitions(prepared, top_module)
        if len(matches) != 1:
            self._fail_generic_top_contract(
                step,
                workspace,
                f"frontend prepare requires exactly one {top_module} module, found {len(matches)}",
                {
                    "module": top_module,
                    "count": len(matches),
                    "files": [str(path) for path in matches],
                },
                prepared,
            )
        return {
            "id": "design_top",
            "status": "module_only",
            "module": top_module,
            "source": str(matches[0]),
            "count": 1,
            "interface_applicable": False,
        }

    def _fail_generic_top_contract(
        self,
        step: WorkspaceStep,
        workspace: dict[str, Any],
        message: str,
        info: dict[str, Any],
        prepared: dict[str, Any],
    ) -> None:
        contract = {
            **info,
            "id": "design_top",
            "status": "failed",
            "detail": message,
            "interface_applicable": False,
        }
        report = {
            "prepare": "fail",
            "rtl_files": len(prepared.get("rtl_files", [])),
            "incdirs": len(prepared.get("incdirs", [])),
            "defines": len(prepared.get("defines", [])),
            "contracts": [contract],
            "ownership": prepared.get("ownership", {}),
            "comparison_inputs": prepared,
            "readiness": self._build_readiness_facts(
                workspace,
                prepared,
                contract,
                outputs_persisted=False,
                top_required=True,
            ),
        }
        json_write(step.output["json"], report)
        json_write(step.report["step"], report)
        self._update_substep(
            step,
            PrepareSubFlowEnum.collect_inputs.value,
            ok=False,
            info={"error": message, **info},
        )
        raise RuntimeError(f"prepare failed: {message}")

    def _validate_frontend_cpu_top(
        self,
        step: WorkspaceStep,
        workspace: dict[str, Any],
        prepared: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._requires_frontend_cpu_top(workspace):
            return {"id": "cpu_top", "status": "not_required"}

        required_top = str(workspace.get("required_cpu_top_module", "")).strip() or ECOS_CPU_TOP
        if not is_simple_sv_identifier(required_top):
            self._fail_cpu_top_contract(
                step,
                f"frontend prepare requires a valid SystemVerilog CPU top identifier: {required_top}",
                {"module": required_top},
                prepared,
            )

        matches = self._top_definitions(prepared, required_top)
        if len(matches) != 1:
            self._fail_cpu_top_contract(
                step,
                f"frontend prepare requires exactly one {required_top} module, found {len(matches)}",
                {"module": required_top, "count": len(matches), "files": [str(path) for path in matches]},
                prepared,
            )

        expected = self._expected_cpu_top_contract(workspace)
        source, actual = module_port_contract_from_files(
            matches, required_top, defined_macros=macro_names(prepared.get("defines"))
        )
        if not actual:
            # The defines select no definition this parser can see (a macro
            # may come from an included header): read the first one written.
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
        unconnected = outputs_beyond((port["name"] for port in expected), actual)
        if unconnected:
            # The socket leaves them unconnected: allowed, and recorded.
            differences["extra"] = [
                name for name in differences["extra"] if name not in unconnected
            ]
            result["unconnected_outputs"] = unconnected
        result["differences"] = differences
        if not any(differences.values()):
            return result

        details: list[str] = []
        if differences["missing"]:
            details.append(f"missing ports: {', '.join(differences['missing'])}")
        if differences["extra"]:
            details.append(f"{UNDRIVEN_PORTS_TEXT}: {', '.join(differences['extra'])}")
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
            prepared,
        )
        return result

    @staticmethod
    def _top_definitions(prepared: dict[str, Any], module_name: str) -> list[Path]:
        """Return the files that define *module_name* under the prepared defines.

        A module written once per `` `ifdef `` branch, in one file or two, is
        one definition once the defines select a branch. If that selection
        leaves none, every branch is searched so the error can name the files.
        """
        rtl_files = prepared["rtl_files"]
        selected = module_definitions(
            rtl_files, module_name, defined_macros=macro_names(prepared.get("defines"))
        )
        return selected or module_definitions(rtl_files, module_name)

    @staticmethod
    def _inject_custom_cpu_top_define(
        workspace: dict[str, Any],
        prepared: dict[str, Any],
    ) -> None:
        core_id = str(
            workspace.get("frontend_core_id")
            or workspace.get("cpu_wrapper_id")
            or ""
        ).strip()
        required_top = str(workspace.get("required_cpu_top_module", "")).strip()
        if core_id != "custom-filelist" or not required_top:
            return

        defines = [
            str(define).strip()
            for define in prepared.get("defines", [])
            if str(define).strip()
            and str(define).split("=", maxsplit=1)[0].strip() != ECOS_CUSTOM_CPU_TOP_DEFINE
        ]
        defines.append(f"{ECOS_CUSTOM_CPU_TOP_DEFINE}={required_top}")
        prepared["defines"] = defines

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
        prepared: dict[str, Any],
    ) -> None:
        contract = {
            **info,
            "id": "cpu_top",
            "status": "failed",
            "detail": message,
        }
        report = {
            "prepare": "fail",
            "rtl_files": len(prepared.get("rtl_files", [])),
            "incdirs": len(prepared.get("incdirs", [])),
            "defines": len(prepared.get("defines", [])),
            "contracts": [contract],
            "ownership": prepared.get("ownership", {}),
            "comparison_inputs": prepared,
            "readiness": self._build_readiness_facts(
                {},
                prepared,
                contract,
                outputs_persisted=False,
                top_required=True,
            ),
        }
        json_write(step.output["json"], report)
        json_write(step.report["step"], report)
        self._update_substep(
            step,
            PrepareSubFlowEnum.collect_inputs.value,
            ok=False,
            info={"error": message, **info},
        )
        raise RuntimeError(f"prepare failed: {message}")

    def _build_readiness_facts(
        self,
        workspace: dict[str, Any],
        prepared: dict[str, Any],
        contract: dict[str, Any],
        *,
        outputs_persisted: bool,
        top_required: bool | None = None,
    ) -> dict[str, Any]:
        rtl_files = [str(path) for path in prepared.get("rtl_files", [])]
        incdirs = [str(path) for path in prepared.get("incdirs", [])]
        contract_status = str(contract.get("status", "")).strip().lower()
        differences = contract.get("differences", {})
        differences = differences if isinstance(differences, dict) else {}
        missing = differences.get("missing", [])
        extra = differences.get("extra", [])
        mismatches = differences.get("mismatches", [])
        missing = missing if isinstance(missing, list) else []
        extra = extra if isinstance(extra, list) else []
        mismatches = mismatches if isinstance(mismatches, list) else []
        expected_ports = contract.get("expected_ports", 0)
        expected_ports = (
            expected_ports
            if isinstance(expected_ports, int)
            and not isinstance(expected_ports, bool)
            and expected_ports >= 0
            else 0
        )
        matched_ports = max(0, expected_ports - len(missing) - len(mismatches))
        source = str(contract.get("source", "")).strip()
        normalized_sources = {
            str(Path(path).expanduser().resolve(strict=False)) for path in rtl_files
        }
        source_in_inputs = bool(source) and str(
            Path(source).expanduser().resolve(strict=False)
        ) in normalized_sources
        required = (
            self._requires_frontend_cpu_top(workspace)
            if top_required is None
            else top_required
        )
        definitions = contract.get("count")
        if not isinstance(definitions, int) or isinstance(definitions, bool):
            definitions = 1 if source else 0

        return {
            "schema_version": 1,
            "sources": {
                "rtl_total": len(rtl_files),
                "rtl_resolved": sum(Path(path).is_file() for path in rtl_files),
                "include_dir_total": len(incdirs),
                "include_dir_resolved": sum(Path(path).is_dir() for path in incdirs),
            },
            "top": {
                "required": required,
                "module": str(contract.get("module", "")),
                "definitions": max(0, definitions),
                "source_in_inputs": source_in_inputs,
            },
            "interface": {
                "applicable": bool(
                    contract.get("interface_applicable", contract_status != "not_required")
                ),
                "verified": expected_ports > 0,
                "expected_ports": expected_ports,
                "matched_ports": matched_ports,
                "missing_ports": len(missing),
                "extra_ports": len(extra),
                "mismatched_ports": len(mismatches),
            },
            "reproducibility": {
                # The fingerprint is a record; it counts only when it has content.
                "input_fingerprint": bool(prepared.get("source_fingerprint")),
                "merged_filelist": outputs_persisted,
                "prepared_manifest": outputs_persisted,
            },
        }

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
            "header_files": len(data["header_files"]),
            "incdirs": len(data["incdirs"]),
            "defines": len(data["defines"]),
            "unsupported": len(data["unsupported"]),
            "missing_incdirs": len(data["missing_incdirs"]),
        }

    @staticmethod
    def _filelist_notes(label: str, data: dict[str, list[Any]]) -> list[dict[str, str]]:
        notes = [
            {
                "source": label,
                "kind": "unsupported_entry",
                "entry": str(entry),
                "detail": "read from the filelist but not passed to any tool",
            }
            for entry in data["unsupported"]
        ]
        notes.extend(
            {
                "source": label,
                "kind": "missing_incdir",
                "entry": str(path),
                "detail": "include or library directory does not exist",
            }
            for path in data["missing_incdirs"]
        )
        return notes

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
        is_generic = (
            normalize_frontend_design_kind(workspace.get("frontend_design_kind"))
            == GENERIC_RTL
        )
        # `input_filelist` is replaced by the merged list only when it is not
        # itself the input: overwriting a filelist the user supplied would
        # drop its include directories and defines on the next run.
        replace_input = not is_generic and bool(
            str(workspace.get("cpu_filelist", "")).strip()
            or str(workspace.get("soc_filelist", "")).strip()
        )
        if replace_input:
            workspace["input_filelist"] = str(merged_path)

        params_path = str(workspace.get("parameters_path", "")).strip()
        if params_path:
            params = json_read(params_path)
            if replace_input:
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
    def _parse_sv_filelist(filelist_path: str) -> dict[str, list[Any]]:
        """Read one filelist tree; every listed file must exist."""
        parsed = read_complete_filelist(filelist_path)
        return {
            "rtl_files": list(parsed.sources),
            "header_files": list(parsed.headers),
            "incdirs": list(parsed.incdirs),
            "defines": list(parsed.defines),
            "libdirs": list(parsed.libdirs),
            "libexts": list(parsed.libexts),
            "unsupported": list(parsed.unsupported),
            "missing_incdirs": list(parsed.missing_incdirs),
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
        update_substep_ok(step, name, ok, info=info)
