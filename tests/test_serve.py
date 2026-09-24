"""The local viewer is read-only, host-checked, and reports verification failures visibly."""

import json
import threading
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

import pytest
from test_lab_stage import load_stage

from evidencegraph.docket.render import render
from evidencegraph.export.bagit import export_bundle
from evidencegraph.lab.stage import construct_stage
from evidencegraph.manifest import read_manifest
from evidencegraph.suggest.providers import LexicalBaseline
from evidencegraph.suggest.run import run_key, run_suggestions, show
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
    # A wiki export needs the wiki-saves substrate and the wiki derived stages, not the
    # registry substrate the generic recipe names.
    assert "reconcile registry" not in steps and not steps["reconcile wiki-saves"]["done"]
    assert steps["reconcile wiki-saves"]["command"].endswith("--substrate wiki-saves")
    assert list(steps)[2:6] == ["facts", "identity", "lineage", "reconcile wiki-saves"]
    optional = {s["step"]: s for s in summary["optional"]}
    assert set(optional) == {"scan", "validate", "suggest"}
    # Suggestions read Inspect transcripts, which a wiki export does not have.
    assert optional["suggest"]["applicable"] is False and not optional["suggest"]["done"]


def test_pipeline_marks_reconciliation_done_only_for_the_applicable_substrate(viewer, wiki_case):
    from evidencegraph.reconcile.engine import reconcile_case

    reconcile_case(wiki_case, "wiki-saves")
    status, summary = call(viewer + "/api/case")
    assert status == 200
    steps = {step["step"]: step for step in summary["pipeline"]}
    assert steps["reconcile wiki-saves"]["done"]
    assert steps["reconcile wiki-saves"]["detail"] == "12 relations derived"
    assert all(step.get("applicable", True) for step in summary["pipeline"])


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
    status, everything = call(viewer + "/api/relations?limit=1000")
    severity = {
        "contradicted": 0,
        "ambiguous": 1,
        "unmatched": 2,
        "not_assessable": 3,
        "supported": 4,
    }
    ranks = [severity[r["outcome"]] for r in everything["rows"]]
    assert ranks == sorted(ranks), "unresolved relations are listed before supported ones"
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


def test_viewer_source_is_not_part_of_the_analyzer_identity(tmp_path):
    from evidencegraph.provenance import source_tree_sha256

    tree = tmp_path / "pkg"
    (tree / "ui").mkdir(parents=True)
    (tree / "engine.py").write_text("RULE = 1\n")
    (tree / "ui" / "server.py").write_text("VIEW = 1\n")
    before = source_tree_sha256(tree, exclude=("ui",))
    (tree / "ui" / "server.py").write_text("VIEW = 2\n")
    assert source_tree_sha256(tree, exclude=("ui",)) == before
    assert source_tree_sha256(tree) != before
    (tree / "engine.py").write_text("RULE = 2\n")
    assert source_tree_sha256(tree, exclude=("ui",)) != before


@pytest.fixture
def incident_case(tmp_path):
    stage = tmp_path / "stage"
    case = tmp_path / "case"
    construct_stage(stage, seed=3, spoof="B:2", drop=["A"])
    load_stage(case, stage)
    render(case)
    return case


def suggest_baselines(case):
    run_suggestions(
        case, task="write-claims", provider=LexicalBaseline(), model="baseline-lexical-1"
    )
    run_suggestions(
        case, task="search", query="receipt", provider=LexicalBaseline(), model="baseline-lexical-1"
    )
    return "write-claims:baseline", run_key("search", "baseline", "receipt")


def forensic_views(base) -> dict:
    return {
        path: call(base + path)[1]
        for path in ("/api/docket", "/api/relations?limit=1000", "/api/entities?limit=1000")
    }


