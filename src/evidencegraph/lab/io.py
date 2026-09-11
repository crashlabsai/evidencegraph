"""Durable lab output helpers extracted from pilot.board."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def append_json(path: Path, value: object) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def save_json(path: Path, value: object) -> None:
    from evidencegraph.manifest import atomic_json

    atomic_json(path, value)
