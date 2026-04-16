#!/usr/bin/env python
"""Tests for fecompiler.utility — json_read / json_write."""

from __future__ import annotations

import json
from pathlib import Path

from fecompiler.utility import json_read, json_write


# ── json_read ─────────────────────────────────────────────────────────────────

def test_json_read_returns_dict_for_valid_file(tmp_path):
    f = tmp_path / "data.json"
    f.write_text(json.dumps({"key": "value", "num": 42}), encoding="utf-8")
    result = json_read(f)
    assert result == {"key": "value", "num": 42}


def test_json_read_returns_empty_dict_when_file_missing(tmp_path):
    result = json_read(tmp_path / "nonexistent.json")
    assert result == {}


def test_json_read_returns_empty_dict_for_malformed_json(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not valid json", encoding="utf-8")
    result = json_read(f)
    assert result == {}


def test_json_read_accepts_string_path(tmp_path):
    f = tmp_path / "s.json"
    f.write_text('{"a": 1}', encoding="utf-8")
    result = json_read(str(f))
    assert result["a"] == 1


def test_json_read_preserves_unicode(tmp_path):
    f = tmp_path / "unicode.json"
    payload = {"名称": "芯片"}
    f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert json_read(f) == payload


# ── json_write ────────────────────────────────────────────────────────────────

def test_json_write_creates_file_with_correct_content(tmp_path):
    target = tmp_path / "out.json"
    result = json_write(target, {"x": 1})
    assert result is True
    assert json.loads(target.read_text()) == {"x": 1}


def test_json_write_creates_parent_directories(tmp_path):
    target = tmp_path / "a" / "b" / "c.json"
    json_write(target, {"nested": True})
    assert target.exists()


def test_json_write_indented_output(tmp_path):
    target = tmp_path / "pretty.json"
    json_write(target, {"a": 1})
    text = target.read_text()
    # indented JSON must contain newlines
    assert "\n" in text


def test_json_write_preserves_unicode(tmp_path):
    target = tmp_path / "uni.json"
    json_write(target, {"名称": "芯片"})
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["名称"] == "芯片"


def test_json_write_overwrites_existing_file(tmp_path):
    target = tmp_path / "over.json"
    json_write(target, {"v": 1})
    json_write(target, {"v": 2})
    assert json.loads(target.read_text())["v"] == 2


def test_json_write_accepts_string_path(tmp_path):
    target = tmp_path / "str.json"
    result = json_write(str(target), {"ok": True})
    assert result is True
    assert target.exists()
