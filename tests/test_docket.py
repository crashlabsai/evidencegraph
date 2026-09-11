"""Docket answers must state only what the acquired evidence shows."""

import json
from datetime import UTC, datetime, timedelta

from inspect_ai.event import SandboxEvent, ToolEvent
from inspect_ai.model import ChatMessageUser

from evidencegraph.case import add_witness, configuration, ingest, init_case
from evidencegraph.docket.answers import aggregate_outcome, fixture_exposure, lab_answers
from evidencegraph.lab.logs import sample, write_eval
from evidencegraph.manifest import read_manifest
from evidencegraph.provenance import sha256_text
from evidencegraph.schema import CaseConfig, ClockBound, Outcome, TrustDomain, TrustDomainRelation
from evidencegraph.store import Store

T0 = datetime(2026, 1, 1, tzinfo=UTC)
SECRET = "protected fixture bytes"
COMMITMENT = sha256_text(SECRET)


def ts(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def row(key, subkind, time, **attrs):
    return {
        "entity_id": key,
        "natural_key": key,
        "subkind": subkind,
        "witness_id": "registry",
        "citation_id": "ref-" + key,
        "time_lower": ts(time),
        "time_upper": ts(time),
        "attrs": attrs,
    }


def refresh(time=1, commitment: str | None = COMMITMENT):
    attrs = {"kind": "cache_refresh", "name": "release.txt", "source_route": "protected"}
    if commitment:
        attrs["payload_sha256"] = commitment
    return row("refresh", "refresh", time, **attrs)


def read(
    key, time, *, route="cache", status=200, payload: str | None = "ordinary public placeholder"
):
    return row(
        key,
        "read",
        time,
        route=route,
        name="release.txt",
        status=status,
        payload_sha256=sha256_text(payload) if payload is not None else None,
    )


class FakeStore:
    def __init__(self, produced=(), **entities):
        self.produced = list(produced)
        self.data = entities

    def entities(self, subkind, *, native=True):
        return self.data.get(subkind, [])

    def query(self, sql, params=None):
        return self.produced if "kind='produced'" in sql else []


def answers(store):
    return {a.question_id: a for a in lab_answers(store, CaseConfig(title="unit"), {})}


def test_aggregate_outcome_never_upgrades_unresolved_records():
    assert aggregate_outcome([]) == Outcome.NOT_ASSESSABLE
    assert aggregate_outcome(["unmatched", "unmatched"]) == Outcome.UNMATCHED
    assert aggregate_outcome(["unmatched", "ambiguous"]) == Outcome.AMBIGUOUS
    assert aggregate_outcome(["unmatched", "supported"]) == Outcome.SUPPORTED


def test_lq1_status_follows_attribution_evidence():
    record = row("r1", "registry_mutation", 0, name="x", namespace="lab", sha256="a")
    unmatched = {"subject_id": "r1", "object_id": "r1", "outcome": "unmatched", "method": "k"}
    lq1 = answers(FakeStore([unmatched], registry_mutation=[record]))["LQ1"]
    assert lq1.outcome == Outcome.UNMATCHED and lq1.gaps
    supported = {
        "subject_id": "r1",
        "object_id": "t1",
        "outcome": "supported",
        "method": "independent_receipt",
    }
    lq1 = answers(FakeStore([supported], registry_mutation=[record]))["LQ1"]
    assert lq1.outcome == Outcome.SUPPORTED
    assert any("declared assumption" in a for a in lq1.assumptions)


def test_lq8_reports_only_observed_parts():
    record = row("r1", "registry_mutation", 0, name="x", namespace="lab", sha256="a")
    # A post-refresh cache read of placeholder bytes, no denial anywhere.
    store = FakeStore(registry_mutation=[record], refresh=[refresh()], read=[read("c1", 2)])
    lq8 = answers(store)["LQ8"]
    assert lq8.outcome == Outcome.UNMATCHED
    assert "no protected-route denial observed" in lq8.headline
    assert "denied" not in lq8.headline
    assert lq8.numbers["protected_route_denials"] == []
    assert lq8.numbers["post_refresh_reads"][0]["matches_refresh_commitment"] is False
    # The served bytes match the registry's refresh commitment: exposure is supported.
    exposed = read("c2", 3, payload=SECRET)
    store = FakeStore(
        registry_mutation=[record], refresh=[refresh()], read=[read("c1", 2), exposed]
    )
    lq8 = answers(store)["LQ8"]
    assert lq8.outcome == Outcome.SUPPORTED
    assert lq8.headline.startswith("1 post-refresh cache reads served the refresh-committed bytes")
    assert fixture_exposure(store)["matched"] == [exposed]
    # An observed denial is counted and cited, never assumed.
    denial = read("d1", 0.5, route="protected", status=403, payload=None)
    lq8 = answers(
        FakeStore(registry_mutation=[record], refresh=[refresh()], read=[exposed, denial])
    )["LQ8"]
    assert "1 protected-route denials observed" in lq8.headline
    assert denial["citation_id"] in lq8.citation_ids
    # Without a refresh commitment the served bytes cannot be identified.
    lq8 = answers(
        FakeStore(registry_mutation=[record], refresh=[refresh(commitment=None)], read=[exposed])
    )["LQ8"]
    assert lq8.outcome == Outcome.NOT_ASSESSABLE
    assert "cannot be matched" in lq8.headline
    # A read before the refresh served whatever the cache held then.
    lq8 = answers(FakeStore(registry_mutation=[record], refresh=[refresh(time=5)], read=[exposed]))[
        "LQ8"
    ]
    assert lq8.outcome == Outcome.NOT_ASSESSABLE


def test_lq7_uses_the_final_recorded_transcript_event(tmp_path):
    # B's last tool call ends at 1s, the refresh is at 10s, but B's transcript records
    # a sandbox execution at 20s: the refresh preceded B's final event.
    tool = ToolEvent(
        id="call",
        function="bash",
        arguments={"cmd": "echo REAL"},
        result="REAL",
        timestamp=T0,
        completed=T0 + timedelta(seconds=1),
    )
    sandbox = SandboxEvent(
        action="exec", cmd="sleep 1", result=0, output="", timestamp=T0 + timedelta(seconds=20)
    )
    log = write_eval(
        tmp_path / "agent.eval",
        [sample("B", [ChatMessageUser(content="Handle: B")], [tool, sandbox], T0, T0)],
        T0,
    )
    public = tmp_path / "public"
    public.mkdir()
    (public / "ledger.jsonl").write_text(
        json.dumps(
            {
                "id": "event-000001",
                "namespace": "lab",
                "name": "x",
                "payload": "p",
                "sha256": sha256_text("p"),
                "ts": ts(0.5),
            }
        )
        + "\n"
    )
    (public / "registry-refresh.json").write_text(
        json.dumps([{"kind": "cache_refresh", "ts": ts(10)}])
    )
    case = tmp_path / "case"
    init_case(
        case,
        CaseConfig(
            title="LQ7",
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
    add_witness(case, public, adapter="lab-public", trust_domain="registry")
    add_witness(case, log, adapter="inspect-eval", trust_domain="runner")
    ingest(case)
    manifest = read_manifest(case)
    with Store(case, manifest) as store:
        lq7 = {a.question_id: a for a in lab_answers(store, configuration(case), manifest)}["LQ7"]
    assert lq7.outcome == Outcome.CONTRADICTED
    assert lq7.numbers["delta_seconds"] == -10
    assert lq7.numbers["final_event"]["event_type"] == "sandbox"
    assert lq7.numbers["final_event"]["event_uuid"] == sandbox.uuid
    assert lq7.numbers["final_event"]["events_considered"] == 2
    assert "(sandbox)" in lq7.headline
