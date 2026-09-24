"""Typed suggestion records. They are stored beside the graph, never inside it."""

from typing import Any, Literal

from pydantic import Field

from evidencegraph.schema import Frozen

Disposition = Literal["review", "uncertain", "background", "unscored"]

INTERPRETATION = (
    "Model suggestions about what cited transcript text says, for review ordering only. "
    "They are not evidence, do not change any relation, coverage population or docket "
    "answer, and a confident answer is not a verified one."
)


class SpanPacket(Frozen):
    """One cited message prepared for a provider: redacted locally and bounded."""

    span_id: str
    witness_id: str
    transcript_id: str
    message_id: str
    position: int = Field(ge=0)
    role: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: dict[str, Any]
    original_chars: int = Field(ge=0)
    truncated: bool
    redactions: int = Field(ge=0)


class ExchangeRecord(Frozen):
    """What was sent and what came back, by content hash. Credentials are never recorded."""

    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal["ok", "http_error", "network_error", "not_sent"]
    http_status: int | None = None
    provider_request_id: str | None = None
    attempts: int = Field(default=0, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    started_at: str
    error: str | None = None


class Answer(Frozen):
    """A validated answer, or the reason no usable answer exists. Never repaired."""

    question_key: str
    status: Literal["answered", "invalid", "unavailable"]
    label: str | None = None
    probabilities: dict[str, float] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    value: float | None = None
    detail: str = ""


class Suggestion(Frozen):
    suggestion_id: str
    run_id: str
    task: str
    span_id: str
    witness_id: str
    transcript_id: str
    message_id: str
    position: int = Field(ge=0)
    role: str
    source_sha256: str
    request_sha256: str
    truncated: bool
    redactions: int = Field(ge=0)
    resolved_model: str | None = None
    answers: dict[str, Answer]
    disposition: Disposition
    reason: str


class SuggestionRun(Frozen):
    run_id: str
    run_key: str
    task: str
    query: str | None = None
    provider: str
    endpoint: str | None = None
    requested_model: str
    resolved_models: tuple[str, ...] = ()
    questions: dict[str, str]
    docket_question: str
    packet_version: str
    redaction_version: str
    policy_version: str
    input_fingerprint: str
    analyzer_build_id: str
    suggest_build_id: str
    started_at: str
    completed_at: str
    scope: dict[str, Any]
    counts: dict[str, int]
    usage: dict[str, int]
    interpretation: str = INTERPRETATION
