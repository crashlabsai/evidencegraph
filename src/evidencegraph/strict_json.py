"""Shared rejection rules for JSON constructs Python accepts too permissively."""

from __future__ import annotations

import json
import math
from pathlib import Path


class StrictJsonError(ValueError):
    """An input exceeds strict JSON syntax or resource limits."""


def unique_json_object[T](pairs: list[tuple[str, T]]) -> dict[str, T]:
    value: dict[str, T] = {}
    for key, item in pairs:
        if key in value:
            raise StrictJsonError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def load_strict_json(path: Path, *, max_bytes: int) -> object:
    """Bound bytes before parsing and report excessive nesting as invalid input."""
    with path.open("rb") as source:
        content = source.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise StrictJsonError(f"JSON input exceeds {max_bytes} bytes")
    try:
        return json.loads(
            content,
            object_pairs_hook=unique_json_object,
            parse_float=finite_json_float,
            parse_constant=reject_json_constant,
        )
    except RecursionError as exc:
        raise StrictJsonError(f"{path.name} JSON nesting exceeds the parser limit") from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise StrictJsonError(f"invalid JSON: {exc}") from exc


def finite_json_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise StrictJsonError(f"non-finite JSON number {raw!r}")
    return value


def reject_json_constant(raw: str) -> None:
    raise StrictJsonError(f"non-standard JSON constant {raw!r}")
