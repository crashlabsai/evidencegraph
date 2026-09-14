"""Common publication boundary for question-driven derived tables."""

from collections.abc import Callable, Iterable
from pathlib import Path

from evidencegraph.case import configuration
from evidencegraph.ids import relation_id
from evidencegraph.manifest import update_manifest_stage
from evidencegraph.provenance import analyzer_build_id, sha256_file
from evidencegraph.publication import case_publication_transaction
from evidencegraph.schema import CaseConfig, Outcome, Relation, RelationKind, TrustDomainRelation
from evidencegraph.store import PartitionWriter, Store, validate_graph


def configuration_sha256(root: Path) -> str:
    return sha256_file(root / "case.json")


def stale_stages(root: Path, manifest: dict) -> list[str]:
    """Stages whose recorded case.json hash differs from the current declarations."""
    current = configuration_sha256(root)
    return sorted(
        name
        for name, stage in manifest.get("stages", {}).items()
        if (name.startswith("ingest.") or "case_config_sha256" in stage.get("parameters", {}))
        and stage.get("parameters", {}).get("case_config_sha256") != current
    )


def require_current_configuration(root: Path, manifest: dict) -> str:
    """Every ingested and derived stage must have been computed under the current case.json.

    Trust declarations and clock bounds are inputs to the conclusions; a conclusion
    computed under different assumptions is stale, not merely old.
    """
    current = configuration_sha256(root)
    stale = stale_stages(root, manifest)
    if stale:
        raise ValueError(
            "case configuration changed since "
            + ", ".join(stale)
            + "; re-run ingest and the derived stages before rendering, validating or exporting"
        )
    # An upgraded rule must not silently render an attribution made by the old rule.
    stale_builds = sorted(
        name
        for name, stage in manifest.get("stages", {}).items()
        if (name.startswith("ingest.") or "case_config_sha256" in stage.get("parameters", {}))
        and stage.get("analyzer_build_id") != analyzer_build_id()
    )
    if stale_builds:
        raise ValueError(
            "analyzer changed since "
            + ", ".join(stale_builds)
            + "; re-run ingest and the derived stages with the current analyzer"
        )
    return current


def authentic_witnesses(config: CaseConfig, manifest: dict) -> frozenset[str]:
    declared = {d.id for d in config.trust_domains if d.authentic_records}
    return frozenset(
        w for w, meta in manifest["witnesses"].items() if meta["trust_domain"] in declared
    )


def exclusive_receipt_witnesses(config: CaseConfig, manifest: dict) -> frozenset[str]:
    declared = {d.id for d in config.trust_domains if d.exclusive_receipt_tokens}
    return frozenset(
        w for w, meta in manifest["witnesses"].items() if meta["trust_domain"] in declared
    )


def trust_relation(config: CaseConfig, manifest: dict, witness_ids: Iterable[str]) -> str:
    domains = {manifest["witnesses"][w]["trust_domain"] for w in witness_ids}
    if len(domains) == 1:
        return "same"
    relations = {d.id: d.related_to for d in config.trust_domains}
    pairs = []
    for a in domains:
        for b in domains:
            if a < b:
                pairs.append(relations[a].get(b, relations[b].get(a, "unknown")))
    if pairs and all(p == "independent" for p in pairs):
        return "independent"
    if pairs and all(p == "same" for p in pairs):
        return "same"
    return "unknown"


def edge(
    kind: RelationKind,
    subject: dict,
    obj: dict,
    *,
    method: str,
    rationale: str,
    outcome: str = "supported",
    candidates: tuple[str, ...] = (),
    domain: str = "same",
    run_id: str = "derived",
    authenticated: bool = False,
) -> Relation:
    return Relation(
        relation_id=relation_id(kind, subject["entity_id"], obj["entity_id"], method),
        kind=kind,
        subject_id=subject["entity_id"],
        object_id=obj["entity_id"],
        outcome=Outcome(outcome),
        method=method,
        rationale=rationale,
        witness_ids=tuple(sorted({subject["witness_id"], obj["witness_id"]})),
        citation_ids=tuple(sorted({subject["citation_id"], obj["citation_id"]})),
        candidates=candidates,
        trust_domain_relation=TrustDomainRelation(domain),
        authenticated=authenticated,
        run_id=run_id,
    )


def run_stage(root: Path, name: str, derive: Callable, *, params: dict | None = None) -> dict:
    config = configuration(root)
    with case_publication_transaction(root) as manifest:
        if not manifest["partitions"]:
            raise ValueError("ingest witnesses first")
        config_sha256 = require_current_configuration(root, manifest)
        manifest["partitions"].pop(name, None)
        if name not in {"facts", "identity", "lineage"}:
            manifest.pop("validation", None)
        for downstream in ("coverage", "docket"):
            if downstream != name:
                manifest["partitions"].pop(downstream, None)
                manifest["stages"].pop(downstream, None)
        manifest["reports"] = {}
        writer = PartitionWriter(root, name)
        with Store(root, manifest) as store:
            for table, row in derive(store, config, manifest):
                writer.add(table, row)
        partition = writer.finish()
        manifest["partitions"][name] = partition
        update_manifest_stage(
            manifest, name, params={**(params or {}), "case_config_sha256": config_sha256}
        )
        with Store(root, manifest) as store:
            validate_graph(store)
    return {table: data["rows"] for table, data in partition.items()}
