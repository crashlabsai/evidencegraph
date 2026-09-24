"""Prepare under the case lock, infer without it, publish only onto unchanged evidence.

Preparation freezes the ingested evidence into packets and records a fingerprint of
the ingest generation. Inference runs outside the lock, recording every exchange as it
completes so a failed or superseded run can still be replayed. Publication re-takes the
lock and refuses if the evidence changed, rather than attaching answers to a graph they
were not computed from. Suggestions live under `manifest["suggestions"]`, which no
forensic stage reads, and publishing them invalidates nothing.
"""

import json
import os
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from evidencegraph.derive import configuration_sha256, require_current_configuration
from evidencegraph.manifest import (
    CaseMutationLockError,
    atomic_json,
    case_mutation_lock,
    read_manifest,
)
from evidencegraph.provenance import (
    analyzer_build_id,
    sha256_bytes,
    sha256_file,
    sha256_text,
    source_tree_sha256,
)
from evidencegraph.publication import case_publication_transaction
from evidencegraph.store import safe_path
from evidencegraph.strict_json import load_strict_json
from evidencegraph.suggest.answers import validate_exchange
from evidencegraph.suggest.packets import (
    DEFAULT_ROLES,
    PACKET_VERSION,
    REDACTION_VERSION,
    build_packets,
)
from evidencegraph.suggest.policy import DISPOSITION_ORDER, POLICY_VERSION, disposition
from evidencegraph.suggest.providers import Provider, RawExchange, now
from evidencegraph.suggest.questions import Question, task_questions
from evidencegraph.suggest.records import (
    ExchangeRecord,
    SpanPacket,
    Suggestion,
    SuggestionRun,
)

# Exact request bytes, the recorded exchange, and the exact response bytes if any.
Result = tuple[bytes, ExchangeRecord, bytes | None]


PUBLISH_WAIT_S = 30.0


class StaleSuggestionRun(ValueError):
    pass


def suggest_build_id() -> str:
    return "suggest+" + source_tree_sha256(Path(__file__).resolve().parent)[:20]


