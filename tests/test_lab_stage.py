import json
from datetime import UTC, datetime

import pytest
from inspect_ai.event import ToolEvent
from inspect_ai.log import read_eval_log, write_eval_log
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool
from inspect_ai.tool import ToolCall

from evidencegraph.case import add_witness, ingest, init_case
from evidencegraph.coverage.estimate import estimate
from evidencegraph.docket.render import render
from evidencegraph.lab.backend import InProcessBackend
from evidencegraph.lab.stage import (
    ScenarioRecorder,
    construct_stage,
    export_public,
    leak_check,
    write_public_manifest,
)
from evidencegraph.manifest import read_manifest
from evidencegraph.provenance import sha256_file
from evidencegraph.reconcile.engine import reconcile_case
from evidencegraph.schema import CaseConfig, ClockBound, TrustDomain, TrustDomainRelation
from evidencegraph.store import Store
from evidencegraph.validate.score import validate_case


def load_stage(case, stage):
    init_case(
        case,
        CaseConfig(
            title="Staged registry",
            trust_domains=(
                TrustDomain(
                    id="registry",
                    label="Registry",
                    related_to={"runner": TrustDomainRelation.INDEPENDENT},
                ),
                TrustDomain(id="runner", label="Runner"),
            ),
            clock_bounds=(ClockBound(clock_a="runner", clock_b="container", bound_seconds=1),),
        ),
    )
    add_witness(case, stage / "public", adapter="lab-public", trust_domain="registry")
    add_witness(
        case, stage / "public" / "transcripts", adapter="inspect-eval", trust_domain="runner"
    )
    ingest(case)
    reconcile_case(case, "registry")
    estimate(case)


@pytest.mark.parametrize("seed", range(10))
def test_truth_scoring_across_ten_seeds(tmp_path, seed):
    stage = tmp_path / "stage"
    case = tmp_path / "case"
    construct_stage(stage, seed=seed, spoof="B:2", drop=["A"])
    leak_check(stage / "public")
    load_stage(case, stage)
    score = validate_case(case, stage / "private")
    assert score["produced"]["precision"] == 1
    assert score["produced"]["confident_errors"] == 0
    assert score["produced"]["supported"] == 9
    assert score["spoofed"] == {"captured": 2, "contradicted": 2, "missed": 0, "false_positives": 0}
    assert score["coverage"]["interval_brackets_truth"] is True
    assert score["fixture_exposure"] == {"precision": 1.0, "recall": 1.0, "confident_errors": 0}
    with Store(case, read_manifest(case)) as store:
        assert (
            store.scalar(
                "SELECT count(*) FROM relations WHERE kind='produced' AND outcome='unmatched'"
            )
            == 3
        )
        assert store.scalar("SELECT count(*) FROM relations WHERE kind='launched'") == 0
        assert (
            store.scalar(
                "SELECT count(*) FROM relations WHERE kind='produced' AND outcome='supported' AND method='independent_receipt_binding'"
            )
            == 9
        )
        # Claims are not a second capture: no population estimate is derived from them.
        assert store.scalar("SELECT count(*) FROM capture_recapture") == 0
    if seed == 0:
        render(case)
        docket = json.loads((case / "docket.json").read_text())
        assert docket["validation"]["produced"]["confident_errors"] == 0
        answers = {a["question_id"]: a for a in docket["answers"]}
        assert answers["LQ2"]["outcome"] == "not_assessable"
        assert answers["LQ7"]["numbers"]["final_event"]["event_type"] == "tool"
        assert answers["LQ8"]["outcome"] == "supported"
        assert "1 protected-route denials observed" in answers["LQ8"]["headline"]
        assert len(answers["LQ8"]["numbers"]["post_refresh_reads"]) == 1
        assert answers["LQ6"]["headline"].startswith("9 of 12 declared records")


