"""Small value, path, and file helpers used across the application layer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _optional_text(value)
        if text:
            return text
    return ""


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _existing_path_item(path: Any, label: str) -> dict[str, str] | None:
    path_text = str(path or "").strip()
    if not path_text:
        return None
    try:
        resolved = Path(path_text).expanduser().resolve()
    except Exception:
        return None
    if not resolved.exists():
        return None
    return {"label": label, "path": str(resolved)}


def _first_existing(directory: Path | None, names: tuple[str, ...]) -> Path | str:
    if directory is None:
        return ""
    for name in names:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return directory / names[0] if names else ""


def _optional_path(path: Any) -> Path | None:
    path_text = str(path or "").strip()
    if not path_text:
        return None
    return Path(path_text)


def _json_read(path: Any) -> Any:
    path_text = str(path or "").strip()
    if not path_text:
        return None
    try:
        resolved = Path(path_text).expanduser().resolve()
        if not resolved.is_file():
            return None
        with resolved.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _read_text_tail(path: Any, max_bytes: int) -> str:
    path_text = str(path or "").strip()
    if not path_text:
        return ""
    try:
        resolved = Path(path_text).expanduser().resolve()
        if not resolved.is_file():
            return ""
        with resolved.open("rb") as f:
            size = resolved.stat().st_size
            f.seek(max(size - max_bytes, 0))
            data = f.read(max_bytes)
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _step_section(step: Any, section: str) -> dict[str, Any]:
    value = step.get(section, {}) if hasattr(step, "get") else {}
    return value if isinstance(value, dict) else {}


def _resolve_path(value: Any, base_dir: Path) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


def _resolve_optional_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return str(Path(text).expanduser().resolve())


def _normalize_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = value.splitlines()
    else:
        items = [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
