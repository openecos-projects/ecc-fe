"""Frontend catalog registry for cores, SoC harnesses, toolchains, and tests."""

from fecompiler.catalog.contract import check_catalog_contracts
from fecompiler.catalog.registry import (
    catalog_payload,
    cpu_filelist_supports_difftest,
    validate_frontend_config,
)

__all__ = [
    "catalog_payload",
    "check_catalog_contracts",
    "cpu_filelist_supports_difftest",
    "validate_frontend_config",
]
