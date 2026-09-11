import json
from datetime import UTC, datetime, timedelta

import pytest
from inspect_ai.event import SandboxEvent, ToolEvent
from inspect_ai.model import ChatMessageUser

from evidencegraph.case import add_witness, ingest, init_case
from evidencegraph.lab.logs import sample, write_eval
from evidencegraph.manifest import read_manifest
from evidencegraph.refs import resolve_citation
from evidencegraph.schema import CaseConfig, Citation, TrustDomain, Witness
from evidencegraph.store import Store


def test_native_citations_custom_handles_and_launch_abstention(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    launch = ToolEvent(
        id="launch-call",
        function="start_job",
        arguments={"version": "a" * 64},
        result=json.dumps({"started": True, "job_id": "job-1"}),
        timestamp=start,
    )
    path = write_eval(
        tmp_path / "agent.eval",
        [sample("B", [ChatMessageUser(content="Agent label = B")], [launch], start, start)],
        start,
    )
    case = tmp_path / "case"
    init_case(
        case,
        CaseConfig(
            title="Native launch",
            trust_domains=(TrustDomain(id="runner", label="Runner"),),
            handle_patterns=(r"Agent label = (\S+)",),
        ),
    )
    add_witness(case, path, adapter="inspect-eval", trust_domain="runner")
    ingest(case)
    manifest = read_manifest(case)
    witness = Witness.model_validate(next(iter(manifest["witnesses"].values())))
    with Store(case, manifest) as store:
        assert [e["attrs"]["claimed_handle"] for e in store.entities("handle", native=False)] == [
            "B"
        ]
        launches = store.query("SELECT outcome FROM relations WHERE kind='launched'")
        assert launches == [{"outcome": "ambiguous"}]
        assert store.scalar("SELECT count(*) FROM relations WHERE kind='produced'") == 0
        refs = [Citation.model_validate(row) for row in store.query("SELECT * FROM citations")]
    assert {ref.locator_kind for ref in refs} == {"event", "message", "file"}
    for ref in refs:
        result = resolve_citation(case / witness.snapshot_path, witness, ref)
        assert isinstance(result, dict)
        if ref.locator_kind == "event":
            assert result["uuid"] == launch.uuid
        elif ref.locator_kind == "message":
            assert result["content"] == "Agent label = B"
    event_ref = next(ref for ref in refs if ref.locator_kind == "event")
    with pytest.raises(ValueError, match="fragment hash mismatch"):
        resolve_citation(
            case / witness.snapshot_path,
            witness,
            event_ref.model_copy(update={"content_sha256": "0" * 64}),
        )


@pytest.mark.parametrize(
    "tool_output,sandbox_output,expected",
    [
        ("REAL", "REAL\nextra", "supported"),
        ("fabricated", "REAL", "contradicted"),
        ("", "REAL", "not_assessable"),
    ],
)
def test_tool_sandbox_consistency_stays_in_runner_domain(
    tmp_path, tool_output, sandbox_output, expected
):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    tool = ToolEvent(
        id="call",
        function="bash",
        arguments={"cmd": "echo REAL"},
        result=tool_output,
        timestamp=start,
        completed=start + timedelta(seconds=1),
    )
    sandbox = SandboxEvent(
        action="exec",
        cmd="bash -c 'echo REAL'",
        result=0,
        output=sandbox_output,
        timestamp=start,
    )
    path = write_eval(
        tmp_path / "agent.eval",
        [
            sample(
                "B",
                [ChatMessageUser(content="Run a command")],
                [tool, sandbox],
                start,
                start + timedelta(seconds=2),
            )
        ],
        start,
    )
    case = tmp_path / "case"
    init_case(
        case,
        CaseConfig(title="Consistency", trust_domains=(TrustDomain(id="runner", label="Runner"),)),
    )
    add_witness(case, path, adapter="inspect-eval", trust_domain="runner")
    ingest(case)
    with Store(case, read_manifest(case)) as store:
        rows = store.query(
            "SELECT outcome, trust_domain_relation FROM relations WHERE kind='corroborated_by'"
        )
        assert rows == [{"outcome": expected, "trust_domain_relation": "same"}]


def test_unrelated_sandbox_execution_is_not_corroboration(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    tool = ToolEvent(
        id="call",
        function="bash",
        arguments={"cmd": "echo REAL"},
        result="REAL",
        timestamp=start,
        completed=start + timedelta(seconds=1),
    )
    matching = SandboxEvent(
        action="exec", cmd="echo REAL", result=0, output="REAL", timestamp=start
    )
    unrelated = SandboxEvent(
        action="exec",
        cmd="rm unrelated-file",
        result=1,
        output="permission denied",
        timestamp=start + timedelta(seconds=1.5),
    )
    path = write_eval(
        tmp_path / "agent.eval",
        [
            sample(
                "B",
                [ChatMessageUser(content="Handle: B")],
                [tool, matching, unrelated],
                start,
                start + timedelta(seconds=2),
            )
        ],
        start,
    )
    case = tmp_path / "case"
    init_case(
        case,
        CaseConfig(title="Pairs", trust_domains=(TrustDomain(id="runner", label="Runner"),)),
    )
    add_witness(case, path, adapter="inspect-eval", trust_domain="runner")
    ingest(case)
    with Store(case, read_manifest(case)) as store:
        by_uuid = {e["attrs"]["event_uuid"]: e["entity_id"] for e in store.entities("sandbox_exec")}
        rows = {
            r["object_id"]: r
            for r in store.query("SELECT * FROM relations WHERE kind='corroborated_by'")
        }
        assert rows[by_uuid[matching.uuid]]["outcome"] == "supported"
        assert rows[by_uuid[unrelated.uuid]]["outcome"] == "unmatched"
        citations = {c["citation_id"]: c["locator"] for c in store.query("SELECT * FROM citations")}
        for sandbox in (matching, unrelated):
            cited = {citations[c] for c in rows[by_uuid[sandbox.uuid]]["citation_ids"]}
            assert cited == {tool.uuid, sandbox.uuid}
        final = store.entities("transcript_final_event")
        assert len(final) == 1
        assert final[0]["attrs"]["event_type"] == "sandbox"
        assert final[0]["attrs"]["event_uuid"] == unrelated.uuid
        assert final[0]["attrs"]["events_considered"] == 3
        assert final[0]["time_upper"] == unrelated.timestamp.isoformat()
