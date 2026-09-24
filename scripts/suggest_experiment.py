"""Compare suggestion providers on a staged incident plus the labelled claims corpus.

    uv run python scripts/suggest_experiment.py cases/suggest-exp
    TYPESAFE_API_KEY=... uv run python scripts/suggest_experiment.py cases/suggest-exp \
        --typesafe --max-requests 400

The case holds the staged registry incident (one withheld transcript, two fabricated
receipts) and the claims corpus. Lab-message labels are derived from the scripted
structure; corpus labels come from `evidencegraph.lab.claims`. Scores print for the
`dev` and `lab` splits unless --heldout is given, so wording can be tuned without
looking at the held-out messages. The script also checks that the docket is
byte-identical before and after suggestions and that the exported bundle re-derives
every recorded answer offline.
"""

import argparse
import json
import sys
from pathlib import Path

from evidencegraph.adapters.inspect_eval import read_transcripts
from evidencegraph.case import add_witness, ingest, init_case
from evidencegraph.coverage.estimate import estimate
from evidencegraph.docket.render import compute_docket, render
from evidencegraph.export.bagit import export_bundle
from evidencegraph.lab.claims import SEARCH_QUERIES, construct_claims_corpus
from evidencegraph.lab.stage import construct_stage
from evidencegraph.manifest import read_manifest
from evidencegraph.reconcile.engine import reconcile_case
from evidencegraph.schema import CaseConfig, ClockBound, TrustDomain, TrustDomainRelation
from evidencegraph.suggest.evaluate import evaluate
from evidencegraph.suggest.providers import make_provider
from evidencegraph.suggest.run import run_key, run_suggestions


def lab_labels(transcripts: Path) -> list[dict]:
    rows = []
    s1 = SEARCH_QUERIES["S1"][0]
    for transcript in read_transcripts(sorted(transcripts.glob("*.eval"))):
        for message in transcript.messages:
            if message.role == "system":
                continue
            calls = [c.arguments for c in getattr(message, "tool_calls", None) or []]
            if "final-report.txt" in message.text + json.dumps(calls):
                rows.append({"query": s1, "message_id": message.id, "relevant": True})
            if message.role == "user":
                label, family = "plans_write", "lab_request"
            elif message.role == "assistant":
                functions = {c.function for c in message.tool_calls or []}
                label = "plans_write" if "registry_write" in functions else "no_write_claim"
                family = "lab_attempt" if label == "plans_write" else "lab_read"
            else:
                writes = getattr(message, "function", None) == "registry_write"
                label = "claims_completed_write" if writes else "no_write_claim"
                family = "lab_receipt" if writes else "lab_read"
            rows.append(
                {"message_id": message.id, "write_claim": label, "family": family, "split": "lab"}
            )
    return rows


def build(out: Path, seed: int) -> tuple[Path, Path]:
    stage, corpus, case = out / "stage", out / "corpus", out / "case"
    construct_stage(stage, seed=seed, spoof="B:2", drop=["A"])
    construct_claims_corpus(corpus)
    init_case(
        case,
        CaseConfig(
            title="Suggestion experiment",
            trust_domains=(
                TrustDomain(
                    id="registry",
                    label="Registry",
                    related_to={"runner": TrustDomainRelation.INDEPENDENT},
                ),
                TrustDomain(id="runner", label="Runner", exclusive_receipt_tokens=True),
            ),
            clock_bounds=(ClockBound(clock_a="runner", clock_b="container", bound_seconds=1),),
        ),
    )
    add_witness(case, stage / "public", adapter="lab-public", trust_domain="registry")
    add_witness(
        case, stage / "public" / "transcripts", adapter="inspect-eval", trust_domain="runner"
    )
    add_witness(
        case, corpus / "public" / "transcripts", adapter="inspect-eval", trust_domain="runner"
    )
    ingest(case)
    reconcile_case(case, "registry")
    estimate(case)
    render(case)
    labels = out / "labels.jsonl"
    rows = lab_labels(stage / "public" / "transcripts")
    rows += [
        json.loads(line) for line in (corpus / "private" / "labels.jsonl").read_text().splitlines()
    ]
    labels.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return case, labels


def forensic_state(case: Path) -> dict:
    """Everything a forensic conclusion is read from, plus a fresh docket recomputation."""
    manifest = read_manifest(case)
    return {
        "docket_file": (case / "docket.json").read_bytes(),
        "docket_recomputed": compute_docket(case, manifest),
        "partitions": manifest["partitions"],
        "stages": manifest["stages"],
        "reports": manifest["reports"],
    }


def claims_line(result: dict) -> str:
    split = result["slices"]["split"]
    parts = [f"{name} {v['accuracy']:.0%} (n={v['n']})" for name, v in split.items()]
    return (
        f"accuracy {result['accuracy']:.0%} over {result['answered']} answered; "
        + ", ".join(parts)
        + f"; brier {result['brier']:.3f}; log loss {result['log_loss']:.3f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--typesafe", action="store_true", help="Also query TypeSafe (paid)")
    parser.add_argument("--max-requests", type=int, default=0)
    parser.add_argument("--model", default=None)
    parser.add_argument("--heldout", action="store_true", help="Report the held-out split too")
    args = parser.parse_args()
    if args.out.exists():
        sys.exit(f"{args.out} exists; use a new directory")
    case, labels = build(args.out, args.seed)
    before = forensic_state(case)
    providers = ["baseline"] + (["typesafe"] if args.typesafe else [])
    splits = [None] if args.heldout else ["dev", "lab"]
    results: dict = {"claims": {}, "search": {}}

    def fresh(name: str):
        return make_provider(
            case,
            name,
            model=args.model,
            allow_network=args.typesafe,
            max_requests=args.max_requests,
            base_url=None,
            from_run=None,
        )

    for name in providers:
        provider, model = fresh(name)
        budget = args.max_requests if provider.network else None
        run = run_suggestions(
            case, task="write-claims", provider=provider, model=model, max_requests=budget
        )
        results["claims"][name] = {"run": run}
        print(f"[claims {name}] usage {run['usage']} models {run['resolved_models']}")
        for split in splits:
            scored = evaluate(case, run["run_key"], labels, split=split)
            results["claims"][name][split or "all"] = scored
            print(f"[claims {name} {split or 'all'}] {claims_line(scored)}")
            for error in scored["errors"]:
                print(f"    {error}")
        for query_id, (query, _) in SEARCH_QUERIES.items():
            provider, model = fresh(name)
            run_suggestions(
                case,
                task="search",
                provider=provider,
                model=model,
                query=query,
                max_requests=budget,
            )
            scored = evaluate(case, run_key("search", name, query), labels)
            results["search"][f"{name}:{query_id}"] = scored
            print(
                f"[search {name} {query_id}] AP {scored['average_precision']:.2f}; "
                f"P@5 {scored['precision@5']:.2f}; R@10 {scored['recall@10']:.2f}; "
                f"top {scored['top'][:5]}"
            )
    after = forensic_state(case)
    identical = before == after
    print(f"forensic docket, partitions, stages and reports identical: {identical}")
    bundle = export_bundle(case, args.out / "bundle")
    print(
        f"bundle verified: {bundle['verified']}; suggestions: {bundle.get('suggestions_rechecked')}"
    )
    results["forensic_state_identical"] = identical
    results["bundle"] = {k: v for k, v in bundle.items() if k != "bundle"}
    (args.out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    if not identical:
        sys.exit("suggestions changed forensic state")


if __name__ == "__main__":
    main()
