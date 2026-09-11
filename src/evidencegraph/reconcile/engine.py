"""Crossledger's conservative decision order behind substrate-specific keys.

Field check, candidate collection, version exclusion, R3, unmatched, temporal
filter, exact namespace, optional 2B no-op, payload separation, then abstention.
"""

import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from evidencegraph.derive import edge, run_stage, trust_relation
from evidencegraph.reconcile.substrate import Substrate
from evidencegraph.reconcile.substrates.registry import RegistrySubstrate


def seconds(value: str) -> float:
    timestamp = datetime.fromisoformat(value)
    if timestamp.utcoffset() is None:
        raise ValueError("timestamp requires an explicit timezone")
    return timestamp.timestamp()


def reconcile_record(
    record: dict,
    actions: list[dict],
    *,
    substrate: Substrate,
    bound: float | None,
    domain: str = "unknown",
):
    if bound is not None and (not math.isfinite(bound) or bound < 0):
        raise ValueError("clock bound must be finite and nonnegative")
    ids = [a["entity_id"] for a in actions]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate action ids")
    key = substrate.key_of_record(record)

    def result(outcome, kept, method, rationale):
        obj = kept[0] if outcome == "supported" else record
        relation = edge(
            "produced",
            record,
            obj,
            method=method,
            outcome=outcome,
            candidates=tuple(a["entity_id"] for a in kept),
            domain=domain,
            rationale=rationale,
            run_id="reconcile.registry",
        )
        if kept:
            relation = relation.model_copy(
                update={
                    "witness_ids": tuple(
                        sorted({record["witness_id"], *(a["witness_id"] for a in kept)})
                    ),
                    "citation_ids": tuple(
                        sorted({record["citation_id"], *(a["citation_id"] for a in kept)})
                    ),
                }
            )
        return relation

    if key is None:
        return result("not_assessable", [], "field_check", "Record has no substrate key")
    candidates = [
        a
        for a in actions
        if any(
            k.name == key.name and (key.namespace is None or k.namespace == key.namespace)
            for k in substrate.keys_created(a)
        )
    ]
    digest = record["attrs"].get("sha256")
    candidates = [
        a for a in candidates if not digest or substrate.attested_sha256(a, key) in {None, digest}
    ]
    # For receipts the event id distinguishes repeated writes of identical bytes.
    event_matches = getattr(substrate, "event_matches", None)
    if event_matches is not None:
        candidates = [a for a in candidates if event_matches(record, a)]
    latest = seconds(record["time_upper"]) if record.get("time_upper") else None
    if (
        candidates
        and latest is not None
        and bound is not None
        and all(
            a.get("time_lower") is not None and seconds(a["time_lower"]) > latest + bound
            for a in candidates
        )
    ):
        return result(
            "contradicted",
            candidates,
            "contradiction_R3",
            "Every candidate starts later than the record plus the declared clock bound",
        )
    if not candidates:
        return result(
            "unmatched",
            [],
            "key_and_version",
            "No recorded action matches this key, version and receipt identifier",
        )
    if bound is None or latest is None or any(not a.get("time_lower") for a in candidates):
        return result(
            "not_assessable",
            candidates,
            "clock_bound_missing",
            "Attribution needs recorded timestamps and a declared relation between source clocks",
        )
    compatible = [a for a in candidates if seconds(a["time_lower"]) <= latest + bound]
    if key.namespace is None:
        return result(
            "ambiguous",
            compatible,
            "namespace_unknown",
            "A name without its namespace cannot uniquely establish the affected substrate",
        )
    if len(compatible) > 1 and getattr(substrate, "earliest_creation_is_noop", lambda r: False)(
        record
    ):
        ordered = sorted(compatible, key=lambda a: a["time_lower"])
        if seconds(ordered[0]["time_upper"]) + 2 * bound < seconds(ordered[1]["time_lower"]):
            compatible = ordered[:1]
    if len(compatible) > 1 and isinstance(record["attrs"].get("payload"), str):
        payload = record["attrs"]["payload"]
        exact = [a for a in compatible if substrate.payload(a, key) == payload]
        unknown = [a for a in compatible if substrate.payload(a, key) is None]
        compatible = exact + unknown if exact else compatible
    if len(compatible) == 1:
        if domain != "independent":
            return result(
                "ambiguous",
                compatible,
                "trust_not_independent",
                "One matching transcript claim, but no declared independent observation",
            )
        return result(
            "supported",
            compatible,
            "independent_receipt",
            "One compatible native tool event agrees with the independently recorded key, bytes and event id",
        )
    return result(
        "ambiguous",
        compatible,
        "unseparated_candidates",
        "Available witnesses cannot separate the remaining candidates",
    )


