"""Native Inspect logs via Scout public APIs, with hash-before-read citations."""

import asyncio
import inspect as _inspect
import json
import re
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from inspect_ai.event import Event, SandboxEvent, ToolEvent
from inspect_ai.model import ChatMessage
from inspect_scout import Transcript, TranscriptContent, Transcripts, transcripts_from

from evidencegraph.adapters.base import AdapterOutput, Builder
from evidencegraph.provenance import sha256_text, verify_file_identity
from evidencegraph.reconcile.consistency import check_transcript, result_text
from evidencegraph.refs import citation
from evidencegraph.schema import Clock, Outcome, TimeClaim, Witness

ALL_CONTENT = TranscriptContent(messages="all", events="all")


def open_transcripts(paths: Sequence[str | Path]) -> Transcripts:
    return transcripts_from([str(p) for p in paths])


async def iter_transcripts(
    transcripts: Transcripts, content: TranscriptContent = ALL_CONTENT
) -> AsyncIterator[Transcript]:
    async with transcripts.reader() as reader:
        async for info in reader.index():
            loaded = reader.read(info, content)
            if _inspect.isawaitable(loaded):
                loaded = await loaded
            yield loaded


def read_transcripts(
    paths: Sequence[str | Path], content: TranscriptContent = ALL_CONTENT
) -> list[Transcript]:
    async def collect():
        return [t async for t in iter_transcripts(open_transcripts(paths), content)]

    return asyncio.run(collect())


def event_end(event: Event):
    return getattr(event, "completed", None) or event.timestamp


def ref_for_event(witness: Witness, transcript: Transcript, event: Event):
    if not event.uuid:
        raise ValueError("event has no reproducible uuid")
    return citation(
        witness.witness_id,
        "event",
        event.uuid,
        event.model_dump_json().encode(),
        transcript_id=transcript.transcript_id,
        event_id=event.uuid,
    )


def ref_for_message(witness: Witness, transcript: Transcript, message: ChatMessage):
    if not message.id:
        raise ValueError("message has no reproducible id")
    return citation(
        witness.witness_id,
        "message",
        message.id,
        message.model_dump_json().encode(),
        transcript_id=transcript.transcript_id,
        message_id=message.id,
    )


