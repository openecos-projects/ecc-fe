from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OutputMode(str, Enum):
    TEXT = "text"
    PLAIN = "plain"
    JSON = "json"
    JSONL = "jsonl"


@dataclass(frozen=True, slots=True)
class CommandContext:
    workspace_dir: str
    output_mode: OutputMode


@dataclass(frozen=True, slots=True)
class CommandResult:
    records: tuple[dict[str, object], ...] = field(default_factory=tuple)
    exit_code: int = 0

    @classmethod
    def ok(cls, records: list[dict[str, object]]) -> CommandResult:
        return cls(records=tuple(records))

    @classmethod
    def err(
        cls,
        records: list[dict[str, object]],
        *,
        exit_code: int = 1,
    ) -> CommandResult:
        return cls(records=tuple(records), exit_code=exit_code)
