"""Flow step states."""

from __future__ import annotations

from enum import Enum


class StateEnum(str, Enum):
    """Flow step running state."""

    Invalid    = "Invalid"      # tool or config invalid
    Unstart    = "Unstart"      # step not yet started
    Success    = "Success"      # step completed successfully
    Ongoing    = "Ongoing"      # step is running
    Pending    = "Pending"      # step is queued
    Incomplete = "Incomplete"   # step failed
