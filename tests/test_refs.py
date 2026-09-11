import pytest

from evidencegraph.acquire import acquire
from evidencegraph.provenance import sha256_bytes
from evidencegraph.refs import csv_rows, json_citation, jsonl_rows, resolve_citation


def test_exact_line_rules(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'{"a":1}\r\n\n{"a":2}\n{"a":3}')
    rows = list(jsonl_rows(path, "w-test"))
    assert [ref.locator for _, ref in rows] == ["1", "3", "4"]
    assert [ref.content_sha256 for _, ref in rows] == [
        sha256_bytes(b'{"a":1}'),
        sha256_bytes(b'{"a":2}'),
        sha256_bytes(b'{"a":3}'),
    ]


@pytest.mark.parametrize("content", [b'{"a":1,"a":2}', b'{"a":NaN}', b"[]"])
def test_strict_jsonl(tmp_path, content):
    path = tmp_path / "bad.jsonl"
    path.write_bytes(content)
    with pytest.raises(ValueError):
        list(jsonl_rows(path, "w-test"))


def test_csv_multiline_locator(tmp_path):
    path = tmp_path / "rows.csv"
    path.write_text('key,value\na,"one\ntwo"\nb,three\n')
    rows = list(csv_rows(path, "w-test"))
    assert [ref.locator for _, ref in rows] == ["1", "2"]
    assert rows[0][0]["value"] == "one\ntwo"


def test_json_citation_resolves_nested_values_and_rejects_wrong_fragment(tmp_path):
    path = tmp_path / "reference.json"
    path.write_text('{"values":[{"name":"example"}]}')
    witness = acquire(
        tmp_path / "case",
        path,
        adapter="reference-list",
        trust_domain="reference",
        kind="reference_list",
    )
    snapshot = tmp_path / "case" / witness.snapshot_path
    ref = json_citation(witness.witness_id, "$.values[0].name", "example")
    assert resolve_citation(snapshot, witness, ref) == "example"
    wrong = ref.model_copy(update={"content_sha256": "0" * 64})
    with pytest.raises(ValueError, match="fragment hash mismatch"):
        resolve_citation(snapshot, witness, wrong)
