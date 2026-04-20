#!/usr/bin/env python
"""Tests for fecompiler.allflow.builder — DEFAULT_FLOW_STEPS, sanitize_step_token, build_allflow."""

from __future__ import annotations

from fecompiler.allflow.builder import (
    DEFAULT_FLOW_STEPS,
    build_allflow,
    sanitize_step_token,
)


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


def test_default_flow_steps_all_use_ecc_tool():
    for name, tool in DEFAULT_FLOW_STEPS:
        if name == "prepare":
            assert tool == "fe"
        elif name in ("elab", "lint", "sim"):
            assert tool in ("slang", "verilator")
        else:
            assert tool == "ecc"


# ── sanitize_step_token ───────────────────────────────────────────────────────

def test_sanitize_alphanumeric_unchanged():
    assert sanitize_step_token("copyfiles") == "copyfiles"


def test_sanitize_spaces_become_underscores():
    assert sanitize_step_token("place route") == "place_route"


def test_sanitize_special_chars_become_underscores():
    # trailing underscore is stripped by .strip("_")
    assert sanitize_step_token("fix-fanout!") == "fix_fanout"


def test_sanitize_strips_leading_trailing_underscores():
    assert sanitize_step_token("_step_") == "step"


def test_sanitize_empty_string_returns_step():
    assert sanitize_step_token("") == "step"


def test_sanitize_all_special_chars_returns_step():
    assert sanitize_step_token("---") == "step"


def test_sanitize_mixed_case_preserved():
    assert sanitize_step_token("MyStep") == "MyStep"


def test_sanitize_numbers_preserved():
    assert sanitize_step_token("step2") == "step2"


# ── build_allflow ─────────────────────────────────────────────────────────────

def test_build_allflow_returns_list_of_3_tuples():
    flow = build_allflow()
    assert isinstance(flow, list)
    for entry in flow:
        assert len(entry) == 3


def test_build_allflow_length_matches_default_steps():
    assert len(build_allflow()) == len(DEFAULT_FLOW_STEPS)


def test_build_allflow_all_states_are_unstart():
    for _, _, state in build_allflow():
        assert state == "Unstart"


def test_build_allflow_names_match_default_steps():
    flow = build_allflow()
    for (exp_name, exp_tool), (got_name, got_tool, _) in zip(DEFAULT_FLOW_STEPS, flow):
        assert got_name == exp_name
        assert got_tool == exp_tool