def reconcile_registry(store, config, manifest, bound):
    substrate = RegistrySubstrate()
    actions = store.entities("tool_event")
    # Avoid a records × all-actions scan at corpus scale.
    index = defaultdict(list)
    for action in actions:
        for key in substrate.keys_created(action):
            index[key].append(action)
    records = store.entities("registry_mutation")
    for record in records:
        key = substrate.key_of_record(record)
        candidates = index.get(key, []) if key and key.namespace else actions
        witnesses = [record["witness_id"], *(a["witness_id"] for a in candidates)]
        domain = trust_relation(config, manifest, witnesses) if candidates else "unknown"
        declared = bound
        if declared is None:
            bounds = [
                b.bound_seconds
                for b in config.clock_bounds
                if {b.clock_a, b.clock_b} == {"runner", "container"}
            ]
            declared = min(bounds) if bounds else None
        yield (
            "relations",
            reconcile_record(
                record, candidates, substrate=substrate, bound=declared, domain=domain
            ),
        )
    # Missing successful claims are contradictions only under a complete, independent population.
    declarations = store.entities("population")
    by_id = {r["natural_key"]: r for r in records}
    for action in actions:
        receipt = substrate.receipt(action)
        if not receipt:
            continue
        matching = by_id.get(receipt["event_id"])
        declared = [d for d in declarations if d["attrs"].get("namespace") == receipt["namespace"]]
        if matching:
            continue
        population = declared[0] if len(declared) == 1 else None
        if population:
            pop = population["attrs"]
            population_records = {
                r["natural_key"]
                for r in records
                if r["attrs"].get("namespace") == receipt["namespace"]
            }
            complete = (
                pop.get("complete") is True
                and set(pop.get("event_ids", [])) == population_records
                and pop.get("count") == len(population_records)
            )
            in_window = bool(
                action["time_lower"]
                and pop.get("started_at")
                and pop.get("stopped_at")
                and seconds(pop["started_at"])
                <= seconds(action["time_lower"])
                <= seconds(pop["stopped_at"])
            )
            domain = trust_relation(
                config, manifest, [population["witness_id"], action["witness_id"]]
            )
            outcome = (
                "contradicted"
                if complete and in_window and domain == "independent"
                else "not_assessable"
            )
            yield (
                "relations",
                edge(
                    "executed",
                    action,
                    population,
                    method="complete_registry_absence",
                    outcome=outcome,
                    rationale="Successful receipt claims a mutation absent from the declared complete independent registry during the observation window"
                    if outcome == "contradicted"
                    else "Absence cannot establish spoofing without independent complete capture over the tool event window",
                    domain=domain,
                    run_id="reconcile.registry",
                ),
            )


def reconcile_case(root: Path, substrate: str, *, bound: float | None = None) -> dict:
    if substrate == "wiki-saves":
        from evidencegraph.reconcile.substrates.wiki_saves import reconcile_wiki

        return run_stage(root, "reconcile.wiki-saves", reconcile_wiki)
    if substrate == "registry":
        return run_stage(
            root,
            "reconcile.registry",
            lambda s, c, m: reconcile_registry(s, c, m, bound),
            params={"bound": bound},
        )
    raise ValueError(f"unknown substrate: {substrate}")
