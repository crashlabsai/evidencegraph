"""Recompute what the published bytes identify. Never use a manifest's actual as truth."""

from collections import Counter
from pathlib import Path

from evidencegraph.derive import run_stage
from evidencegraph.schema import Fact
from evidencegraph.store import safe_path
from evidencegraph.strict_json import load_strict_json


def measurements(store) -> tuple[dict, dict]:
    revs = store.entities("revision")
    pages = store.entities("page")
    labels = store.entities("label")
    events = store.entities("wiki_event")
    r, p, lab, ev = [[x["attrs"] for x in rows] for rows in (revs, pages, labels, events)]
    counts = {
        "revisions": len(r),
        "pages": len(p),
        "labels": len(lab),
        "events": len(ev),
        "records": len(store.entities("record")),
        "links": len(store.entities("link")),
    }
    facts = {
        "held_revisions": len(r),
        "held_pages": len(p),
        "published_labels": len(lab),
        "held_revision_body_bytes": sum(x.get("body_len", 0) for x in r),
        "blank_label_revisions": sum(x.get("label") == "" for x in r),
        "human_handle_labels": sum(x.get("is_human_handle") is True for x in lab),
        "pages_with_earlier_unpublished_revisions": sum(x.get("n_revs_before", 0) > 0 for x in p),
        "pages_beginning_at_seq_1": sum(x.get("n_revs_before", 0) == 0 for x in p),
        "published_names_with_slash": sum("/" in x.get("name", "") for x in p),
        "published_names_with_tilde": sum("~" in x.get("name", "") for x in p),
        "non_ascii_published_names": sum(not x.get("name", "").isascii() for x in p),
        "dse_storage_buckets": len({x.get("bucket") for x in p if x.get("wiki") == "dse"}),
        "page_family_covered_pages": sum(
            x.get("page_family_source") not in {None, "none"} for x in p
        ),
        "page_family_uncovered_pages": sum(
            x.get("page_family_source") in {None, "none"} for x in p
        ),
    }
    for wiki in sorted({x.get("wiki") for x in r}):
        facts[f"{wiki}_held_revisions"] = sum(x.get("wiki") == wiki for x in r)
        facts[f"{wiki}_held_pages"] = sum(x.get("wiki") == wiki for x in p)
    for encoding in ("ascii", "utf8", "latin1"):
        facts[f"{encoding}_revision_bodies"] = sum(x.get("body_encoding") == encoding for x in r)
    facts["non_ascii_revision_bodies"] = (
        facts["utf8_revision_bodies"] + facts["latin1_revision_bodies"]
    )
    for grade in ("reqlog", "rclog", "write_date"):
        facts[f"save_grade_{grade}"] = sum(x.get("time_grade") == grade for x in r)
    deletes = [x for x in ev if x.get("event_type") == "delete"]
    recreations = [x for x in ev if x.get("relation_type") == "first_recreation_of"]
    probes = [x for x in ev if x.get("event_type") == "probe"]
    facts.update(
        {
            "dse_admin_deletion_events": len(deletes),
            "dse_admin_deleted_pages": len({x.get("page_key") for x in deletes}),
            "dse_admin_deletions_without_held_page": sum(
                x.get("page_held") is False for x in deletes
            ),
            "dse_admin_deleted_pages_without_held_page": len(
                {x.get("page_key") for x in deletes if x.get("page_held") is False}
            ),
            "first_recreation_relations": len(recreations),
            "first_recreation_pages": len({x.get("page_key") for x in recreations}),
            "first_recreation_relations_with_revision_ref": sum(
                bool(x.get("revision_ref")) for x in recreations
            ),
            "first_recreation_relations_without_revision_ref": sum(
                not x.get("revision_ref") for x in recreations
            ),
            "stored_revisions_with_first_recreation_relation": sum(
                x.get("relation_type") == "first_recreation_of" for x in r
            ),
            "first_recreation_relations_grade_reqlog": sum(
                x.get("time_grade") == "reqlog" for x in recreations
            ),
            "dse_script_probe_requests": len(probes),
            "may_script_probe_requests": sum(
                x.get("time", "").startswith("2026-05") for x in probes
            ),
            "june_script_probe_requests": sum(
                x.get("time", "").startswith("2026-06") for x in probes
            ),
        }
    )
    for grade in ("reqlog", "rclog"):
        facts[f"dse_admin_deletions_grade_{grade}"] = sum(
            x.get("time_grade") == grade for x in deletes
        )
    for name, label in [("agent_relent", "AgentRelent"), ("admin1", "[Admin1]")]:
        matches = [x for x in lab if x.get("label") == label]
        if len(matches) == 1:
            for key, value in matches[0].items():
                if key.startswith(("save_request", "stored_revision")) and isinstance(value, int):
                    facts[f"{name}_{key}"] = value
    checks = {
        "published revisions": len(r),
        "published pages": len(p),
        "distinct labels": len(lab),
        "human-handle labels": facts["human_handle_labels"],
        "blank-label revisions": facts["blank_label_revisions"],
        "published body bytes": facts["held_revision_body_bytes"],
        "pages beginning at seq 1": facts["pages_beginning_at_seq_1"],
        "pages with earlier unpublished revisions": facts[
            "pages_with_earlier_unpublished_revisions"
        ],
        "non-ASCII bodies": facts["non_ascii_revision_bodies"],
        "ASCII bodies": facts["ascii_revision_bodies"],
        "UTF-8 byte bodies": facts["utf8_revision_bodies"],
        "Latin-1 byte bodies": facts["latin1_revision_bodies"],
        "source body hash failures": sum(not x.get("body_hash_verified") for x in r),
        "event type populations": dict(Counter(x["event_type"] for x in ev)),
        "events rows": len(ev),
        "save revision references resolve one-to-one": len(
            {x.get("revision_ref") for x in ev if x.get("event_type") == "save"}
            & {x["rev_id"] for x in r}
        ),
        "first-recreation relations": len(recreations),
        "recreated pages": facts["first_recreation_pages"],
        "recreations with revision refs": facts["first_recreation_relations_with_revision_ref"],
        "recreations without revision refs": facts[
            "first_recreation_relations_without_revision_ref"
        ],
        "stored first-recreation relation edges": facts[
            "first_recreation_relations_with_revision_ref"
        ],
        "stored revisions carrying recreation relations": facts[
            "stored_revisions_with_first_recreation_relation"
        ],
        "stored recreation edges on save events": sum(
            x.get("event_type") == "save" for x in recreations
        ),
        "May probes": facts["may_script_probe_requests"],
        "June probes": facts["june_script_probe_requests"],
        "probe events": len(probes),
        "revert events": sum(x.get("event_type") == "revert" for x in ev),
    }
    for wiki in sorted({x.get("wiki") for x in r}):
        checks[f"{wiki} revisions"] = facts[f"{wiki}_held_revisions"]
        checks[f"{wiki} pages"] = facts[f"{wiki}_held_pages"]
    for typ in ("save", "delete", "revert", "probe"):
        checks[f"{typ} grades"] = dict(
            Counter(x.get("time_grade") for x in ev if x["event_type"] == typ)
        )
    checks["deletion grades"] = checks["delete grades"]
    checks["recreation grades"] = dict(Counter(x.get("time_grade") for x in recreations))
    if probes:
        checks["first probe"] = min(x["time"] for x in probes)
        checks["last probe"] = max(x["time"] for x in probes)
    for name, values, key in [
        ("pages ordered by page_key", p, "page_key"),
        ("labels ordered by UTF-8 bytes", lab, "label"),
    ]:
        ordered = [x[key] for x in sorted(values, key=lambda x: x["source_line"])]
        checks[name] = ordered == sorted(ordered)
    for key, value in counts.items():
        facts[f"counts.{key}"] = value
    return facts, checks


