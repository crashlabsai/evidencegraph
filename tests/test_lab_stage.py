import json
from datetime import datetime

import pytest
from inspect_ai.event import ToolEvent
from inspect_ai.log import read_eval_log, write_eval_log
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool
from inspect_ai.tool import ToolCall

from evidencegraph.case import add_witness, ingest, init_case
from evidencegraph.coverage.estimate import estimate
from evidencegraph.docket.render import render
from evidencegraph.lab.stage import construct_stage, leak_check
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
