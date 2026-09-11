"""Build a local, zero-cost staged incident and its verifiable docket."""

import argparse
from pathlib import Path

from evidencegraph.case import add_witness, ingest, init_case
from evidencegraph.coverage.estimate import estimate
from evidencegraph.docket.render import render
from evidencegraph.export.bagit import export_bundle
from evidencegraph.lab.stage import construct_stage
from evidencegraph.reconcile.engine import reconcile_case
from evidencegraph.scanners.validation import run_scan, scout_database, validation_csv
from evidencegraph.schema import CaseConfig, ClockBound, TrustDomain, TrustDomainRelation
from evidencegraph.validate.score import validate_case


def demo(out: Path, seed: int = 1) -> Path:
    if out.exists():
        raise ValueError("choose a new output directory")
    stage, case = out / "stage", out / "case"
    construct_stage(stage, seed=seed, spoof="B:2", drop=["A"])
    init_case(
        case,
        CaseConfig(
            title=f"Staged registry incident — seed {seed}",
            trust_domains=(
                TrustDomain(
                    id="registry",
                    label="Host registry",
                    related_to={"runner": TrustDomainRelation.INDEPENDENT},
                ),
                TrustDomain(id="runner", label="Inspect runner"),
            ),
            clock_bounds=(ClockBound(clock_a="runner", clock_b="container", bound_seconds=1),),
        ),
    )
    add_witness(case, stage / "public", adapter="lab-public", trust_domain="registry")
    add_witness(
        case, stage / "public" / "transcripts", adapter="inspect-eval", trust_domain="runner"
    )
    ingest(case)
    reconcile_case(case, "registry")
    keys = stage / "private" / "validation.csv"
    validation_csv(case, stage / "private", keys)
    run_scan(case, scanner="claimed-writes", validation=keys)
    scout_database(case)
    estimate(case, seed=seed)
    validate_case(case, stage / "private")
    render(case)
    export_bundle(case, out / "bundle")
    return case / "DOCKET.md"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    print(demo(args.out, args.seed))
