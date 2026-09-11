"""Hash before parsing, snapshot regular files, and detect changes during capture."""

import os
import shutil
import stat
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from evidencegraph.ids import witness_id
from evidencegraph.provenance import sha256_file, verify_file_identity
from evidencegraph.schema import Witness


def _copy_regular(source: Path, target: Path) -> None:
    if not stat.S_ISREG(source.lstat().st_mode):
        raise ValueError(f"evidence must be a regular file: {source}")
    shutil.copyfile(source, target)
    if target.is_symlink() or not target.is_file():
        raise ValueError(f"captured input is not a regular file: {target}")


@contextmanager
def capture_paths(paths: Sequence[Path]) -> Iterator[tuple[Path, ...]]:
    with tempfile.TemporaryDirectory(prefix="eg-inputs-") as directory:
        targets = []
        for index, source in enumerate(paths):
            target = Path(directory) / str(index) / source.name
            target.parent.mkdir()
            _copy_regular(source, target)
            targets.append(target)
        yield tuple(targets)


def verify_artifact_origins(witnesses: Sequence[Witness]) -> None:
    for witness in witnesses:
        verify_file_identity(
            Path(witness.origin),
            expected_size=witness.size_bytes,
            expected_sha256=witness.sha256,
            subject=witness.witness_id,
        )


def acquire(
    root: Path,
    source: Path,
    *,
    adapter: str,
    trust_domain: str,
    kind: str,
    origin: str | None = None,
    coverage_claim: str = "No completeness claim",
) -> Witness:
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"not a regular evidence file: {source}")
    digest = sha256_file(source)
    size = source.stat().st_size
    identifier = witness_id(digest, adapter=adapter, filename=source.name)
    relative = Path("evidence") / identifier / source.name
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        verify_file_identity(
            destination, expected_size=size, expected_sha256=digest, subject=identifier
        )
    else:
        with capture_paths([source]) as captured:
            verify_file_identity(
                captured[0], expected_size=size, expected_sha256=digest, subject=identifier
            )
            shutil.copyfile(captured[0], destination)
        os.chmod(destination, 0o444)
    verify_file_identity(source, expected_size=size, expected_sha256=digest, subject=identifier)
    return Witness(
        witness_id=identifier,
        kind=kind,
        trust_domain=trust_domain,
        adapter=adapter,
        origin=origin or str(source.absolute()),
        sha256=digest,
        size_bytes=size,
        acquired_at=datetime.now(UTC).isoformat(),
        coverage_claim=coverage_claim,
        snapshot_path=relative.as_posix(),
        filename=source.name,
    )
