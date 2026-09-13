"""Trustworthy host collection: the grader's source of truth for the replica.

Directory polling alone cannot establish who wrote each board entry or capture every
modification. This collector instead mediates every mutation and records, in one
total order, who produced it, when, and what changed. It captures the three cases a
poller misses:

- concurrent writes (many producers mutating at once),
- overwrites (the same path rewritten, old and new content both recorded), and
- background-process writes (a producer's detached work landing after its turn ends).

Attribution is by authenticated connection identity, following ``pilot.board``: the
producer behind a connection is fixed when the connection is created, and neither a
claimed handle nor any argument the producer controls can select a different writer.

Host truth lives here and is the grader only. It must never be handed to a method or
an investigator. The observable board view (:meth:`Collector.visible`) is the only
thing downstream generation may expose.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

if __package__:
    from .runtime_io import append_json
else:
    from runtime_io import append_json  # pyright: ignore[reportMissingImports]


def sha256_text(value: str) -> str:
    """Keep the collector usable in the standard-library-only lab runtime."""
    return sha256(value.encode("utf-8")).hexdigest()


MAX_NAME_BYTES = 256
MAX_PAYLOAD_BYTES = 16_384

MutationKind = Literal["create", "overwrite", "remove"]


class UnauthenticatedConnectionError(ValueError):
    """A write arrived on a connection the collector never issued."""


class CollectionClosedError(RuntimeError):
    """A write arrived after the collector was stopped or failed closed."""


class CollectionPersistenceError(RuntimeError):
    """Durable logging failed, so the collector refused to publish the mutation and
    closed itself: continuing would let observable state diverge from durable truth."""


@dataclass(frozen=True)
class Writer:
    """The real producer behind a connection. `slot` mirrors pilot's collector slot."""

    agent_id: str
    handle: str
    slot: int


@dataclass(frozen=True)
class Mutation:
    """One recorded board mutation, in host-truth order.

    `seq` is the collector's global monotonic order and is the tie-breaker under
    concurrency. `writer_*` is the authenticated producer, never a claimed handle.
    `payload_sha256` and `prior_sha256` fix what the mutation changed; for a create
    `prior_sha256` is None, for a remove `payload_sha256` is None. `background` is
    True when the producer's turn had already ended, modelling a detached write.
    """

    seq: int
    kind: MutationKind
    namespace: str
    name: str
    path: str
    payload: str | None
    payload_sha256: str | None
    prior_sha256: str | None
    writer_agent_id: str
    writer_handle: str
    writer_slot: int
    wall_ts: str
    mono_ns: int
    background: bool


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _board_path(namespace: str, name: str) -> str:
    return f"/cache/{namespace}/{name}"