def derive_facts(store, config, manifest, root: Path):
    values, checks = measurements(store)
    witnesses = [
        w
        for w in manifest["witnesses"].values()
        if w["filename"] == "manifest.json" and w["adapter"] == "collusion-wiki"
    ]
    if len(witnesses) != 1:
        raise ValueError("facts requires exactly one acquired collusion.wiki manifest")
    witness = witnesses[0]
    published = load_strict_json(
        safe_path(root, witness["snapshot_path"]), max_bytes=32 * 1024 * 1024
    )
    if not isinstance(published, dict):
        raise ValueError("manifest must be an object")
    refs = tuple(
        x["citation_id"]
        for x in store.query(
            "SELECT citation_id FROM citations WHERE witness_id=? ORDER BY citation_id LIMIT 1",
            [witness["witness_id"]],
        )
    )
    samples = tuple(
        x["citation_id"]
        for x in store.query(
            "SELECT min(citation_id) AS citation_id FROM native_entities GROUP BY witness_id ORDER BY 1"
        )
    )

    def make(name, expected, actual, computable, reason):
        return Fact(
            fact_name=name,
            published_value=expected,
            graph_value=actual,
            status=("exact" if actual == expected else "differs")
            if computable
            else "not_computable",
            reason=reason,
            citation_ids=refs + samples,
        )

    for name, spec in published.get("facts", {}).items():
        available = name in values
        reason = "Recomputed from published rows; source metadata remains a publisher assertion"
        if name in {
            "first_recreation_relations",
            "first_recreation_relations_with_revision_ref",
            "first_recreation_relations_grade_reqlog",
        }:
            available = False
            reason = f"Export exposes {values.get(name)} row-level values, but collapses recreation-edge multiplicity and does not retain each relation's separate corroboration grade"
        if name in {"non_ascii_published_names"}:
            reason = "Computed on the published names after redaction; the publisher's original computation may precede redaction"
        if not available:
            if "row-level" not in reason:
                reason = "Required raw logs, unredacted IPs or acquisition measurements are not published; manifest value is not evidence of reproduction"
        yield "facts", make(name, spec["value"], values.get(name), available, reason)
    for check in published.get("checks", []):
        name = check["name"]
        omitted = {
            "first-recreation relations",
            "recreation grades",
            "recreations with revision refs",
            "stored first-recreation relation edges",
            "stored recreation edges on save events",
        }
        yield (
            "facts",
            make(
                "check." + name,
                check["expected"],
                checks.get(name),
                name in checks and name not in omitted,
                "Computed independently of publisher's actual/ok fields"
                if name in checks
                else "Check requires unpublished raw fields or an acquisition procedure this graph did not perform",
            ),
        )
    for name, expected in published.get("counts", {}).items():
        key = f"counts.{name}"
        yield (
            "facts",
            make(
                key,
                expected.get("value") if isinstance(expected, dict) else expected,
                values.get(key),
                key in values,
                "Physical rows in the published table",
            ),
        )
    for name in ("events", "records", "links"):
        key = f"counts.{name}"
        if name not in published.get("counts", {}):
            yield (
                "facts",
                make(
                    key,
                    None,
                    values[key],
                    False,
                    "Observed physical row count; no corresponding count in this manifest",
                ),
            )


def audit_facts(root: Path) -> dict:
    return run_stage(root, "facts", lambda s, c, m: derive_facts(s, c, m, root))
