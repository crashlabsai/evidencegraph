from evidencegraph.provenance import sha256_text
from evidencegraph.reconcile.substrate import Key, NoopHooks


class RegistrySubstrate(NoopHooks):
    def __init__(self, function: str = "registry_write"):
        self.function = function

    def key_of_record(self, record: dict) -> Key | None:
        attrs = record["attrs"]
        if not isinstance(attrs.get("name"), str):
            return None
        return Key(attrs.get("namespace"), attrs["name"])

    def receipt(self, action: dict) -> dict | None:
        attrs = action["attrs"]
        if (
            attrs.get("function") != self.function
            or attrs.get("error")
            or attrs.get("pending")
            or attrs.get("truncated")
        ):
            return None
        receipt = attrs.get("receipt")
        if not isinstance(receipt, dict) or receipt.get("accepted") is not True:
            return None
        args = attrs.get("arguments", {})
        if args.get("name") != receipt.get("name") or not isinstance(args.get("payload"), str):
            return None
        if args.get("namespace") and args["namespace"] != receipt.get("namespace"):
            return None
        if receipt.get("sha256") != sha256_text(args["payload"]):
            return None
        if receipt.get("payload") is not None and receipt["payload"] != args["payload"]:
            return None
        if not isinstance(receipt.get("namespace"), str) or not isinstance(
            receipt.get("event_id"), str
        ):
            return None
        return receipt

    def keys_created(self, action: dict) -> tuple[Key, ...]:
        receipt = self.receipt(action)
        return (Key(receipt["namespace"], receipt["name"]),) if receipt else ()

    def attested_sha256(self, action: dict, key: Key) -> str | None:
        receipt = self.receipt(action)
        return receipt["sha256"] if receipt else None

    def payload(self, action: dict, key: Key) -> str | None:
        return action["attrs"].get("arguments", {}).get("payload")

    def namespace(self, key: Key) -> str | None:
        return key.namespace

    def event_matches(self, record: dict, action: dict) -> bool:
        receipt = self.receipt(action)
        return bool(receipt and receipt["event_id"] == record["natural_key"])

    def binding(self, record: dict, action: dict) -> str:
        """Whether the registry's published receipt-token commitment binds this claim.

        Every other receipt field (key, digest, event id, timestamp) is public once the
        ledger is, so a receipt copied into a forged transcript event matches them all.
        A matching token establishes receipt possession. It cannot separate a genuine
        tool event from a copy of the complete receipt, including the token. Causal
        attribution additionally needs declared recorder authenticity or token
        exclusivity. Returns "bound", "mismatch", "unbound" (record committed,
        claim carries no token) or "unavailable" (record publishes no commitment).
        """
        commitment = record["attrs"].get("receipt_token_sha256")
        if not isinstance(commitment, str):
            return "unavailable"
        receipt = self.receipt(action)
        token = receipt.get("receipt_token") if receipt else None
        if not isinstance(token, str):
            return "unbound"
        return "bound" if sha256_text(token) == commitment else "mismatch"

    def earliest_creation_is_noop(self, record: dict) -> bool:
        # Registry writes mutate the version history, even when bytes repeat.
        # Crossledger's mkdir 2B rule is only valid for explicitly idempotent creation.
        return record["attrs"].get("idempotent_creation") is True