def test_suggestion_queue_matches_show_and_leaves_forensic_views_unchanged(incident_case):
    server, base = start(incident_case)
    try:
        status, empty = call(base + "/api/suggestions")
        assert status == 200 and empty["runs"] == []
        assert empty["commands"][0]["command"] == f"eg suggest claims {incident_case}"
        before = forensic_views(base)
        claims, search = suggest_baselines(incident_case)
        assert forensic_views(base) == before
        published = (incident_case / "manifest.json").read_bytes()

        status, listing = call(base + "/api/suggestions")
        runs = {run["run_key"]: run for run in listing["runs"]}
        assert status == 200 and set(runs) == {claims, search}
        assert runs[search]["query"] == "receipt" and "not evidence" in listing["interpretation"]
        run = runs[claims]
        assert run["healthy"] and run["stale"] == [] and run["provider"] == "baseline"
        assert sum(run["dispositions"].values()) == run["total"] > 0
        assert set(listing["meanings"]["write-claims"]) == set(listing["disposition_order"])

        status, queue = call(base + f"/api/suggestions/{quote(claims)}?limit=1000")
        assert status == 200 and queue["total"] == queue["run_total"] == run["total"]
        # The same order `eg suggest show` prints, every row a published message citation
        # with its full distribution and the exact text the provider was sent.
        shown = show(incident_case, claims, limit=1000)["suggestions"]
        assert [row["span_id"] for row in queue["rows"]] == [s["span_id"] for s in shown]
        assert [row["rank"] for row in queue["rows"]] == list(range(1, run["total"] + 1))
        criteria = set(queue["questions"]["write_claim"]["criteria"])
        for row in queue["rows"]:
            assert queue["citation_index"][row["span_id"]]["locator_kind"] == "message"
            assert row["message"]["role"] == row["role"] and row["message_error"] is None
            assert set(row["answers"]["write_claim"]["probabilities"]) == criteria

        status, review = call(base + f"/api/suggestions/{quote(claims)}?disposition=review")
        assert review["total"] == run["dispositions"]["review"] > 0
        assert {row["disposition"] for row in review["rows"]} == {"review"}
        # A filter hides rows; it never renumbers the queue.
        assert [row["rank"] for row in review["rows"]] == [
            row["rank"] for row in queue["rows"] if row["disposition"] == "review"
        ]
        status, found = call(base + f"/api/suggestions/{quote(claims)}?q=RECEIPT")
        assert 0 < found["total"] < queue["total"]
        assert all("receipt" in json.dumps(row["message"]).lower() for row in found["rows"])
        status, paged = call(base + f"/api/suggestions/{quote(claims)}?limit=2&offset=2")
        assert [row["rank"] for row in paged["rows"]] == [3, 4] and paged["total"] == run["total"]

        status, checked = call(base + f"/api/suggestions/{quote(claims)}/recheck")
        assert status == 200 and checked["result"] == "reproduced"
        assert call(base + "/api/suggestions/write-claims:unknown")[0] == 404
        steps = {s["step"]: s for s in call(base + "/api/case")[1]["optional"]}
        assert steps["suggest"]["done"] and steps["suggest"]["applicable"]
        # Reading suggestions never writes to the case.
        assert (incident_case / "manifest.json").read_bytes() == published
    finally:
        server.shutdown()
        server.server_close()


def test_changed_suggestion_files_are_reported_not_raised(incident_case, monkeypatch):
    claims, search = suggest_baselines(incident_case)
    view = CaseView(incident_case)
    monkeypatch.setattr("evidencegraph.suggest.run.POLICY_VERSION", "next")
    runs = {run["run_key"]: run for run in view.suggestions()["runs"]}
    assert runs[claims]["stale"] == ["review policy changed since this run"]
    monkeypatch.undo()

    # A changed request blob withholds only the rows that sent it.
    row = view.suggestion_queue(claims, {})["rows"][0]
    blob = incident_case / "suggest" / "blobs" / row["request_sha256"]
    blob.write_bytes(blob.read_bytes() + b" ")
    rows = view.suggestion_queue(claims, {"limit": "1000"})["rows"]
    broken = [r for r in rows if r["request_sha256"] == row["request_sha256"]]
    assert broken and all(r["message"] is None for r in broken)
    assert "failed its hash check" in broken[0]["message_error"]
    assert all(r["message"] for r in rows if r["request_sha256"] != row["request_sha256"])

    # A changed run file makes that run unreadable, and the others still list.
    entry = read_manifest(incident_case)["suggestions"][search]
    path = incident_case / entry["suggestions"]
    path.write_text(path.read_text() + "\n")
    runs = {run["run_key"]: run for run in view.suggestions()["runs"]}
    assert runs[claims]["healthy"] and not runs[search]["healthy"]
    assert "changed since publication" in runs[search]["error"]
    server, base = start(incident_case)
    try:
        status, error = call(base + f"/api/suggestions/{quote(search)}")
        assert status == 400 and "changed since publication" in error["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_bundled_suggestions_are_readable_and_offer_no_commands(incident_case, tmp_path):
    claims, _ = suggest_baselines(incident_case)
    bundle = tmp_path / "bundle"
    export_bundle(incident_case, bundle)
    view = CaseView(bundle / "data", bundle=True)
    listing = view.suggestions()
    assert listing["commands"] == [] and all(run["healthy"] for run in listing["runs"])
    queue = view.suggestion_queue(claims, {})
    assert queue["rows"] and all(row["message"] for row in queue["rows"])
    assert view.suggestion_recheck(claims)["result"] == "reproduced"
