"""Offline, paired evidence-removal checks. Development fixtures, not an accuracy study.

Only this validation module reads private scoring truth. The reconciler receives
ordinary acquired witnesses. Full-receipt copying uses a public transcript before
withholding it, never the private dispatch bindings or a real credential.
"""

import json
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from inspect_ai.event import ToolEvent
from inspect_ai.log import read_eval_log, write_eval_log
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool
from inspect_ai.tool import ToolCall

from evidencegraph.case import add_witness, ingest, init_case
from evidencegraph.docket.render import render
from evidencegraph.export.bagit import export_bundle, verify_bundle
from evidencegraph.lab.io import save_json
from evidencegraph.lab.stage import construct_stage, leak_check
from evidencegraph.manifest import read_manifest
from evidencegraph.provenance import runtime_versions, sha256_file, sha256_text, source_tree_sha256
from evidencegraph.reconcile.engine import reconcile_case, reconcile_registry
from evidencegraph.reconcile.substrates.registry import RegistrySubstrate
from evidencegraph.schema import CaseConfig, ClockBound, TrustDomain, TrustDomainRelation
from evidencegraph.store import Store
from evidencegraph.validate.score import validate_case
from evidencegraph.validate.truth_store import host_key


@dataclass(frozen=True)
class Scenario:
    id: str
    view: str
    description: str
    # Expected: supported, correct, errors, captured fabricated events, contradicted.
    expected: tuple[int, int, int, int, int]
    exclusive: bool = True
    independent: bool = True
    clock: bool = True
    invalid_assumption: bool = False


SCENARIOS = (
    Scenario(
        "clean", "clean", "Complete evidence; tokens isolated by construction", (12, 12, 0, 0, 0)
    ),
    Scenario("fabricated", "fabricated", "Two invented successful receipts", (12, 12, 0, 2, 2)),
    Scenario(
        "missing_transcript", "missing", "Withhold A; retain two invented receipts", (9, 9, 0, 2, 2)
    ),
    Scenario(
        "copied_public_receipt",
        "public-copy",
        "Copy A's ledger row into B; withhold A",
        (9, 9, 0, 1, 0),
    ),
    Scenario(
        "full_receipt_relay",
        "full-copy",
        "Copy A's full receipt into B; withhold A",
        (0, 0, 0, 1, 0),
        exclusive=False,
    ),
    Scenario(
        "false_exclusivity",
        "full-copy",
        "Same relay bytes, falsely declare exclusive tokens",
        (10, 9, 1, 1, 0),
        invalid_assumption=True,
    ),
    Scenario(
        "shared_tokens_no_attack",
        "clean",
        "Clean bytes; token exclusivity not established",
        (0, 0, 0, 0, 0),
        exclusive=False,
    ),
    Scenario(
        "missing_tokens", "no-tokens", "Remove receipt tokens, retain commitments", (0, 0, 0, 0, 0)
    ),
    Scenario(
        "missing_clock_bound",
        "clean",
        "No relation declared between source clocks",
        (0, 0, 0, 0, 0),
        clock=False,
    ),
    Scenario(
        "dependent_witnesses",
        "fabricated",
        "Registry and transcript declared dependent",
        (0, 0, 0, 2, 0),
        independent=False,
    ),
    Scenario(
        "incomplete_capture",
        "incomplete",
        "Invented receipts; population not complete",
        (12, 12, 0, 2, 0),
    ),
    Scenario(
        "capture_window_edge",
        "window-edge",
        "Invented receipts at capture's upper boundary",
        (12, 12, 0, 2, 0),
    ),
)


def _inventory(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): {"sha256": sha256_file(p), "size_bytes": p.stat().st_size}
        for p in sorted(root.rglob("*"))
        if p.is_file() and p != root / "manifest.json"
    }


