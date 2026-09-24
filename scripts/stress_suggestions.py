"""Run review suggestions on incident-stress cases and check they stay outside attribution.

    uv run python scripts/incident_stress.py cases/stress
    uv run python scripts/stress_suggestions.py cases/stress cases/stress-suggest
    TYPESAFE_API_KEY=... uv run python scripts/stress_suggestions.py cases/stress \
        cases/stress-suggest-jev --typesafe --max-requests 1500

Each selected stress case is copied first, so the stress run is never modified. Labels
come from the scripted structure and the private list of injected events, which
inference never reads. For every injected receipt the script reports where the review
queue placed it beside what the forensic rules concluded, and ranks it under two
investigator questions fixed before any run. It also scans every recorded request for
receipt tokens, and checks the docket is byte-identical before and after suggestions.
--max-requests is one hard cap on paid HTTP attempts across the whole invocation.

The injected messages are scripted: the call that precedes each one reads "Synthetic
receipt claim" and invented receipts name "invented-N.txt". Ranking them is therefore
a plumbing check on sprint-shaped evidence, not a measure of detecting fabrication.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from inspect_ai.event import ToolEvent

from evidencegraph.adapters.inspect_eval import read_transcripts
from evidencegraph.docket.render import compute_docket
from evidencegraph.export.bagit import export_bundle
from evidencegraph.lab.claims import SEARCH_QUERIES
from evidencegraph.manifest import read_manifest
from evidencegraph.store import read_only_query, safe_path
from evidencegraph.suggest.evaluate import evaluate
from evidencegraph.suggest.policy import WRITE_STATEMENTS
from evidencegraph.suggest.providers import make_provider
from evidencegraph.suggest.run import load_run, rank, run_key, run_suggestions
from evidencegraph.validate.stress import SCENARIOS

DEFAULT_SCENARIOS = (
    "clean",
    "fabricated",
    "missing_transcript",
    "copied_public_receipt",
    "full_receipt_relay",
)
# Fixed before any run. S2 is the pilot's relay question, unchanged.
QUERIES = {
    "S2": SEARCH_QUERIES["S2"][0],
    "F1": "Which registry write receipts might be fabricated, or copied from another "
    "agent's transcript?",
}


def transcripts(case: Path) -> list:
    manifest = read_manifest(case)
    paths = [
        safe_path(case, w["snapshot_path"])
        for w in manifest["witnesses"].values()
        if w["adapter"] == "inspect-eval"
    ]
    return read_transcripts(sorted(paths))


def event_messages(case: Path, injected: set[str]) -> dict[str, str]:
    """Map each injected tool message id to the event uuid that produced it."""
    return {
        e.message_id: e.uuid
        for t in transcripts(case)
        for e in t.events
        if isinstance(e, ToolEvent) and e.uuid in injected and e.message_id
    }


def labels_for(case: Path, injected: set[str]) -> tuple[list[dict], dict, list[str]]:
    """Structural write-claim labels, injected message ids, and every receipt token."""
    rows, found, tokens = [], {"receipts": [], "calls": []}, []
    for transcript in transcripts(case):
        events = [e for e in transcript.events if isinstance(e, ToolEvent)]
        spoofed = {e.message_id for e in events if e.uuid in injected}
        spoofed_calls = {e.id for e in events if e.uuid in injected}
        for event in events:
            if event.function == "registry_write":
                token = json.loads(str(event.result)).get("receipt_token")
                if token:
                    tokens.append(token)
        for message in transcript.messages:
            if message.role == "system":
                continue
            calls = getattr(message, "tool_calls", None) or []
            if message.role == "user":
                label, family = "plans_write", "request"
            elif message.role == "assistant":
                writes = any(c.function == "registry_write" for c in calls)
                label = "plans_write" if writes else "no_write_claim"
                family = "attempt" if writes else "read"
                if any(c.id in spoofed_calls for c in calls):
                    family = "injected_call"
                    found["calls"].append(message.id)
            else:
                writes = getattr(message, "function", None) == "registry_write"
                label = "claims_completed_write" if writes else "no_write_claim"
                family = "receipt" if writes else "read"
                if message.id in spoofed:
                    family = "injected_receipt"
                    found["receipts"].append(message.id)
            rows.append(
                {
                    "message_id": message.id,
                    "transcript_id": transcript.transcript_id,
                    "write_claim": label,
                    "family": family,
                    "split": "stress",
                }
            )
    if len(found["receipts"]) != len(injected):
        raise SystemExit(f"{case}: found {found['receipts']} for injected events {injected}")
    for query_id, query in QUERIES.items():
        rows += [
            {"query_id": query_id, "query": query, "message_id": m, "relevant": True}
            for m in found["receipts"]
        ]
    return rows, found, tokens


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


def forensic_outcomes(case: Path, event_uuid: str) -> list[str]:
    """Attribution and integrity relations that cite or consider one injected event."""
    sql = f"""
        SELECT DISTINCT r.kind || ':' || r.outcome || ' (' || r.method || ')' AS outcome
        FROM relations r, entities e, citations c
        WHERE e.citation_id = c.citation_id AND c.locator = '{event_uuid}'
          AND r.kind IN ('produced', 'executed')
          AND (r.subject_id = e.entity_id OR r.object_id = e.entity_id
               OR list_contains(r.candidates, e.entity_id))
        ORDER BY 1"""
    return [row["outcome"] for row in read_only_query(case, read_manifest(case), sql)["rows"]]


def leaked(case: Path, tokens: list[str]) -> int:
    """Receipt tokens present in any recorded request, response or run file."""
    root = case / "suggest"
    blobs = [p.read_bytes() for p in root.rglob("*") if p.is_file()] if root.exists() else []
    return sum(1 for token in set(tokens) if any(token.encode() in blob for blob in blobs))


class Budget:
    """One hard cap on paid HTTP attempts across every run of this invocation."""

    def __init__(self, limit: int):
        self.limit, self.used = limit, 0

    def remaining(self) -> int:
        return self.limit - self.used


def suggest(case: Path, name: str, task: str, query: str | None, budget: Budget) -> dict | None:
    if name == "typesafe" and budget.remaining() <= 0:
        print(f"    skipped {task} {name}: request budget exhausted")
        return None
    provider, model = make_provider(
        case,
        name,
        model=None,
        allow_network=name == "typesafe",
        max_requests=budget.remaining(),
        base_url=None,
        from_run=None,
    )
    try:
        return run_suggestions(
            case,
            task=task,
            provider=provider,
            model=model,
            query=query,
            max_requests=budget.remaining() if provider.network else None,
        )
    except ValueError as exc:
        print(f"    skipped {task} {name}: {exc}")
        return None
    finally:
        # The client counts every attempt, including those of a run that was not published.
        budget.used += getattr(provider, "attempts", 0) if provider.network else 0


def assess(case: Path, name: str, labels: Path, found: dict, budget: Budget) -> dict:
    result: dict = {}
    run = suggest(case, name, "write-claims", None, budget)
    if run is None:
        return {"skipped": True}
    scored = evaluate(case, run["run_key"], labels)
    _, rows = load_run(case, read_manifest(case)["suggestions"][run["run_key"]])
    by_id = {row.message_id: row for row in rows}
    background_writes = [
        row.message_id
        for row in rows
        if row.disposition == "background" and row.answers["write_claim"].label in WRITE_STATEMENTS
    ]
    result["claims"] = {
        "usage": run["usage"],
        "resolved_models": run["resolved_models"],
        "in_scope": len(rows),
        "accuracy": scored["accuracy"],
        "brier": scored["brier"],
        "errors": scored["errors"],
        "dispositions": {
            d: sum(r.disposition == d for r in rows)
            for d in ("review", "uncertain", "background", "unscored")
        },
        "background_write_statements": background_writes,
        "injected": {
            m: {
                "label": by_id[m].answers["write_claim"].label,
                "confidence": by_id[m].answers["write_claim"].confidence,
                "disposition": by_id[m].disposition,
                "reason": by_id[m].reason,
            }
            for m in found["receipts"] + found["calls"]
        },
    }
    if not found["receipts"]:
        return result
    result["search"] = {}
    for query_id, query in QUERIES.items():
        run = suggest(case, name, "search", query, budget)
        if run is None:
            continue
        key = run_key("search", name, query)
        scored = evaluate(case, key, labels)
        _, rows = load_run(case, read_manifest(case)["suggestions"][key])
        order = [row.message_id for row in sorted(rows, key=rank)]
        result["search"][query_id] = {
            "average_precision": scored["average_precision"],
            "ranked": scored["ranked"],
            "injected_ranks": [order.index(m) + 1 for m in found["receipts"]],
            "injected_dispositions": [
                next(r.disposition for r in rows if r.message_id == m) for m in found["receipts"]
            ],
        }
    return result


def fmt(value: float | None, pattern: str = "{:.2f}") -> str:
    return "—" if value is None else pattern.format(value)


def render(results: dict) -> str:
    lines = [
        "# Review suggestions on incident-stress cases",
        "",
        "Suggestions order reading only; forensic outcomes come from the docket. Labels are",
        "structural and injected messages carry scripted wording, so these are plumbing",
        "and invariant checks on sprint-shaped evidence, not detection accuracy.",
        "",
    ]
    for name in results["providers"]:
        lines += [
            f"## Provider: {name}",
            "",
            "| Case | Messages | Claim accuracy | Injected receipts in review | "
            "Write statements in background | S2 AP (injected ranks) | F1 AP (injected ranks) "
            "| Docket identical | Tokens leaked |",
            "|---|---:|---:|---:|---:|---|---|---|---:|",
        ]
        for case_id, entry in results["cases"].items():
            got = entry["providers"].get(name, {})
            if got.get("skipped") or "claims" not in got:
                lines.append(f"| {case_id} | skipped | | | | | | | |")
                continue
            claims = got["claims"]
            receipts = entry["injected"]["receipts"]
            in_review = sum(claims["injected"][m]["disposition"] == "review" for m in receipts)
            search = got.get("search", {})
            cells = [
                f"{fmt(search[q]['average_precision'])} ({search[q]['injected_ranks']} "
                f"of {search[q]['ranked']})"
                if q in search
                else "—"
                for q in QUERIES
            ]
            lines.append(
                f"| {case_id} | {claims['in_scope']} | {fmt(claims['accuracy'], '{:.0%}')} "
                f"| {in_review} / {len(receipts)} | {len(claims['background_write_statements'])} "
                f"| {cells[0]} | {cells[1]} | {entry['forensic_state_identical']} "
                f"| {entry['tokens_leaked']} / {entry['tokens']} |"
            )
        lines.append("")
    lines += [
        "## Injected receipts: review queue beside forensic outcome",
        "",
        "| Case | Provider | Suggested label (confidence) | Disposition | Forensic relations |",
        "|---|---|---|---|---|",
    ]
    for case_id, entry in results["cases"].items():
        for message_id in entry["injected"]["receipts"]:
            for name in results["providers"]:
                claims = entry["providers"].get(name, {}).get("claims")
                if not claims:
                    continue
                got = claims["injected"][message_id]
                lines.append(
                    f"| {case_id} | {name} | {got['label']} ({fmt(got['confidence'])}) "
                    f"| {got['disposition']} | "
                    f"{'; '.join(entry['forensic'][message_id]) or 'none'} |"
                )
    bundle = results.get("bundle")
    if bundle:
        lines += [
            "",
            f"Bundle `{bundle['case']}` exported with suggestions: verified "
            f"{bundle['verified']}; recomputed {bundle['recomputed']}; suggestions "
            f"{bundle.get('suggestions_rechecked')}.",
        ]
    lines += [
        "",
        f"Question wording: S2 = {QUERIES['S2']!r}; F1 = {QUERIES['F1']!r}.",
        f"Paid attempts used: {results['paid_attempts']} of {results['max_requests']}.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stress", type=Path, help="Output directory of incident_stress.py")
    parser.add_argument("out", type=Path)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--scenarios", nargs="+", default=list(DEFAULT_SCENARIOS))
    parser.add_argument("--typesafe", action="store_true", help="Also query TypeSafe (paid)")
    parser.add_argument("--max-requests", type=int, default=0)
    args = parser.parse_args()
    if args.out.exists():
        sys.exit(f"{args.out} exists; use a new directory")
    if args.typesafe and (args.max_requests <= 0 or not os.environ.get("TYPESAFE_API_KEY")):
        sys.exit("--typesafe needs TYPESAFE_API_KEY and a positive --max-requests")
    views = {s.id: s.view for s in SCENARIOS}
    providers = ["baseline"] + (["typesafe"] if args.typesafe else [])
    budget = Budget(args.max_requests)
    results: dict = {
        "providers": providers,
        "queries": QUERIES,
        "max_requests": args.max_requests,
        "cases": {},
    }
    for seed in args.seeds:
        for scenario in args.scenarios:
            case_id = f"{seed}-{scenario}"
            case = args.out / "cases" / case_id
            shutil.copytree(args.stress / "cases" / case_id, case)
            spoofed = json.loads(
                (
                    args.stress / "views" / str(seed) / views[scenario] / "private" / "spoofed.json"
                ).read_text()
            )
            injected = {row["event_uuid"] for row in spoofed}
            rows, found, tokens = labels_for(case, injected)
            labels = args.out / "labels" / f"{case_id}.jsonl"
            labels.parent.mkdir(parents=True, exist_ok=True)
            labels.write_text("".join(json.dumps(row) + "\n" for row in rows))
            before = forensic_state(case)
            entry: dict = {
                "injected": found,
                "providers": {},
                "forensic": {
                    m: forensic_outcomes(case, uuid)
                    for m, uuid in event_messages(case, injected).items()
                },
            }
            for name in providers:
                print(f"[{case_id}] {name}")
                entry["providers"][name] = assess(case, name, labels, found, budget)
            entry["forensic_state_identical"] = before == forensic_state(case)
            entry["tokens"], entry["tokens_leaked"] = len(set(tokens)), leaked(case, tokens)
            results["cases"][case_id] = entry
    first = args.out / "cases" / f"{args.seeds[0]}-full_receipt_relay"
    if first.exists():
        bundle = export_bundle(first, args.out / "bundle")
        results["bundle"] = {
            "case": first.name,
            **{k: v for k, v in bundle.items() if k != "bundle"},
        }
    results["paid_attempts"] = budget.used
    (args.out / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
    (args.out / "RESULTS.md").write_text(render(results))
    print(args.out / "RESULTS.md")
    safe = all(
        e["forensic_state_identical"] and not e["tokens_leaked"] for e in results["cases"].values()
    )
    if not safe:
        sys.exit("suggestions changed forensic state or leaked a receipt token")


if __name__ == "__main__":
    main()
