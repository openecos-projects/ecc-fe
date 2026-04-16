#!/usr/bin/env python
"""Tests for fecompiler.data.step — StateEnum."""

from __future__ import annotations

from fecompiler.data.step import StateEnum


def test_state_enum_has_all_required_members():
    names = {e.name for e in StateEnum}
    assert names == {"Invalid", "Unstart", "Success", "Ongoing", "Pending", "Incomplete"}


def test_state_enum_values_match_names():
    for member in StateEnum:
        assert member.value == member.name


def test_state_enum_is_str_subclass():
    assert isinstance(StateEnum.Success, str)
    assert StateEnum.Success == "Success"


def test_state_enum_unstart_value():
    assert StateEnum.Unstart.value == "Unstart"


def test_state_enum_success_value():
    assert StateEnum.Success.value == "Success"


def test_state_enum_incomplete_value():
    assert StateEnum.Incomplete.value == "Incomplete"


def test_state_enum_can_be_compared_to_plain_string():
    assert StateEnum.Success == "Success"
    assert StateEnum.Unstart != "Success"


def test_state_enum_lookup_by_value():
    member = StateEnum("Ongoing")
    assert member is StateEnum.Ongoing
