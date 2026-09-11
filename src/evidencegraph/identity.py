"""Identity hypotheses remain separate, cited edges. Prefixes never authenticate actors."""

import ipaddress
from collections import defaultdict
from pathlib import Path

from evidencegraph.derive import edge, run_stage, trust_relation


def derive_identity(store, config, manifest):
    by_label = defaultdict(list)
    for label in store.entities("label", native=False):
        if label["natural_key"]:
            by_label[label["natural_key"]].append(label)
    for labels in by_label.values():
        labels.sort(key=lambda e: e["entity_id"])
        for label in labels[1:]:
            yield (
                "relations",
                edge(
                    "same_actor_as",
                    labels[0],
                    label,
                    method="same_claimed_label",
                    outcome="ambiguous",
                    rationale="Matching unauthenticated labels may be shared or reused; actor count is not identifiable",
                    domain=trust_relation(
                        config, manifest, [labels[0]["witness_id"], label["witness_id"]]
                    ),
                    run_id="identity",
                ),
            )
    # Retain cross-label shared-prefix evidence as a spanning tree, avoiding an O(n²) clique.
    revisions = store.entities("revision")
    prefixes = defaultdict(set)
    prefix_evidence = {}
    for revision in revisions:
        label, prefix = revision["attrs"].get("label"), revision["attrs"].get("ip16")
        if label and prefix:
            prefixes[prefix].add(label)
            prefix_evidence.setdefault((prefix, label), revision)
    emitted = set()
    for prefix, names in sorted(prefixes.items()):
        names = sorted(n for n in names if n in by_label)
        for name in names[1:]:
            pair = (names[0], name)
            if pair in emitted:
                continue
            emitted.add(pair)
            relation = edge(
                "same_actor_as",
                by_label[names[0]][0],
                by_label[name][0],
                method="shared_ip16",
                outcome="ambiguous",
                run_id="identity",
                rationale=f"Labels appear on shared prefix {prefix}; cloud /16 co-location is insufficient for attribution",
            )
            observations = [prefix_evidence[(prefix, names[0])], prefix_evidence[(prefix, name)]]
            yield (
                "relations",
                relation.model_copy(
                    update={
                        "citation_ids": tuple(
                            sorted(
                                set(relation.citation_ids)
                                | {r["citation_id"] for r in observations}
                            )
                        ),
                        "witness_ids": tuple(
                            sorted(
                                set(relation.witness_ids) | {r["witness_id"] for r in observations}
                            )
                        ),
                    }
                ),
            )
    references = defaultdict(list)
    for prefix in store.entities("network_prefix"):
        if prefix["attrs"].get("network"):
            references[prefix["witness_id"]].append(
                (ipaddress.ip_network(prefix["attrs"]["network"]), prefix)
            )
    for observed in store.entities("network_prefix", native=False):
        key = observed["natural_key"]
        if not observed["attrs"].get("observed_as_reference") or key.count(".") != 1:
            continue
        network = ipaddress.ip_network(key + ".0.0/16")
        for witness, networks in references.items():
            overlaps = [
                ref
                for ref_network, ref in networks
                if ref_network.version == 4 and network.overlaps(ref_network)
            ]
            if overlaps:
                yield (
                    "relations",
                    edge(
                        "member_of",
                        observed,
                        overlaps[0],
                        method="reference_prefix_overlap",
                        outcome="ambiguous",
                        domain=trust_relation(config, manifest, [observed["witness_id"], witness]),
                        rationale=f"Masked /16 overlaps {len(overlaps)} ranges in a dated reference snapshot; does not establish the exact IP, historical ownership or autonomous authorship",
                        run_id="identity",
                    ),
                )


def identify(root: Path) -> dict:
    return run_stage(root, "identity", derive_identity)
