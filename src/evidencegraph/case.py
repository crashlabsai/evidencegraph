"""Case lifecycle and incremental ingestion; truth files are never adapter inputs."""

from pathlib import Path

from evidencegraph.acquire import acquire
from evidencegraph.adapters.collusion_wiki import FILES, CollusionWiki
from evidencegraph.manifest import (
    atomic_json,
    case_mutation_lock,
    read_manifest,
    update_manifest_stage,
)
from evidencegraph.provenance import analyzer_build_id, sha256_file, verify_file_identity
from evidencegraph.publication import case_publication_transaction
from evidencegraph.schema import CaseConfig, Witness
from evidencegraph.store import PartitionWriter, Store, safe_path, validate_graph

PRIVATE_NAMES = {
    "private",
    "truth",
    "audit.jsonl",
    "mutations.jsonl",
    "contexts.json",
    "bindings.jsonl",
    "spoofed.json",
    "schedule.jsonl",
}


def init_case(root: Path, config: CaseConfig) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with case_mutation_lock(root):
        if (root / "manifest.json").exists():
            raise ValueError("case already exists")
        atomic_json(root / "case.json", config.model_dump(mode="json"))
        atomic_json(
            root / "manifest.json",
            {
                "schema_version": "0.1",
                "witnesses": {},
                "partitions": {},
                "stages": {},
                "reports": {},
            },
        )


def configuration(root: Path) -> CaseConfig:
    return CaseConfig.model_validate_json((root / "case.json").read_text())


def adapter_named(name: str, config: CaseConfig | None = None):
    if name == "collusion-wiki":
        return CollusionWiki(config.family_confidence if config else 0.8)
    if name == "reference-list":
        from evidencegraph.adapters.reference_lists import ReferenceLists

        return ReferenceLists()
    if name == "inspect-eval":
        from evidencegraph.adapters.inspect_eval import InspectEval

        return InspectEval(config.handle_patterns) if config else InspectEval()
    if name == "lab-public":
        from evidencegraph.adapters.lab_public import LabPublic

        return LabPublic()
    raise ValueError(f"unknown adapter: {name}")


def add_witness(
    root: Path, path: Path, *, adapter: str, trust_domain: str, origin: str | None = None
) -> list[str]:
    config = configuration(root)
    if trust_domain not in {d.id for d in config.trust_domains}:
        raise ValueError(f"undeclared trust domain: {trust_domain}")
    if PRIVATE_NAMES.intersection(path.parts):
        raise ValueError("private truth cannot be ingested into the evidence graph")
    reader = adapter_named(adapter, config)
    if path.is_symlink():
        raise ValueError("evidence path cannot be a symlink")
    if path.is_dir():
        if adapter == "collusion-wiki":
            paths = sorted(p for p in path.iterdir() if p.name in FILES)
        elif adapter == "inspect-eval":
            paths = sorted(path.glob("*.eval"))
        elif adapter == "lab-public":
            paths = sorted(
                p
                for p in path.iterdir()
                if p.name
                in {
                    "ledger.jsonl",
                    "registry-reads.json",
                    "registry-refresh.json",
                    "population.json",
                }
            )
            paths.extend(sorted((path / "artifacts").glob("*")))
        else:
            paths = sorted(p for p in path.iterdir() if p.suffix == ".json")
    else:
        paths = [path]
    if not paths:
        raise ValueError("no supported evidence files found")
    added = []
    with case_publication_transaction(root) as manifest:
        for source in paths:
            spec = reader.describe(source)
            witness = acquire(
                root,
                source,
                adapter=adapter,
                trust_domain=trust_domain,
                kind=spec["kind"],
                origin=origin,
                coverage_claim=spec.get(
                    "coverage_claim", "Published file; completeness not independently established"
                ),
            )
            existing = manifest["witnesses"].get(witness.witness_id)
            if existing:
                if any(
                    existing[k] != getattr(witness, k)
                    for k in ("sha256", "adapter", "trust_domain", "filename")
                ):
                    raise ValueError(
                        "identical bytes cannot be assigned conflicting witness semantics"
                    )
            else:
                # Two observations may share bytes (for example two empty logs), but the
                # same bytes can never count as two independently trusted witnesses.
                if any(
                    w["sha256"] == witness.sha256 and w["trust_domain"] != trust_domain
                    for w in manifest["witnesses"].values()
                ):
                    raise ValueError("identical bytes cannot be declared in two trust domains")
                manifest["witnesses"][witness.witness_id] = witness.model_dump(mode="json")
            added.append(witness.witness_id)
        invalidate_derived(manifest)
        update_manifest_stage(manifest, "witness.add", params={"witnesses": added})
    return added