def prepare_view(stage: Path, out: Path, view: str) -> tuple[Path, Path]:
    """Transform a copied observable corpus before acquisition; keep truth separate."""
    public, truth = out / "public", out / "private"
    shutil.copytree(stage / "public", public)
    shutil.copytree(stage / "private", truth)
    logs = {}
    for path in sorted((public / "transcripts").glob("*.eval")):
        log = read_eval_log(path)
        assert log.samples and len(log.samples) == 1
        logs[str(log.samples[0].id)] = (path, log)
    b_path, b_log = logs["agent-B"]
    assert b_log.samples
    b = b_log.samples[0]
    reference = next(
        e for e in b.events if isinstance(e, ToolEvent) and e.function == "registry_write"
    )
    if view == "window-edge":
        # Every real registry mutation remains inside capture. Only the widened
        # uncertainty window of the added claims extends beyond its endpoint.
        reference = max(
            (
                event
                for _, log in logs.values()
                for sample in log.samples or []
                for event in sample.events
                if isinstance(event, ToolEvent) and event.completed is not None
            ),
            key=lambda event: event.completed or event.timestamp,
        )
    spoofed = []

    def append(receipt: dict, timestamp: datetime, completed: datetime | None) -> None:
        call = ToolCall(
            id=f"synthetic-claim-{len(spoofed)}",
            function="registry_write",
            arguments={"name": receipt["name"], "payload": receipt["payload"]},
        )
        message = ChatMessageTool(
            content=json.dumps(receipt), tool_call_id=call.id, function=call.function
        )
        event = ToolEvent(
            id=call.id,
            function=call.function,
            arguments=call.arguments,
            result=message.text,
            timestamp=timestamp,
            completed=completed,
            message_id=message.id,
        )
        b.events.append(event)
        b.messages.extend(
            [ChatMessageAssistant(content="Synthetic receipt claim", tool_calls=[call]), message]
        )
        spoofed.append(
            {"event_uuid": event.uuid, "label": "B", "claimed_event_id": receipt["event_id"]}
        )

    if view in {"fabricated", "missing", "incomplete", "window-edge"}:
        for i in range(2):
            payload = f"synthetic invented report {i}"
            append(
                {
                    "accepted": True,
                    "namespace": "registry-lab",
                    "name": f"invented-{i}.txt",
                    "payload": payload,
                    "sha256": sha256_text(payload),
                    "event_id": f"absent-{i}",
                },
                reference.timestamp,
                reference.completed,
            )
    if view in {"public-copy", "full-copy"}:
        record = json.loads((public / "ledger.jsonl").read_text().splitlines()[0])
        receipt = {k: record[k] for k in ("namespace", "name", "path", "payload", "sha256")}
        receipt.update(accepted=True, event_id=record["id"])
        if view == "full-copy":
            a_log = logs["agent-A"][1]
            assert a_log.samples
            original = next(
                e
                for e in a_log.samples[0].events
                if isinstance(e, ToolEvent) and e.function == "registry_write"
            )
            receipt["receipt_token"] = json.loads(str(original.result))["receipt_token"]
        timestamp = datetime.fromisoformat(record["ts"])
        append(receipt, timestamp, timestamp)
    if spoofed:
        replacement = b_path.with_name("transformed.eval")
        write_eval_log(b_log, replacement)
        b_path.unlink()
        replacement.rename(replacement.with_name(sha256_file(replacement) + ".eval"))
    if view in {"missing", "public-copy", "full-copy"}:
        logs["agent-A"][0].unlink()
    if view == "no-tokens":
        for path, log in logs.values():
            assert log.samples
            sample = log.samples[0]
            for event in sample.events:
                if isinstance(event, ToolEvent) and event.function == "registry_write":
                    receipt = json.loads(str(event.result))
                    receipt.pop("receipt_token", None)
                    event.result = json.dumps(receipt)
                    for message in sample.messages:
                        if isinstance(message, ChatMessageTool) and message.id == event.message_id:
                            message.content = str(event.result)
            replacement = path.with_name("transformed.eval")
            write_eval_log(log, replacement)
            path.unlink()
            replacement.rename(replacement.with_name(sha256_file(replacement) + ".eval"))
    if view in {"incomplete", "window-edge"}:
        path = public / "population.json"
        population = json.loads(path.read_text())
        if view == "incomplete":
            population["complete"] = False
        else:
            assert reference.completed
            population["stopped_at"] = reference.completed.isoformat()
        save_json(path, population)
    save_json(truth / "spoofed.json", spoofed)
    save_json(public / "manifest.json", {"files": _inventory(public)})
    leak_check(public)
    return public, truth


