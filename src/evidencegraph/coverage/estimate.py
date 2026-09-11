from collections import Counter
from pathlib import Path

from evidencegraph.coverage.capture_recapture import chapman
from evidencegraph.coverage.declared import message_weighted, proportion_interval, unique_author
from evidencegraph.derive import run_stage
from evidencegraph.schema import CoverageEstimate, CoverageKind, Population


def derive_coverage(store, config, manifest, *, level: float, seed: int):
    records = store.entities("registry_mutation")
    outcomes = store.query(
        "SELECT * FROM relations WHERE kind='produced' AND run_id='reconcile.registry'"
    )
    if records:
        if len(outcomes) != len(records):
            raise ValueError("reconcile all registry records before estimating coverage")
        for namespace in sorted({r["attrs"]["namespace"] for r in records}):
            ids = {r["entity_id"] for r in records if r["attrs"]["namespace"] == namespace}
            population = Population(
                id=namespace,
                namespace=namespace,
                description="Accepted registry mutations in published population declaration",
            )
            declarations = store.entities("population")
            declared = [d for d in declarations if d["attrs"].get("namespace") == namespace]
            if len(declared) != 1 or set(declared[0]["attrs"].get("event_ids", [])) != {
                r["natural_key"] for r in records if r["entity_id"] in ids
            }:
                raise ValueError(
                    "population declaration does not exactly enumerate registry records"
                )
            rows = [r for r in outcomes if r["subject_id"] in ids]
            yield "populations", population
            yield (
                "coverage",
                message_weighted(
                    rows,
                    population,
                    reference_source=declared[0]["witness_id"],
                    seed=seed,
                    level=level,
                ),
            )
            # No capture-recapture here: transcript claims can be fabricated, so they are
            # not a second capture of the population, and the declaration already
            # enumerates every record. Counting claims as members inflated the union
            # above the true population.
    revisions = store.entities("revision")
    if revisions:
        labels = store.entities("label")
        # save_requests in this export are dse-only (edit_actors.jsonl), while stored_revisions spans all wikis.
        by_label = Counter(
            r["attrs"].get("label") for r in revisions if r["attrs"].get("wiki") == "dse"
        )
        for label in labels:
            attrs = label["attrs"]
            denominator = attrs.get("save_requests")
            if denominator is None or denominator == 0:
                continue
            numerator = by_label[label["natural_key"]]
            if numerator > denominator:
                # Mismatched cuts cannot produce a valid fraction; explicitly skip, docket counts it.
                continue
            population = Population(
                id="dse-label-" + label["entity_id"],
                namespace="dse",
                description=f"Publisher-reported DSE save requests for label {label['natural_key']!r}",
            )
            yield (
                "coverage",
                CoverageEstimate(
                    id="cov-" + label["entity_id"],
                    kind=CoverageKind.MESSAGE_WEIGHTED,
                    reference_source=label["witness_id"],
                    population=population,
                    sampling_unit="DSE save request",
                    design="Publisher aggregate denominator, stored DSE revisions numerator; conditional bootstrap",
                    estimate=numerator / denominator,
                    interval=proportion_interval(denominator, numerator, seed=seed, level=level),
                    interval_level=level,
                    n_population=denominator,
                    n_sampled=denominator,
                    n_supported=numerator,
                    assumptions=(
                        "Denominator is an unaudited request-log aggregate; raw requests are unpublished",
                        "Write-date and request-log cuts may differ; this is descriptive retention, not fleet coverage",
                    ),
                ),
            )
        events = store.entities("wiki_event")
        saves = [e for e in events if e["attrs"].get("event_type") == "save"]
        keys = {r["natural_key"] for r in revisions}
        overlap = len({s["attrs"].get("revision_ref") for s in saves} & keys)
        yield (
            "capture_recapture",
            chapman(
                len(saves),
                len(revisions),
                overlap,
                seed=seed,
                level=level,
                witness_a="published-save-events",
                witness_b="stored-revisions",
                violations=(
                    "Published save events are generated from stored revisions, not an independent request capture",
                    "A stored revision implies a request; raw request logs are unavailable",
                ),
            ),
        )
        population = Population(
            id="published-labels",
            namespace="wiki",
            description="Nonempty published labels, not actors",
        )
        yield "populations", population
        yield (
            "coverage",
            unique_author(
                [],
                population,
                {label_row["entity_id"]: label_row["natural_key"] for label_row in labels},
                reference_source="published-labels",
                seed=seed,
                level=level,
                covered_units={
                    r["attrs"].get("label") for r in revisions if r["attrs"].get("label")
                },
            ),
        )


def estimate(root: Path, *, level: float = 0.95, seed: int = 0) -> dict:
    return run_stage(
        root,
        "coverage",
        lambda s, c, m: derive_coverage(s, c, m, level=level, seed=seed),
        params={"level": level, "seed": seed},
    )
