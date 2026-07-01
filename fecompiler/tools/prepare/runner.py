"""Prepare step implementation: normalize / merge RTL inputs for later steps."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.fe.base import BaseStep
from fecompiler.tools.common.rtl_inputs import workspace_input_fingerprint
from fecompiler.tools.prepare.subflow import PrepareSubFlowEnum, init_prepare_subflow
from fecompiler.resources import resolve_thirdparty_path
from fecompiler.utility.json import json_read, json_write


COMPATIBILITY_CPU_ALIAS_TOP = "ysyx_00000000"
STANDARD_CPU_TOP = "ecos_user_cpu_top"
STANDARD_CPU_WRAPPER_GENERATION = "standard_alias_v1"


class PrepareStep(BaseStep):
    """Build a normalized merged RTL filelist for elab/lint/sim."""

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_prepare_subflow(step)
        self.write_standard_outputs(step)

        prepared, source_info = self._collect_inputs(step, workspace)
        alias_info = self._validate_frontend_cpu_alias(step, workspace, prepared["rtl_files"])
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
        }
        if alias_info:
            report["compatibility_alias"] = alias_info
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

    def _collect_inputs(self, step: WorkspaceStep, workspace: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, Any]]:
        cpu_filelist = str(workspace.get("cpu_filelist", "")).strip()
        cpu_adapter_filelist = str(workspace.get("cpu_adapter_filelist", "")).strip()
        soc_filelist = str(workspace.get("soc_filelist", "")).strip()
        filelist = str(workspace.get("input_filelist", "")).strip()
        origin_verilog = str(workspace.get("origin_verilog", "")).strip()

        inputs: dict[str, Any] = {}
        merged: list[Path] = []
        seen_rtl: set[str] = set()
        seen_incdir: set[str] = set()
        seen_define: set[str] = set()
        incdirs: list[Path] = []
        defines: list[str] = []
        cpu_filelist_defines_alias = False

        def _add_unique(items: list[Any], target: list[Any], seen: set[str]) -> None:
            for item in items:
                key = str(item)
                if key not in seen:
                    seen.add(key)
                    target.append(item)

        def _add_filelist(label: str, path: str) -> None:
            nonlocal cpu_filelist_defines_alias
            data = self._parse_sv_filelist(path)
            if label == "cpu_filelist":
                cpu_filelist_defines_alias = self._filelist_defines_module(
                    data["rtl_files"],
                    COMPATIBILITY_CPU_ALIAS_TOP,
                )
            if label == "cpu_adapter_filelist":
                data = self._filter_cpu_adapter_filelist(
                    data,
                    workspace,
                    existing_rtl_files=merged,
                    cpu_filelist_defines_alias=cpu_filelist_defines_alias,
                )
                if data["rtl_files"]:
                    cpu_filelist_defines_alias = (
                        cpu_filelist_defines_alias
                        or self._filelist_defines_module(data["rtl_files"], COMPATIBILITY_CPU_ALIAS_TOP)
                    )
            if label == "soc_filelist":
                data = self._filter_soc_filelist_for_cpu_wrapper(
                    data,
                    workspace,
                    cpu_filelist_defines_alias=cpu_filelist_defines_alias,
                )
            _add_unique(data["rtl_files"], merged, seen_rtl)
            _add_unique(data["incdirs"], incdirs, seen_incdir)
            _add_unique(data["defines"], defines, seen_define)
            inputs[label] = self._filelist_info(path, data)

        # Frontend integration path: explicit CPU + SoC filelists.
        if cpu_filelist or soc_filelist:
            if cpu_filelist:
                _add_filelist("cpu_filelist", cpu_filelist)
            else:
                inputs["cpu_filelist"] = {"path": "", "rtl_files": 0, "skipped": "not provided"}

            if self._uses_generated_standard_cpu_wrapper(workspace):
                generated = self._maybe_generate_standard_cpu_wrapper(
                    step,
                    workspace,
                    cpu_rtl_files=merged,
                    alias_already_present=cpu_filelist_defines_alias,
                )
                if generated is not None:
                    _add_unique([generated], merged, seen_rtl)
                    inputs["generated_cpu_wrapper"] = {
                        "path": str(generated),
                        "rtl_files": 1,
                        "standard_top": self._standard_cpu_top(workspace),
                        "generated": True,
                    }
                    cpu_filelist_defines_alias = True
                else:
                    inputs["generated_cpu_wrapper"] = {
                        "path": "",
                        "rtl_files": 0,
                        "standard_top": self._standard_cpu_top(workspace),
                        "skipped": "compatibility alias already provided",
                    }

            if cpu_adapter_filelist:
                _add_filelist("cpu_adapter_filelist", cpu_adapter_filelist)

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
        prepared = {
            "rtl_files": [str(p) for p in merged],
            "incdirs": [str(p) for p in incdirs],
            "defines": defines,
            "source_fingerprint": workspace_input_fingerprint(workspace),
        }
        return prepared, inputs

    def _validate_frontend_cpu_alias(
        self,
        step: WorkspaceStep,
        workspace: dict[str, Any],
        rtl_files: list[str],
    ) -> dict[str, Any]:
        if not self._requires_frontend_cpu_alias(workspace):
            return {}

        matches = [
            str(Path(path))
            for path in rtl_files
            if self._file_defines_module(Path(path), COMPATIBILITY_CPU_ALIAS_TOP)
        ]
        info: dict[str, Any] = {
            "module": COMPATIBILITY_CPU_ALIAS_TOP,
            "count": len(matches),
            "files": matches,
        }
        if len(matches) == 1:
            return info

        message = (
            "frontend prepare requires exactly one "
            f"{COMPATIBILITY_CPU_ALIAS_TOP} compatibility module, found {len(matches)}"
        )
        self._update_substep(
            step,
            PrepareSubFlowEnum.collect_inputs.value,
            ok=False,
            info={"error": message, **info},
        )
        raise RuntimeError(f"prepare failed: {message}")

    @staticmethod
    def _requires_frontend_cpu_alias(workspace: dict[str, Any]) -> bool:
        return any(
            str(workspace.get(field, "")).strip() == value
            for field, value in (
                ("top_module", "ecos_sim_top"),
                ("soc_wrapper_top", "ecos_sim_top"),
                ("cpu_socket_contract", "ysyx-axi-cpu-socket-v1"),
            )
        ) or any(
            str(workspace.get(field, "")).strip()
            for field in ("soc_wrapper_id", "soc_harness_id")
        )

    @staticmethod
    def _uses_generated_standard_cpu_wrapper(workspace: dict[str, Any]) -> bool:
        return (
            str(workspace.get("cpu_wrapper_generation", "")).strip()
            == STANDARD_CPU_WRAPPER_GENERATION
        )

    @staticmethod
    def _standard_cpu_top(workspace: dict[str, Any]) -> str:
        return str(workspace.get("cpu_standard_top", "")).strip() or STANDARD_CPU_TOP

    def _maybe_generate_standard_cpu_wrapper(
        self,
        step: WorkspaceStep,
        workspace: dict[str, Any],
        *,
        cpu_rtl_files: list[Any],
        alias_already_present: bool,
    ) -> Path | None:
        if alias_already_present:
            return None

        standard_top = self._standard_cpu_top(workspace)
        matches = [
            str(Path(path))
            for path in cpu_rtl_files
            if self._file_defines_module(Path(path), standard_top)
        ]
        if len(matches) != 1:
            message = (
                "standard CPU filelist requires exactly one "
                f"{standard_top} module, found {len(matches)}"
            )
            self._update_substep(
                step,
                PrepareSubFlowEnum.collect_inputs.value,
                ok=False,
                info={"error": message, "standard_top": standard_top, "matches": matches},
            )
            raise RuntimeError(f"prepare failed: {message}")

        wrapper_path = Path(step.output["dir"]) / "generated_standard_cpu_wrapper.sv"
        wrapper_path.write_text(
            _standard_cpu_wrapper_source(standard_top),
            encoding="utf-8",
        )
        return wrapper_path

    @staticmethod
    def _filelist_info(path: str, data: dict[str, list[Any]]) -> dict[str, Any]:
        info = {
            "path": path,
            "rtl_files": len(data["rtl_files"]),
            "incdirs": len(data["incdirs"]),
            "defines": len(data["defines"]),
        }
        if data.get("filtered_rtl_files"):
            info["filtered_rtl_files"] = len(data["filtered_rtl_files"])
            info["filtered"] = [str(item) for item in data["filtered_rtl_files"]]
        return info

    @staticmethod
    def _filter_soc_filelist_for_cpu_wrapper(
        data: dict[str, list[Any]],
        workspace: dict[str, Any],
        *,
        cpu_filelist_defines_alias: bool = False,
    ) -> dict[str, list[Any]]:
        wrapper_top = str(workspace.get("cpu_wrapper_top", "")).strip()
        if not cpu_filelist_defines_alias and (not wrapper_top or wrapper_top == COMPATIBILITY_CPU_ALIAS_TOP):
            return data

        kept: list[Path] = []
        filtered: list[Path] = []
        for path in data["rtl_files"]:
            p = Path(path)
            if PrepareStep._file_defines_module(p, COMPATIBILITY_CPU_ALIAS_TOP):
                filtered.append(p)
                continue
            kept.append(p)

        if not filtered:
            return data
        return {
            "rtl_files": kept,
            "incdirs": data["incdirs"],
            "defines": data["defines"],
            "filtered_rtl_files": filtered,
        }

    @staticmethod
    def _filter_cpu_adapter_filelist(
        data: dict[str, list[Any]],
        workspace: dict[str, Any],
        *,
        existing_rtl_files: list[Any],
        cpu_filelist_defines_alias: bool = False,
    ) -> dict[str, list[Any]]:
        if cpu_filelist_defines_alias:
            return {
                "rtl_files": [],
                "incdirs": [],
                "defines": [],
                "filtered_rtl_files": list(data["rtl_files"]),
            }

        wrapper_top = str(workspace.get("cpu_wrapper_top", "")).strip()
        module_names = {COMPATIBILITY_CPU_ALIAS_TOP}
        if wrapper_top:
            module_names.add(wrapper_top)

        existing_paths = {str(Path(path).resolve()) for path in existing_rtl_files}
        kept: list[Path] = []
        filtered: list[Path] = []
        for path in data["rtl_files"]:
            p = Path(path)
            if str(p.resolve()) in existing_paths:
                filtered.append(p)
                continue
            if any(PrepareStep._file_defines_module(p, module_name) for module_name in module_names):
                kept.append(p)
            else:
                filtered.append(p)

        if not filtered:
            return data
        return {
            "rtl_files": kept,
            "incdirs": data["incdirs"],
            "defines": data["defines"],
            "filtered_rtl_files": filtered,
        }

    @staticmethod
    def _filelist_defines_module(files: list[Any], module_name: str) -> bool:
        return any(PrepareStep._file_defines_module(Path(path), module_name) for path in files)

    @staticmethod
    def _file_defines_module(path: Path, module_name: str) -> bool:
        if path.name in {f"{module_name}.v", f"{module_name}.sv"}:
            return True
        pattern = re.compile(rf"\bmodule\s+{re.escape(module_name)}\b")
        try:
            return bool(pattern.search(path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            return False

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

    def _write_prepared_manifest(self, step: WorkspaceStep, prepared: dict[str, list[str]]) -> Path:
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
            parts = line.split(maxsplit=1)
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
            token = line.split(maxsplit=1)[0].strip("\"'")
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

def _standard_cpu_wrapper_source(standard_top: str) -> str:
    top = standard_top.strip() or STANDARD_CPU_TOP
    return f"""// Generated by ecc-fe prepare.
