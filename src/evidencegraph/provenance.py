"""Provenance primitives: content hashing and reproducible locators.

Hashing is the backbone of the "never repair in place" rule. Every artifact and
every record carries a SHA-256 of the exact bytes it was derived from, so any
downstream claim can be traced to immutable source content.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import platform
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from evidencegraph import __version__

_CHUNK = 1 << 20  # 1 MiB


def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 of a byte string."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Hex SHA-256 of text, encoded as UTF-8. Use for previews and record content
    where the source is already decoded; prefer `sha256_bytes` for raw inputs."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_stream(stream: BinaryIO) -> str:
    """Hex SHA-256 of a binary stream, read in chunks so large inputs stay bounded."""
    h = hashlib.sha256()
    while True:
        chunk = stream.read(_CHUNK)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()


def sha256_file(path: str | Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    with Path(path).open("rb") as fh:
        return sha256_stream(fh)


def verify_file_identity(
    path: str | Path,
    *,
    expected_size: int | None,
    expected_sha256: str,
    subject: str,
) -> None:
    """Require one regular file to retain its recorded size and content digest."""
    target = Path(path)
    if not target.is_file() or target.is_symlink():
        raise ValueError(f"{subject} is missing or not a regular file: {target}")
    actual_size = target.stat().st_size
    if expected_size is not None and actual_size != expected_size:
        raise ValueError(f"{subject} changed size: expected {expected_size}, got {actual_size}")
    actual_sha256 = sha256_file(target)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"{subject} hash mismatch: expected {expected_sha256}, got {actual_sha256}"
        )


def source_tree_sha256(root: str | Path, *, exclude: tuple[str, ...] = ()) -> str:
    """Hash Python source bytes and relative paths into one deterministic identity.

    `exclude` names top-level subpackages left out of the identity.
    """
    package_root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.relative_to(package_root).parts[0] in exclude:
            continue
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


@lru_cache(maxsize=1)
def analyzer_build_id() -> str:
    """Package version plus the exact installed analyzer-source fingerprint.

    The read-only viewer under `ui/` derives nothing, so editing it does not
    invalidate derived stages.
    """
    digest = source_tree_sha256(Path(__file__).resolve().parent, exclude=("ui",))
    return f"{__version__}+source.{digest[:20]}"


def runtime_versions(root_distributions: tuple[str, ...] = ("evidencegraph",)) -> dict[str, str]:
    """Return the active installed dependency closure for reproducible execution.

    Only marker-active requirements reachable from the named distributions are
    included. Development tools that happen to share the environment therefore do
    not make otherwise identical analyzer runs appear different.
    """
    return dict(_runtime_version_items(root_distributions))


@lru_cache(maxsize=8)
def _runtime_version_items(root_distributions: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Cache an immutable runtime representation so callers cannot corrupt it."""
    environment = {key: str(value) for key, value in default_environment().items()}
    pending = deque((name, frozenset[str]()) for name in root_distributions)
    processed_extras: dict[str, set[str]] = {}
    versions: dict[str, str] = {
        "python-implementation": platform.python_implementation(),
        "python-version": platform.python_version(),
    }
    while pending:
        requested_name, requested_extras = pending.popleft()
        canonical_requested = canonicalize_name(requested_name)
        try:
            distribution = importlib.metadata.distribution(requested_name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(
                f"cannot record runtime provenance; distribution {requested_name!r} is missing"
            ) from exc
        installed_name = distribution.metadata.get("Name") or requested_name
        canonical_installed = canonicalize_name(installed_name)
        marker_extras = {"", *requested_extras}
        already_processed = processed_extras.setdefault(canonical_installed, set())
        new_extras = marker_extras - already_processed
        if not new_extras:
            continue
        already_processed.update(new_extras)
        if canonical_requested != canonical_installed:
            processed_extras[canonical_requested] = already_processed
        versions[canonical_installed] = distribution.version
        for raw_requirement in distribution.requires or ():
            try:
                requirement = Requirement(raw_requirement)
            except InvalidRequirement as exc:
                raise RuntimeError(
                    f"cannot parse requirement for {installed_name!r}: {raw_requirement!r}"
                ) from exc
            applies = requirement.marker is None or any(
                requirement.marker.evaluate(environment=environment | {"extra": extra})
                for extra in new_extras
            )
            if applies:
                pending.append((requirement.name, frozenset(requirement.extras)))
    return tuple(sorted(versions.items()))
