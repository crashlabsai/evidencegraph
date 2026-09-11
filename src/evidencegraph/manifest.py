"""Case locks and provenance for atomic graph generations, adapted from Crossledger."""

import importlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from evidencegraph.provenance import analyzer_build_id
from evidencegraph.schema import SCHEMA_VERSION
from evidencegraph.strict_json import load_strict_json


class ManifestIntegrityError(ValueError):
    pass


class CaseMutationLockError(ManifestIntegrityError):
    pass


@contextmanager
def case_mutation_lock(case_root: str | Path) -> Iterator[None]:
    lock_path = Path(case_root) / ".evidencegraph.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    acquired = False
    try:
        try:
            _lock_descriptor(descriptor)
            acquired = True
        except OSError as exc:
            raise CaseMutationLockError(
                f"another command holds the case lock: {lock_path}"
            ) from exc
        yield
    finally:
        if acquired:
            _unlock_descriptor(descriptor)
        os.close(descriptor)


def _lock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt")
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        fcntl = importlib.import_module("fcntl")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt")
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl = importlib.import_module("fcntl")
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def atomic_json(path: Path, value: object) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_manifest(root: Path) -> dict:
    value = load_strict_json(root / "manifest.json", max_bytes=32 * 1024 * 1024)
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ManifestIntegrityError("unsupported case manifest")
    return value


def update_manifest_stage(manifest: dict, stage: str, *, params: dict | None = None) -> None:
    manifest.setdefault("stages", {})[stage] = {
        "completed_at": datetime.now(UTC).isoformat(),
        "analyzer_build_id": analyzer_build_id(),
        "parameters": params or {},
    }
