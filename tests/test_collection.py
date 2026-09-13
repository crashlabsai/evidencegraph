"""Collector truth must remain authoritative when durable storage fails."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from evidencegraph.lab.runtime import collection
from evidencegraph.lab.runtime.collection import (
    MAX_NAME_BYTES,
    MAX_PAYLOAD_BYTES,
    CollectionClosedError,
    CollectionPersistenceError,
    Collector,
    UnauthenticatedConnectionError,
    Writer,
)


@pytest.fixture
def collector(tmp_path):
    return Collector(
        tmp_path / "truth.jsonl", (Writer("agent-A", "A", 0), Writer("agent-B", "B", 1))
    )


@pytest.mark.parametrize("operation", ["create", "overwrite", "remove"])
def test_persistence_failure_never_publishes_and_permanently_closes(
    collector, monkeypatch, operation
):
    connection = collector.connection(0)
    if operation != "create":
        collector.write(connection, "registry", "entry", "original")
    before_visible = collector.visible()
    before_truth = collector.host_truth()
    append = collection.append_json

    def fail_mutation(path, value):
        if value["kind"] == "mutation":
            raise OSError("simulated disk full")
        append(path, value)

    monkeypatch.setattr(collection, "append_json", fail_mutation)
    with pytest.raises(CollectionPersistenceError, match="collector closed") as failure:
        if operation == "remove":
            collector.remove(connection, "registry", "entry")
        else:
            collector.write(connection, "registry", "entry", "new")
    assert isinstance(failure.value.__cause__, OSError)
    assert collector.visible() == before_visible
    assert collector.host_truth() == before_truth
    monkeypatch.setattr(collection, "append_json", append)
    with pytest.raises(CollectionClosedError):
        collector.write(connection, "registry", "later", "must not land")
    assert collector.visible() == before_visible
    assert collector.host_truth() == before_truth


def test_partial_durable_record_also_fails_closed(collector, monkeypatch):
    connection = collector.connection(0)
    append = collection.append_json

    def partial_append(path, value):
        if value["kind"] == "mutation":
            with path.open("a") as stream:
                stream.write('{"kind": "mutation",')
            raise OSError("short write")
        append(path, value)

    monkeypatch.setattr(collection, "append_json", partial_append)
    with pytest.raises(CollectionPersistenceError):
        collector.write(connection, "registry", "entry", "missing")
    assert collector.visible() == []
    assert collector.host_truth() == ()
    with pytest.raises(CollectionClosedError):
        collector.write(connection, "registry", "another", "missing")


def test_unknown_connection_is_logged_without_changing_truth(collector):
    with pytest.raises(UnauthenticatedConnectionError):
        collector.write(object(), "registry", "entry", "forged")
    assert collector.visible() == []
    assert collector.host_truth() == ()
    rows = [json.loads(line) for line in collector._truth_path.read_text().splitlines()]
    assert rows[-1]["reason"] == "unauthenticated"
    # A rejected attempt must not prevent a later authenticated write.
    assert collector.write(collector.connection(0), "registry", "entry", "real").seq == 1


@pytest.mark.parametrize(
    ("namespace", "name", "payload"),
    [
        ("a/b", "entry", "value"),
        ("registry", "..", "value"),
        ("registry", "a/b", "value"),
        ("registry", "\u00e9" * (MAX_NAME_BYTES // 2 + 1), "value"),
        ("registry", "entry", "\u00e9" * (MAX_PAYLOAD_BYTES // 2 + 1)),
    ],
)
def test_invalid_write_is_rejected_without_consuming_sequence(collector, namespace, name, payload):
    with pytest.raises(ValueError):
        collector.write(collector.connection(0), namespace, name, payload)
    assert collector.host_truth() == ()
    assert collector.write(collector.connection(0), "registry", "entry", "valid").seq == 1


def test_stop_is_idempotent_and_rejects_later_writes_and_removes(collector):
    collector.write(collector.connection(0), "registry", "entry", "original")
    before = collector.host_truth()
    collector.stop()
    collector.stop()
    with pytest.raises(CollectionClosedError):
        collector.write(collector.connection(0), "registry", "entry", "changed")
    with pytest.raises(CollectionClosedError):
        collector.remove(collector.connection(0), "registry", "entry")
    assert collector.host_truth() == before
    rows = [json.loads(line) for line in collector._truth_path.read_text().splitlines()]
    assert sum(row["kind"] == "stop" for row in rows) == 1


def test_overwrite_remove_and_background_truth_chain(collector):
    first = collector.write(collector.connection(0), "registry", "entry", "original")
    second = collector.write(
        collector.connection(1), "registry", "entry", "original", background=True
    )
    removed = collector.remove(collector.connection(1), "registry", "entry", background=True)
    assert [row.kind for row in collector.host_truth()] == ["create", "overwrite", "remove"]
    assert second.prior_sha256 == first.payload_sha256
    assert removed.prior_sha256 == second.payload_sha256
    assert removed.payload_sha256 is None
    assert second.writer_agent_id == "agent-B" and second.background
    assert collector.visible() == []
    with pytest.raises(ValueError, match="absent"):
        collector.remove(collector.connection(0), "registry", "entry")
    assert collector.write(collector.connection(0), "registry", "entry", "again").seq == 4


def test_concurrent_writes_have_one_durable_total_order(collector):
    def write(index):
        return collector.write(collector.connection(index % 2), "registry", "entry", str(index))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(32)))
    truth = collector.host_truth()
    assert [row.seq for row in truth] == list(range(1, 33))
    assert all(
        later.prior_sha256 == earlier.payload_sha256
        for earlier, later in zip(truth[:-1], truth[1:], strict=True)
    )
    assert collector.visible()[0]["payload"] == truth[-1].payload
    durable = [json.loads(line) for line in collector._truth_path.read_text().splitlines()]
    assert [row["seq"] for row in durable if row["kind"] == "mutation"] == list(range(1, 33))


def test_duplicate_writer_identity_or_slot_is_rejected(tmp_path):
    first = Writer("agent-A", "A", 0)
    with pytest.raises(ValueError, match="slots"):
        Collector(tmp_path / "slots.jsonl", (first, replace(first, agent_id="other")))
    with pytest.raises(ValueError, match="agent ids"):
        Collector(tmp_path / "ids.jsonl", (first, replace(first, slot=1)))
    assert list(tmp_path.iterdir()) == []