def invalidate_derived(manifest: dict) -> None:
    manifest.pop("validation", None)
    manifest["partitions"] = {
        k: v for k, v in manifest["partitions"].items() if k.startswith("ingest.")
    }
    manifest["reports"] = {}
    manifest["stages"] = {
        k: v for k, v in manifest["stages"].items() if k.startswith(("ingest.", "witness."))
    }


def ingest(root: Path, witness_id: str | None = None) -> dict[str, str]:
    results = {}
    with case_publication_transaction(root) as manifest:
        config = configuration(root)
        config_sha256 = sha256_file(root / "case.json")
        witnesses = manifest["witnesses"]
        if witness_id and witness_id not in witnesses:
            raise ValueError(f"unknown witness: {witness_id}")
        selected = [witnesses[witness_id]] if witness_id else list(witnesses.values())
        changed = False
        for raw in selected:
            witness = Witness.model_validate(raw)
            snapshot = safe_path(root, witness.snapshot_path)
            verify_file_identity(
                snapshot,
                expected_size=witness.size_bytes,
                expected_sha256=witness.sha256,
                subject=witness.witness_id,
            )
            stage = f"ingest.{witness.witness_id}"
            previous = manifest["stages"].get(stage)
            if (
                stage in manifest["partitions"]
                and previous
                and previous["analyzer_build_id"] == analyzer_build_id()
                and previous["parameters"].get("case_config_sha256") == config_sha256
            ):
                for entry in manifest["partitions"][stage].values():
                    verify_file_identity(
                        safe_path(root, entry["path"]),
                        expected_size=None,
                        expected_sha256=entry["sha256"],
                        subject=stage,
                    )
                results[witness.witness_id] = "skipped"
                continue
            if not changed:
                invalidate_derived(manifest)
                changed = True
            writer = PartitionWriter(root, stage)
            clock_ids = []
            for table, row in adapter_named(witness.adapter, config).ingest(snapshot, witness):
                writer.add(table, row)
                if table == "clocks":
                    clock_ids.append(row.model_dump()["clock_id"])
            witness = witness.model_copy(
                update={
                    "row_count": writer.counts.get("citations", 0),
                    "clock_ids": tuple(clock_ids),
                }
            )
            manifest["witnesses"][witness.witness_id] = witness.model_dump(mode="json")
            writer.add("witnesses", witness)
            manifest["partitions"][stage] = writer.finish()
            update_manifest_stage(manifest, stage, params={"case_config_sha256": config_sha256})
            results[witness.witness_id] = "ingested"
        with Store(root, manifest) as store:
            validate_graph(store)
    return results


def describe_witness(root: Path, witness_id: str) -> dict:
    manifest = read_manifest(root)
    if witness_id not in manifest["witnesses"]:
        raise ValueError(f"unknown witness: {witness_id}")
    with Store(root, manifest) as store:
        return {
            "witness": manifest["witnesses"][witness_id],
            "schema": store.query("DESCRIBE entities"),
            "entities": store.query(
                "SELECT subkind,count(*) AS rows,count(distinct natural_key) AS distinct_keys,count(time_lower) AS timed FROM native_entities WHERE witness_id=? GROUP BY 1 ORDER BY 1",
                [witness_id],
            ),
            "join_keys": store.query(
                "SELECT subkind,json_keys(attrs) AS fields FROM native_entities WHERE witness_id=? LIMIT 5",
                [witness_id],
            ),
        }
