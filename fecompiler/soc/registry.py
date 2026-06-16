"""Data-driven SoC wrapper runtime options.

This module is the backend companion of the RTL wrapper contract.  Different
SoCs may have different internal RTL and build scripts, but they should expose
the same simulator-facing contract through a wrapper and a manifest-like
runtime description here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_SOC_WRAPPER_ID = "ysyx-am-soc"

_LEGACY_WRAPPER_DIRS = {
    "ysyx-am-soc": "SoC",
    "ysyx-am-soc-alt": "SoC2",
    "ysyx-am-soc-extended": "SoC3",
}

_LEGACY_WRAPPER_VARIANTS = {
    "ysyx-am-soc": "soc1",
    "ysyx-am-soc-alt": "soc2",
    "ysyx-am-soc-extended": "soc3",
}

_LEGACY_WRAPPER_NAMES = {
    "ysyx-am-soc": "YSYX AM SoC Harness",
    "ysyx-am-soc-alt": "YSYX AM SoC Harness Alt",
    "ysyx-am-soc-extended": "YSYX AM SoC Harness Extended",
}


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
    supports_difftest: bool = True

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
            "soc_supports_difftest": self.supports_difftest,
        }


def get_soc_wrapper(config: dict[str, Any] | str | None) -> SocWrapper | None:
    if isinstance(config, str):
        wrapper_id = _normalize_soc_wrapper_id(config)
        data: dict[str, Any] = {}
    else:
        data = dict(config or {})
        wrapper_id = _wrapper_id_from_config(data)

    manifest = _soc_manifest(wrapper_id)
    if manifest is not None:
        return _wrapper_from_manifest(manifest)
    return _legacy_wrapper_from_directory(wrapper_id)


def soc_runtime_options(config: dict[str, Any] | str | None) -> dict[str, Any]:
    wrapper = get_soc_wrapper(config)
    return wrapper.runtime_options() if wrapper is not None else {}


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


def _soc_manifest(wrapper_id: str) -> dict[str, Any] | None:
    manifest_path = _manifest_paths().get(wrapper_id)
    if manifest_path is None:
        return None
    with manifest_path.open(encoding="utf-8") as f:
        data = json.load(f)
    return dict(data, _manifest_path=str(manifest_path)) if isinstance(data, dict) else None


def _legacy_wrapper_from_directory(wrapper_id: str) -> SocWrapper | None:
    directory_name = _LEGACY_WRAPPER_DIRS.get(wrapper_id)
    if directory_name is None:
        return None

    root = _frontend_repo_root() / "fecompiler" / "thirdparty" / directory_name
    if not root.exists():
        return None

    return SocWrapper(
        id=wrapper_id,
        name=_LEGACY_WRAPPER_NAMES.get(wrapper_id, wrapper_id),
        variant=_LEGACY_WRAPPER_VARIANTS.get(wrapper_id, ""),
        root=root,
        sim_ready=True,
    )


def _wrapper_from_manifest(data: dict[str, Any]) -> SocWrapper:
    manifest_path = Path(str(data.get("_manifest_path", ""))).resolve()
    root = manifest_path.parent if manifest_path.name else None
    sim_ready = bool(data.get("sim_ready", False)) and root is not None and root.exists()
    return SocWrapper(
        id=str(data.get("id", "")).strip(),
        name=str(data.get("name", "")).strip(),
        variant=str(data.get("variant", "")).strip(),
        root=root,
        top_module=str(data.get("top_module", "ecos_sim_top")).strip() or "ecos_sim_top",
        sim_ready=sim_ready,
        contract=str(data.get("contract", "ecos-sim-wrapper-v1")).strip() or "ecos-sim-wrapper-v1",
        soc_filelist=str(data.get("soc_filelist", "filelist.soc.f")).strip() or "filelist.soc.f",
        testbench=str(data.get("testbench", "driver/main.cpp")).strip() or "driver/main.cpp",
        sim_cpp_sources=tuple(_str_list(data.get("sim_cpp_sources", ["driver/dpi_mem.cpp", "driver/difftest.cpp"]))),
        sim_cflags=tuple(_str_list(data.get("sim_cflags", ["-I{soc_root}"]))),
        sim_ldflags=tuple(_str_list(data.get("sim_ldflags", ["-ldl"]))),
        sim_programs_dir=str(data.get("sim_programs_dir", "tests/programs")).strip() or "tests/programs",
        sim_tests_dir=str(data.get("sim_tests_dir", "tests/out")).strip() or "tests/out",
        sim_build_test_script=str(data.get("sim_build_test_script", "scripts/build_test.sh")).strip() or "scripts/build_test.sh",
        supports_difftest=bool(data.get("supports_difftest", True)),
    )


def _manifest_paths() -> dict[str, Path]:
    root = _frontend_repo_root() / "fecompiler" / "thirdparty"
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


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
