from datetime import UTC, datetime, timedelta

import pytest

from evidencegraph.provenance import sha256_text
from evidencegraph.reconcile.engine import reconcile_record
from evidencegraph.reconcile.substrates.registry import RegistrySubstrate

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def entity(
    key,
    *,
    name="object",
    payload="contents",
    time=0,
    namespace="arbitrary-api",
    witness="transcript",
):
    return {
        "entity_id": key,
        "natural_key": "event-1",
        "witness_id": witness,
        "citation_id": "ref-" + key,
        "time_lower": (T0 + timedelta(seconds=time)).isoformat(),
        "time_upper": (T0 + timedelta(seconds=time + 0.1)).isoformat(),
        "attrs": {
            "name": name,
            "namespace": namespace,
            "payload": payload,
            "sha256": sha256_text(payload),
            "function": "registry_write",
            "arguments": {"name": name, "payload": payload},
            "receipt": {
                "accepted": True,
                "name": name,
                "namespace": namespace,
                "sha256": sha256_text(payload),
                "event_id": "event-1",
            },
        },
    }


def run(record, actions, bound: float | None = 1):
    return reconcile_record(
        record, actions, substrate=RegistrySubstrate(), bound=bound, domain="independent"
    )


def test_unique_exact_receipt_is_supported():
    row = run(entity("record", witness="registry"), [entity("tool")])
    assert row.outcome == "supported" and row.candidates == ("tool",)


def test_r3_happens_before_payload_separation():
    row = run(entity("record"), [entity("tool", time=10)])
    assert row.outcome == "contradicted" and row.method == "contradiction_R3"


def test_version_exclusion_and_duplicate_ids():
    record = entity("record")
    assert run(record, [entity("other", payload="old")]).outcome == "unmatched"
    with pytest.raises(ValueError, match="duplicate"):
        run(record, [entity("same"), entity("same")])


def test_two_receipts_remain_ambiguous_and_absent_clocks_abstain():
    assert run(entity("record"), [entity("one"), entity("two")]).outcome == "ambiguous"
    assert run(entity("record"), [entity("one")], bound=None).outcome == "not_assessable"


def test_late_candidate_cannot_win_and_namespaces_do_not_alias():
    assert (
        run(entity("record"), [entity("late", time=10), entity("early", time=-10)]).object_id
        == "early"
    )
    assert (
        run(entity("record", namespace="cache/x"), [entity("one", namespace="x")]).outcome
        == "unmatched"
    )


def test_launch_never_produces_and_argument_receipt_mismatch_refused():
    launch = entity("launch")
    launch["attrs"]["function"] = "start_job"
    assert run(entity("record"), [launch]).outcome == "unmatched"
    bad = entity("bad")
    bad["attrs"]["arguments"]["name"] = "different"
    assert run(entity("record"), [bad]).outcome == "unmatched"


def test_unknown_trust_never_promoted():
    row = reconcile_record(
        entity("record"), [entity("tool")], substrate=RegistrySubstrate(), bound=1
    )
    assert row.outcome == "ambiguous"


def test_idempotent_2b_is_opt_in_for_a_substrate():
    record = entity("record", time=10)
    record["attrs"]["idempotent_creation"] = True
    assert run(record, [entity("first", time=0), entity("second", time=5)]).object_id == "first"
    record["attrs"]["idempotent_creation"] = False
    assert run(record, [entity("first", time=0), entity("second", time=5)]).outcome == "ambiguous"


@pytest.mark.parametrize("bound", [-1, float("inf"), float("nan")])
def test_invalid_bounds(bound):
    with pytest.raises(ValueError):
        run(entity("record"), [], bound)
