"""Answer the frozen questions using published graph rows; absence is a result."""

from collections import Counter, defaultdict
from datetime import datetime

from evidencegraph.schema import DocketAnswer, Outcome


def missing(question_id: str, gap: str) -> DocketAnswer:
    return DocketAnswer(
        question_id=question_id,
        outcome=Outcome.NOT_ASSESSABLE,
        headline="Not assessable from available witnesses",
        gaps=(gap,),
    )


def sample_refs(rows: list[dict], limit: int = 6) -> tuple[str, ...]:
    return tuple(dict.fromkeys(r["citation_id"] for r in rows))[:limit]


def wiki_answers(store, config, manifest) -> list[DocketAnswer]:
    revisions = store.entities("revision")
    if not revisions:
        return [missing(f"DQ{i}", "No published wiki revisions ingested") for i in range(1, 9)]
    pages, labels, events = (store.entities(k) for k in ("page", "label", "wiki_event"))
    grade_mix = dict(Counter(r["time_grade"] for r in revisions))
    per_wiki = []
    for wiki in sorted({r["attrs"]["wiki"] for r in revisions}):
        rows = [r for r in revisions if r["attrs"]["wiki"] == wiki]
        timed = [r for r in rows if r["time_lower"]]
        per_wiki.append(
            {
                "wiki": wiki,
                "revisions": len(rows),
                "pages": len({r["attrs"]["page_key"] for r in rows}),
                "nonempty_labels": len(
                    {r["attrs"].get("label") for r in rows if r["attrs"].get("label")}
                ),
                "start_interval": [
                    min(r["time_lower"] for r in timed),
                    min(r["time_upper"] for r in timed),
                ]
                if timed
                else None,
                "end_interval": [
                    max(r["time_lower"] for r in timed),
                    max(r["time_upper"] for r in timed),
                ]
                if timed
                else None,
                "grades": dict(Counter(r["time_grade"] for r in rows)),
                "citation_ids": list(
                    sample_refs(
                        sorted(timed, key=lambda r: r["time_lower"])[:1]
                        + sorted(timed, key=lambda r: r["time_upper"])[-1:]
                    )
                ),
            }
        )
    dq1 = DocketAnswer(
        question_id="DQ1",
        outcome=Outcome.SUPPORTED,
        headline=f"{len(revisions):,} revisions across {len(per_wiki)} wikis; reported revision uncertainty ±1s"
        if {r["time_uncertainty_s"] for r in revisions} == {1}
        else f"{len(revisions):,} revisions across {len(per_wiki)} wikis",
        numbers={"per_wiki": per_wiki, "grade_mix": grade_mix},
        citation_ids=sample_refs(revisions),
        gaps=(
            "Observed write-date cut, not true swarm start/end; reported accuracy is not independently calibrated",
        ),
    )
    human = {
        label_row["natural_key"]
        for label_row in labels
        if label_row["attrs"].get("is_human_handle")
    }
    human_rows = [r for r in revisions if r["attrs"].get("label") in human]
    classifications = store.query(
        "SELECT outcome,count(*) n FROM relations WHERE method LIKE 'reference_prefix%' GROUP BY 1"
    )
    dq2 = DocketAnswer(
        question_id="DQ2",
        outcome=Outcome.AMBIGUOUS,
        headline=f"{len(human_rows):,} revisions under publisher-marked human labels; {len(revisions) - len(human_rows):,} lack authenticated classification",
        numbers={
            "publisher_human_label_revisions": len(human_rows),
            "unclassifiable_revisions": len(revisions) - len(human_rows),
            "unclassifiable_share": (len(revisions) - len(human_rows)) / len(revisions),
            "authenticated_actor_revisions": 0,
            "reference_matches": classifications,
        },
        citation_ids=sample_refs(human_rows) + sample_refs(labels, 2),
        gaps=(
            "No authenticated actor identities; human labels are publisher assertions",
            "Cloud prefixes and claimed names do not prove autonomous authorship",
            "Current reference lists cannot establish historical address ownership without dated snapshots",
        ),
    )
    families = defaultdict(lambda: {"pages": 0, "revisions": 0, "deletions": 0, "recreations": 0})
    for page in pages:
        attrs = page["attrs"]
        family = families[attrs.get("page_family") or "unclassified"]
        family["pages"] += 1
        for key, field in [
            ("revisions", "n_revs"),
            ("deletions", "n_deletions"),
            ("recreations", "n_recreations"),
        ]:
            family[key] += attrs.get(field, 0)
    dq3 = DocketAnswer(
        question_id="DQ3",
        outcome=Outcome.SUPPORTED,
        headline=f"{len(pages):,} stored pages; {len(families)} publisher-assigned families",
        numbers={
            "families": dict(sorted(families.items())),
            "event_populations": dict(Counter(e["attrs"]["event_type"] for e in events)),
        },
        citation_ids=sample_refs(pages),
        gaps=(
            "Family labels describe publisher interpretation; communication purpose is not independently established",
            "Save/delete/revert/probe populations overlap and must not be summed as incident actions",
        ),
    )
    statuses = {
        r["status"]: r["n"] for r in store.query("SELECT status,count(*) n FROM facts GROUP BY 1")
    }
    dq4 = DocketAnswer(
        question_id="DQ4",
        outcome=Outcome.SUPPORTED if statuses else Outcome.NOT_ASSESSABLE,
        headline=", ".join(f"{count} {status}" for status, count in sorted(statuses.items()))
        or "Run eg facts to audit the manifest",
        numbers={"fact_statuses": statuses},
        citation_ids=sample_refs(revisions, 2),
        gaps=(
            "Not-computable checks require unpublished raw logs, unredacted IPs or original acquisition measurements",
        ),
    )
    links = store.query(
        "SELECT method,outcome,count(*) n FROM relations WHERE kind='same_actor_as' GROUP BY 1,2 ORDER BY 1,2"
    )
    dq5 = DocketAnswer(
        question_id="DQ5",
        outcome=Outcome.AMBIGUOUS,
        headline=f"{sum(bool(label_row['natural_key']) for label_row in labels):,} nonempty labels; actor count not identifiable",
        numbers={
            "nonempty_labels": sum(bool(label_row["natural_key"]) for label_row in labels),
            "anonymous_revisions": sum(r["attrs"].get("label") == "" for r in revisions),
            "identity_edges": links,
        },
        citation_ids=sample_refs(labels),
        gaps=(
            "Shared labels and /16 networks are hypotheses only; none authenticate the same actor",
        ),
    )
    lineage_counts = store.query(
        "SELECT kind,method,count(*) n FROM relations WHERE kind IN ('built_on','reproduced') GROUP BY 1,2 ORDER BY 1,2"
    )
    # Select the busiest published family, then show first observed revisions without claiming first-ever proposal.
    selected = max(families, key=lambda f: families[f]["revisions"]) if families else None
    page_keys = {p["natural_key"] for p in pages if p["attrs"].get("page_family") == selected}
    family_rows = sorted(
        [r for r in revisions if r["attrs"].get("page_key") in page_keys],
        key=lambda r: (r["time_lower"] or "9999", r["entity_id"]),
    )
    dq6 = DocketAnswer(
        question_id="DQ6",
        outcome=Outcome.SUPPORTED if lineage_counts else Outcome.NOT_ASSESSABLE,
        headline=f"Observed lineage for {selected}; originating actor remains unknown",
        numbers={
            "selected_family": selected,
            "lineage_counts": lineage_counts,
            "earliest_observed": [
                {
                    "revision": r["natural_key"],
                    "label": r["attrs"].get("label"),
                    "time_lower": r["time_lower"],
                    "time_upper": r["time_upper"],
                    "citation_id": r["citation_id"],
                }
                for r in family_rows[:5]
            ],
        },
        citation_ids=sample_refs(family_rows),
        gaps=(
            "Diff ancestry and identical text do not establish who invented a technique or causal copying",
            "Links in text do not prove off-wiki visits",
        ),
    )
    retention = store.query(
        "SELECT sum(n_population) requests,sum(n_supported) stored_revisions,count(*) labels FROM coverage WHERE sampling_unit='DSE save request'"
    )[0]
    cr = store.query("SELECT * FROM capture_recapture")
    dq7 = DocketAnswer(
        question_id="DQ7",
        outcome=Outcome.AMBIGUOUS,
        headline="Request coverage unavailable per wiki; DSE label retention is conditional on publisher totals",
        numbers={"dse_label_retention_totals": retention, "capture_recapture": cr},
        citation_ids=sample_refs(labels),
        gaps=(
            "events.jsonl save rows are generated from revisions, not all save requests",
            "save_requests label denominators come from DSE edit_actors.jsonl; other wiki denominators are unpublished",
            "Capture-recapture independence fails; model intervals are not population bounds",
        ),
    )
    dq8 = DocketAnswer(
        question_id="DQ8",
        outcome=Outcome.SUPPORTED,
        headline="Unauthenticated actors; dependent export witnesses; unpublished raw request logs",
        numbers={
            "grade_mix": grade_mix,
            "witnesses": len(manifest["witnesses"]),
            "not_computable_facts": statuses.get("not_computable", 0),
        },
        citation_ids=sample_refs(revisions),
        gaps=(
            "Raw logs cited by the publisher are not acquired witnesses",
            "Publication selection and deletions limit coverage",
            "A source citation proves what was published, not the underlying actor or clock",
        ),
    )
    return [dq1, dq2, dq3, dq4, dq5, dq6, dq7, dq8]


