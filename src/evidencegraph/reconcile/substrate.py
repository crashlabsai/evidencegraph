from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Key:
    namespace: str | None
    name: str


class Substrate(Protocol):
    def key_of_record(self, record: dict) -> Key | None: ...
    def keys_created(self, action: dict) -> tuple[Key, ...]: ...
    def attested_sha256(self, action: dict, key: Key) -> str | None: ...
    def payload(self, action: dict, key: Key) -> str | None: ...
    def namespace(self, key: Key) -> str | None: ...

    # Implementations may expose listings/removals. The core applies these only
    # when the adapter explicitly declares that they are complete for this key.


class NoopHooks:
    def listings(self, action: dict) -> tuple:
        return ()

    def removals(self, action: dict) -> tuple:
        return ()
