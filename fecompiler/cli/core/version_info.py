from __future__ import annotations

import importlib.metadata
import platform

from fecompiler.runtime.server import PROTOCOL_VERSION

VERSION_SCHEMA = 1
SOURCE_VERSION = "0.1.0-alpha.0"


def ecc_fe_version() -> str:
    try:
        return importlib.metadata.version("ecc-fe")
    except importlib.metadata.PackageNotFoundError:
        return SOURCE_VERSION


def root_version_line() -> str:
    return f"ecc-fe {ecc_fe_version()}"


def version_payload() -> dict[str, object]:
    return {
        "schema_version": VERSION_SCHEMA,
        "runtime": "ECC-FE CLI",
        "ecc_fe": ecc_fe_version(),
        "rpc_protocol": PROTOCOL_VERSION,
        "python": platform.python_version(),
        "platform": platform.system().lower(),
        "architecture": platform.machine().lower(),
    }


def version_text(payload: dict[str, object]) -> str:
    ordered = (
        "ecc_fe",
        "runtime",
        "rpc_protocol",
        "python",
        "platform",
        "architecture",
    )
    return "\n".join(f"{key} {payload[key]}" for key in ordered)
