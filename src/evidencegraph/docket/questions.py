"""The scope contract. Add a real question here before extending graph vocabulary."""

QUESTIONS = {
    "DQ1": "When did observed activity start and end per wiki, with what counts and time grades?",
    "DQ2": "Which revisions have evidence of agent or human authorship, and which remain unclassifiable?",
    "DQ3": "Which pages and families served as substrates, with how many revisions, deletions and recreations?",
    "DQ4": "Which published manifest facts and checks can be independently recomputed from the export?",
    "DQ5": "Which labels can be linked, and how many distinct actors can the evidence establish?",
    "DQ6": "What observed textual lineage and reproduction can be traced for a page family?",
    "DQ7": "What share of save requests survives as stored revisions, and is capture-recapture identifiable?",
    "DQ8": "Which answers depend on unauthenticated fields, missing witnesses or uncertain clocks?",
    "LQ1": "Which transcript action produced each registry record, or is attribution unresolved?",
    "LQ2": "Who launched the background job, using which script version?",
    "LQ3": "What produced the first post-refresh probe-output.txt?",
    "LQ4": "Who published, adapted and copied each artifact version?",
    "LQ5": "Which claimed writes lack independent records, and which tool/sandbox pairs disagree?",
    "LQ6": "What fraction of the declared record population is explained by transcripts?",
    "LQ7": "Is refresh after B's last transcript event under the declared clock bound?",
    "LQ8": "Which read exposed the protected fixture?",
}

ENTITY_QUESTIONS = {
    "identity": ("DQ2", "DQ5"),
    "session": ("LQ1", "LQ6"),
    "action": ("DQ3", "LQ1", "LQ5"),
    "artifact_version": ("DQ6", "LQ4", "LQ8"),
    "message": ("DQ1", "DQ6"),
    "substrate": ("DQ3", "DQ6"),
    "task": ("LQ7",),
    "job": ("LQ2", "LQ3"),
}
RELATION_QUESTIONS = {
    "authored": ("DQ2", "LQ4"),
    "executed": ("DQ7", "LQ5"),
    "launched": ("LQ2",),
    "produced": ("LQ1", "LQ3", "LQ6"),
    "read": ("LQ8",),
    "sent": ("DQ2",),
    "received": ("LQ8",),
    "built_on": ("DQ6", "LQ4"),
    "reproduced": ("DQ6", "LQ4"),
    "same_actor_as": ("DQ5",),
    "located_on": ("DQ3", "DQ6"),
    "corroborated_by": ("LQ5",),
    "member_of": ("DQ3", "LQ4"),
}
# Explicit column lists: adding a model field without updating this contract fails tests.
COLUMN_QUESTIONS = {
    "annotations": (
        "LQ5",
        "annotation_id scanner transcript_id value citation_ids validation model spend_usd",
    ),
    "witnesses": (
        "DQ8",
        "witness_id kind trust_domain adapter adapter_version origin sha256 size_bytes acquired_at coverage_claim clock_ids row_count snapshot_path filename",
    ),
    "clocks": ("DQ1", "clock_id witness_id label note"),
    "entities": (
        "DQ1",
        "entity_id kind subkind natural_key witness_id citation_id attrs time_lower time_upper time_grade time_uncertainty_s winning_clock_id",
    ),
    "time_claims": (
        "DQ1",
        "entity_id witness_id clock_id lower upper grade uncertainty_s winning citation_id note",
    ),
    "relations": (
        "LQ1",
        "relation_id kind subject_id object_id outcome method rationale witness_ids citation_ids candidates trust_domain_relation authenticated run_id",
    ),
    "citations": (
        "DQ8",
        "citation_id witness_id locator_kind locator content_sha256 preview transcript_id event_id message_id",
    ),
    "populations": ("LQ6", "id namespace time_range description"),
    "coverage": (
        "LQ6",
        "id kind reference_source population sampling_unit design estimate interval interval_level n_population n_sampled n_supported ambiguous_handling not_assessable_handling exclusions assumptions run_id",
    ),
    "capture_recapture": (
        "DQ7",
        "id witness_a witness_b unit_kind n_a n_b m_both n_hat_chapman interval level assumptions assumption_violations interpretation",
    ),
    "facts": ("DQ4", "fact_name published_value graph_value status reason citation_ids"),
    "docket_answers": (
        "DQ8",
        "question_id outcome headline numbers citation_ids coverage_ids gaps assumptions",
    ),
}
