"""Bounded, locally redacted source packets built from verified snapshot bytes.

A packet comes from the native message in the hashed snapshot, not from a clipped
citation preview, and its citation must already be published in the current graph
with the same content hash. Redaction runs before anything leaves the machine and is
deterministic; it can miss secrets, so egress still needs a data-handling decision.
"""

import json
import re
from pathlib import Path
from typing import Any

from inspect_ai.model import ChatMessage, ChatMessageAssistant, ChatMessageTool

from evidencegraph.adapters.inspect_eval import read_transcripts, ref_for_message
from evidencegraph.provenance import verify_file_identity
from evidencegraph.schema import Witness
from evidencegraph.store import Store, safe_path
from evidencegraph.suggest.records import SpanPacket

PACKET_VERSION = "1"
REDACTION_VERSION = "1"
DEFAULT_ROLES = ("user", "assistant", "tool")
MAX_ARGUMENT_CHARS = 1000
REDACTED = "[redacted]"

SECRET_KEY = re.compile(
    r"(?i)(token|secret|passw(?:or)?d|api[_-]?key|authorization|credential|cookie|private[_-]?key)"
)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b((?:receipt_)?token|secret|passw(?:or)?d|api[_-]?key)"
    r"([\"']?\s*[:=]\s*[\"']?)([^\s\"',}\]]{6,})"
)
BEARER = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{8,})")
KEY_SHAPES = re.compile(r"\b(?:apikey_[A-Za-z0-9_]{16,}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_\w{20,})")


def redact_value(value: Any) -> tuple[Any, int]:
    """Replace values under secret-looking keys, recursively."""
    if isinstance(value, dict):
        count, result = 0, {}
        for key, item in value.items():
            if isinstance(key, str) and SECRET_KEY.search(key) and item not in (None, ""):
                result[key] = REDACTED
                count += 1
            else:
                result[key], found = redact_value(item)
                count += found
        return result, count
    if isinstance(value, list):
        items = [redact_value(item) for item in value]
        return [item for item, _ in items], sum(found for _, found in items)
    if isinstance(value, str):
        return redact_text(value)
    return value, 0


def redact_text(text: str) -> tuple[str, int]:
    """Structured JSON text is redacted by key; free text by assignment and key shapes."""
    count = 0
    stripped = text.strip()
    if stripped[:1] in {"{", "["}:
        try:
            parsed = json.loads(stripped)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            try:
                redacted, count = redact_value(parsed)
                text = json.dumps(redacted, ensure_ascii=False)
            except RecursionError:
                count = 0
    text, found = SECRET_ASSIGNMENT.subn(lambda m: m[1] + m[2] + REDACTED, text)
    count += found
    text, found = BEARER.subn(lambda m: m[1] + REDACTED, text)
    count += found
    text, found = KEY_SHAPES.subn(REDACTED, text)
    return text, count + found


def clip(text: str, limit: int) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit], True)


def message_state(message: ChatMessage, max_chars: int) -> tuple[dict, int, bool, int] | None:
    """The state sent for one message, or None when it carries nothing to classify."""
    original = message.text
    text, redactions = redact_text(original)
    text, truncated = clip(text, max_chars)
    body: dict[str, Any] = {"role": message.role, "text": text}
    if isinstance(message, ChatMessageTool):
        body["tool_function"] = message.function
        if message.error:
            error, found = redact_text(str(message.error.message))
            redactions += found
            body["tool_error"] = clip(error, MAX_ARGUMENT_CHARS)[0]
    if isinstance(message, ChatMessageAssistant) and message.tool_calls:
        calls = []
        for call in message.tool_calls:
            arguments, found = redact_value(call.arguments)
            redactions += found
            rendered, clipped = clip(json.dumps(arguments, ensure_ascii=False), MAX_ARGUMENT_CHARS)
            truncated = truncated or clipped
            calls.append({"function": call.function, "arguments": rendered})
        body["tool_calls"] = calls
    if not text.strip() and not body.get("tool_calls") and not body.get("tool_error"):
        return None
    if truncated:
        body["truncated"] = True
    return {"message": body}, len(original), truncated, redactions


def build_packets(
    case: Path,
    manifest: dict,
    *,
    roles: tuple[str, ...] = DEFAULT_ROLES,
    max_chars: int = 4000,
) -> tuple[list[SpanPacket], dict]:
    """Enumerate every in-scope message of every ingested Inspect witness, in order."""
    if max_chars < 200:
        raise ValueError("max_chars must be at least 200")
    witnesses = sorted(
        (w for w in manifest["witnesses"].values() if w["adapter"] == "inspect-eval"),
        key=lambda w: w["witness_id"],
    )
    if not witnesses:
        raise ValueError("no Inspect transcripts ingested")
    with Store(case, manifest) as store:
        published = {
            row["citation_id"]: row["content_sha256"]
            for row in store.query(
                "SELECT citation_id, content_sha256 FROM citations WHERE locator_kind='message'"
            )
        }
    packets: list[SpanPacket] = []
    scope = {"transcripts": 0, "messages": 0, "in_scope": 0, "empty": 0}
    for raw in witnesses:
        witness = Witness.model_validate(raw)
        path = safe_path(case, witness.snapshot_path)
        verify_file_identity(
            path,
            expected_size=witness.size_bytes,
            expected_sha256=witness.sha256,
            subject=witness.witness_id,
        )
        for transcript in read_transcripts([path]):
            scope["transcripts"] += 1
            for position, message in enumerate(transcript.messages):
                scope["messages"] += 1
                if message.role not in roles:
                    continue
                ref = ref_for_message(witness, transcript, message)
                if published.get(ref.citation_id) != ref.content_sha256:
                    raise ValueError(
                        f"message {message.id} is not a published citation of the current "
                        "graph; re-run ingest"
                    )
                prepared = message_state(message, max_chars)
                if prepared is None:
                    scope["empty"] += 1
                    continue
                state, original_chars, truncated, redactions = prepared
                scope["in_scope"] += 1
                packets.append(
                    SpanPacket(
                        span_id=ref.citation_id,
                        witness_id=witness.witness_id,
                        transcript_id=transcript.transcript_id,
                        message_id=str(message.id),
                        position=position,
                        role=message.role,
                        source_sha256=ref.content_sha256,
                        state=state,
                        original_chars=original_chars,
                        truncated=truncated,
                        redactions=redactions,
                    )
                )
    return packets, {**scope, "roles": list(roles), "max_chars": max_chars}
