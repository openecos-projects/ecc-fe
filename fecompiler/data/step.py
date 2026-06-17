"""Step enums and metrics — mirrors chipcompiler/data/step.py in ecos-studio/ecc."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from fecompiler.utility.json import json_read, json_write


class StepEnum(Enum):
    """Generic flow step names used by ecc-fe."""

    PREPARE = "prepare"
    REVIEW = "review"
    ELAB  = "elab"
    LINT  = "lint"
    SIM   = "sim"


class StateEnum(str, Enum):
    """Flow step running state."""

    Invalid    = "Invalid"      # tool or config invalid
    Unstart    = "Unstart"      # step not yet started
    Success    = "Success"      # step completed successfully
    Ongoing    = "Ongoing"      # step is running
    Pending    = "Pending"      # step is queued
    Incomplete = "Incomplete"   # step failed


@dataclass
class StepMetrics:
    """Step metrics data — mirrors chipcompiler/data/step.py::StepMetrics."""

    path:   str        = ""
    data:   dict       = field(default_factory=dict)
    report: list       = field(default_factory=list)


def load_metrics(path: str) -> StepMetrics:
    m = StepMetrics(path=path)
    m.data = json_read(path)
    return m


def save_metrics(metrics: StepMetrics) -> None:
    if metrics.path:
        json_write(metrics.path, metrics.data)