class Collector:
    """A host-owned, thread-safe board that records complete, attributed host truth.

    Producers mutate the board only through connections the host issued. Every
    accepted mutation is appended to an in-memory list and to a durable log under one
    lock, so no concurrent mutation is lost and the `seq` order is total. Rejected
    writes are logged too, but never enter :meth:`host_truth`.
    """

    def __init__(self, truth_path: Path, writers: tuple[Writer, ...]) -> None:
        if len({w.slot for w in writers}) != len(writers):
            raise ValueError("writer slots must be unique")
        if len({w.agent_id for w in writers}) != len(writers):
            raise ValueError("writer agent ids must be unique")
        self._truth_path = truth_path
        self._connections: dict[object, Writer] = {object(): w for w in writers}
        self._lock = threading.Lock()
        self._seq = 0
        self._state: dict[tuple[str, str], str | None] = {}
        self._mutations: list[Mutation] = []
        self._stopped = False
        self._failed = False
        truth_path.touch(exist_ok=False)
        append_json(truth_path, {"kind": "start", "ts": _utc_now_iso()})

    def connection(self, slot: int) -> object:
        """Return the opaque connection token for one writer slot (host/harness only)."""
        return next(key for key, w in self._connections.items() if w.slot == slot)

    def writer_of(self, connection: object) -> Writer:
        """Resolve a connection to its authenticated producer, or raise."""
        writer = self._connections.get(connection)
        if writer is None:
            raise UnauthenticatedConnectionError("no such connection")
        return writer

    def write(
        self,
        connection: object,
        namespace: str,
        name: str,
        payload: str | None,
        *,
        background: bool = False,
    ) -> Mutation:
        """Create or overwrite one board entry, recording attributed host truth.

        The writer is taken from `connection`, never from `name`, `payload`, or any
        other argument. A first write to a path is a `create`; any later write is an
        `overwrite`, whose `prior_sha256` chains to whatever content was there, even
        if the new content is identical, so no modification is silently dropped.
        """
        return self._record(
            connection, namespace, name, payload, remove=False, background=background
        )

    def remove(
        self, connection: object, namespace: str, name: str, *, background: bool = False
    ) -> Mutation:
        """Remove one board entry, recording the content that was there before."""
        return self._record(connection, namespace, name, None, remove=True, background=background)

    def _record(
        self,
        connection: object,
        namespace: str,
        name: str,
        payload: str | None,
        *,
        remove: bool,
        background: bool,
    ) -> Mutation:
        writer = self._connections.get(connection)
        request = {"namespace": namespace, "name": name, "remove": remove}
        if writer is None:
            append_json(
                self._truth_path,
                {
                    "kind": "rejected",
                    "ts": _utc_now_iso(),
                    "request": request,
                    "reason": "unauthenticated",
                },
            )
            raise UnauthenticatedConnectionError("no such connection")
        reason = self._validate(namespace, name, payload, remove=remove)
        if reason is not None:
            append_json(
                self._truth_path,
                {
                    "kind": "rejected",
                    "ts": _utc_now_iso(),
                    "request": request,
                    "reason": reason,
                    "writer": writer.agent_id,
                    "slot": writer.slot,
                },
            )
            raise ValueError(reason)

        with self._lock:
            if self._stopped or self._failed:
                append_json(
                    self._truth_path,
                    {
                        "kind": "rejected",
                        "ts": _utc_now_iso(),
                        "request": request,
                        "reason": "closed",
                    },
                )
                raise CollectionClosedError("collection is stopped or failed closed")
            key = (namespace, name)
            present = key in self._state
            prior = self._state.get(key)
            prior_sha = sha256_text(prior) if prior is not None else None
            if remove:
                if not present:
                    reason = "remove of an absent entry"
                    append_json(
                        self._truth_path,
                        {
                            "kind": "rejected",
                            "ts": _utc_now_iso(),
                            "request": request,
                            "reason": reason,
                        },
                    )
                    raise ValueError(reason)
                kind: MutationKind = "remove"
                new_payload: str | None = None
                new_sha: str | None = None
            else:
                kind = "overwrite" if present else "create"
                new_payload = payload
                new_sha = sha256_text(payload) if payload is not None else None
            # Persist first. Nothing below touches observable state until the durable
            # record is on disk, so a failed write can never publish an unlogged mutation.
            mutation = Mutation(
                seq=self._seq + 1,
                kind=kind,
                namespace=namespace,
                name=name,
                path=_board_path(namespace, name),
                payload=new_payload,
                payload_sha256=new_sha,
                prior_sha256=prior_sha,
                writer_agent_id=writer.agent_id,
                writer_handle=writer.handle,
                writer_slot=writer.slot,
                wall_ts=_utc_now_iso(),
                mono_ns=time.monotonic_ns(),
                background=background,
            )
            try:
                append_json(self._truth_path, _mutation_record(mutation))
            except Exception as exc:
                # Persistence is now uncertain (a partial line may exist). Fail closed:
                # refuse every later write rather than continue with divergent state.
                self._failed = True
                raise CollectionPersistenceError(
                    "durable logging failed; collector closed to keep truth consistent"
                ) from exc
            # Durable record committed; now publish the same mutation in memory.
            self._seq += 1
            if remove:
                del self._state[key]
            else:
                self._state[key] = payload
            self._mutations.append(mutation)
            return mutation

    def _validate(
        self, namespace: str, name: str, payload: str | None, *, remove: bool
    ) -> str | None:
        if not namespace or "/" in namespace:
            return "namespace must be a single path segment"
        if not name or name in (".", "..") or "/" in name:
            return "name must be a single path segment"
        if len(name.encode()) > MAX_NAME_BYTES:
            return "name exceeds the message size limit"
        if not remove and payload is not None and len(payload.encode()) > MAX_PAYLOAD_BYTES:
            return "payload exceeds the message size limit"
        return None

    def visible(self) -> list[dict[str, str | None]]:
        """The current board state a producer or a collector-dump would observe."""
        with self._lock:
            return [
                {"namespace": ns, "name": nm, "path": _board_path(ns, nm), "payload": payload}
                for (ns, nm), payload in sorted(self._state.items())
            ]

    def host_truth(self) -> tuple[Mutation, ...]:
        """Every recorded mutation in total order. Grader only; never given to a method."""
        with self._lock:
            return tuple(self._mutations)

    def stop(self) -> None:
        """Close the collector. Later writes are rejected, not silently dropped."""
        with self._lock:
            if not self._stopped:
                self._stopped = True
                append_json(self._truth_path, {"kind": "stop", "ts": _utc_now_iso()})


def _mutation_record(mutation: Mutation) -> dict[str, object]:
    return {
        "kind": "mutation",
        "seq": mutation.seq,
        "op": mutation.kind,
        "namespace": mutation.namespace,
        "name": mutation.name,
        "path": mutation.path,
        "payload_sha256": mutation.payload_sha256,
        "prior_sha256": mutation.prior_sha256,
        "writer": mutation.writer_agent_id,
        "handle": mutation.writer_handle,
        "slot": mutation.writer_slot,
        "wall_ts": mutation.wall_ts,
        "mono_ns": mutation.mono_ns,
        "background": mutation.background,
    }
