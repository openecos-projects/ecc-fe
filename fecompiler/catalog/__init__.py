"""Frontend catalog registry for cores, SoC harnesses, toolchains, and tests."""

from fecompiler.catalog.registry import catalog_payload, validate_frontend_config

__all__ = [
    "catalog_payload",
    "validate_frontend_config",
]
