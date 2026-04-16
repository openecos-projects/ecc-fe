"""Utility helpers — json.py mirrors chipcompiler/utility/json.py."""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Any


def json_read(file_path: str | Path) -> dict[str, Any]:
    """Read a JSON file and return its content as a dictionary.

    Supports plain JSON and gzip-compressed JSON (.gz suffix).
    Returns {} if the file is missing, unreadable, or malformed.
    """
    p = str(file_path)
    if not os.path.isfile(p):
        return {}
    try:
        if p.endswith(".gz"):
            with gzip.open(p, "rt", encoding="utf-8") as f:
                return json.load(f)
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def json_write(file_path: str | Path, data: dict[str, Any] = {}, indent: int = 2) -> bool:
    """Write *data* as JSON to *file_path*; create parent dirs as needed.

    Supports plain JSON and gzip-compressed JSON (.gz suffix).
    Returns True on success, False on any OS/IO error.
    """
    p = str(file_path)
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        if p.endswith(".gz"):
            with gzip.open(p, "wt", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, ensure_ascii=False)
        else:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, ensure_ascii=False)
        return True
    except Exception:
        return False


def dict_to_str(d: Any, indent: int = 0) -> str:
    """Render nested dictionaries as ASCII tables for log output.

    - Scalar values and simple lists are rendered as a two-column key/value table.
    - Nested dictionaries become titled sections.
    - Lists of dictionaries become row tables with an index column.
    """

    def _render_dict_block(mapping, lines, base_indent, depth, title, is_root=False):
        current_indent = base_indent + depth
        if title is not None:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"{'  ' * current_indent}[{title}]")
        pending_rows: list[list[str]] = []
        child_depth = depth if is_root else depth + 1
        for key, value in mapping.items():
            if isinstance(value, dict):
                _flush_key_value_rows(lines, pending_rows, current_indent)
                _render_dict_block(value, lines, base_indent, child_depth, str(key))
                continue
            if isinstance(value, list) and not _is_inline_list(value):
                _flush_key_value_rows(lines, pending_rows, current_indent)
                _render_list_block(value, lines, base_indent, child_depth, str(key))
                continue
            pending_rows.append([str(key), _format_inline_value(value)])
        _flush_key_value_rows(lines, pending_rows, current_indent)

    def _render_list_block(values, lines, base_indent, depth, title):
        current_indent = base_indent + depth
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"{'  ' * current_indent}[{title}]")
        if not values:
            _append_table(lines, _build_table(["#", "Value"], [["-", "[]"]], current_indent))
            return
        if all(isinstance(item, dict) for item in values):
            headers = _collect_headers(values)
            rows = [
                [str(i)] + [_format_inline_value(item.get(h, "")) for h in headers]
                for i, item in enumerate(values, 1)
            ]
            _append_table(lines, _build_table(["#"] + headers, rows, current_indent))
            return
        rows = [[str(i), _format_inline_value(v)] for i, v in enumerate(values, 1)]
        _append_table(lines, _build_table(["#", "Value"], rows, current_indent))

    def _flush_key_value_rows(lines, rows, ind):
        if not rows:
            return
        _append_table(lines, _build_table(["Key", "Value"], rows, ind))
        rows.clear()

    def _append_table(lines, table_lines):
        if not table_lines:
            return
        if lines and lines[-1] != "" and not lines[-1].lstrip().startswith("["):
            lines.append("")
        lines.extend(table_lines)

    def _build_table(headers, rows, ind):
        string_rows = [[str(c) for c in row] for row in rows]
        widths = [len(str(h)) for h in headers]
        for row in string_rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(cell))
        prefix = "  " * ind
        border = prefix + "+-" + "-+-".join("-" * w for w in widths) + "-+"
        header_line = prefix + "| " + " | ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)) + " |"
        table = [border, header_line, border]
        for row in string_rows:
            table.append(prefix + "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(row)) + " |")
        table.append(border)
        return table

    def _collect_headers(items):
        seen: set[str] = set()
        headers: list[str] = []
        for item in items:
            for k in item:
                if str(k) not in seen:
                    seen.add(str(k))
                    headers.append(str(k))
        return headers

    def _is_inline_list(values):
        return all(not isinstance(v, (dict, list)) for v in values)

    def _format_inline_value(value):
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, (dict, list)):
                    parts.append(json.dumps(item, ensure_ascii=False))
                else:
                    parts.append(_format_inline_value(item))
            return "[" + ", ".join(parts) + "]"
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    if not isinstance(d, dict):
        return _format_inline_value(d)
    lines: list[str] = []
    _render_dict_block(d, lines, base_indent=indent, depth=0, title=None, is_root=True)
    return "\n".join(lines)


__all__ = ["json_read", "json_write", "dict_to_str"]
