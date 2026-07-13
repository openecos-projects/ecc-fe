"""Data-driven CPU wrapper metadata.

CPU RTLs expose very different native ports.  ECOS treats each CPU integration
as a wrapper that adapts the native core to a stable CPU socket contract used by
the selected SoC wrapper.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CPU_WRAPPER_ID = "custom-filelist"
DEFAULT_CPU_SOCKET = "ysyx-axi-cpu-socket-v1"


@dataclass(frozen=True, slots=True)
class CpuWrapper:
    id: str
    name: str
    socket_contract: str = DEFAULT_CPU_SOCKET
    wrapper_contract: str = "ecos-cpu-wrapper-v1"
    wrapper_top: str = "cpu_top"
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

    manifest = _cpu_manifest(wrapper_id)
    if manifest is not None:
        return _wrapper_from_manifest(manifest)

    if wrapper_id == "custom-filelist":
        return CpuWrapper(
            id="custom-filelist",
            name="My CPU Top",
            sim_ready=True,
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


def _cpu_manifest(wrapper_id: str) -> dict[str, Any] | None:
    manifest_path = _manifest_paths().get(wrapper_id)
    if manifest_path is None:
        return None
    with manifest_path.open(encoding="utf-8") as f:
        data = json.load(f)
    return dict(data) if isinstance(data, dict) else None


def _wrapper_from_manifest(data: dict[str, Any]) -> CpuWrapper:
    return CpuWrapper(
        id=str(data.get("id", "")).strip(),
        name=str(data.get("name", "")).strip(),
        socket_contract=str(data.get("socket_contract", DEFAULT_CPU_SOCKET)).strip() or DEFAULT_CPU_SOCKET,
        wrapper_contract=str(data.get("wrapper_contract", "ecos-cpu-wrapper-v1")).strip() or "ecos-cpu-wrapper-v1",
        wrapper_top=str(data.get("wrapper_top", "cpu_top")).strip() or "cpu_top",
        sim_ready=bool(data.get("sim_ready", False)),
        supports_difftest=bool(data.get("supports_difftest", True)),
    )


def _manifest_paths() -> dict[str, Path]:
    root = Path(__file__).resolve().parents[1] / "adapters"
    paths: dict[str, Path] = {}
    if not root.exists():
        return paths
    for manifest_path in sorted(root.glob("*/manifest.json")):
        try:
            with manifest_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        wrapper_id = str(data.get("id", "")).strip()
        if wrapper_id:
            paths[wrapper_id] = manifest_path
    return paths
