import json

from evidencegraph.case import ingest
from evidencegraph.coverage.estimate import estimate
from evidencegraph.docket.facts import audit_facts
from evidencegraph.docket.render import render
from evidencegraph.identity import identify
from evidencegraph.lineage import lineage
from evidencegraph.manifest import read_manifest
from evidencegraph.reconcile.engine import reconcile_case
from evidencegraph.store import Store


def test_ingest_preserves_native_counts_and_byte_hashes(wiki_case):
    with Store(wiki_case, read_manifest(wiki_case)) as store:
        assert len(store.entities("revision")) == 12
        assert len(store.entities("page")) == 3
        assert len(store.entities("label")) == 3
        assert store.scalar("SELECT count(*) FROM time_claims WHERE winning") == 25
        assert (
            store.scalar(
                "SELECT count(*) FROM revisions WHERE json_extract_string(attrs,'$.body_encoding')='utf8'"
            )
            == 3
        )
        assert store.scalar("SELECT count(*) FROM revisions WHERE label=''") == 4
    before = read_manifest(wiki_case)
    assert set(ingest(wiki_case).values()) == {"skipped"}
    assert read_manifest(wiki_case) == before


def test_docket_facts_identity_lineage_and_dependent_capture(wiki_case):
    audit_facts(wiki_case)
    identify(wiki_case)
    lineage(wiki_case)
    reconcile_case(wiki_case, "wiki-saves")
    estimate(wiki_case)
    render(wiki_case)
    docket = json.loads((wiki_case / "docket.json").read_text())
    answers = {a["question_id"]: a for a in docket["answers"]}
    assert len(answers) == 16
    assert answers["DQ5"]["numbers"]["nonempty_labels"] == 2
    assert answers["DQ2"]["numbers"]["publisher_human_label_revisions"] == 4
    assert len((wiki_case / "DOCKET.md").read_text().splitlines()) <= 65
    with Store(wiki_case, read_manifest(wiki_case)) as store:
        facts = {f["fact_name"]: f for f in store.query("SELECT * FROM facts")}
        assert facts["held_revisions"]["status"] == "exact"
        assert (
            facts["check.published revisions"]["status"] == "exact"
        )  # ignores the deliberately wrong 'actual'
        assert facts["unpublished_ip_count"]["status"] == "not_computable"
        assert (
            store.scalar(
                "SELECT count(*) FROM relations WHERE kind='same_actor_as' AND outcome='supported'"
            )
            == 0
        )
        assert store.scalar("SELECT count(*) FROM relations WHERE kind='built_on'") == 9
        assert (
            store.scalar(
                "SELECT count(*) FROM relations WHERE kind='executed' AND outcome='supported'"
            )
            == 12
        )
        cr = store.query("SELECT * FROM capture_recapture")[0]
        assert cr["assumption_violations"]
        assert "not a population bound" in cr["interpretation"]