def configuration_for(scenario: Scenario) -> CaseConfig:
    return CaseConfig(
        title=f"Synthetic stress: {scenario.id}"
        + (" — deliberately false declaration" if scenario.invalid_assumption else ""),
        trust_domains=(
            TrustDomain(
                id="registry",
                label="Registry",
                related_to={
                    "runner": TrustDomainRelation.INDEPENDENT
                    if scenario.independent
                    else TrustDomainRelation.SAME
                },
            ),
            TrustDomain(
                id="runner",
                label="Scripted Inspect records",
                exclusive_receipt_tokens=scenario.exclusive,
            ),
        ),
        clock_bounds=(
            ClockBound(
                clock_a="runner",
                clock_b="container",
                bound_seconds=0.05,
                source="Shared in-process clock; collection padded by 0.1 s",
            ),
        )
        if scenario.clock
        else (),
    )


def score_controls(case: Path, config: CaseConfig, truth: Path) -> dict:
    """Ablate the new declaration gate; score after prediction using host bindings."""
    manifest = read_manifest(case)
    with Store(case, manifest) as store:
        actions = {a["entity_id"]: a for a in store.entities("tool_event")}
        records = {r["entity_id"]: r for r in store.entities("registry_mutation")}
        legacy = config.model_copy(
            update={
                "trust_domains": tuple(
                    d.model_copy(update={"exclusive_receipt_tokens": True})
                    for d in config.trust_domains
                )
            }
        )
        predictions = [
            r for _, r in reconcile_registry(store, legacy, manifest, None) if r.kind == "produced"
        ]
        claims = [a for a in actions.values() if RegistrySubstrate().receipt(a)]
    # Private labels are read only after both methods have produced their decisions.
    key = host_key(truth)
    if {r["natural_key"] for r in records.values()} != set(key["produced"]):
        raise ValueError("control and truth populations differ")
    supported = [r for r in predictions if r.outcome == "supported"]
    correct = sum(
        actions[r.object_id]["attrs"]["event_uuid"]
        == key["produced"][records[r.subject_id]["natural_key"]]["event_uuid"]
        for r in supported
    )
    fabricated = {r["event_uuid"] for r in key["spoofed"]}
    return {
        "implicit_token_exclusivity": {
            "supported": len(supported),
            "correct": correct,
            "confident_errors": len(supported) - correct,
            "description": "Pre-fix decision rule: a matching token implicitly grants event binding, with all other rules unchanged",
        },
        "accept_success_claims": {
            "accepted_claims": len(claims),
            "false_accepts": sum(a["attrs"]["event_uuid"] in fabricated for a in claims),
            "description": "Scripted positive control accepting every well-formed successful receipt; claim-level, not a model or investigator evaluation",
        },
    }


