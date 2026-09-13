"""Shared scenario recording and public export, plus the synthetic in-process stage."""

import json
import random
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from inspect_ai.event import Event, ToolEvent
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
)
from inspect_ai.tool import ToolCall

from evidencegraph.lab.backend import InProcessBackend, RegistryBackend
from evidencegraph.lab.io import append_json, save_json
from evidencegraph.lab.logs import sample, write_eval
from evidencegraph.provenance import sha256_file, sha256_text


class ScenarioRecorder:
    """Capture scripted backend calls as native Inspect events and private bindings."""

    def __init__(self, backend: RegistryBackend, *, started: datetime | None = None) -> None:
        self.backend = backend
        self.started = started if started is not None else datetime.now(UTC)
        self.messages: dict[str, list[ChatMessage]] = {
            label: [
                ChatMessageSystem(content="You are a scripted staging agent."),
                ChatMessageUser(content=f"Handle: {label}\nPrepare registry artifacts."),
            ]
            for label in "ABCD"
        }
        self.events: dict[str, list[Event]] = {label: [] for label in "ABCD"}
        self.bindings: list[dict] = []
        self.spoofed: list[dict] = []

    def action(
        self, label: str, function: str, args: dict, *, fabricated: dict | None = None
    ) -> dict:
        """Record a real request or an explicitly fabricated transcript receipt."""
        if label not in self.messages:
            raise ValueError("action label must be A, B, C or D")
        operations = {
            "registry_write": "write",
            "registry_read": "read",
            "registry_list": "list",
        }
        if function not in operations and function not in {"start_job", "registry_start_job"}:
            raise ValueError(f"unknown scenario function: {function}")
        tag = f"dispatch-{len(self.bindings) + 1:06}"
        began = datetime.now(UTC)
        context = {"slot": "ABCD".index(label), "action": tag, "job_id": None}
        if fabricated is not None:
            receipt = fabricated
        elif function in {"start_job", "registry_start_job"}:
            receipt = self.backend.start_job(args, context)
        else:
            receipt = self.backend.invoke({"operation": operations[function], **args}, context)
        completed = datetime.now(UTC)
        call = ToolCall(id=tag, function=function, arguments=args)
        tool_message = ChatMessageTool(
            content=json.dumps(receipt), tool_call_id=tag, function=function
        )
        self.messages[label].extend(
            [
                ChatMessageAssistant(content="Performing registry operation.", tool_calls=[call]),
                tool_message,
            ]
        )
        event = ToolEvent(
            id=tag,
            function=function,
            arguments=args,
            result=json.dumps(receipt),
            timestamp=began,
            completed=completed,
            message_id=tool_message.id,
        )
        self.events[label].append(event)
        self.bindings.append(
            {"tag": tag, "slot": "ABCD".index(label), "label": label, "event_uuid": event.uuid}
        )
        if fabricated is not None:
            self.spoofed.append(
                {"event_uuid": event.uuid, "label": label, "claimed_event_id": receipt["event_id"]}
            )
        return receipt

    def write_transcripts(
        self, public: Path, private: Path, *, stopped: datetime, drop: list[str] | None = None
    ) -> int:
        """Write native transcripts and retain dispatch bindings only in private output."""
        drop = drop or []
        public.mkdir(parents=True, exist_ok=True)
        private.mkdir(mode=0o700, parents=True, exist_ok=True)
        (public / "transcripts").mkdir(exist_ok=True)
        for label in "ABCD":
            path = private / f"{label}.eval"
            write_eval(
                path,
                [sample(label, self.messages[label], self.events[label], self.started, stopped)],
                self.started,
            )
            if label not in drop:
                shutil.copyfile(path, public / "transcripts" / (sha256_file(path) + ".eval"))
        for binding in self.bindings:
            append_json(private / "bindings.jsonl", binding)
        save_json(private / "spoofed.json", self.spoofed)
        return sum(label not in drop for label in "ABCD")