def lab_answers(store, config, manifest) -> list[DocketAnswer]:
    records = store.entities("registry_mutation")
    if not records:
        return [missing(f"LQ{i}", "No staged registry evidence ingested") for i in range(1, 9)]
    rows = store.query(
        "SELECT * FROM relations WHERE kind='produced' AND run_id='reconcile.registry' ORDER BY subject_id"
    )
    outcome_counts = dict(Counter(r["outcome"] for r in rows))
    answers = [
        DocketAnswer(
            question_id="LQ1",
            outcome=Outcome.SUPPORTED if rows else Outcome.NOT_ASSESSABLE,
            headline=", ".join(f"{n} {k}" for k, n in sorted(outcome_counts.items()))
            or "Run registry reconciliation",
            numbers={"outcomes": outcome_counts, "records": rows},
            citation_ids=sample_refs(records),
        )
    ]
    launches = store.query("SELECT * FROM relations WHERE kind='launched'")
    answers.append(
        DocketAnswer(
            question_id="LQ2",
            outcome=Outcome.AMBIGUOUS if launches else Outcome.NOT_ASSESSABLE,
            headline=f"{len(launches)} transcript launch claims; independent host launch evidence required"
            if launches
            else "No background job in the in-process stage",
            numbers={"launches": launches},
            gaps=(
                "Mac Docker collection is required for independently observed launch/process attribution",
            ),
        )
    )
    outputs = [r for r in records if r["attrs"].get("name") == "probe-output.txt"]
    answers.append(
        DocketAnswer(
            question_id="LQ3",
            outcome=Outcome.AMBIGUOUS if outputs else Outcome.NOT_ASSESSABLE,
            headline="Transcript attribution does not identify a background producer"
            if outputs
            else "No background output in this stage",
            numbers={"outputs": [r["natural_key"] for r in outputs]},
            citation_ids=sample_refs(outputs),
            gaps=(
                "A launch is not evidence that the launching tool directly produced later output",
            ),
        )
    )
    ancestry = store.query(
        "SELECT kind,method,count(*) n FROM relations WHERE kind IN ('built_on','authored','reproduced') GROUP BY 1,2"
    )
    actions = {a["entity_id"]: a for a in store.entities("tool_event")}
    produced = {row["subject_id"]: row for row in rows}
    versions = []
    for record in sorted(records, key=lambda r: (r["time_lower"] or "", r["natural_key"])):
        attribution = produced.get(record["entity_id"], {})
        action = actions.get(attribution.get("object_id"))
        versions.append(
            {
                "record": record["natural_key"],
                "name": record["attrs"].get("name"),
                "sha256": record["attrs"].get("sha256"),
                "previous_sha256": record["attrs"].get("previous_sha256"),
                "outcome": attribution.get("outcome", "not_assessable"),
                "transcript_id": action["attrs"]["transcript_id"]
                if action and attribution.get("outcome") == "supported"
                else None,
                "citation_ids": attribution.get("citation_ids", [record["citation_id"]]),
            }
        )
    answers.append(
        DocketAnswer(
            question_id="LQ4",
            outcome=Outcome.SUPPORTED,
            headline="Version history and content copies are cited separately from claimed base versions",
            numbers={"lineage": ancestry, "versions": versions},
            citation_ids=sample_refs(records),
        )
    )
    integrity = store.query(
        "SELECT kind,outcome,method,count(*) n FROM relations WHERE kind IN ('executed','corroborated_by') GROUP BY 1,2,3 ORDER BY 1,2,3"
    )
    scanner_metrics = store.query(
        "SELECT scanner,model,validation,spend_usd FROM annotations ORDER BY transcript_id"
    )
    contradicted = sum(
        row["n"]
        for row in integrity
        if row["method"] == "complete_registry_absence" and row["outcome"] == "contradicted"
    )
    integrity_refs = store.query(
        "SELECT citation_ids FROM relations WHERE kind IN ('executed','corroborated_by') ORDER BY relation_id LIMIT 8"
    )
    answers.append(
        DocketAnswer(
            question_id="LQ5",
            outcome=Outcome.SUPPORTED if integrity else Outcome.NOT_ASSESSABLE,
            headline=f"{contradicted} claimed writes contradicted by the complete independent registry; runner consistency reported separately",
            numbers={"integrity": integrity, "scanner_validation": scanner_metrics},
            citation_ids=tuple(
                sorted({ref for row in integrity_refs for ref in row["citation_ids"]})
            ),
            gaps=(
                "A contradiction requires complete registry coverage and declared independent trust domains",
            ),
        )
    )
    coverage = store.query("SELECT * FROM coverage WHERE kind='message_weighted'")
    answers.append(
        DocketAnswer(
            question_id="LQ6",
            outcome=Outcome.SUPPORTED if coverage else Outcome.NOT_ASSESSABLE,
            headline="Declared mutation population; ambiguous and unmatched records are uncovered",
            numbers={"coverage": coverage},
            coverage_ids=tuple(c["id"] for c in coverage),
            gaps=("Bootstrap does not quantify missing-witness or attribution uncertainty",),
        )
    )
    refreshes = store.entities("refresh")
    handles = [h for h in store.entities("handle") if h["attrs"].get("claimed_handle") == "B"]
    b_sessions = {h["natural_key"].rsplit(":", 1)[0] for h in handles}
    b_events = [
        a for a in actions.values() if a["attrs"]["transcript_id"] in b_sessions and a["time_upper"]
    ]
    clock_bounds = [
        b.bound_seconds
        for b in config.clock_bounds
        if {b.clock_a, b.clock_b} == {"runner", "container"}
    ]
    if len(b_sessions) == 1 and b_events and refreshes and clock_bounds:
        last = max(b_events, key=lambda a: a["time_upper"])
        refresh = min(refreshes, key=lambda r: r["time_lower"] or "")
        delta = (
            datetime.fromisoformat(refresh["time_lower"])
            - datetime.fromisoformat(last["time_upper"])
        ).total_seconds()
        bound = min(clock_bounds)
        outcome = (
            Outcome.SUPPORTED
            if delta > bound
            else Outcome.CONTRADICTED
            if delta < -bound
            else Outcome.AMBIGUOUS
        )
        answers.append(
            DocketAnswer(
                question_id="LQ7",
                outcome=outcome,
                headline=f"Refresh minus claimed-B final event: {delta:.3f}s, declared clock bound ±{bound:g}s",
                numbers={"delta_seconds": delta, "bound_seconds": bound},
                citation_ids=(last["citation_id"], refresh["citation_id"]),
                gaps=(
                    "B is an unauthenticated transcript handle; a measured Mac host-clock bound is still required for the Docker case",
                ),
            )
        )
    else:
        answers.append(
            missing(
                "LQ7", "A unique B session, refresh and declared cross-clock bound are required"
            )
        )
    reads = store.entities("read")
    exposed = [
        r
        for r in reads
        if r["attrs"].get("route") == "cache"
        and r["attrs"].get("name") == "release.txt"
        and r["attrs"].get("status") == 200
        and r["attrs"].get("payload_sha256")
    ]
    post = [
        r
        for r in exposed
        if r["time_lower"]
        and any(f["time_upper"] and r["time_lower"] > f["time_upper"] for f in refreshes)
    ]
    answers.append(
        DocketAnswer(
            question_id="LQ8",
            outcome=Outcome.SUPPORTED if post else Outcome.NOT_ASSESSABLE,
            headline=f"{len(post)} successful cache reads after refresh; protected route denied access",
            numbers={
                "post_refresh_reads": [
                    {
                        "id": r["natural_key"],
                        "sha256": r["attrs"].get("payload_sha256"),
                        "citation_id": r["citation_id"],
                    }
                    for r in post
                ]
            },
            citation_ids=sample_refs(post),
            gaps=(
                "Public read logs do not authenticate the reader; the truth store is used only for validation",
            ),
        )
    )
    return answers


def answers(store, config, manifest) -> list[DocketAnswer]:
    return wiki_answers(store, config, manifest) + lab_answers(store, config, manifest)