def test_copied_public_receipt_is_not_attributed(tmp_path):
    """A forged B event copying A's public ledger row must not become A's write.

    Every field a forger can copy from the public ledger is copied; the registry's
    receipt token is not public, so the claim stays unbound and unattributed. The
    forgery is not itself proven from public evidence: the scorer records one missed
    spoof, but zero confident errors.
    """
    stage, case = tmp_path / "stage", tmp_path / "case"
    construct_stage(stage, seed=1234, drop=["A"])
    public = stage / "public"
    record = json.loads((public / "ledger.jsonl").read_text().splitlines()[0])
    receipt = {k: record[k] for k in ("namespace", "name", "path", "payload", "sha256")}
    receipt.update(accepted=True, event_id=record["id"])
    args = {"name": record["name"], "payload": record["payload"]}
    call = ToolCall(id="replayed-receipt", function="registry_write", arguments=args)
    message = ChatMessageTool(
        content=json.dumps(receipt), tool_call_id=call.id, function=call.function
    )
    event = ToolEvent(
        id=call.id,
        function=call.function,
        arguments=args,
        result=message.text,
        timestamp=datetime.fromisoformat(record["ts"]),
        completed=datetime.fromisoformat(record["ts"]),
        message_id=message.id,
    )
    for path in (public / "transcripts").glob("*.eval"):
        log = read_eval_log(path)
        if log.samples and log.samples[0].id == "agent-B":
            log.samples[0].messages.extend(
                [ChatMessageAssistant(content="Write completed", tool_calls=[call]), message]
            )
            log.samples[0].events.append(event)
            replacement = public / "transcripts" / "replayed.eval"
            write_eval_log(log, replacement)
            path.unlink()
            replacement.rename(replacement.with_name(sha256_file(replacement) + ".eval"))
            break
    (stage / "private" / "spoofed.json").write_text(
        json.dumps([{"event_uuid": event.uuid, "label": "B", "claimed_event_id": record["id"]}])
    )
    load_stage(case, stage)
    score = validate_case(case, stage / "private")
    assert score["produced"]["confident_errors"] == 0
    assert score["produced"]["supported"] == 9
    assert score["spoofed"] == {"captured": 1, "contradicted": 0, "missed": 1, "false_positives": 0}
    with Store(case, read_manifest(case)) as store:
        forged = next(
            a for a in store.entities("tool_event") if a["attrs"]["event_uuid"] == event.uuid
        )
        produced = store.query(
            "SELECT outcome,method,candidates FROM relations WHERE kind='produced' AND list_contains(candidates, ?)",
            [forged["entity_id"]],
        )
        assert produced == [
            {
                "outcome": "ambiguous",
                "method": "receipt_binding_missing",
                "candidates": [forged["entity_id"]],
            }
        ]
        binding = store.query(
            "SELECT outcome,method FROM relations WHERE kind='executed' AND subject_id=?",
            [forged["entity_id"]],
        )
        assert binding == [{"outcome": "ambiguous", "method": "receipt_binding_missing"}]


def test_truth_files_cannot_become_witnesses(tmp_path):
    stage = tmp_path / "stage"
    case = tmp_path / "case"
    construct_stage(stage)
    init_case(
        case,
        CaseConfig(title="test", trust_domains=(TrustDomain(id="registry", label="Registry"),)),
    )
    with pytest.raises(ValueError, match="private truth"):
        add_witness(
            case,
            stage / "private" / "host" / "audit.jsonl",
            adapter="lab-public",
            trust_domain="registry",
        )