// User-facing contract: provide module {top} with this AXI-like CPU socket.
// SoC-facing compatibility contract: module {COMPATIBILITY_CPU_ALIAS_TOP}.

module {COMPATIBILITY_CPU_ALIAS_TOP} (
  input         clock,
  input         reset,
  input         io_interrupt,
  input         io_master_awready,
  output        io_master_awvalid,
  output [31:0] io_master_awaddr,
  output [3:0]  io_master_awid,
  output [7:0]  io_master_awlen,
  output [2:0]  io_master_awsize,
  output [1:0]  io_master_awburst,
  output        io_master_awlock,
  output [3:0]  io_master_awcache,
  output [2:0]  io_master_awprot,
  output [3:0]  io_master_awqos,
  output [3:0]  io_master_awregion,
  input         io_master_wready,
  output        io_master_wvalid,
  output [31:0] io_master_wdata,
  output [3:0]  io_master_wstrb,
  output        io_master_wlast,
  output        io_master_bready,
  input         io_master_bvalid,
  input  [1:0]  io_master_bresp,
  input  [3:0]  io_master_bid,
  input         io_master_arready,
  output        io_master_arvalid,
  output [31:0] io_master_araddr,
  output [3:0]  io_master_arid,
  output [7:0]  io_master_arlen,
  output [2:0]  io_master_arsize,
  output [1:0]  io_master_arburst,
  output        io_master_arlock,
  output [3:0]  io_master_arcache,
  output [2:0]  io_master_arprot,
  output [3:0]  io_master_arqos,
  output [3:0]  io_master_arregion,
  output        io_master_rready,
  input         io_master_rvalid,
  input  [1:0]  io_master_rresp,
  input  [31:0] io_master_rdata,
  input         io_master_rlast,
  input  [3:0]  io_master_rid,
  output        io_slave_awready,
  input         io_slave_awvalid,
  input  [31:0] io_slave_awaddr,
  input  [3:0]  io_slave_awid,
  input  [7:0]  io_slave_awlen,
  input  [2:0]  io_slave_awsize,
  input  [1:0]  io_slave_awburst,
  input         io_slave_awlock,
  input  [3:0]  io_slave_awcache,
  input  [2:0]  io_slave_awprot,
  input  [3:0]  io_slave_awqos,
  input  [3:0]  io_slave_awregion,
  output        io_slave_wready,
  input         io_slave_wvalid,
  input  [31:0] io_slave_wdata,
  input  [3:0]  io_slave_wstrb,
  input         io_slave_wlast,
  input         io_slave_bready,
  output        io_slave_bvalid,
  output [1:0]  io_slave_bresp,
  output [3:0]  io_slave_bid,
  output        io_slave_arready,
  input         io_slave_arvalid,
  input  [31:0] io_slave_araddr,
  input  [3:0]  io_slave_arid,
  input  [7:0]  io_slave_arlen,
  input  [2:0]  io_slave_arsize,
  input  [1:0]  io_slave_arburst,
  input         io_slave_arlock,
  input  [3:0]  io_slave_arcache,
  input  [2:0]  io_slave_arprot,
  input  [3:0]  io_slave_arqos,
  input  [3:0]  io_slave_arregion,
  input         io_slave_rready,
  output        io_slave_rvalid,
  output [1:0]  io_slave_rresp,
  output [31:0] io_slave_rdata,
  output        io_slave_rlast,
  output [3:0]  io_slave_rid
);

  localparam [31:0] HALT_ADDR = 32'h1000_000c;
  localparam [31:0] UART_ADDR = 32'h1000_0000;

  reg        aw_pending_q;
  reg [31:0] aw_addr_q;

  wire aw_fire = io_master_awvalid & io_master_awready;
  wire w_fire  = io_master_wvalid & io_master_wready;

  wire [31:0] write_addr = aw_fire ? io_master_awaddr : aw_addr_q;
  wire halt_write_fire = w_fire & (aw_pending_q | aw_fire) & (write_addr == HALT_ADDR);
  wire uart_write_fire = w_fire & (aw_pending_q | aw_fire) & (write_addr == UART_ADDR);

  function automatic [7:0] axi_wstrb_byte;
    input [31:0] data;
    input [3:0] strb;
    begin
      casez (strb)
        4'b???1: axi_wstrb_byte = data[7:0];
        4'b??10: axi_wstrb_byte = data[15:8];
        4'b?100: axi_wstrb_byte = data[23:16];
        4'b1000: axi_wstrb_byte = data[31:24];
        default: axi_wstrb_byte = data[7:0];
      endcase
    end
  endfunction

  {top} u_cpu (
    .clock(clock),
    .reset(reset),
    .io_interrupt(io_interrupt),
    .io_master_awready(io_master_awready),
    .io_master_awvalid(io_master_awvalid),
    .io_master_awaddr(io_master_awaddr),
    .io_master_awid(io_master_awid),
    .io_master_awlen(io_master_awlen),
    .io_master_awsize(io_master_awsize),
    .io_master_awburst(io_master_awburst),
    .io_master_awlock(io_master_awlock),
    .io_master_awcache(io_master_awcache),
    .io_master_awprot(io_master_awprot),
    .io_master_awqos(io_master_awqos),
    .io_master_awregion(io_master_awregion),
    .io_master_wready(io_master_wready),
    .io_master_wvalid(io_master_wvalid),
    .io_master_wdata(io_master_wdata),
    .io_master_wstrb(io_master_wstrb),
    .io_master_wlast(io_master_wlast),
    .io_master_bready(io_master_bready),
    .io_master_bvalid(io_master_bvalid),
    .io_master_bresp(io_master_bresp),
    .io_master_bid(io_master_bid),
    .io_master_arready(io_master_arready),
    .io_master_arvalid(io_master_arvalid),
    .io_master_araddr(io_master_araddr),
    .io_master_arid(io_master_arid),
    .io_master_arlen(io_master_arlen),
    .io_master_arsize(io_master_arsize),
    .io_master_arburst(io_master_arburst),
    .io_master_arlock(io_master_arlock),
    .io_master_arcache(io_master_arcache),
    .io_master_arprot(io_master_arprot),
    .io_master_arqos(io_master_arqos),
    .io_master_arregion(io_master_arregion),
    .io_master_rready(io_master_rready),
    .io_master_rvalid(io_master_rvalid),
    .io_master_rresp(io_master_rresp),
    .io_master_rdata(io_master_rdata),
    .io_master_rlast(io_master_rlast),
    .io_master_rid(io_master_rid)
  );

  assign io_slave_awready = 1'b0;
  assign io_slave_wready = 1'b0;
  assign io_slave_bvalid = 1'b0;
  assign io_slave_bresp = 2'b00;
  assign io_slave_bid = 4'b0000;
  assign io_slave_arready = 1'b0;
  assign io_slave_rvalid = 1'b0;
  assign io_slave_rresp = 2'b00;
  assign io_slave_rdata = 32'b0;
  assign io_slave_rlast = 1'b0;
  assign io_slave_rid = 4'b0000;

  always @(posedge clock) begin
    if (reset) begin
      aw_pending_q <= 1'b0;
      aw_addr_q <= 32'b0;
    end else begin
      if (aw_fire) begin
        aw_pending_q <= 1'b1;
        aw_addr_q <= io_master_awaddr;
      end
      if (w_fire) begin
        aw_pending_q <= 1'b0;
      end
`ifndef SYNTHESIS
      if (uart_write_fire) begin
        $write("%c", axi_wstrb_byte(io_master_wdata, io_master_wstrb));
        $fflush();
      end
`endif
      if (halt_write_fire) begin
        if (io_master_wdata == 32'b0) begin
          $display("HIT GOOD TRAP");
          $finish;
        end else begin
          $fatal(1, "HIT BAD TRAP, code=%0d", io_master_wdata);
        end
      end
    end
  end

endmodule
"""