def run_stress(out: Path, *, seeds: tuple[int, ...] = (0, 1, 2), bundles: bool = True) -> dict:
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError("provide at least one distinct seed")
    if out.exists():
        raise ValueError("choose a new output directory")
    out.mkdir(parents=True)
    rows, failures = [], []
    source_root = Path(__file__).resolve().parents[1]
    source_hash = source_tree_sha256(source_root)
    for seed in seeds:
        world = out / "worlds" / str(seed)
        construct_stage(world, seed=seed, settle_seconds=0.1)
        views = {}
        for scenario in SCENARIOS:
            if scenario.view not in views:
                views[scenario.view] = prepare_view(
                    world, out / "views" / str(seed) / scenario.view, scenario.view
                )
            public, truth = views[scenario.view]
            case = out / "cases" / f"{seed}-{scenario.id}"
            config = configuration_for(scenario)
            init_case(case, config)
            add_witness(case, public, adapter="lab-public", trust_domain="registry")
            add_witness(case, public / "transcripts", adapter="inspect-eval", trust_domain="runner")
            ingest(case)
            reconcile_case(case, "registry")
            controls = score_controls(case, config, truth)
            score = validate_case(case, truth)
            render(case)
            with Store(case, read_manifest(case)) as store:
                outcomes = dict(
                    Counter(
                        r["outcome"]
                        for r in store.query("SELECT outcome FROM relations WHERE kind='produced'")
                    )
                )
                methods = dict(
                    Counter(
                        r["method"]
                        for r in store.query("SELECT method FROM relations WHERE kind='produced'")
                    )
                )
            produced, spoofed = score["produced"], score["spoofed"]
            actual = (
                produced["supported"],
                produced["correct"],
                produced["confident_errors"],
                spoofed["captured"],
                spoofed["contradicted"],
            )
            passed = actual == scenario.expected and spoofed["false_positives"] == 0
            if not passed:
                failures.append(
                    {
                        "seed": seed,
                        "scenario": scenario.id,
                        "expected": scenario.expected,
                        "actual": actual,
                    }
                )
            row = {
                "seed": seed,
                "scenario": scenario.id,
                "description": scenario.description,
                "invalid_assumption_control": scenario.invalid_assumption,
                "case": case.relative_to(out).as_posix(),
                "public_inventory_sha256": sha256_file(public / "manifest.json"),
                "truth_inventory": _inventory(truth),
                "case_config_sha256": sha256_file(case / "case.json"),
                "score": score,
                "controls": controls,
                "outcomes": outcomes,
                "methods": methods,
                "expected": scenario.expected,
                "passed": passed,
            }
            if (
                bundles
                and seed == seeds[0]
                and scenario.id in {"clean", "missing_transcript", "full_receipt_relay"}
            ):
                bundle = out / "bundles" / f"{seed}-{scenario.id}"
                export_bundle(case, bundle)
                row["bundle"] = bundle.relative_to(out).as_posix()
                row["verification"] = verify_bundle(bundle, recompute=True)
            rows.append(row)
            print(
                f"seed={seed} {scenario.id}: supported={actual[0]} errors={actual[2]} fabricated_receipts_contradicted={actual[4]}/{actual[3]}",
                flush=True,
            )
    if source_tree_sha256(source_root) != source_hash:
        raise ValueError("analyzer source changed during the run; rerun with a stable checkout")
    result = {
        "schema_version": "incident-stress-1",
        "source_tree_sha256": source_hash,
        "runtime": runtime_versions(),
        "seeds": seeds,
        "design": "Paired development checks on one scripted scenario per seed; views share truth and are not independent trials",
        "limitations": [
            "No real attackers, model calls, human investigators, or production incident logs",
            "No accuracy, time-saving, calibrated-coverage, or containment-effectiveness estimate",
            "False exclusivity is deliberately invalid and must produce a confident error",
            "Bundle recomputation verifies the docket against the bundled graph, not authenticity of the original capture or truth labels",
        ],
        "rows": rows,
        "failures": failures,
        "passed": not failures,
    }
    save_json(out / "results.json", result)
    (out / "RESULTS.md").write_text(render_results(result), encoding="utf-8")
    return result


def render_results(result: dict) -> str:
    lines = [
        "# Evidence sufficiency stress results",
        "",
        result["design"] + ".",
        "",
        "Each row below is one view of the first seed. All seeds and private scoring inventories are in `results.json`. A fabricated claim not contradicted remains unresolved; it is not accepted as true.",
        "",
        "| Evidence condition | Correct supported / 12 | Confident errors | Fabricated claims contradicted | Old token rule errors | Claim-only false accepts |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in result["rows"]:
        if row["seed"] != result["seeds"][0]:
            continue
        p, s = row["score"]["produced"], row["score"]["spoofed"]
        c = row["controls"]
        lines.append(
            f"| {row['scenario']} | {p['correct']} / {p['population']} | {p['confident_errors']} | {s['contradicted']} / {s['captured']} | {c['implicit_token_exclusivity']['confident_errors']} | {c['accept_success_claims']['false_accepts']} |"
        )
    lines.extend(
        [
            "",
            f"Contract checks: {sum(r['passed'] for r in result['rows'])}/{len(result['rows'])} passed.",
            "",
            "`false_exclusivity` is an intentionally false declaration on exactly the same receipt-relay evidence. Its error is the expected negative control, not evidence of safe behavior under that declaration.",
            "",
            "Default abstention also removes correct attributions when receipt isolation is unknown. The clean `shared_tokens_no_attack` control makes this cost visible.",
            "",
            "## Limits",
            "",
            *[f"- {line}." for line in result["limitations"]],
            "",
            "Run `uv run python scripts/incident_stress.py NEW_DIRECTORY` to regenerate. Paths, timestamps, tokens, and native UUIDs change; scenario decisions and scores should reproduce.",
            "",
        ]
    )
    return "\n".join(lines)
