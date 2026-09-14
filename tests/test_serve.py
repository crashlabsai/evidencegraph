"""The local viewer is read-only, host-checked, and reports verification failures visibly."""

import json
import threading
import urllib.error
import urllib.request
from typing import Any

import pytest

from evidencegraph.docket.render import render
from evidencegraph.export.bagit import export_bundle
from evidencegraph.manifest import read_manifest
from evidencegraph.ui.server import CaseView, make_server, resolve_root


def start(path):
    server = make_server(path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def viewer(wiki_case):
    server, base = start(wiki_case)
    yield base
    server.shutdown()
    server.server_close()


def call(url, *, method="GET", body=None, host=None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, method=method, data=data)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if host:
        request.add_header("Host", host)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read()
            content_type = response.headers.get("Content-Type", "")
            status = response.status
    except urllib.error.HTTPError as exc:
        payload, content_type, status = exc.read(), exc.headers.get("Content-Type", ""), exc.code
    return status, json.loads(payload) if content_type.startswith("application/json") else payload


def test_shell_static_files_and_case_summary(viewer):
    status, html = call(viewer + "/")
    assert status == 200 and b"Evidencegraph" in html
    assert call(viewer + "/static/app.js")[0] == 200
    assert call(viewer + "/static/../server.py")[0] == 404
    assert call(viewer + "/static/%2e%2e/server.py")[0] == 404
    assert call(viewer + "/nothing")[0] == 404
    status, summary = call(viewer + "/api/case")
    assert status == 200
    assert summary["title"] == "Synthetic wiki" and summary["bundle"] is False
    assert summary["stale"] == [] and summary["witness_count"] == 5
    assert summary["stale_analyzer"] == [] and summary["analyzer_build_id"].startswith("0.1.0+")
    assert all("exclusive_receipt_tokens" in d for d in summary["config"]["trust_domains"])
    steps = {step["step"]: step for step in summary["pipeline"]}
    assert steps["ingest"]["done"] and not steps["docket"]["done"]
    assert steps["docket"]["command"].startswith("eg docket ")


def test_docket_is_indexed_once_rendered(viewer, wiki_case):
    status, error = call(viewer + "/api/docket")
    assert status == 404 and "eg docket" in error["error"]
    render(wiki_case)
    status, data = call(viewer + "/api/docket")
    assert status == 200 and data["verified"] is True
    assert data["relevant"] == [f"DQ{i}" for i in range(1, 9)]
    answers = {a["question_id"]: a for a in data["docket"]["answers"]}
    for ref in answers["DQ1"]["citation_ids"]:
        assert data["citation_index"][ref]["locator_kind"] == "jsonl_line"
        assert data["citation_index"][ref]["witness_id"] in data["witness_index"]
    assert data["questions"]["DQ1"]["label"] == "Activity"
    assert set(data["outcome_meanings"]) == {
        "supported",
        "ambiguous",
        "contradicted",
        "unmatched",
        "not_assessable",
    }
    status, report = call(viewer + "/api/reports/DOCKET.md")
    assert status == 200 and report.startswith(b"# Synthetic wiki")
    assert call(viewer + "/api/reports/../case.json")[0] == 404


def test_citation_resolves_and_reports_tampering(viewer, wiki_case):
    status, entities = call(viewer + "/api/entities?subkind=revision&limit=1")
    assert status == 200 and entities["total"] == 12
    citation_id = entities["rows"][0]["citation_id"]
    status, cited = call(viewer + f"/api/citations/{citation_id}")
    assert status == 200 and cited["verified"] is True and cited["error"] is None
    assert cited["source_row"]["rev_id"] == entities["rows"][0]["natural_key"]
    assert entities["rows"][0]["entity_id"] in {e["entity_id"] for e in cited["entities"]}
    assert cited["command"].endswith(citation_id)
    assert call(viewer + "/api/citations/ref-unknown")[0] == 404

    snapshot = wiki_case / cited["witness"]["snapshot_path"]
    snapshot.chmod(0o644)
    snapshot.write_bytes(snapshot.read_bytes().replace(b"shared content 1", b"shared content X"))
    status, tampered = call(viewer + f"/api/citations/{citation_id}")
    assert status == 200 and tampered["verified"] is False
    assert "hash mismatch" in tampered["error"] and tampered["source_row"] is None
    status, witness = call(viewer + f"/api/witnesses/{cited['witness']['witness_id']}")
    assert status == 200 and witness["verified"] is False and "hash mismatch" in witness["error"]


def test_witnesses_relations_and_entities_browse_the_graph(viewer):
    status, witnesses = call(viewer + "/api/witnesses")
    assert status == 200 and len(witnesses["witnesses"]) == 5
    assert all(w["ingested"] for w in witnesses["witnesses"])
    assert "archive" in witnesses["trust_domains"]
    revisions = next(w for w in witnesses["witnesses"] if w["filename"] == "revisions.jsonl")
    status, detail = call(viewer + f"/api/witnesses/{revisions['witness_id']}")
    assert status == 200 and detail["verified"] is True
    assert {row["subkind"]: row["rows"] for row in detail["entities"]}["revision"] == 12
    assert call(viewer + "/api/witnesses/w-missing")[0] == 404

    status, relations = call(viewer + "/api/relations?limit=5")
    assert status == 200 and relations["total"] > 5 and len(relations["rows"]) == 5
    kind = relations["facets"]["kind"][0]["value"]
    status, filtered = call(viewer + f"/api/relations?kind={kind}&limit=1000")
    assert status == 200 and {r["kind"] for r in filtered["rows"]} == {kind}
    assert filtered["total"] == len(filtered["rows"])
    row = filtered["rows"][0]
    assert row["subject_subkind"] and row["object_subkind"]
    for ref in row["citation_ids"]:
        assert ref in filtered["citation_index"]

    status, entity = call(viewer + f"/api/entities/{row['subject_id']}")
    assert status == 200 and entity["entity"]["entity_id"] == row["subject_id"]
    assert isinstance(entity["entity"]["attrs"], dict)
    assert any(r["relation_id"] == row["relation_id"] for r in entity["outgoing"])
    assert entity["citation"]["citation_id"] == entity["entity"]["citation_id"]
    assert call(viewer + "/api/entities/e-missing")[0] == 404
    assert call(viewer + "/api/entities?limit=x")[0] == 400
    status, searched = call(viewer + "/api/entities?q=Page0&subkind=revision")
    assert status == 200 and searched["total"] == 4


def test_query_is_read_only_and_limited(viewer):
    status, tables = call(viewer + "/api/tables")
    assert status == 200 and {t["name"] for t in tables["tables"]} >= {"entities", "relations"}
    assert tables["examples"]
    status, result = call(
        viewer + "/api/query", method="POST", body={"sql": "SELECT count(*) AS n FROM revisions"}
    )
    assert status == 200 and result["rows"] == [{"n": 12}] and result["truncated"] is False
    status, result = call(
        viewer + "/api/query", method="POST", body={"sql": "SELECT * FROM revisions", "limit": 2}
    )
    assert status == 200 and len(result["rows"]) == 2 and result["truncated"] is True
    assert (
        call(viewer + "/api/query", method="POST", body={"sql": "DELETE FROM entities"})[0] == 400
    )
    status, error = call(
        viewer + "/api/query", method="POST", body={"sql": "SELECT read_text('/etc/passwd')"}
    )
    assert status == 400 and error["error"]
    assert call(viewer + "/api/query", method="POST", body={"sql": "SELECT nope FROM"})[0] == 400
    assert call(viewer + "/api/query", method="POST", body={"nope": 1})[0] == 400
    assert call(viewer + "/api/query")[0] == 405
    assert call(viewer + "/api/case", method="POST", body={})[0] == 405


def test_foreign_host_header_is_rejected(viewer):
    assert call(viewer + "/api/case", host="evil.example")[0] == 403
    assert call(viewer + "/api/case", host="localhost:1")[0] == 200


def test_changed_declarations_are_reported_as_stale(viewer, wiki_case):
    path = wiki_case / "case.json"
    config = json.loads(path.read_text())
    config["family_confidence"] = 0.5
    path.write_text(json.dumps(config))
    status, summary = call(viewer + "/api/case")
    assert status == 200
    stages = read_manifest(wiki_case)["stages"]
    assert "witness.add" in stages and "witness.add" not in summary["stale"]
    assert summary["stale"] == sorted(name for name in stages if name.startswith("ingest."))


def test_analyzer_upgrade_is_reported_as_stale(viewer, wiki_case):
    path = wiki_case / "manifest.json"
    manifest = json.loads(path.read_text())
    stage = next(name for name in manifest["stages"] if name.startswith("ingest."))
    manifest["stages"][stage]["analyzer_build_id"] = "0.0.0+source.earlier"
    path.write_text(json.dumps(manifest))
    status, summary = call(viewer + "/api/case")
    assert status == 200 and summary["stale"] == [] and summary["stale_analyzer"] == [stage]


def test_exported_bundle_is_served_read_only(wiki_case, tmp_path):
    render(wiki_case)
    bundle = tmp_path / "bundle"
    export_bundle(wiki_case, bundle)
    assert resolve_root(bundle) == (bundle / "data", True)
    with pytest.raises(ValueError):
        resolve_root(tmp_path)
    server, base = start(bundle)
    try:
        status, summary = call(base + "/api/case")
        assert status == 200 and summary["bundle"] is True and summary["stale"] == []
        assert next(s for s in summary["pipeline"] if s["step"] == "export")["done"]
        status, docket = call(base + "/api/docket")
        assert status == 200 and docket["verified"] is True
    finally:
        server.shutdown()
        server.server_close()
    assert CaseView(bundle / "data", bundle=True).validation() == {
        "validation": None,
        "verified": None,
    }