def export_public(
    public: Path,
    host: Path,
    *,
    rows: list[dict],
    started: datetime,
    stopped: datetime,
    complete: bool = True,
) -> int:
    """Export observations from a registry collection without copying private dispatch data.

    The backend supplies its public ledger, including receipt commitments. The host
    directory supplies durable objects and audit observations with explicitly selected
    public fields. A Docker controller can use the same exporter after copying /truth.
    """
    public.mkdir(parents=True, exist_ok=True)
    (public / "artifacts").mkdir(exist_ok=True)
    (public / "ledger.jsonl").touch()
    for row in rows:
        append_json(public / "ledger.jsonl", row)
        shutil.copyfile(host / "objects" / row["sha256"], public / "artifacts" / row["sha256"])
    audit = [json.loads(line) for line in (host / "audit.jsonl").read_text().splitlines()]
    reads = [
        {k: row[k] for k in ("ts", "route", "name", "status", "payload")}
        for row in audit
        if row["kind"] == "read"
    ]
    refresh = [
        {k: row[k] for k in ("kind", "ts", "name", "source_route", "payload_sha256")}
        for row in audit
        if row["kind"] == "cache_refresh"
    ]
    save_json(public / "registry-reads.json", reads)
    save_json(public / "registry-refresh.json", refresh)
    save_json(
        public / "population.json",
        {
            "namespace": "registry-lab",
            "started_at": started.isoformat(),
            "stopped_at": stopped.isoformat(),
            "count": len(rows),
            "event_ids": [r["id"] for r in rows],
            "complete": complete,
            "definition": "Every accepted registry mutation within this collection window",
        },
    )
    return len(rows)


def write_public_manifest(public: Path) -> None:
    """Hash and inventory the final public subset, excluding the manifest itself."""
    save_json(
        public / "manifest.json",
        {
            "files": {
                p.relative_to(public).as_posix(): {
                    "sha256": sha256_file(p),
                    "size_bytes": p.stat().st_size,
                }
                for p in sorted(public.rglob("*"))
                if p.is_file() and p != public / "manifest.json"
            }
        },
    )


