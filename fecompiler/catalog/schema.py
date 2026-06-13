"""Lightweight catalog schemas for frontend workspace selection.

The first catalog layer is intentionally data-driven.  It describes what ECOS
Studio can present and validate without pulling third-party core sources into
the repository.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_CAPABILITY_ORDER = {
    "metadata_only": 0,
    "filelist_ready": 1,
    "lint_ready": 2,
    "elab_ready": 3,
    "sim_ready": 4,
}


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    id: str
    name: str
    description: str = ""
    status: str = "planned"
    integration_level: str = "metadata_only"
    isa: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CatalogEntry":
        return cls(
            id=str(raw.get("id", "")).strip(),
            name=str(raw.get("name", "")).strip(),
            description=str(raw.get("description", "")).strip(),
            status=str(raw.get("status", "planned")).strip() or "planned",
            integration_level=str(raw.get("integration_level", "metadata_only")).strip()
            or "metadata_only",
            isa=[str(item).strip() for item in raw.get("isa", []) if str(item).strip()],
            tags=[str(item).strip() for item in raw.get("tags", []) if str(item).strip()],
            data=dict(raw),
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)

    @property
    def sim_ready(self) -> bool:
        return self.supports("sim_ready")

    @property
    def filelist_ready(self) -> bool:
        return self.supports("filelist_ready") or bool(self.data.get("cpu_filelist"))

    def supports(self, capability: str) -> bool:
        level = _CAPABILITY_ORDER.get(self.integration_level, -1)
        required = _CAPABILITY_ORDER.get(capability, 10)
        return level >= required


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    field: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "field": self.field,
        }


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    support_level: str
    summary: str
    normalized: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "support_level": self.support_level,
            "summary": self.summary,
            "normalized": self.normalized,
            "issues": [issue.to_dict() for issue in self.issues],
        }
