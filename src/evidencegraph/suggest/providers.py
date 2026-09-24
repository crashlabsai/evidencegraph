"""Decision providers behind one byte-level interface.

A provider receives the exact request body and returns the exact response bytes, so
every answer can be recorded, hashed, exported and replayed without the provider. The
TypeSafe client is plain HTTP rather than the SDK: no hidden retries, no extra
dependency, and nothing sent unless the caller opts in to network egress with a hard
request budget. The lexical baseline answers the same questions locally.
"""

import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from evidencegraph.provenance import sha256_bytes
from evidencegraph.store import safe_path

TYPESAFE_URL = "https://api.typesafe.ai"
TYPESAFE_MODEL = "jev-1.13.0"
BASELINE_MODEL = "baseline-lexical-1"
RETRY_STATUSES = {429, 529}
MAX_RETRY_WAIT_S = 10.0
MAX_RESPONSE_BYTES = 1 << 20


@dataclass(frozen=True)
class RawExchange:
    status: Literal["ok", "http_error", "network_error", "not_sent"]
    started_at: str
    response: bytes | None = None
    http_status: int | None = None
    provider_request_id: str | None = None
    attempts: int = 0
    latency_ms: float | None = None
    error: str | None = None


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def network(self) -> bool: ...

    @property
    def endpoint(self) -> str | None: ...

    def send(self, body: bytes) -> RawExchange: ...


def now() -> str:
    return datetime.now(UTC).isoformat()


class RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """urllib would re-send the Authorization header to wherever a redirect points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


class TypeSafeHTTP:
    """POST /v1/systemone with a hard cap on HTTP attempts, including retries."""

    name = "typesafe"
    network = True

    def __init__(
        self,
        api_key: str,
        *,
        max_requests: int,
        base_url: str = TYPESAFE_URL,
        timeout_s: float = 60,
        max_attempts: int = 2,
    ):
        if max_requests <= 0:
            raise ValueError("a positive request budget is required")
        parts = urllib.parse.urlsplit(base_url)
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in LOOPBACK):
            raise ValueError("the provider URL must use https (plain http only on loopback)")
        self.api_key = api_key
        self.secret = api_key.encode()
        self.endpoint = base_url.rstrip("/") + "/v1/systemone"
        self.opener = urllib.request.build_opener(RefuseRedirects)
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.budget = max_requests
        self.attempts = 0
        self.lock = threading.Lock()

    def reserve(self) -> bool:
        with self.lock:
            if self.attempts >= self.budget:
                return False
            self.attempts += 1
            return True

    def bounded(self, stream) -> tuple[bytes | None, str | None]:
        """Read at most MAX_RESPONSE_BYTES and never keep a copy of the key."""
        payload = stream.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            return None, f"response exceeds {MAX_RESPONSE_BYTES} bytes"
        if self.secret and self.secret in payload:
            return payload.replace(self.secret, b"[redacted]"), "response echoed the API key"
        return payload, None

    def send(self, body: bytes) -> RawExchange:
        started = now()
        attempts = 0
        while True:
            if not self.reserve():
                return RawExchange(
                    "not_sent", started, attempts=attempts, error="request budget exhausted"
                )
            attempts += 1
            request = urllib.request.Request(
                self.endpoint,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            began = time.monotonic()
            try:
                with self.opener.open(request, timeout=self.timeout_s) as response:
                    payload, problem = self.bounded(response)
                    return RawExchange(
                        "ok" if payload is not None else "http_error",
                        started,
                        response=payload,
                        http_status=response.status,
                        provider_request_id=response.headers.get("x-typesafe-request-id"),
                        attempts=attempts,
                        latency_ms=(time.monotonic() - began) * 1000,
                        error=problem,
                    )
            except urllib.error.HTTPError as exc:
                payload, problem = self.bounded(exc)
                retry = exc.code in RETRY_STATUSES and attempts < self.max_attempts
                if retry:
                    time.sleep(retry_delay(exc.headers.get("retry-after"), attempts))
                    continue
                excerpt = (payload or b"")[:300].decode("utf-8", "replace")
                return RawExchange(
                    "http_error",
                    started,
                    response=payload,
                    http_status=exc.code,
                    provider_request_id=exc.headers.get("x-typesafe-request-id"),
                    attempts=attempts,
                    latency_ms=(time.monotonic() - began) * 1000,
                    error=problem or f"HTTP {exc.code}: {excerpt}",
                )
            except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                if attempts < self.max_attempts:
                    time.sleep(retry_delay(None, attempts))
                    continue
                return RawExchange(
                    "network_error", started, attempts=attempts, error=type(exc).__name__
                )


def retry_delay(header: str | None, attempt: int) -> float:
    try:
        wait = float(header) if header else 2.0**attempt
    except ValueError:
        wait = 2.0**attempt
    return min(max(wait, 0.0), MAX_RETRY_WAIT_S)


class Replay:
    """Answer from a recorded run's response bytes; never contacts a provider.

    Works for unpublished and interrupted runs too: only the exchange index and the
    content-addressed blobs are needed.
    """

    name = "replay"
    network = False
    endpoint = None

    def __init__(self, case: Path, run_id: str):
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError("replay needs a 32-character run id")
        index = safe_path(case, f"suggest/runs/{run_id}/exchanges.jsonl")
        if not index.is_file():
            raise ValueError(f"no recorded exchanges for run {run_id}")
        self.case = case
        self.responses: dict[str, str] = {}
        models = set()
        for line in index.read_text().splitlines():
            record = json.loads(line)
            request = json.loads(self.blob(record["request_sha256"]))
            models.add(request["model"])
            if record["status"] == "ok" and record["response_sha256"]:
                self.responses.setdefault(record["request_sha256"], record["response_sha256"])
        if len(models) != 1:
            raise ValueError(f"run {run_id} has no single requested model to replay")
        self.requested_model = models.pop()

    def blob(self, digest: str) -> bytes:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid recorded digest")
        path = safe_path(self.case, f"suggest/blobs/{digest}")
        if not path.is_file():
            raise ValueError(f"recorded blob {digest} is missing")
        payload = path.read_bytes()
        if sha256_bytes(payload) != digest:
            raise ValueError(f"recorded blob {digest} failed its hash check")
        return payload

    def send(self, body: bytes) -> RawExchange:
        started = now()
        digest = self.responses.get(sha256_bytes(body))
        if digest is None:
            return RawExchange("not_sent", started, error="no recorded response for this request")
        return RawExchange("ok", started, response=self.blob(digest), http_status=200)


# Keyword rules for the local baseline. They classify surface wording only and are the
# comparison point a model must beat, not a claim about what any agent did.
WRITE_WORDS = re.compile(
    r"(?i)\b(writ\w*|wrote|sav\w*|upload\w*|stor\w*|cop(?:y|ied)|mirror\w*|registry|receipt)\b"
)
QUOTE = re.compile(
    r"(?i)(\btold me\b|\baccording to\b|\breported that\b|\bcopied from\b|\bper \w+'s\b"
    r"|\b\w+ says it\b|\bbanner said\b)"
)
DENY = re.compile(r"(?i)\b(did not|didn't|never|haven't|have not|nothing)\b")
FAIL = re.compile(r"(?i)\b(fail\w*|reject\w*|denied|error|refused|forbidden)\b")
PLAN = re.compile(
    r"(?i)\b(will|going to|about to|let me|i'll|plan\w*|next|please|prepare|mirror)\b"
)
DONE = re.compile(
    r"(?i)\b(wrote|written|saved|uploaded|stored|created|accepted|succeeded|copied|is in)\b"
)
TRUNCATED = re.compile(r"(?i)^\s*(\.\.\.|…|\[truncated)")
STOPWORDS = frozenset(
    "a an the of to in on for and or is was were be did does do which who what when "
    "that this it its any anyone someone by with from at as".split()
)


def terms(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9._-]*", text.lower()) if t not in STOPWORDS}


def baseline_write_claim(message: dict) -> str:
    text = message.get("text", "")
    if message.get("role") == "tool":
        if message.get("tool_function") != "registry_write":
            return "no_write_claim"
        try:
            receipt = json.loads(text)
        except ValueError:
            receipt = None
        if message.get("tool_error") or FAIL.search(text):
            return "reports_failed_write"
        if isinstance(receipt, dict) and (receipt.get("accepted") or receipt.get("event_id")):
            return "claims_completed_write"
        return "insufficient_context"
    calls = [c for c in message.get("tool_calls", []) if c.get("function") == "registry_write"]
    if TRUNCATED.search(text):
        return "insufficient_context"
    if not WRITE_WORDS.search(text) and not calls:
        return "no_write_claim"
    for pattern, label in (
        (QUOTE, "quotes_other_claim"),
        (FAIL, "reports_failed_write"),
        (DENY, "denies_write"),
        (PLAN, "plans_write"),
        (DONE, "claims_completed_write"),
    ):
        if pattern.search(text):
            return label
    return "plans_write" if calls else "no_write_claim"


def baseline_level(question: dict, message: dict) -> int:
    text = message.get("text", "") + " " + json.dumps(message.get("tool_calls", []))
    levels = len(question["criteria"])
    query = question["instructions"].get("query")
    if query is None:
        if not WRITE_WORDS.search(text):
            return 0
        return levels - 1 if re.search(r"\b[\w-]+\.[a-z]{1,4}\b", text) else 1
    wanted = terms(query)
    if not wanted:
        return 0
    overlap = len(wanted & terms(text)) / len(wanted)
    return min(levels - 1, round(overlap * (levels - 1) + 0.49)) if overlap else 0


class LexicalBaseline:
    """Answers every supported question locally with one-hot keyword rules."""

    name = "baseline"
    network = False
    endpoint = None

    def send(self, body: bytes) -> RawExchange:
        started = now()
        request = json.loads(body)
        message = request["state"]["message"]
        answers = {}
        for key, question in request["questions"].items():
            if question["type"] == "choice" and key == "write_claim":
                chosen = baseline_write_claim(message)
                options = list(question["criteria"])
                answers[key] = {
                    "type": "choice",
                    "choice": chosen,
                    "probabilities": {o: float(o == chosen) for o in options},
                    "confidence": 1.0,
                }
            elif question["type"] == "score":
                level = baseline_level(question, message)
                criteria = question["criteria"]
                answers[key] = {
                    "type": "score",
                    "score": float(level),
                    "legend": {str(i): c for i, c in enumerate(criteria)},
                    "probabilities": {str(i): float(i == level) for i in range(len(criteria))},
                    "confidence": 1.0,
                }
            else:
                return RawExchange(
                    "http_error", started, http_status=422, error=f"baseline cannot answer {key}"
                )
        payload = json.dumps(
            {"model": BASELINE_MODEL, "answers": answers, "usage": {"input_tokens": 0}},
            separators=(",", ":"),
        ).encode()
        return RawExchange("ok", started, response=payload, http_status=200, attempts=1)


def make_provider(
    case: Path,
    name: str,
    *,
    model: str | None,
    allow_network: bool,
    max_requests: int,
    base_url: str | None,
    from_run: str | None,
) -> tuple[Provider, str]:
    """Build a provider and the model name its requests will carry."""
    if name == "baseline":
        return LexicalBaseline(), BASELINE_MODEL
    if name == "replay":
        if not from_run:
            raise ValueError("--provider replay needs --from-run RUN_ID")
        replay = Replay(case, from_run)
        return replay, model or replay.requested_model
    if name == "typesafe":
        if not allow_network:
            raise ValueError(
                "TypeSafe receives redacted transcript excerpts over the network. Pass "
                "--allow-network once the case's data-handling terms permit that egress."
            )
        if max_requests <= 0:
            raise ValueError("--max-requests must bound the paid HTTP attempts, retries included")
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            raise ValueError("set TYPESAFE_API_KEY in the environment")
        url = base_url or os.environ.get("TYPESAFE_BASE_URL") or TYPESAFE_URL
        return TypeSafeHTTP(key, max_requests=max_requests, base_url=url), model or TYPESAFE_MODEL
    raise ValueError("provider must be baseline, typesafe or replay")
