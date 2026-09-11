"""Observed ancestry and text equality, without inventing direction or intent."""

from collections import defaultdict
from pathlib import Path

from evidencegraph.derive import edge, run_stage, trust_relation


def derive_lineage(store, config, manifest):
    revisions = store.entities("revision")
    by_key = defaultdict(list)
    for revision in revisions:
        by_key[revision["natural_key"]].append(revision)
    for revision in revisions:
        attrs = revision["attrs"]
        base = attrs.get("diff_base")
        if base:
            # Public diff_base is a rev_id, not an RCS revision number.
            for parent in by_key.get(str(base), []):
                if parent["attrs"].get("page_key") != attrs.get("page_key"):
                    continue
                yield (
                    "relations",
                    edge(
                        "built_on",
                        revision,
                        parent,
                        method=f"rcs_diff_base:{attrs.get('diff_base_reason') or 'previous_revision'}",
                        rationale="Published RCS diff base establishes textual ancestry, not intention or authenticated authorship",
                        run_id="lineage",
                    ),
                )
    for field, method in [
        ("body_sha256", "exact_source_sha256"),
        ("normalized_sha256", "normalized_whitespace"),
    ]:
        groups = defaultdict(list)
        for entity in revisions + store.entities("record"):
            attrs = entity["attrs"]
            digest = attrs.get(field) or (
                attrs.get("source_text_sha256") if field == "body_sha256" else None
            )
            if digest:
                groups[digest].append(entity)
        for group in groups.values():
            group.sort(key=lambda e: (e["time_lower"] or "9999", e["entity_id"]))
            for other in group[1:]:
                first = group[0]
                yield (
                    "relations",
                    edge(
                        "reproduced",
                        other,
                        first,
                        method=method,
                        rationale="Equal source bytes"
                        if field == "body_sha256"
                        else "Equal text after whitespace normalization; no causal direction established",
                        outcome="supported" if field == "body_sha256" else "ambiguous",
                        domain=trust_relation(
                            config, manifest, [first["witness_id"], other["witness_id"]]
                        ),
                        run_id="lineage",
                    ),
                )
    records = {e["natural_key"]: e for e in store.entities("record")}
    for link in store.entities("link"):
        for key in link["attrs"].get("record_ids", []):
            if key in records:
                yield (
                    "relations",
                    edge(
                        "located_on",
                        records[key],
                        link,
                        method="text_contains_url",
                        rationale="Published text mentions this URL; followed=false does not establish a visit",
                        run_id="lineage",
                    ),
                )


def lineage(root: Path) -> dict:
    return run_stage(root, "lineage", derive_lineage)
