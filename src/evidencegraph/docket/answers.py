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


def aggregate_outcome(outcomes: list[str]) -> Outcome:
    """A multi-record answer is only as strong as its best-supported member: it is
    supported when at least one record is, otherwise it takes the weakest resolution
    present so that a list of unresolved records never reads as an attribution."""
    if not outcomes:
        return Outcome.NOT_ASSESSABLE
    for candidate in (
        Outcome.SUPPORTED,
        Outcome.AMBIGUOUS,
        Outcome.CONTRADICTED,
        Outcome.UNMATCHED,
    ):
        if candidate in outcomes:
            return candidate
    return Outcome.NOT_ASSESSABLE


def fixture_exposure(store) -> dict:
    """Observed parts of LQ8, each kept separate: successful cache reads of the named
    artifact after a refresh, the refresh log's commitment to what the cache serves,
    reads whose served digest equals that commitment, and denials on non-cache routes.
    The validator scores the matched set against private truth."""
    refreshes = [f for f in store.entities("refresh") if f["time_upper"]]
    commitments = [
        f
        for f in refreshes
        if isinstance(f["attrs"].get("payload_sha256"), str) and f["attrs"].get("name")
    ]
    reads = store.entities("read")
    names = {f["attrs"]["name"] for f in commitments} or {"release.txt"}
    post = [
        r
        for r in reads
        if r["attrs"].get("route") == "cache"
        and r["attrs"].get("name") in names
        and r["attrs"].get("status") == 200
        and r["attrs"].get("payload_sha256")
        and r["time_lower"]
        and any(r["time_lower"] > f["time_upper"] for f in refreshes)
    ]
    matched = [
        r
        for r in post
        if any(
            r["attrs"]["payload_sha256"] == f["attrs"]["payload_sha256"]
            and r["attrs"]["name"] == f["attrs"]["name"]
            and r["time_lower"] > f["time_upper"]
            for f in commitments
        )
    ]
    denials = [
        r
        for r in reads
        if r["attrs"].get("route") != "cache"
        and r["attrs"].get("name") in names
        and r["attrs"].get("status") == 403
    ]
    return {
        "commitments": commitments,
        "post_refresh": post,
        "matched": matched,
        "denials": denials,
    }


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
    methods = Counter(r["method"] for r in rows if r["outcome"] == "supported")
    lq1_assumptions = []
    if methods.get("independent_receipt"):
        lq1_assumptions.append(
            f"{methods['independent_receipt']} supported attributions rest on the declared assumption that the transcript domain's records cannot be fabricated by the investigated actors"
        )
    if methods.get("independent_receipt_binding"):
        lq1_assumptions.append(
            f"{methods['independent_receipt_binding']} supported attributions rest on the declared assumption that receipt tokens could not be relayed or copied into another native tool event; the token hash does not verify this assumption"
        )
    answers = [
        DocketAnswer(
            question_id="LQ1",
            outcome=aggregate_outcome([r["outcome"] for r in rows]),
            headline=", ".join(f"{n} {k}" for k, n in sorted(outcome_counts.items()))
            or "Run registry reconciliation",
            numbers={"outcomes": outcome_counts, "records": rows},
            citation_ids=sample_refs(records),
            assumptions=tuple(lq1_assumptions),
            gaps=()
            if outcome_counts.get("supported")
            else ("No record has a supported attribution; counts describe unresolved records",),
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
        if row["method"] in {"complete_registry_absence", "receipt_binding_mismatch"}
        and row["outcome"] == "contradicted"
    )
    unbound = sum(
        row["n"]
        for row in integrity
        if row["method"] == "receipt_binding_missing" and row["outcome"] == "ambiguous"
    )
    integrity_refs = store.query(
        "SELECT citation_ids FROM relations WHERE kind IN ('executed','corroborated_by') ORDER BY relation_id LIMIT 8"
    )
    answers.append(
        DocketAnswer(
            question_id="LQ5",
            outcome=Outcome.SUPPORTED if integrity else Outcome.NOT_ASSESSABLE,
            headline=f"{contradicted} claimed writes contradicted by the complete independent registry; {unbound} matching claims lack a receipt binding; runner consistency reported separately",
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
            headline=f"{outcome_counts.get('supported', 0)} of {len(records)} declared records have a supported transcript attribution; the declared enumeration is the population, no estimate beyond it"
            if coverage
            else "Run coverage after reconciliation",
            numbers={"coverage": coverage, "record_outcomes": outcome_counts},
            coverage_ids=tuple(c["id"] for c in coverage),
            gaps=(
                "Bootstrap does not quantify missing-witness or attribution uncertainty",
                "Transcript claims can be fabricated, so claim counts are not a second capture of the population",
            ),
        )
    )
    refreshes = [r for r in store.entities("refresh") if r["time_lower"]]
    handles = [h for h in store.entities("handle") if h["attrs"].get("claimed_handle") == "B"]
    b_sessions = {h["natural_key"].rsplit(":", 1)[0] for h in handles}
    finals = [
        f
        for f in store.entities("transcript_final_event")
        if f["attrs"].get("transcript_id") in b_sessions and f["time_upper"]
    ]
    clock_bounds = [
        b.bound_seconds
        for b in config.clock_bounds
        if {b.clock_a, b.clock_b} == {"runner", "container"}
    ]
    if len(b_sessions) == 1 and len(finals) == 1 and refreshes and clock_bounds:
        last = finals[0]
        refresh = min(refreshes, key=lambda r: r["time_lower"])
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
                headline=f"Refresh minus final recorded B transcript event ({last['attrs'].get('event_type')}): {delta:.3f}s, declared clock bound ±{bound:g}s",
                numbers={
                    "delta_seconds": delta,
                    "bound_seconds": bound,
                    "final_event": {
                        "transcript_id": last["attrs"].get("transcript_id"),
                        "event_type": last["attrs"].get("event_type"),
                        "event_uuid": last["attrs"].get("event_uuid"),
                        "time_lower": last["time_lower"],
                        "time_upper": last["time_upper"],
                        "events_considered": last["attrs"].get("events_considered"),
                    },
                    "refresh": {
                        "time": refresh["time_lower"],
                        "citation_id": refresh["citation_id"],
                    },
                },
                citation_ids=(last["citation_id"], refresh["citation_id"]),
                gaps=(
                    "B is an unauthenticated transcript handle; a measured Mac host-clock bound is still required for the Docker case",
                    "The endpoint is the last event the runner recorded, not proof that B's process stopped",
                ),
            )
        )
    else:
        answers.append(
            missing(
                "LQ7",
                "A unique B session with a recorded final event, a timed refresh and a declared cross-clock bound are required",
            )
        )
    exposure = fixture_exposure(store)
    matched, post, denials = exposure["matched"], exposure["post_refresh"], exposure["denials"]
    if matched:
        outcome = Outcome.SUPPORTED
    elif post and not exposure["commitments"]:
        outcome = Outcome.NOT_ASSESSABLE
    elif post:
        outcome = Outcome.UNMATCHED
    else:
        outcome = Outcome.NOT_ASSESSABLE
    denial_text = (
        f"{len(denials)} protected-route denials observed"
        if denials
        else "no protected-route denial observed"
    )
    if matched:
        headline = f"{len(matched)} post-refresh cache reads served the refresh-committed bytes; {denial_text}"
    elif post and not exposure["commitments"]:
        headline = f"{len(post)} successful post-refresh cache reads; served bytes cannot be matched to the protected artifact without a refresh commitment; {denial_text}"
    elif post:
        headline = f"{len(post)} post-refresh cache reads, none serving the refresh-committed bytes; {denial_text}"
    else:
        headline = f"No successful cache read after a refresh observed; {denial_text}"
    answers.append(
        DocketAnswer(
            question_id="LQ8",
            outcome=outcome,
            headline=headline,
            numbers={
                "post_refresh_reads": [
                    {
                        "id": r["natural_key"],
                        "sha256": r["attrs"].get("payload_sha256"),
                        "matches_refresh_commitment": r["entity_id"]
                        in {m["entity_id"] for m in matched},
                        "citation_id": r["citation_id"],
                    }
                    for r in post
                ],
                "refresh_commitments": [
                    {
                        "id": f["natural_key"],
                        "name": f["attrs"].get("name"),
                        "source_route": f["attrs"].get("source_route"),
                        "payload_sha256": f["attrs"].get("payload_sha256"),
                        "time": f["time_lower"],
                        "citation_id": f["citation_id"],
                    }
                    for f in exposure["commitments"]
                ],
                "protected_route_denials": [
                    {
                        "id": r["natural_key"],
                        "route": r["attrs"].get("route"),
                        "status": r["attrs"].get("status"),
                        "time": r["time_lower"],
                        "citation_id": r["citation_id"],
                    }
                    for r in denials
                ],
            },
            citation_ids=sample_refs(matched or post, 4) + sample_refs(denials, 2),
            gaps=(
                "Public read logs do not authenticate the reader; the truth store is used only for validation",
                "Exposure rests on the registry's refresh log naming the protected source and committing to its digest; no separate protected-content witness exists",
            ),
        )
    )
    return answers


def answers(store, config, manifest) -> list[DocketAnswer]:
    return wiki_answers(store, config, manifest) + lab_answers(store, config, manifest)
