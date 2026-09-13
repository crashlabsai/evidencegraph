"""Registry operations used by scripted scenario recorders."""

from pathlib import Path
from typing import Protocol

from evidencegraph.lab.registry import Registry


class RegistryBackend(Protocol):
    """Execute registry requests with a private, harness-supplied dispatch context."""

    def invoke(self, request: dict, context: dict) -> dict:
        """Perform a direct registry operation and return its receipt."""
        ...

    def start_job(self, request: dict, context: dict) -> dict:
        """Launch the selected script version and return its job receipt."""
        ...

    def refresh(self, name: str = "release.txt") -> dict | None:
        """Refresh the named cache entry."""
        ...

    def drain(self, timeout_seconds: float) -> dict | None:
        """Wait for outstanding jobs, stopping them when the timeout expires."""
        ...

    def close(self) -> None:
        """Stop collection after outstanding work has been drained."""
        ...


class InProcessBackend:
    """Keep the synthetic stage's registry behavior without starting host processes."""

    def __init__(self, root: Path, *, fixture: str | None = None) -> None:
        self.registry = Registry(root, fixture=fixture)

    def invoke(self, request: dict, context: dict) -> dict:
        return self.registry.operate(request, context)

    def start_job(self, request: dict, context: dict) -> dict:
        raise NotImplementedError("the in-process backend does not execute jobs")

    def refresh(self, name: str = "release.txt") -> None:
        self.registry.refresh_cache(name)

    def drain(self, timeout_seconds: float) -> None:
        # This backend has no processes or outstanding work.
        return None

    def close(self) -> None:
        self.registry.collector.stop()
        self.registry.closed = True