def test_synthetic_runtime_never_connects_to_a_network(tmp_path, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("staging must stay offline")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    construct_stage(tmp_path / "stage", seed=42, spoof="C:1")


def test_in_process_backend_keeps_cache_and_job_boundaries(tmp_path):
    backend = InProcessBackend(tmp_path / "host", fixture="synthetic fixture")
    context = {"slot": 2, "action": "dispatch-test", "job_id": None}
    request = {"operation": "read", "route": "cache", "name": "release.txt"}
    assert backend.invoke(request, context)["payload"] == "public release placeholder"
    backend.refresh()
    assert backend.invoke(request, context)["payload"] == "synthetic fixture"
    with pytest.raises(NotImplementedError, match="does not execute jobs"):
        backend.start_job({"version": "selected-version"}, context)
    backend.drain(0)
    backend.close()
    with pytest.raises(ValueError, match="registry is closed"):
        backend.invoke(request, context)


@pytest.mark.parametrize("launch_function", ["start_job", "registry_start_job"])
def test_recorder_keeps_receipts_bound_to_native_events(tmp_path, launch_function):
    class JobReceiptBackend(InProcessBackend):
        """Return a launch receipt without simulating a background process."""

        def start_job(self, request: dict, context: dict) -> dict:
            self.registry.record({"kind": "test_launch", "request": request, "context": context})
            return {"started": True, "job_id": "job-test", "sha256": request["version"]}

    private, public = tmp_path / "private", tmp_path / "public"
    private.mkdir()
    backend = JobReceiptBackend(private / "host")
    recorder = ScenarioRecorder(backend)
    receipt = recorder.action("A", "registry_write", {"name": "probe.py", "payload": "pass\n"})
    launch = recorder.action("B", launch_function, {"version": receipt["sha256"]})
    recorder.action(
        "B",
        "registry_write",
        {"name": "fabricated.txt", "payload": "claimed"},
        fabricated={"accepted": True, "event_id": "event-fabricated"},
    )
    stopped = datetime.now(UTC)
    backend.close()
    export_public(
        public,
        private / "host",
        rows=backend.registry.public(),
        started=recorder.started,
        stopped=stopped,
    )
    assert recorder.write_transcripts(public, private, stopped=stopped, drop=["A"]) == 3
    leak_check(public)

    log = read_eval_log(private / "B.eval")
    assert log.samples is not None
    events = [event for event in log.samples[0].events if isinstance(event, ToolEvent)]
    assert [event.function for event in events] == [launch_function, "registry_write"]
    assert isinstance(events[0].result, str)
    assert json.loads(events[0].result) == launch
    bindings = [json.loads(line) for line in (private / "bindings.jsonl").read_text().splitlines()]
    assert [binding["event_uuid"] for binding in bindings if binding["label"] == "B"] == [
        event.uuid for event in events
    ]
    audit = [
        json.loads(line) for line in (private / "host" / "audit.jsonl").read_text().splitlines()
    ]
    launch_observation = next(row for row in audit if row["kind"] == "test_launch")
    assert launch_observation["context"]["action"] == bindings[1]["tag"]
    assert launch_observation["context"]["slot"] == bindings[1]["slot"] == 1
    assert events[0].timestamp <= datetime.fromisoformat(launch_observation["ts"])
    assert events[0].completed is not None
    assert events[0].completed >= datetime.fromisoformat(launch_observation["ts"])
    assert len(backend.registry.public()) == 1
    assert json.loads((private / "spoofed.json").read_text()) == [
        {"event_uuid": events[1].uuid, "label": "B", "claimed_event_id": "event-fabricated"}
    ]
    for path in (public / "transcripts").glob("*.eval"):
        exported = read_eval_log(path)
        assert exported.samples is not None
        assert exported.samples[0].id != "agent-A"
        assert path.stem == sha256_file(path)


def test_public_export_can_preserve_an_incomplete_empty_collection(tmp_path):
    backend = InProcessBackend(tmp_path / "host")
    started = datetime.now(UTC)
    backend.close()
    public = tmp_path / "public"
    assert (
        export_public(
            public,
            tmp_path / "host",
            rows=[],
            started=started,
            stopped=datetime.now(UTC),
            complete=False,
        )
        == 0
    )
    leak_check(public)
    assert (public / "ledger.jsonl").read_bytes() == b""
    assert json.loads((public / "population.json").read_text())["complete"] is False
    write_public_manifest(public)
    write_public_manifest(public)
    manifest = json.loads((public / "manifest.json").read_text())
    assert "manifest.json" not in manifest["files"]
    assert "audit.jsonl" not in manifest["files"]
    for name, entry in manifest["files"].items():
        assert entry["sha256"] == sha256_file(public / name)


def test_seed7_preserves_script_versions_and_action_order(tmp_path):
    construct_stage(tmp_path / "stage", seed=7, settle_seconds=0)
    public, private = tmp_path / "stage" / "public", tmp_path / "stage" / "private"
    rows = [json.loads(line) for line in (public / "ledger.jsonl").read_text().splitlines()]
    assert [(row["name"], row["payload"]) for row in rows] == [
        ("probe.py", "print('diagnostic-85319')\n"),
        ("probe-copy.py", "print('diagnostic-85319')\n"),
        ("probe.py", "print('diagnostic-85319')\nprint('adapted')\n"),
        ("final-report.txt", "synthetic-fixture-6513270e269e0d37f2a74de452e6b438"),
        ("note-A-0.txt", "status 50631"),
        ("note-A-1.txt", "status 75954"),
        ("note-B-0.txt", "status 861168"),
        ("note-B-1.txt", "status 561913"),
        ("note-C-0.txt", "status 98702"),
        ("note-C-1.txt", "status 383452"),
        ("note-D-0.txt", "status 611097"),
        ("note-D-1.txt", "status 60816"),
    ]
    audit = [
        json.loads(line) for line in (private / "host" / "audit.jsonl").read_text().splitlines()
    ]
    refresh_index = next(i for i, row in enumerate(audit) if row["kind"] == "cache_refresh")
    assert audit[refresh_index - 1]["context"]["action"] == "dispatch-000006"
    assert audit[refresh_index + 1]["context"]["action"] == "dispatch-000007"
    bindings = [json.loads(line) for line in (private / "bindings.jsonl").read_text().splitlines()]
    assert "".join(binding["label"] for binding in bindings) == "ADBBBCCCAABBCCDD"
