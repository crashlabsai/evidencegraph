"""Compatibility imports for the standard-library-only collector."""

from evidencegraph.lab.runtime.collection import (
    MAX_NAME_BYTES,
    MAX_PAYLOAD_BYTES,
    CollectionClosedError,
    CollectionPersistenceError,
    Collector,
    Mutation,
    MutationKind,
    UnauthenticatedConnectionError,
    Writer,
    sha256_text,
)

__all__ = [
    "MAX_NAME_BYTES",
    "MAX_PAYLOAD_BYTES",
    "CollectionClosedError",
    "CollectionPersistenceError",
    "Collector",
    "Mutation",
    "MutationKind",
    "UnauthenticatedConnectionError",
    "Writer",
    "sha256_text",
]
