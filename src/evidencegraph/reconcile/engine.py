"""Crossledger's conservative decision order behind substrate-specific keys.

Field check, candidate collection, version exclusion, receipt-binding exclusion,
unmatched, execution-window filter (R3 late starts and early completions), exact
namespace, optional 2B no-op, payload separation, binding or declared authenticity,
then abstention.
"""

import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from evidencegraph.derive import (
    authentic_witnesses,
    edge,
    exclusive_receipt_witnesses,
    run_stage,
    trust_relation,
)
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
    authentic: frozenset[str] = frozenset(),
    exclusive_receipts: frozenset[str] = frozenset(),
):
    """Attribute one independently observed record to at most one recorded action.

    `authentic` lists witnesses whose trust domain declares `authentic_records`; only
    those, or a matching receipt in a witness with declared exclusive receipt tokens,
    can turn a unique matching claim into a supported attribution. Possession of a
    transferable receipt does not bind a native event to the mutation.
    """
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
    binding = getattr(substrate, "binding", None)
    if binding is not None and candidates:
        forged = [a for a in candidates if binding(record, a) == "mismatch"]
        if len(forged) == len(candidates):
            return result(
                "contradicted",
                forged,
                "receipt_binding_mismatch",
                "Every candidate carries a receipt token that differs from the registry's published commitment for this record",
            )
        candidates = [a for a in candidates if a not in forged]
    if not candidates:
        return result(
            "unmatched",
            [],
            "key_and_version",
            "No recorded action matches this key, version and receipt identifier",
        )
    # A record may know only when it was observed (a listing) rather than when the
    # mutation happened; then nothing excludes candidates that completed earlier.
    earliest = seconds(record["time_lower"]) if record.get("time_lower") else None
    latest = seconds(record["time_upper"]) if record.get("time_upper") else None
    if (
        bound is None
        or latest is None
        or any(not a.get("time_lower") or not a.get("time_upper") for a in candidates)
    ):
        return result(
            "not_assessable",
            candidates,
            "clock_bound_missing",
            "Attribution needs recorded execution intervals on both sides and a declared relation between source clocks",
        )
    late = [a for a in candidates if seconds(a["time_lower"]) > latest + bound]
    if len(late) == len(candidates):
        return result(
            "contradicted",
            candidates,
            "contradiction_R3",
            "Every candidate starts later than the record plus the declared clock bound",
        )
    # A synchronous call that returned before the mutation cannot have produced it.
    early = (
        [a for a in candidates if seconds(a["time_upper"]) < earliest - bound]
        if earliest is not None
        else []
    )
    compatible = [a for a in candidates if a not in late and a not in early]
    if not compatible:
        return result(
            "contradicted",
            candidates,
            "execution_window_disjoint",
            "No candidate's recorded execution interval overlaps the record within the declared clock bound; a completed synchronous call cannot produce a later mutation",
        )
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
        chosen = compatible[0]
        status = binding(record, chosen) if binding is not None else "unavailable"
        if status == "bound" and chosen["witness_id"] in exclusive_receipts:
            return result(
                "supported",
                compatible,
                "independent_receipt_binding",
                "One compatible native tool event agrees with the independent record and its receipt-token commitment, under the declared assumption that this token could not be relayed or copied into another tool event",
            )
        if status == "unbound":
            return result(
                "ambiguous",
                compatible,
                "receipt_binding_missing",
                "The registry committed to a receipt token for this record but the transcript receipt carries none; a receipt copied from public data would look identical",
            )
        if chosen["witness_id"] in authentic:
            return result(
                "supported",
                compatible,
                "independent_receipt",
                "One compatible native tool event agrees with the independently recorded key, bytes and event id, under the declared assumption that this transcript's records could not be fabricated by the investigated actors",
            )
        if status == "bound":
            return result(
                "ambiguous",
                compatible,
                "receipt_possession_only",
                "The claim carries a matching receipt token, but a copy of the complete receipt would match identically; attribution requires declared authentic records or exclusive receipt tokens",
            )
        return result(
            "ambiguous",
            compatible,
            "attribution_unbound",
            "One matching transcript claim agrees with the independent record, but nothing binds this transcript event to the mutation; a receipt copied from public data would match identically",
        )
    return result(
        "ambiguous",
        compatible,
        "unseparated_candidates",
        "Available witnesses cannot separate the remaining candidates",
    )


def declared_bound(config, bound):
    if bound is not None:
        return bound
    bounds = [
        b.bound_seconds
        for b in config.clock_bounds
        if {b.clock_a, b.clock_b} == {"runner", "container"}
    ]
    return min(bounds) if bounds else None


def reconcile_registry(store, config, manifest, bound):
    substrate = RegistrySubstrate()
    actions = store.entities("tool_event")
    authentic = authentic_witnesses(config, manifest)
    exclusive_receipts = exclusive_receipt_witnesses(config, manifest)
    declared = declared_bound(config, bound)
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
        yield (
            "relations",
            reconcile_record(
                record,
                candidates,
                substrate=substrate,
                bound=declared,
                domain=domain,
                authentic=authentic,
                exclusive_receipts=exclusive_receipts,
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
        if matching:
            # The claim names a real record; check whether the receipt is the caller's own.
            status = substrate.binding(matching, action)
            if status in {"mismatch", "unbound"}:
                domain = trust_relation(
                    config, manifest, [matching["witness_id"], action["witness_id"]]
                )
                yield (
                    "relations",
                    edge(
                        "executed",
                        action,
                        matching,
                        method="receipt_binding_mismatch"
                        if status == "mismatch"
                        else "receipt_binding_missing",
                        outcome="contradicted"
                        if status == "mismatch" and domain == "independent"
                        else "ambiguous",
                        rationale="Receipt token differs from the registry's published commitment for the record it claims"
                        if status == "mismatch"
                        else "Claim matches a published record but carries no receipt token; the registry committed to one, so the claim is not bound to the mutation",
                        domain=domain,
                        run_id="reconcile.registry",
                    ),
                )
            continue
        declared_populations = [
            d for d in declarations if d["attrs"].get("namespace") == receipt["namespace"]
        ]
        population = declared_populations[0] if len(declared_populations) == 1 else None
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
            # Absence is evidence only if the whole execution window, widened by the
            # clock bound, lies inside the interval the registry claims to cover.
            in_window = bool(
                declared is not None
                and action["time_lower"]
                and action["time_upper"]
                and pop.get("started_at")
                and pop.get("stopped_at")
                and seconds(pop["started_at"]) <= seconds(action["time_lower"]) - declared
                and seconds(action["time_upper"]) + declared <= seconds(pop["stopped_at"])
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
                    rationale="Successful receipt claims a mutation absent from the declared complete independent registry, whose capture covers the whole tool window plus the clock bound"
                    if outcome == "contradicted"
                    else "Absence cannot establish spoofing unless independent complete capture covers the whole tool execution window widened by a declared clock bound",
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
