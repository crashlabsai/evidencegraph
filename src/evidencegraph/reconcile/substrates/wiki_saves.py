from collections import defaultdict

from evidencegraph.derive import edge, trust_relation


def reconcile_wiki(store, config, manifest):
    by_key = defaultdict(list)
    for revision in store.entities("revision"):
        by_key[revision["natural_key"]].append(revision)
    for event in store.entities("wiki_event"):
        attrs = event["attrs"]
        if attrs.get("event_type") != "save":
            continue
        candidates = [
            r
            for r in by_key.get(attrs.get("revision_ref"), [])
            if r["attrs"].get("page_key") == attrs.get("page_key")
        ]
        if len(candidates) == 1:
            revision = candidates[0]
            yield (
                "relations",
                edge(
                    "executed",
                    event,
                    revision,
                    method="published_revision_ref",
                    outcome="supported",
                    candidates=(revision["entity_id"],),
                    domain=trust_relation(
                        config, manifest, [event["witness_id"], revision["witness_id"]]
                    ),
                    rationale="Save event references this stored revision one-to-one; both are generated from the same archive, not independent request coverage",
                    run_id="reconcile.wiki-saves",
                ),
            )
        else:
            yield (
                "relations",
                edge(
                    "executed",
                    event,
                    event,
                    method="published_revision_ref",
                    outcome="ambiguous" if candidates else "unmatched",
                    candidates=tuple(r["entity_id"] for r in candidates),
                    rationale="Published revision reference missing or not unique",
                    run_id="reconcile.wiki-saves",
                ),
            )
