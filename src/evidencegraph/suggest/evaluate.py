"""Score a published suggestion run against labels kept outside the case.

Inference never reads labels, and scoring writes nothing into the case. Labels are
whatever the caller supplies; this module checks their shape, not their truth.
"""

import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path

from evidencegraph.manifest import read_manifest
from evidencegraph.suggest.records import Suggestion
from evidencegraph.suggest.run import load_run, rank

BUCKETS = ((0.0, 0.5), (0.5, 0.8), (0.8, 0.95), (0.95, 1.0001))


def read_labels(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows or not all(isinstance(r, dict) and r.get("message_id") for r in rows):
        raise ValueError("labels are JSONL objects that each name a message_id")
    return rows


def matcher(rows: list[Suggestion]) -> Callable[[dict], Suggestion | None]:
    """Find the span a label names; message ids need a transcript id when reused."""
    by_message: dict[str, list[Suggestion]] = defaultdict(list)
    for row in rows:
        by_message[row.message_id].append(row)

    def find(label: dict) -> Suggestion | None:
        transcript = label.get("transcript_id")
        found = [
            row
            for row in by_message.get(label["message_id"], [])
            if transcript is None or row.transcript_id == transcript
        ]
        if len(found) > 1:
            raise ValueError(
                f"label for message {label['message_id']} matches {len(found)} spans; "
                "add its transcript_id"
            )
        return found[0] if found else None

    return find


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def score_claims(rows: list[Suggestion], labels: list[dict], split: str | None) -> dict:
    wanted = [r for r in labels if "write_claim" in r and (split is None or r["split"] == split)]
    ids = Counter((r.get("transcript_id"), r["message_id"]) for r in wanted)
    if any(n > 1 for n in ids.values()):
        raise ValueError("duplicate write_claim labels for one message")
    find = matcher(rows)
    confusion: dict[str, Counter] = defaultdict(Counter)
    slices: dict[str, dict[str, Counter]] = {
        "family": defaultdict(Counter),
        "split": defaultdict(Counter),
        "disposition": defaultdict(Counter),
        "confidence": defaultdict(Counter),
    }
    brier, log_loss, errors, missing, unscored = [], [], [], [], []
    for label in wanted:
        row = find(label)
        if row is None:
            missing.append(label["message_id"])
            continue
        answer = row.answers["write_claim"]
        if answer.status != "answered" or answer.probabilities is None:
            unscored.append(label["message_id"])
            continue
        truth, got = label["write_claim"], answer.label
        correct = int(got == truth)
        confusion[truth][got or ""] += 1
        confidence = answer.confidence or 0.0
        bucket = next(f"{lo:.2f}-{min(hi, 1):.2f}" for lo, hi in BUCKETS if lo <= confidence < hi)
        for name, key in (
            ("family", label.get("family", "")),
            ("split", label.get("split", "")),
            ("disposition", row.disposition),
            ("confidence", bucket),
        ):
            slices[name][key]["n"] += 1
            slices[name][key]["correct"] += correct
        brier.append(
            sum((p - (option == truth)) ** 2 for option, p in answer.probabilities.items())
        )
        log_loss.append(-math.log(max(answer.probabilities.get(truth, 0.0), 1e-6)))
        if not correct:
            errors.append(
                {
                    "message_id": label["message_id"],
                    "family": label.get("family"),
                    "expected": truth,
                    "got": got,
                    "confidence": answer.confidence,
                    "p_expected": answer.probabilities.get(truth),
                }
            )
    answered = len(brier)
    classes = sorted(
        {r["write_claim"] for r in wanted} | {g for c in confusion.values() for g in c}
    )
    return {
        "labels": len(wanted),
        "answered": answered,
        "unscored": unscored,
        "missing_from_run": missing,
        "accuracy": ratio(sum(c[t] for t, c in confusion.items()), answered),
        "brier": sum(brier) / answered if answered else None,
        "log_loss": sum(log_loss) / answered if answered else None,
        "per_class": {
            c: {
                "precision": ratio(confusion[c][c], sum(confusion[t][c] for t in confusion)),
                "recall": ratio(confusion[c][c], sum(confusion[c].values())),
                "support": sum(confusion[c].values()),
            }
            for c in classes
        },
        "slices": {
            name: {
                key: {"n": v["n"], "accuracy": ratio(v["correct"], v["n"])}
                for key, v in sorted(values.items())
            }
            for name, values in slices.items()
        },
        "confusion": {t: dict(c) for t, c in sorted(confusion.items())},
        "errors": errors,
    }


def score_search(rows: list[Suggestion], labels: list[dict], query: str) -> dict:
    wanted = [r for r in labels if r.get("query") == query and r.get("relevant")]
    if not wanted:
        raise ValueError("labels name no relevant messages for this run's query")
    find = matcher(rows)
    matched = {label["message_id"]: find(label) for label in wanted}
    relevant = {row.span_id for row in matched.values() if row is not None}
    ranking = sorted(rows, key=rank)
    hits = [row.span_id in relevant for row in ranking]
    precision_sum, found = 0.0, 0
    for position, hit in enumerate(hits, 1):
        if hit:
            found += 1
            precision_sum += found / position
    first = next((i for i, hit in enumerate(hits, 1) if hit), None)
    return {
        "relevant": len(wanted),
        "ranked": len(ranking),
        "missing_from_run": sorted(m for m, row in matched.items() if row is None),
        **{f"recall@{k}": sum(hits[:k]) / len(wanted) for k in (3, 5, 10)},
        **{f"precision@{k}": sum(hits[:k]) / k for k in (3, 5, 10)},
        "average_precision": precision_sum / len(wanted),
        "reciprocal_rank": 1 / first if first else 0.0,
        "top": [row.message_id for row in ranking[:10]],
    }


def evaluate(case: Path, key: str, labels_path: Path, *, split: str | None = None) -> dict:
    manifest = read_manifest(case)
    entry = manifest.get("suggestions", {}).get(key)
    if entry is None:
        raise ValueError(f"no suggestion run {key}; see `eg suggest show CASE`")
    run, rows = load_run(case, entry)
    labels = read_labels(labels_path)
    result = (
        score_claims(rows, labels, split)
        if run.task == "write-claims"
        else score_search(rows, labels, run.query or "")
    )
    return {
        "run_key": key,
        "run_id": run.run_id,
        "provider": run.provider,
        "resolved_models": list(run.resolved_models),
        "split": split,
        **result,
        "interpretation": "Agreement with the supplied labels on this corpus only; not a "
        "forensic accuracy estimate",
    }
