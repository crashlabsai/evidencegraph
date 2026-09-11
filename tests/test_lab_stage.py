import json

import pytest

from evidencegraph.case import add_witness, ingest, init_case
from evidencegraph.coverage.estimate import estimate
from evidencegraph.docket.render import render
from evidencegraph.lab.stage import construct_stage, leak_check
from evidencegraph.manifest import read_manifest
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
    with Store(case, read_manifest(case)) as store:
        assert (
            store.scalar(
                "SELECT count(*) FROM relations WHERE kind='produced' AND outcome='unmatched'"
            )
            == 3
        )
        assert store.scalar("SELECT count(*) FROM relations WHERE kind='launched'") == 0
    if seed == 0:
        render(case)
        docket = json.loads((case / "docket.json").read_text())
        assert docket["validation"]["produced"]["confident_errors"] == 0
        assert {a["question_id"]: a for a in docket["answers"]}["LQ2"][
            "outcome"
        ] == "not_assessable"


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
