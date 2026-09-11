from datetime import UTC, datetime, timedelta

import pytest

from evidencegraph.provenance import sha256_text
from evidencegraph.reconcile.engine import reconcile_record, reconcile_registry
from evidencegraph.reconcile.substrates.registry import RegistrySubstrate
from evidencegraph.schema import CaseConfig, ClockBound, TrustDomain, TrustDomainRelation

T0 = datetime(2026, 1, 1, tzinfo=UTC)
TOKEN = "issued-to-the-caller"


def ts(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def entity(
    key,
    *,
    name="object",
    payload="contents",
    time: float = 0,
    end: float | None = None,
    namespace="arbitrary-api",
    witness="transcript",
    token=None,
    commitment=None,
):
    receipt = {
        "accepted": True,
        "name": name,
        "namespace": namespace,
        "sha256": sha256_text(payload),
        "event_id": "event-1",
    }
    if token is not None:
        receipt["receipt_token"] = token
    attrs = {
        "name": name,
        "namespace": namespace,
        "payload": payload,
        "sha256": sha256_text(payload),
        "function": "registry_write",
        "arguments": {"name": name, "payload": payload},
        "receipt": receipt,
    }
    if commitment is not None:
        attrs["receipt_token_sha256"] = commitment
    return {
        "entity_id": key,
        "natural_key": "event-1",
        "witness_id": witness,
        "citation_id": "ref-" + key,
        "time_lower": ts(time),
        "time_upper": ts(time + 0.1 if end is None else end),
        "attrs": attrs,
    }


def record(**overrides):
    return entity("record", witness="registry", **overrides)


def run(rec, actions, bound: float | None = 1, authentic=frozenset()):
    return reconcile_record(
        rec,
        actions,
        substrate=RegistrySubstrate(),
        bound=bound,
        domain="independent",
        authentic=authentic,
    )


def test_bound_receipt_is_supported():
    row = run(record(commitment=sha256_text(TOKEN)), [entity("tool", token=TOKEN)])
    assert row.outcome == "supported" and row.candidates == ("tool",)
    assert row.method == "independent_receipt_binding"


def test_copied_receipt_without_binding_or_authenticity_stays_ambiguous():
    # Every public field of a receipt can be copied from the ledger; only the token
    # or a declared authentic-records domain separates a copy from the caller's own.
    row = run(record(), [entity("tool")])
    assert row.outcome == "ambiguous" and row.method == "attribution_unbound"
    row = run(record(commitment=sha256_text(TOKEN)), [entity("tool")])
    assert row.outcome == "ambiguous" and row.method == "receipt_binding_missing"


def test_declared_authentic_records_promote_an_unbound_match():
    row = run(record(), [entity("tool")], authentic=frozenset({"transcript"}))
    assert row.outcome == "supported" and row.method == "independent_receipt"
    # The declaration is per witness, not global.
    row = run(record(), [entity("tool")], authentic=frozenset({"other"}))
    assert row.outcome == "ambiguous"


def test_token_mismatch_is_contradicted_even_when_declared_authentic():
    rec = record(commitment=sha256_text(TOKEN))
    row = run(rec, [entity("tool", token="forged")], authentic=frozenset({"transcript"}))
    assert row.outcome == "contradicted" and row.method == "receipt_binding_mismatch"
    # A forged token beside a genuine one leaves the genuine one alone.
    row = run(rec, [entity("bad", token="forged"), entity("good", token=TOKEN)])
    assert row.outcome == "supported" and row.object_id == "good"


def test_r3_happens_before_payload_separation():
    row = run(record(), [entity("tool", time=10)])
    assert row.outcome == "contradicted" and row.method == "contradiction_R3"


def test_completed_call_cannot_produce_a_later_mutation():
    # A synchronous write that returned at 1s cannot explain a mutation a day later.
    row = run(record(time=86400), [entity("tool", time=0, end=1, token=TOKEN)])
    assert row.outcome == "contradicted" and row.method == "execution_window_disjoint"
    # Overlap within the bound is enough.
    row = run(
        record(time=1.5, commitment=sha256_text(TOKEN)),
        [entity("tool", time=0, end=1, token=TOKEN)],
    )
    assert row.outcome == "supported"


def test_listing_without_known_creation_time_keeps_earlier_candidates():
    rec = record(commitment=sha256_text(TOKEN), time=10)
    rec["time_lower"] = None
    row = run(rec, [entity("tool", time=0, end=1, token=TOKEN)])
    assert row.outcome == "supported"


def test_version_exclusion_and_duplicate_ids():
    rec = record()
    assert run(rec, [entity("other", payload="old")]).outcome == "unmatched"
    with pytest.raises(ValueError, match="duplicate"):
        run(rec, [entity("same"), entity("same")])


def test_two_receipts_remain_ambiguous_and_absent_clocks_abstain():
    assert run(record(), [entity("one"), entity("two")]).outcome == "ambiguous"
    assert run(record(), [entity("one")], bound=None).outcome == "not_assessable"
    candidate = entity("one")
    candidate["time_upper"] = None
    assert run(record(), [candidate]).outcome == "not_assessable"


def test_late_candidate_cannot_win_and_namespaces_do_not_alias():
    rec = record(commitment=sha256_text(TOKEN))
    row = run(rec, [entity("late", time=10, token=TOKEN), entity("early", time=-0.5, token=TOKEN)])
    assert row.object_id == "early"
    assert run(record(namespace="cache/x"), [entity("one", namespace="x")]).outcome == "unmatched"


def test_launch_never_produces_and_argument_receipt_mismatch_refused():
    launch = entity("launch")
    launch["attrs"]["function"] = "start_job"
    assert run(record(), [launch]).outcome == "unmatched"
    bad = entity("bad")
    bad["attrs"]["arguments"]["name"] = "different"
    assert run(record(), [bad]).outcome == "unmatched"


def test_unknown_trust_never_promoted():
    row = reconcile_record(
        record(commitment=sha256_text(TOKEN)),
        [entity("tool", token=TOKEN)],
        substrate=RegistrySubstrate(),
        bound=1,
    )
    assert row.outcome == "ambiguous"


def test_idempotent_2b_is_opt_in_for_a_substrate():
    # A listing observed at 10s shows the key; creation time itself is unknown.
    rec = record(time=10, commitment=sha256_text(TOKEN))
    rec["time_lower"] = None
    rec["attrs"]["idempotent_creation"] = True
    first, second = entity("first", time=0, token=TOKEN), entity("second", time=5, token=TOKEN)
    assert run(rec, [first, second]).object_id == "first"
    rec["attrs"]["idempotent_creation"] = False
    assert run(rec, [first, second]).outcome == "ambiguous"


@pytest.mark.parametrize("bound", [-1, float("inf"), float("nan")])
def test_invalid_bounds(bound):
    with pytest.raises(ValueError):
        run(record(), [], bound)


class FakeStore:
    def __init__(self, **entities):
        self.data = entities

    def entities(self, subkind):
        return self.data.get(subkind, [])


CONFIG = CaseConfig(
    title="absence",
    trust_domains=(
        TrustDomain(
            id="runner", label="runner", related_to={"registry": TrustDomainRelation.INDEPENDENT}
        ),
        TrustDomain(id="registry", label="registry"),
    ),
    clock_bounds=(ClockBound(clock_a="runner", clock_b="container", bound_seconds=1),),
)
MANIFEST = {
    "witnesses": {
        "transcript": {"trust_domain": "runner"},
        "registry": {"trust_domain": "registry"},
    }
}


def population(start, stop):
    row = entity("population", witness="registry")
    row["attrs"] = {
        "namespace": "arbitrary-api",
        "complete": True,
        "event_ids": [],
        "count": 0,
        "started_at": ts(start),
        "stopped_at": ts(stop),
    }
    return row


def absence(action_start, action_end, *, bound, capture=(0, 10)):
    rows = list(
        reconcile_registry(
            FakeStore(
                tool_event=[entity("claim", time=action_start, end=action_end)],
                population=[population(*capture)],
            ),
            CONFIG.model_copy(update={"clock_bounds": ()}),
            MANIFEST,
            bound,
        )
    )
    assert [r.method for _, r in rows] == ["complete_registry_absence"]
    return rows[0][1].outcome


def test_absence_needs_the_whole_widened_tool_window_inside_capture():
    # Capture stops at 10s; a call still running until 20s may have written at 19s.
    assert absence(9, 20, bound=1) == "not_assessable"
    # No declared bound: the windows cannot be related.
    assert absence(5, 6, bound=None) == "not_assessable"
    # A 5s bound pushes a call ending at 9s past the end of capture.
    assert absence(8, 9, bound=5) == "not_assessable"
    # Fully inside capture with the bound applied on both sides.
    assert absence(3, 4, bound=1) == "contradicted"
    assert absence(0.5, 4, bound=1) == "not_assessable"


def test_matched_claims_are_checked_for_binding():
    rec = record(commitment=sha256_text(TOKEN))
    rows = list(
        reconcile_registry(
            FakeStore(
                tool_event=[entity("copied"), entity("forged", token="wrong")],
                registry_mutation=[rec],
            ),
            CONFIG,
            MANIFEST,
            None,
        )
    )
    by_subject = {r.subject_id: r for _, r in rows if r.kind == "executed"}
    assert by_subject["copied"].outcome == "ambiguous"
    assert by_subject["copied"].method == "receipt_binding_missing"
    assert by_subject["forged"].outcome == "contradicted"
    assert by_subject["forged"].method == "receipt_binding_mismatch"
    produced = next(r for _, r in rows if r.kind == "produced")
    assert produced.outcome == "ambiguous" and produced.candidates == ("copied",)
