"""Private scanner keys and validation of independently produced claims."""

import asyncio
import csv
import json
from pathlib import Path
from uuid import uuid4

from inspect_scout import transcripts_db

from evidencegraph.adapters.inspect_eval import read_transcripts
from evidencegraph.derive import run_stage
from evidencegraph.ids import entity_id
from evidencegraph.manifest import read_manifest, update_manifest_stage
from evidencegraph.provenance import sha256_file, verify_file_identity
from evidencegraph.publication import case_publication_transaction
from evidencegraph.scanners.claimed_writes import ClaimedWrites, mock_claim_scanner
from evidencegraph.schema import Annotation
from evidencegraph.store import Store, safe_path


def validation_csv(case: Path, truth: Path, dest: Path) -> dict:
    from evidencegraph.validate.truth_store import host_key

    key = host_key(truth)
    actual = {p["event_uuid"] for p in key["produced"].values() if p["event_uuid"]}
    with Store(case, read_manifest(case)) as store:
        actions = store.entities("tool_event")
    groups = {a["attrs"]["transcript_id"]: [] for a in actions}
    for action in actions:
        if action["attrs"]["event_uuid"] in actual:
            groups[action["attrs"]["transcript_id"]].append(action["attrs"]["event_uuid"])
    if dest.exists():
        raise ValueError("validation key destination already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "target", "split"])
        writer.writeheader()
        for tid, targets in sorted(groups.items()):
            writer.writerow(
                {"id": tid, "target": json.dumps(sorted(targets)), "split": "validation"}
            )
    return {"path": str(dest), "rows": len(groups), "sha256": sha256_file(dest)}


def run_scan(
    case: Path, *, scanner: str, validation: Path, model: str = "mockllm/claims", max_usd: float = 0
) -> dict:
    if scanner != "claimed-writes":
        raise ValueError("available scanner: claimed-writes")
    if model != "mockllm/claims" or max_usd != 0:
        raise ValueError(
            "Offline Scout control only: --model mockllm/claims --max-usd 0. Paid execution requires provider-enforced spend accounting."
        )
    with validation.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or any(row.get("split") != "validation" for row in rows):
        raise ValueError("held-out validation CSV required")
    targets = {row["id"]: set(json.loads(row["target"])) for row in rows}
    if len(targets) != len(rows):
        raise ValueError("duplicate validation transcript IDs")

    def derive(store, config, manifest):

        actions = store.entities("tool_event")
        refs = {a["attrs"]["event_uuid"]: a["citation_id"] for a in actions}
        seen = set()
        for witness in manifest["witnesses"].values():
            if witness["adapter"] != "inspect-eval":
                continue
            path = safe_path(case, witness["snapshot_path"])
            verify_file_identity(
                path,
                expected_size=witness["size_bytes"],
                expected_sha256=witness["sha256"],
                subject=witness["witness_id"],
            )
            for transcript in read_transcripts([path]):
                tid = transcript.transcript_id
                if tid not in targets:
                    raise ValueError("validation keys must cover every scanned transcript")
                seen.add(tid)
                # Scripted native logs have ToolEvents but no model timeline. Scout
                # otherwise builds an empty timeline and omits the raw messages.
                presented = transcript.model_copy(update={"events": [], "timelines": []})

                async def scan_one(transcript=transcript, presented=presented):
                    return await mock_claim_scanner(transcript)(presented)

                result = asyncio.run(scan_one())
                if isinstance(result, list):
                    raise ValueError("expected one structured scanner result")
                value = result.value
                if not isinstance(value, dict):
                    raise ValueError("scanner did not return structured claims")
                parsed = ClaimedWrites.model_validate(value)
                claims = {write.event_uuid for write in parsed.writes}
                if claims - set(refs):
                    raise ValueError("scanner cited an unknown tool event")
                tp = len(claims & targets[tid])
                fp = len(claims - targets[tid])
                fn = len(targets[tid] - claims)
                yield (
                    "annotations",
                    Annotation(
                        annotation_id=entity_id("annotation", witness["witness_id"], tid),
                        scanner=scanner,
                        transcript_id=tid,
                        value=value,
                        citation_ids=tuple(sorted(refs[u] for u in claims)),
                        validation={
                            "true_positive": tp,
                            "false_positive": fp,
                            "false_negative": fn,
                            "precision": tp / len(claims) if claims else None,
                            "key_sha256": sha256_file(validation),
                            "interpretation": "Scripted claim-repetition control; not real model quality",
                        },
                        model=model,
                        spend_usd=0,
                    ),
                )
        if seen != set(targets):
            raise ValueError("validation contains transcripts outside the captured population")

    return run_stage(
        case,
        "scan.claimed-writes",
        derive,
        params={"model": model, "spend_usd": 0, "validation_sha256": sha256_file(validation)},
    )


def scout_database(case: Path) -> dict:
    with case_publication_transaction(case) as manifest:
        witnesses = [w for w in manifest["witnesses"].values() if w["adapter"] == "inspect-eval"]
        if not witnesses:
            raise ValueError("no Inspect witnesses ingested")
        paths = []
        for w in witnesses:
            path = safe_path(case, w["snapshot_path"])
            verify_file_identity(
                path,
                expected_size=w["size_bytes"],
                expected_sha256=w["sha256"],
                subject=w["witness_id"],
            )
            paths.append(path)
        transcripts = read_transcripts(paths)
        destination = case / "scout" / uuid4().hex

        async def insert():
            async with transcripts_db(str(destination)) as db:
                await db.insert(transcripts)

        asyncio.run(insert())
        manifest["scout"] = {
            "path": destination.relative_to(case).as_posix(),
            "files": {
                p.relative_to(case).as_posix(): sha256_file(p)
                for p in destination.rglob("*")
                if p.is_file()
            },
        }
        update_manifest_stage(manifest, "scout-db", params={"transcripts": len(transcripts)})
    return {"path": str(destination), "transcripts": len(transcripts)}
