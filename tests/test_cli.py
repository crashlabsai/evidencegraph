import json

import pytest
from typer.testing import CliRunner

from evidencegraph.case import ingest
from evidencegraph.cli import app
from evidencegraph.manifest import CaseMutationLockError, case_mutation_lock, read_manifest
from evidencegraph.publication import case_publication_transaction


def test_query_and_citation_are_usable(wiki_case):
    runner = CliRunner()
    result = runner.invoke(app, ["query", str(wiki_case), "SELECT count(*) n FROM revisions"])
    assert result.exit_code == 0, result.output + str(result.exception)
    assert json.loads(result.output) == [{"n": 12}]
    result = runner.invoke(app, ["query", str(wiki_case), "DELETE FROM entities"])
    assert result.exit_code != 0
    result = runner.invoke(app, ["query", str(wiki_case), "SELECT read_text('/etc/passwd')"])
    assert result.exit_code != 0


def test_lock_and_failed_publication_preserve_manifest(wiki_case):
    original = (wiki_case / "manifest.json").read_bytes()
    with case_mutation_lock(wiki_case):
        with pytest.raises(CaseMutationLockError):
            with case_mutation_lock(wiki_case):
                pass
    with pytest.raises(RuntimeError):
        with case_publication_transaction(wiki_case) as manifest:
            manifest["witnesses"] = {}
            raise RuntimeError("simulated failure")
    assert (wiki_case / "manifest.json").read_bytes() == original


def test_tamper_incremental_source_fails_before_publication(wiki_case):
    manifest = read_manifest(wiki_case)
    witness = next(w for w in manifest["witnesses"].values() if w["filename"] == "revisions.jsonl")
    snapshot = wiki_case / witness["snapshot_path"]
    snapshot.chmod(0o644)
    snapshot.write_text("changed")
    with pytest.raises(ValueError):
        ingest(wiki_case)
    assert read_manifest(wiki_case) == manifest


def test_ingest_cache_depends_on_case_configuration(wiki_case):
    assert set(ingest(wiki_case).values()) == {"skipped"}
    path = wiki_case / "case.json"
    config = json.loads(path.read_text())
    config["family_confidence"] = 0.6
    path.write_text(json.dumps(config))
    assert set(ingest(wiki_case).values()) == {"ingested"}
    assert set(ingest(wiki_case).values()) == {"skipped"}