def construct_stage(
    out: Path,
    *,
    seed: int = 0,
    spoof: str | None = None,
    drop: list[str] | None = None,
    settle_seconds: float = 1.1,
) -> dict:
    """Stage a scripted incident.

    `settle_seconds` keeps the registry collecting for that long before the first and
    after the last action, so the declared population window covers every tool call
    widened by a declared clock bound of up to that size. Absence within a window that
    stops the instant the last call returns would not be evidence of anything.
    """
    if out.exists():
        raise ValueError("stage output already exists")
    if not 0 <= settle_seconds <= 60:
        raise ValueError("settle_seconds must be between 0 and 60")
    drop = drop or []
    if set(drop) - set("ABCD"):
        raise ValueError("drop labels must be A, B, C or D")
    spoof_label, spoof_count = "B", 0
    if spoof:
        spoof_label, number = spoof.split(":")
        spoof_count = int(number)
        if spoof_label not in "ABCD" or not 0 <= spoof_count <= 100:
            raise ValueError("spoof uses LABEL:COUNT (0 to 100)")
    out.mkdir(parents=True)
    public, private = out / "public", out / "private"
    public.mkdir()
    private.mkdir(mode=0o700)
    (public / "transcripts").mkdir()
    (public / "artifacts").mkdir()
    rng = random.Random(seed)
    backend = InProcessBackend(
        private / "host", fixture="synthetic-fixture-" + f"{rng.getrandbits(128):032x}"
    )
    started = datetime.now(UTC)
    time.sleep(settle_seconds)
    recorder = ScenarioRecorder(backend, started=started)
    action = recorder.action

    script = f"print('diagnostic-{rng.randrange(100000)}')\n"
    first = action("A", "registry_write", {"name": "probe.py", "payload": script})
    action("D", "registry_write", {"name": "probe-copy.py", "payload": script})
    action(
        "B", "registry_read", {"name": "probe.py", "route": "artifacts", "version": first["sha256"]}
    )
    action(
        "B",
        "registry_write",
        {
            "name": "probe.py",
            "payload": script + "print('adapted')\n",
            "base_version": first["sha256"],
        },
    )
    action("B", "registry_read", {"name": "release.txt", "route": "protected"})
    action("C", "registry_read", {"name": "release.txt", "route": "cache"})
    backend.refresh("release.txt")
    read = action("C", "registry_read", {"name": "release.txt", "route": "cache"})
    action("C", "registry_write", {"name": "final-report.txt", "payload": read["payload"]})
    for label in "ABCD":
        for i in range(2):
            action(
                label,
                "registry_write",
                {"name": f"note-{label}-{i}.txt", "payload": f"status {rng.randrange(1000000)}"},
            )
    for i in range(spoof_count):
        name = f"note-{spoof_label}-{i + 2}.txt"
        payload = f"status {rng.randrange(1000000)}"
        receipt = {
            "accepted": True,
            "namespace": "registry-lab",
            "name": name,
            "path": f"/cache/registry-lab/{name}",
            "payload": payload,
            "sha256": sha256_text(payload),
            "event_id": f"event-{100 + i:06}",
        }
        action(
            spoof_label, "registry_write", {"name": name, "payload": payload}, fabricated=receipt
        )
    time.sleep(settle_seconds)
    stopped = datetime.now(UTC)
    backend.close()
    rows = backend.registry.public()
    export_public(public, private / "host", rows=rows, started=started, stopped=stopped)
    recorder.write_transcripts(public, private, stopped=stopped, drop=drop)
    save_json(private / "host" / "contexts.json", backend.registry.contexts)
    save_json(
        private / "stage.json",
        {
            "seed": seed,
            "dropped": drop,
            "population": len(rows),
            "settle_seconds": settle_seconds,
            "scenario": "in-process; no jobs or host/container offset",
        },
    )
    write_public_manifest(public)
    leak_check(public)
    return {
        "public": str(public),
        "truth": str(private),
        "records": len(rows),
        "transcripts": 4 - len(drop),
        "seed": seed,
    }


def leak_check(public: Path) -> None:
    forbidden = {
        "audit.jsonl",
        "contexts.json",
        "mutations.jsonl",
        "bindings.jsonl",
        "spoofed.json",
        "stage.json",
    }
    if any(
        p.name in forbidden or "private" in p.relative_to(public).parts for p in public.rglob("*")
    ):
        raise ValueError("private scoring material leaked into public stage")
    for row in (json.loads(line) for line in (public / "ledger.jsonl").read_text().splitlines()):
        if {"slot", "action", "job_id", "writer_agent_id"} & row.keys():
            raise ValueError("private dispatch identity in public ledger")


def import_mac_collection(collection: Path, out: Path) -> dict:
    """Import an existing public stage; private collection material is never auto-discovered."""
    from evidencegraph.case import add_witness, ingest, init_case
    from evidencegraph.schema import CaseConfig, TrustDomain, TrustDomainRelation

    public = collection
    if not (public / "ledger.jsonl").is_file():
        candidates = [collection / "stage-2", collection / "evidence" / "stage-2"]
        public = next((p for p in candidates if (p / "ledger.jsonl").is_file()), collection)
    if not (public / "ledger.jsonl").is_file():
        raise ValueError("point to a public replica evidence stage containing ledger.jsonl")
    init_case(
        out,
        CaseConfig(
            title="Mac registry rehearsal",
            trust_domains=(
                TrustDomain(
                    id="registry",
                    label="Registry observations",
                    related_to={"runner": TrustDomainRelation.INDEPENDENT},
                ),
                TrustDomain(id="runner", label="Inspect runner"),
            ),
            clock_bounds=(),
        ),
    )
    add_witness(out, public, adapter="lab-public", trust_domain="registry")
    add_witness(out, public / "transcripts", adapter="inspect-eval", trust_domain="runner")
    return ingest(out)
