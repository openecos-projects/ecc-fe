"""Data-driven SoC wrapper runtime options.

This module is the backend companion of the RTL wrapper contract.  Different
SoCs may have different internal RTL and build scripts, but they should expose
the same simulator-facing contract through a wrapper and a manifest-like
runtime description here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_SOC_WRAPPER_ID = "ysyx-am-soc"


@dataclass(frozen=True, slots=True)
class SocWrapper:
    id: str
    name: str
    variant: str
    root: Path | None
    top_module: str = "ecos_sim_top"
    sim_ready: bool = False
    contract: str = "ecos-sim-wrapper-v1"
    soc_filelist: str = "filelist.soc.f"
    testbench: str = "driver/main.cpp"
    sim_cpp_sources: tuple[str, ...] = ("driver/dpi_mem.cpp", "driver/difftest.cpp")
    sim_cflags: tuple[str, ...] = ("-I{soc_root}",)
    sim_ldflags: tuple[str, ...] = ("-ldl",)
    sim_programs_dir: str = "tests/programs"
    sim_tests_dir: str = "tests/out"
    sim_build_test_script: str = "scripts/build_test.sh"

    def runtime_options(self) -> dict[str, Any]:
        if not self.sim_ready or self.root is None:
            return {}

        root = self.root.resolve()
        return {
            "soc_wrapper_id": self.id,
            "soc_wrapper_contract": self.contract,
            "soc_variant": self.variant,
            "top_module": self.top_module,
            "sim_soc_root": str(root),
            "soc_filelist": str(root / self.soc_filelist),
            "testbench": str(root / self.testbench),
            "sim_cpp_sources": [str(root / source) for source in self.sim_cpp_sources],
            "sim_cflags": [flag.format(soc_root=root) for flag in self.sim_cflags],
            "sim_ldflags": list(self.sim_ldflags),
            "sim_programs_dir": str(root / self.sim_programs_dir),
            "sim_tests_dir": str(root / self.sim_tests_dir),
            "sim_build_test_script": str(root / self.sim_build_test_script),
        }


def get_soc_wrapper(config: dict[str, Any] | str | None) -> SocWrapper | None:
    if isinstance(config, str):
        wrapper_id = _normalize_soc_wrapper_id(config)
        data: dict[str, Any] = {}
    else:
        data = dict(config or {})
        wrapper_id = _wrapper_id_from_config(data)

    repo_root = _frontend_repo_root()
    if wrapper_id == "ysyx-am-soc":
        return _ysyx_wrapper("ysyx-am-soc", "YSYX AM SoC Harness", "soc1", repo_root / "fecompiler" / "thirdparty" / "SoC")
    if wrapper_id == "ysyx-am-soc-alt":
        return _ysyx_wrapper("ysyx-am-soc-alt", "YSYX AM SoC Harness Alt", "soc2", repo_root / "fecompiler" / "thirdparty" / "SoC2")
    if wrapper_id == "ysyx-am-soc-extended":
        return _ysyx_wrapper("ysyx-am-soc-extended", "YSYX AM SoC Harness Extended", "soc3", repo_root / "fecompiler" / "thirdparty" / "SoC3")
    if wrapper_id == "minimal-riscv-soc":
        return SocWrapper(
            id="minimal-riscv-soc",
            name="Minimal RISC-V SoC Harness",
            variant="minimal-riscv",
            root=None,
            top_module="ecos_sim_top",
            sim_ready=False,
        )
    return None


def soc_runtime_options(config: dict[str, Any] | str | None) -> dict[str, Any]:
    wrapper = get_soc_wrapper(config)
    return wrapper.runtime_options() if wrapper is not None else {}


def _ysyx_wrapper(wrapper_id: str, name: str, variant: str, root: Path) -> SocWrapper:
    return SocWrapper(
        id=wrapper_id,
        name=name,
        variant=variant,
        root=root,
        top_module="ecos_sim_top",
        sim_ready=root.exists(),
    )


def _wrapper_id_from_config(data: dict[str, Any]) -> str:
    for field in ("soc_wrapper_id", "soc_harness_id", "soc_id"):
        value = str(data.get(field, "")).strip()
        if value:
            return _normalize_soc_wrapper_id(value)
    return _normalize_soc_wrapper_id(str(data.get("soc_variant", "")).strip())


def _normalize_soc_wrapper_id(value: str) -> str:
    text = value.strip()
    variant_map = {
        "": DEFAULT_SOC_WRAPPER_ID,
        "soc1": "ysyx-am-soc",
        "soc2": "ysyx-am-soc-alt",
        "soc3": "ysyx-am-soc-extended",
    }
    return variant_map.get(text, text)


def _frontend_repo_root() -> Path:
    env_root = os.getenv("ECOS_FE_COMPILER_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[2]
