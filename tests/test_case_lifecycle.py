"""Witness identity and derived-result freshness across the case lifecycle."""

import json
import shutil
from datetime import UTC, datetime, timedelta

import pytest
from inspect_ai.event import ToolEvent
from inspect_ai.model import ChatMessageUser

from evidencegraph.case import add_witness, ingest, init_case
from evidencegraph.docket.render import render
from evidencegraph.export.bagit import export_bundle, verify_bundle
from evidencegraph.lab.logs import sample, write_eval
from evidencegraph.manifest import read_manifest
from evidencegraph.provenance import sha256_text
from evidencegraph.reconcile.engine import reconcile_case
from evidencegraph.schema import CaseConfig, ClockBound, TrustDomain, TrustDomainRelation
from evidencegraph.store import Store
from evidencegraph.validate.score import validate_case

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_identical_empty_logs_are_separate_observations(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("registry-reads.json", "registry-refresh.json"):
        (source / name).write_text("[]")
    case = tmp_path / "case"
    init_case(
        case,
        CaseConfig(title="Empty logs", trust_domains=(TrustDomain(id="registry", label="R"),)),
    )
    added = add_witness(case, source, adapter="lab-public", trust_domain="registry")
    assert len(set(added)) == 2
    witnesses = read_manifest(case)["witnesses"]
    assert {w["kind"] for w in witnesses.values()} == {"read_log", "refresh_log"}
    assert len({w["sha256"] for w in witnesses.values()}) == 1
    assert set(ingest(case).values()) == {"ingested"}
    # Adding the same observation again is a no-op, not a conflict.
    assert add_witness(case, source, adapter="lab-public", trust_domain="registry") == added


def test_identical_bytes_cannot_be_two_independent_witnesses(tmp_path):
    source = tmp_path / "a" / "registry-reads.json"
    source.parent.mkdir()
    source.write_text("[]")
    twin = tmp_path / "b" / "registry-refresh.json"
    twin.parent.mkdir()
    shutil.copyfile(source, twin)
    case = tmp_path / "case"
    init_case(
        case,
        CaseConfig(
            title="Twins",
            trust_domains=(
                TrustDomain(
                    id="registry", label="R", related_to={"runner": TrustDomainRelation.INDEPENDENT}
                ),
                TrustDomain(id="runner", label="T"),
            ),
        ),
    )
    add_witness(case, source, adapter="lab-public", trust_domain="registry")
    with pytest.raises(ValueError, match="two trust domains"):
        add_witness(case, twin, adapter="lab-public", trust_domain="runner")


def one_write_case(root):
    case = root / "case"
    receipt = {
        "accepted": True,
        "namespace": "lab",
        "name": "note.txt",
        "payload": "hello",
        "sha256": sha256_text("hello"),
        "event_id": "event-000001",
        "receipt_token": "token-1",
    }
    ledger = root / "ledger.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "id": receipt["event_id"],
                "ts": (T0 + timedelta(seconds=0.5)).isoformat(),
                "receipt_token_sha256": sha256_text("token-1"),
                **{k: receipt[k] for k in ("namespace", "name", "payload", "sha256")},
            }
        )
        + "\n"
    )
    event = ToolEvent(
        id="call-1",
        function="registry_write",
        arguments={"name": "note.txt", "payload": "hello"},
        result=json.dumps(receipt),
        timestamp=T0,
        completed=T0 + timedelta(seconds=1),
    )
    log = write_eval(
        root / "agent.eval",
        [sample("B", [ChatMessageUser(content="Handle: B")], [event], T0, T0)],
        T0,
    )
    init_case(
        case,
        CaseConfig(
            title="Freshness",
            trust_domains=(
                TrustDomain(
                    id="registry", label="R", related_to={"runner": TrustDomainRelation.INDEPENDENT}
                ),
                TrustDomain(id="runner", label="T"),
            ),
            clock_bounds=(ClockBound(clock_a="runner", clock_b="container", bound_seconds=1),),
        ),
    )
    add_witness(case, ledger, adapter="lab-public", trust_domain="registry")
    add_witness(case, log, adapter="inspect-eval", trust_domain="runner")
    ingest(case)
    reconcile_case(case, "registry")
    return case


def outcomes(case):
    with Store(case, read_manifest(case)) as store:
        return store.query(
            "SELECT outcome,trust_domain_relation FROM relations WHERE kind='produced'"
        )


def test_changed_trust_assumptions_invalidate_derived_results(tmp_path):
    case = one_write_case(tmp_path)
    assert outcomes(case) == [{"outcome": "supported", "trust_domain_relation": "independent"}]
    render(case)
    export_bundle(case, tmp_path / "bundle")
    config = json.loads((case / "case.json").read_text())
    config["trust_domains"][0]["related_to"]["runner"] = "same"
    (case / "case.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="configuration changed"):
        render(case)
    with pytest.raises(ValueError, match="configuration changed"):
        export_bundle(case, tmp_path / "stale-bundle")
    with pytest.raises(ValueError, match="configuration changed"):
        reconcile_case(case, "registry")
    with pytest.raises(ValueError, match="configuration changed"):
        validate_case(case, tmp_path / "no-truth")
    assert not (tmp_path / "stale-bundle").exists()
    # The earlier bundle carries its own configuration and stays internally consistent.
    assert verify_bundle(tmp_path / "bundle", recompute=True)["verified"]
    assert set(ingest(case).values()) == {"ingested"}
    reconcile_case(case, "registry")
    assert outcomes(case) == [{"outcome": "ambiguous", "trust_domain_relation": "same"}]
    render(case)
    docket = json.loads((case / "docket.json").read_text())
    assert {a["question_id"]: a["outcome"] for a in docket["answers"]}["LQ1"] == "ambiguous"