class InspectEval:
    def __init__(self, handle_patterns: tuple[str, ...] = (r"(?m)^Handle:\s*(\S+)",)):
        self.handle_patterns = tuple(re.compile(pattern) for pattern in handle_patterns)

    def describe(self, path: Path) -> dict:
        if path.suffix != ".eval":
            raise ValueError("Inspect adapter requires .eval logs")
        return {"kind": "inspect_eval"}

    def ingest(self, path: Path, witness: Witness) -> AdapterOutput:
        verify_file_identity(
            path,
            expected_size=witness.size_bytes,
            expected_sha256=witness.sha256,
            subject=witness.witness_id,
        )
        builder = Builder(witness)
        transcripts = read_transcripts([path])
        ids = [t.transcript_id for t in transcripts]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate Scout transcript ids")
        clock_id = f"{witness.witness_id}:runner"
        yield "clocks", Clock(clock_id=clock_id, witness_id=witness.witness_id, label="runner")
        for transcript in transcripts:
            ref = citation(
                witness.witness_id,
                "file",
                transcript.transcript_id,
                path.read_bytes(),
                transcript_id=transcript.transcript_id,
            )
            session = builder.entity(
                "session",
                "transcript",
                transcript.transcript_id,
                ref,
                {"transcript_id": transcript.transcript_id},
            )
            event_entities = {}
            seen_events = set()
            # The transcript's final recorded event of any type, for questions about
            # what happened after a session ended. Tool events alone would miss later
            # sandbox, model or state activity.
            timed = [e for e in transcript.events if e.uuid and e.timestamp]
            if timed:
                final = max(enumerate(timed), key=lambda pair: (event_end(pair[1]), pair[0]))[1]
                ref = ref_for_event(witness, transcript, final)
                lower, upper = final.timestamp.isoformat(), event_end(final).isoformat()
                last = builder.entity(
                    "task",
                    "transcript_final_event",
                    transcript.transcript_id,
                    ref,
                    {
                        "transcript_id": transcript.transcript_id,
                        "event_type": final.event,
                        "event_uuid": final.uuid,
                        "events_considered": len(timed),
                    },
                    time_lower=lower,
                    time_upper=upper,
                    time_grade="recorded_event",
                    time_uncertainty_s=0,
                    winning_clock_id=clock_id,
                )
                builder.edge("member_of", last, session, ref, method="scout_transcript_locator")
                yield (
                    "time_claims",
                    TimeClaim(
                        entity_id=last.entity_id,
                        witness_id=witness.witness_id,
                        clock_id=clock_id,
                        lower=lower,
                        upper=upper,
                        grade="recorded_event",
                        uncertainty_s=0,
                        winning=True,
                        citation_id=ref.citation_id,
                        note="Final recorded event of the transcript; clock offset requires a case declaration",
                    ),
                )
            for event in transcript.events:
                if not isinstance(event, (ToolEvent, SandboxEvent)):
                    continue
                if event.uuid in seen_events:
                    raise ValueError("duplicate Inspect event uuid within a transcript")
                seen_events.add(event.uuid)
                ref = ref_for_event(witness, transcript, event)
                attrs = {"transcript_id": transcript.transcript_id, "event_uuid": event.uuid}
                subkind = "tool_event" if isinstance(event, ToolEvent) else "sandbox_exec"
                if isinstance(event, ToolEvent):
                    raw = result_text(event.result)
                    try:
                        receipt = json.loads(raw)
                    except (ValueError, TypeError):
                        receipt = None
                    attrs.update(
                        {
                            "function": event.function,
                            "arguments": event.arguments,
                            "result_sha256": sha256_text(raw),
                            "receipt": receipt,
                            "error": str(event.error) if event.error else None,
                            "pending": getattr(event, "pending", False),
                            "truncated": getattr(event, "truncated", False),
                        }
                    )
                else:
                    attrs.update(
                        {
                            "cmd": event.cmd,
                            "result": event.result,
                            "output_sha256": sha256_text(event.output or ""),
                        }
                    )
                lower = event.timestamp.isoformat()
                upper = event_end(event).isoformat()
                entity = builder.entity(
                    "action",
                    subkind,
                    transcript.transcript_id + ":" + str(event.uuid),
                    ref,
                    attrs,
                    time_lower=lower,
                    time_upper=upper,
                    time_grade="recorded_event",
                    time_uncertainty_s=0,
                    winning_clock_id=clock_id,
                )
                event_entities[event.uuid] = entity
                builder.edge("member_of", entity, session, ref, method="scout_transcript_locator")
                if (
                    isinstance(event, ToolEvent)
                    and event.function == "start_job"
                    and not event.error
                ):
                    receipt = attrs.get("receipt")
                    if (
                        isinstance(receipt, dict)
                        and receipt.get("started") is True
                        and receipt.get("job_id")
                    ):
                        job = builder.entity(
                            "job",
                            "job",
                            str(receipt["job_id"]),
                            ref,
                            {
                                "claimed_script_sha256": event.arguments.get("version"),
                                "authenticated": False,
                            },
                        )
                        builder.edge(
                            "launched",
                            entity,
                            job,
                            ref,
                            method="transcript_launch_receipt",
                            outcome=Outcome.AMBIGUOUS,
                            rationale="Transcript claims a launch; public host process attestation is unavailable. This is never a produced edge.",
                        )
                yield (
                    "time_claims",
                    TimeClaim(
                        entity_id=entity.entity_id,
                        witness_id=witness.witness_id,
                        clock_id=clock_id,
                        lower=lower,
                        upper=upper,
                        grade="recorded_event",
                        uncertainty_s=0,
                        winning=True,
                        citation_id=ref.citation_id,
                        note="Recorded event window; clock offset requires a case declaration",
                    ),
                )
            for message in transcript.messages:
                ref = ref_for_message(witness, transcript, message)
                entity = builder.entity(
                    "message",
                    "chat_message",
                    transcript.transcript_id + ":" + str(message.id),
                    ref,
                    {
                        "role": message.role,
                        "message_id": message.id,
                        "transcript_id": transcript.transcript_id,
                        "content_sha256": ref.content_sha256,
                    },
                )
                builder.edge("sent", session, entity, ref, method="transcript_message")
                if message.role in {"system", "user"}:
                    matches = (
                        match
                        for pattern in self.handle_patterns
                        for match in pattern.finditer(message.text)
                    )
                    for match in matches:
                        handle = builder.entity(
                            "identity",
                            "handle",
                            transcript.transcript_id + ":" + match.group(1),
                            ref,
                            {"claimed_handle": match.group(1), "authenticated": False},
                        )
                        builder.edge(
                            "member_of",
                            handle,
                            session,
                            ref,
                            method="configured_handle_pattern",
                            outcome=Outcome.AMBIGUOUS,
                        )
            for tool, pairs in check_transcript(transcript):
                tool_entity = event_entities[tool.uuid]
                tool_ref = ref_for_event(witness, transcript, tool)
                for sandbox, outcome, detail in pairs:
                    builder.edge(
                        "corroborated_by",
                        tool_entity,
                        event_entities[sandbox.uuid],
                        tool_ref,
                        method="tool_sandbox_consistency",
                        outcome=Outcome(outcome),
                        rationale=detail,
                        also=(ref_for_event(witness, transcript, sandbox),),
                    )
                if not pairs:
                    builder.edge(
                        "corroborated_by",
                        tool_entity,
                        session,
                        tool_ref,
                        method="tool_sandbox_consistency",
                        outcome=Outcome.NOT_ASSESSABLE,
                        rationale="No sandbox execution recorded within the tool's window; no independent execution conclusion",
                    )
            yield from builder.drain()