def input_fingerprint(case: Path, manifest: dict) -> str:
    """Identity of the ingested evidence a run read; reconciliation does not change it."""
    ingest = {k: v for k, v in manifest["partitions"].items() if k.startswith("ingest.")}
    return sha256_text(
        json.dumps(
            {
                "case_config_sha256": configuration_sha256(case),
                "witnesses": manifest["witnesses"],
                "ingest": ingest,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def run_key(task: str, provider: str, query: str | None) -> str:
    key = f"{task}:{provider}"
    return key + ":" + sha256_text(query)[:12] if query else key


def request_body(packet: SpanPacket, questions: tuple[Question, ...], model: str) -> bytes:
    return json.dumps(
        {"state": packet.state, "model": model, "questions": {q.key: q.wire() for q in questions}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def write_blob(case: Path, data: bytes) -> str:
    """Content-addressed, write-once storage for exact request and response bytes."""
    digest = sha256_bytes(data)
    path = safe_path(case, f"suggest/blobs/{digest}")
    if path.exists():
        if sha256_file(path) != digest:
            raise ValueError(f"stored blob {digest} is corrupt")
        return digest
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".blob-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    return digest


@dataclass(frozen=True)
class Prepared:
    task: str
    key: str
    query: str | None
    questions: tuple[Question, ...]
    model: str
    fingerprint: str
    packets: list[SpanPacket]
    scope: dict


def prepare(
    case: Path,
    *,
    task: str,
    provider: str,
    model: str,
    query: str | None = None,
    roles: tuple[str, ...] = DEFAULT_ROLES,
    max_chars: int = 4000,
) -> Prepared:
    questions = task_questions(task, query)
    with case_mutation_lock(case):
        manifest = read_manifest(case)
        if not manifest["partitions"]:
            raise ValueError("ingest witnesses first")
        require_current_configuration(case, manifest)
        fingerprint = input_fingerprint(case, manifest)
        packets, scope = build_packets(case, manifest, roles=roles, max_chars=max_chars)
    if not packets:
        raise ValueError(f"no messages in scope for roles {', '.join(roles)}")
    return Prepared(
        task, run_key(task, provider, query), query, questions, model, fingerprint, packets, scope
    )


def execute(
    case: Path,
    prepared: Prepared,
    provider: Provider,
    run_dir: Path,
    *,
    concurrency: int = 4,
    progress: Callable[[int, int], None] | None = None,
) -> list[Result]:
    """Send each distinct request once and record every exchange as it completes.

    Identical spans produce identical request bytes and share one answer: providers are
    not bitwise deterministic, so asking twice would only manufacture disagreement.
    """
    lock = threading.Lock()
    done = [0]
    index = run_dir / "exchanges.jsonl"
    bodies = [request_body(p, prepared.questions, prepared.model) for p in prepared.packets]
    distinct = list(dict.fromkeys(bodies))

    def one(body: bytes) -> Result:
        write_blob(case, body)
        try:
            raw: RawExchange = provider.send(body)
        except Exception as exc:  # a provider failure is a recorded outcome, not a crash
            raw = RawExchange("network_error", now(), error=f"provider raised {type(exc).__name__}")
        record = ExchangeRecord(
            request_sha256=sha256_bytes(body),
            response_sha256=write_blob(case, raw.response) if raw.response is not None else None,
            status=raw.status,
            http_status=raw.http_status,
            provider_request_id=raw.provider_request_id,
            attempts=raw.attempts,
            latency_ms=raw.latency_ms,
            started_at=raw.started_at,
            error=raw.error,
        )
        with lock:
            with index.open("a", encoding="utf-8") as stream:
                stream.write(record.model_dump_json() + "\n")
            done[0] += 1
            if progress:
                progress(done[0], len(distinct))
        return body, record, raw.response

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        answered = dict(zip(distinct, pool.map(one, distinct), strict=True))
    return [answered[body] for body in bodies]


def interpret(
    prepared: Prepared,
    run_id: str,
    results: list[Result],
) -> list[Suggestion]:
    rows = []
    for packet, (body, record, response) in zip(prepared.packets, results, strict=True):
        resolved, answers = validate_exchange(
            body, record, response, requested_model=prepared.model
        )
        outcome, reason = disposition(prepared.task, answers)
        rows.append(
            Suggestion(
                suggestion_id="s-" + sha256_text(f"{run_id}\0{packet.span_id}")[:24],
                run_id=run_id,
                task=prepared.task,
                span_id=packet.span_id,
                witness_id=packet.witness_id,
                transcript_id=packet.transcript_id,
                message_id=packet.message_id,
                position=packet.position,
                role=packet.role,
                source_sha256=packet.source_sha256,
                request_sha256=record.request_sha256,
                truncated=packet.truncated,
                redactions=packet.redactions,
                resolved_model=resolved,
                answers=answers,
                disposition=outcome,
                reason=reason,
            )
        )
    return rows


def run_suggestions(
    case: Path,
    *,
    task: str,
    provider: Provider,
    model: str,
    query: str | None = None,
    roles: tuple[str, ...] = DEFAULT_ROLES,
    max_chars: int = 4000,
    concurrency: int = 4,
    max_requests: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    prepared = prepare(
        case,
        task=task,
        provider=provider.name,
        model=model,
        query=query,
        roles=roles,
        max_chars=max_chars,
    )
    distinct = len({request_body(p, prepared.questions, model) for p in prepared.packets})
    if provider.network and max_requests is not None and distinct > max_requests:
        raise ValueError(
            f"{distinct} distinct requests are in scope but --max-requests is {max_requests}; "
            "raise the budget or narrow --role"
        )
    run_id = uuid4().hex
    run_dir = safe_path(case, f"suggest/runs/{run_id}")
    run_dir.mkdir(parents=True)
    started = now()
    results = execute(case, prepared, provider, run_dir, concurrency=concurrency, progress=progress)
    rows = interpret(prepared, run_id, results)
    records = list({record.request_sha256: record for _, record, _ in results}.values())
    run = SuggestionRun(
        run_id=run_id,
        run_key=prepared.key,
        task=task,
        query=query,
        provider=provider.name,
        endpoint=provider.endpoint,
        requested_model=model,
        resolved_models=tuple(sorted({r.resolved_model for r in rows if r.resolved_model})),
        questions={q.key: q.sha256() for q in prepared.questions},
        docket_question=prepared.questions[0].docket_question,
        packet_version=PACKET_VERSION,
        redaction_version=REDACTION_VERSION,
        policy_version=POLICY_VERSION,
        input_fingerprint=prepared.fingerprint,
        analyzer_build_id=analyzer_build_id(),
        suggest_build_id=suggest_build_id(),
        started_at=started,
        completed_at=now(),
        scope=prepared.scope,
        counts=dict(
            Counter(r.disposition for r in rows)
            + Counter(f"{a.status}:{a.question_key}" for r in rows for a in r.answers.values())
        ),
        usage=usage(results, records),
    )
    with (run_dir / "suggestions.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(row.model_dump_json() + "\n")
    atomic_json(run_dir / "run.json", run.model_dump(mode="json"))
    primary = "write_claim" if task == "write-claims" else "search_relevance"
    if not any(row.answers[primary].status == "answered" for row in rows):
        raise ValueError(
            f"run {run_id} produced no answers ({rows[0].reason}); it was not published, so "
            f"any earlier {prepared.key} run is kept. Its exchanges are in suggest/runs/{run_id}"
        )
    deadline = time.monotonic() + PUBLISH_WAIT_S
    while True:
        try:
            return publish(case, prepared, run, run_dir, records)
        except CaseMutationLockError as exc:
            if time.monotonic() > deadline:
                raise ValueError(
                    f"{exc}; run {run_id} is recorded but unpublished. Publish it later with "
                    f"`--provider replay --from-run {run_id}`"
                ) from exc
            time.sleep(0.5)


def usage(results: list[Result], records: list[ExchangeRecord]) -> dict[str, int]:
    tokens = {"input_tokens": 0, "output_tokens": 0}
    for _, record, response in {r[1].request_sha256: r for r in results}.values():
        if record.status == "ok" and response:
            try:
                reported = json.loads(response)
            except (ValueError, RecursionError):
                continue
            reported = reported.get("usage") if isinstance(reported, dict) else None
            if not isinstance(reported, dict):
                continue
            for key in tokens:
                value = reported.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    tokens[key] += value
    return {
        "requests": len(records),
        "attempts": sum(r.attempts for r in records),
        "sent_ok": sum(r.status == "ok" for r in records),
        **tokens,
    }


def publish(
    case: Path,
    prepared: Prepared,
    run: SuggestionRun,
    run_dir: Path,
    records: list[ExchangeRecord],
) -> dict:
    with case_publication_transaction(case) as manifest:
        if input_fingerprint(case, manifest) != prepared.fingerprint:
            raise StaleSuggestionRun(
                f"the case evidence changed during suggestion run {run.run_id}; its exchanges "
                f"are kept for `--provider replay --from-run {run.run_id}`"
            )
        blobs = {r.request_sha256 for r in records} | {
            r.response_sha256 for r in records if r.response_sha256
        }
        files = {
            (run_dir / name).relative_to(case).as_posix(): sha256_file(run_dir / name)
            for name in ("run.json", "suggestions.jsonl", "exchanges.jsonl")
        }
        files.update({f"suggest/blobs/{digest}": digest for digest in sorted(blobs)})
        relative = run_dir.relative_to(case).as_posix()
        manifest.setdefault("suggestions", {})[run.run_key] = {
            "run_id": run.run_id,
            "task": run.task,
            "run": f"{relative}/run.json",
            "suggestions": f"{relative}/suggestions.jsonl",
            "exchanges": f"{relative}/exchanges.jsonl",
            "input_fingerprint": run.input_fingerprint,
            "files": files,
        }
    return {
        "run_key": run.run_key,
        "run_id": run.run_id,
        "provider": run.provider,
        "resolved_models": list(run.resolved_models),
        "counts": run.counts,
        "usage": run.usage,
        "scope": run.scope,
    }


def load_run(root: Path, entry: dict) -> tuple[SuggestionRun, list[Suggestion]]:
    for path, digest in entry["files"].items():
        if not path.startswith("suggest/blobs/") and sha256_file(safe_path(root, path)) != digest:
            raise ValueError(f"suggestion file changed since publication: {path}")
    run = SuggestionRun.model_validate(
        load_strict_json(safe_path(root, entry["run"]), max_bytes=8 * 1024 * 1024)
    )
    rows = [
        Suggestion.model_validate_json(line)
        for line in safe_path(root, entry["suggestions"]).read_text().splitlines()
    ]
    return run, rows


def stale_reasons(case: Path, manifest: dict, entry: dict, run: SuggestionRun) -> list[str]:
    reasons = []
    if entry["input_fingerprint"] != input_fingerprint(case, manifest):
        reasons.append("ingested evidence changed since this run")
    current = {q.key: q.sha256() for q in task_questions(run.task, run.query)}
    if current != run.questions:
        reasons.append("question wording changed since this run")
    if run.policy_version != POLICY_VERSION:
        reasons.append("review policy changed since this run")
    if run.suggest_build_id != suggest_build_id():
        reasons.append("suggestion code changed since this run")
    return reasons


def relevance(row: Suggestion) -> float | None:
    answer = row.answers.get("search_relevance" if row.task == "search" else "write_relevance")
    return answer.value if answer is not None and answer.status == "answered" else None


def rank(row: Suggestion) -> tuple:
    """Search ranks by relevance alone; claims by disposition, then relevance."""
    value = relevance(row)
    order = (row.transcript_id, row.position)
    if row.task == "search":
        return (value is None, -(value or 0.0), *order)
    return (DISPOSITION_ORDER.index(row.disposition), -(value or 0.0), *order)


def excerpt(case: Path, row: Suggestion, limit: int = 160) -> str:
    payload = safe_path(case, f"suggest/blobs/{row.request_sha256}").read_bytes()
    if sha256_bytes(payload) != row.request_sha256:
        raise ValueError(f"recorded request {row.request_sha256} failed its hash check")
    message = json.loads(payload)["state"]["message"]
    text = message.get("text", "")
    text = text[:limit] + ("…" if len(text) > limit else "")
    extras = [f"calls {c['function']} {c['arguments'][:80]}" for c in message.get("tool_calls", [])]
    if message.get("tool_error"):
        extras.append(f"tool error {message['tool_error'][:80]}")
    return " ".join([text, *(f"[{e}]" for e in extras)]).strip()


def show(case: Path, key: str | None = None, *, limit: int = 20) -> dict:
    manifest = read_manifest(case)
    entries = manifest.get("suggestions", {})
    runs = []
    for run_name, entry in sorted(entries.items()):
        run, _ = load_run(case, entry)
        runs.append(
            {
                "run_key": run_name,
                "run_id": run.run_id,
                "provider": run.provider,
                "requested_model": run.requested_model,
                "resolved_models": list(run.resolved_models),
                "query": run.query,
                "completed_at": run.completed_at,
                "counts": run.counts,
                "usage": run.usage,
                "stale": stale_reasons(case, manifest, entry, run),
            }
        )
    result: dict = {
        "runs": runs,
        "interpretation": SuggestionRun.model_fields["interpretation"].default,
    }
    selected = key or (next(iter(entries)) if len(entries) == 1 else None)
    if selected is None:
        return result
    if selected not in entries:
        raise ValueError(f"no suggestion run {selected}; see `eg suggest show CASE`")
    _, rows = load_run(case, entries[selected])
    ordered = sorted(rows, key=rank)
    result["run_key"] = selected
    result["shown"] = min(limit, len(ordered))
    result["total"] = len(ordered)
    result["suggestions"] = [
        {
            "disposition": row.disposition,
            "reason": row.reason,
            "answers": {
                k: {"label": a.label, "confidence": a.confidence, "value": a.value}
                if a.status == "answered"
                else {"status": a.status, "detail": a.detail}
                for k, a in row.answers.items()
            },
            "transcript_id": row.transcript_id,
            "message_id": row.message_id,
            "role": row.role,
            "span_id": row.span_id,
            "excerpt": excerpt(case, row),
        }
        for row in ordered[:limit]
    ]
    return result


def drop(case: Path, key: str) -> dict:
    """Unpublish one run. Its files stay on disk and can still be replayed."""
    with case_publication_transaction(case) as manifest:
        entry = manifest.get("suggestions", {}).pop(key, None)
        if entry is None:
            raise ValueError(f"no suggestion run {key}; see `eg suggest show CASE`")
    return {"dropped": key, "run_id": entry["run_id"]}


def recheck(root: Path, entry: dict) -> str:
    """Re-validate recorded responses and re-derive dispositions without any provider.

    The result is a report, never an exception: suggestion code may legitimately
    change between export and verification, and that must not fail a bundle.
    """
    try:
        return reproduce(root, entry)
    except (ValueError, OSError, KeyError) as exc:
        return f"does not reproduce: {exc}"


def reproduce(root: Path, entry: dict) -> str:
    run, rows = load_run(root, entry)
    for path, digest in entry["files"].items():
        if path.startswith("suggest/blobs/") and sha256_file(safe_path(root, path)) != digest:
            raise ValueError(f"suggestion blob changed: {path}")
    records: dict[str, ExchangeRecord] = {}
    for line in safe_path(root, entry["exchanges"]).read_text().splitlines():
        record = ExchangeRecord.model_validate_json(line)
        if records.setdefault(record.request_sha256, record) is not record:
            raise ValueError(f"run {run.run_id} records one request twice")
    for row in rows:
        record = records[row.request_sha256]
        body = safe_path(root, f"suggest/blobs/{row.request_sha256}").read_bytes()
        response = (
            safe_path(root, f"suggest/blobs/{record.response_sha256}").read_bytes()
            if record.response_sha256
            else None
        )
        resolved, answers = validate_exchange(
            body, record, response, requested_model=run.requested_model
        )
        if resolved != row.resolved_model or answers != row.answers:
            raise ValueError(f"recorded answers do not reproduce for {row.span_id}")
        if run.policy_version == POLICY_VERSION and disposition(run.task, answers) != (
            row.disposition,
            row.reason,
        ):
            raise ValueError(f"review disposition does not reproduce for {row.span_id}")
    if run.policy_version != POLICY_VERSION:
        return "answers reproduced; disposition policy version differs"
    return "reproduced"
