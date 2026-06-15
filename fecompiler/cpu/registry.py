"""Data-driven CPU wrapper metadata.

CPU RTLs expose very different native ports.  ECOS treats each CPU integration
as a wrapper that adapts the native core to a stable CPU socket contract used by
the selected SoC wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_CPU_WRAPPER_ID = "custom-filelist"
DEFAULT_CPU_SOCKET = "ysyx-axi-cpu-socket-v1"


@dataclass(frozen=True, slots=True)
class CpuWrapper:
    id: str
    name: str
    socket_contract: str = DEFAULT_CPU_SOCKET
    wrapper_contract: str = "ecos-cpu-wrapper-v1"
    wrapper_top: str = "ysyx_00000000"
    sim_ready: bool = False
    supports_difftest: bool = True

    def metadata(self) -> dict[str, Any]:
        return {
            "cpu_wrapper_id": self.id,
            "cpu_wrapper_contract": self.wrapper_contract,
            "cpu_socket_contract": self.socket_contract,
            "cpu_wrapper_top": self.wrapper_top,
            "cpu_supports_difftest": self.supports_difftest,
        }


def get_cpu_wrapper(config: dict[str, Any] | str | None) -> CpuWrapper | None:
    if isinstance(config, str):
        wrapper_id = config.strip() or DEFAULT_CPU_WRAPPER_ID
    else:
        data = dict(config or {})
        wrapper_id = (
            str(data.get("cpu_wrapper_id", "")).strip()
            or str(data.get("core_id", "")).strip()
            or str(data.get("frontend_core_id", "")).strip()
            or DEFAULT_CPU_WRAPPER_ID
        )

    if wrapper_id == "custom-filelist":
        return CpuWrapper(
            id="custom-filelist",
            name="My CPU Filelist",
            sim_ready=True,
        )
    if wrapper_id == "picorv32":
        return CpuWrapper(
            id="picorv32",
            name="PicoRV32",
            wrapper_top="ecos_picorv32_cpu_wrapper",
            sim_ready=True,
            supports_difftest=False,
        )
    if wrapper_id == "scr1":
        return CpuWrapper(
            id="scr1",
            name="SCR1",
            wrapper_top="ecos_scr1_cpu_wrapper",
            sim_ready=False,
        )
    if wrapper_id == "ibex":
        return CpuWrapper(
            id="ibex",
            name="Ibex",
            wrapper_top="ecos_ibex_cpu_wrapper",
            sim_ready=False,
        )
    if wrapper_id == "cv32e40p":
        return CpuWrapper(
            id="cv32e40p",
            name="CV32E40P",
            wrapper_top="ecos_cv32e40p_cpu_wrapper",
            sim_ready=False,
        )
    return None
