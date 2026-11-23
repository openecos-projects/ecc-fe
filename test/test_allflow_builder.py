#!/usr/bin/env python
"""Tests for the default flow and the design-kind profiles."""

from __future__ import annotations

from fecompiler.allflow.builder import DEFAULT_FLOW_STEPS
from fecompiler.allflow.profile import CPU_CORE, GENERIC_RTL, flow_steps_for


# ── DEFAULT_FLOW_STEPS ────────────────────────────────────────────────────────

def test_default_flow_steps_is_non_empty():
    assert len(DEFAULT_FLOW_STEPS) > 0


def test_default_flow_steps_first_entry_is_prepare():
    name, tool = DEFAULT_FLOW_STEPS[0]
    assert name == "prepare"
    assert tool == "fe"


def test_default_flow_steps_all_entries_are_2_tuples():
    for entry in DEFAULT_FLOW_STEPS:
        assert len(entry) == 2
        assert isinstance(entry[0], str)
        assert isinstance(entry[1], str)


def test_default_flow_steps_match_production_flow():
    assert DEFAULT_FLOW_STEPS == [
        ("prepare", "fe"),
        ("review", "fe"),
        ("elab", "slang"),
        ("lint", "verilator"),
        ("sim", "verilator"),
    ]


def test_generic_rtl_profile_omits_simulation():
    assert flow_steps_for(GENERIC_RTL) == [
        ("prepare", "fe"),
        ("review", "fe"),
        ("elab", "slang"),
        ("lint", "verilator"),
    ]


def test_missing_design_kind_retains_legacy_cpu_flow():
    assert flow_steps_for("") == flow_steps_for(CPU_CORE) == DEFAULT_FLOW_STEPS
