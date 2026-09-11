"""Score confident claims against private host truth, preserving abstentions."""

import shutil
from pathlib import Path
from uuid import uuid4

from evidencegraph.manifest import atomic_json, update_manifest_stage
from evidencegraph.provenance import sha256_file, sha256_text
from evidencegraph.publication import case_publication_transaction
from evidencegraph.store import Store
from evidencegraph.validate.truth_store import host_key


def validate_case(root: Path, truth: Path) -> dict:
    key = host_key(truth)
    with case_publication_transaction(root) as manifest:
        with Store(root, manifest) as store:
            records = {r["entity_id"]: r for r in store.entities("registry_mutation")}
            actions = {a["entity_id"]: a for a in store.entities("tool_event")}
            rows = store.query(
                "SELECT * FROM relations WHERE kind='produced' AND run_id='reconcile.registry'"
            )
            if len(rows) != len(records):
                raise ValueError("reconcile the full registry population before validation")
            if {r["natural_key"] for r in records.values()} != set(key["produced"]):
                raise ValueError("truth and graph populations differ")
            correct = errors = supported = 0
            details = []
            for row in rows:
                expected = key["produced"][records[row["subject_id"]]["natural_key"]]
                if row["outcome"] == "supported":
                    supported += 1
                    action = actions.get(row["object_id"])
                    accurate = bool(
                        action and action["attrs"]["event_uuid"] == expected["event_uuid"]
                    )
                    correct += accurate
                    errors += not accurate
                    if not accurate:
                        details.append(
                            {
                                "record": records[row["subject_id"]]["natural_key"],
                                "predicted": action["attrs"]["event_uuid"] if action else None,
                                "expected": expected["event_uuid"],
                            }
                        )
            captured = {a["attrs"]["event_uuid"] for a in actions.values()}
            identifiable = sum(
                p["event_uuid"] in captured for p in key["produced"].values() if p["event_uuid"]
            )
            spoofed = {s["event_uuid"] for s in key["spoofed"]} & captured
            flags = store.query(
                "SELECT * FROM relations WHERE kind='executed' AND method='complete_registry_absence'"
            )
            flagged = {
                actions[f["subject_id"]]["attrs"]["event_uuid"]
                for f in flags
                if f["outcome"] == "contradicted"
            }
            coverage = store.query(
                "SELECT estimate,interval FROM coverage WHERE kind='message_weighted'"
            )
            true_coverage = identifiable / len(records) if records else None
            bracket = (
                all(
                    c["interval"] and c["interval"][0] <= true_coverage <= c["interval"][1]
                    for c in coverage
                )
                if coverage and true_coverage is not None
                else None
            )
            result = {
                "produced": {
                    "precision": correct / supported if supported else None,
                    "recall": correct / identifiable if identifiable else None,
                    "recall_over_all_records": correct / len(records) if records else None,
                    "supported": supported,
                    "correct": correct,
                    "confident_errors": errors,
                    "abstention_rate": sum(
                        r["outcome"] in {"ambiguous", "unmatched", "not_assessable"} for r in rows
                    )
                    / len(rows)
                    if rows
                    else None,
                    "population": len(records),
                    "identifiable_with_captured_transcripts": identifiable,
                },
                "spoofed": {
                    "captured": len(spoofed),
                    "contradicted": len(spoofed & flagged),
                    "missed": len(spoofed - flagged),
                    "false_positives": len(flagged - spoofed),
                },
                "coverage": {
                    "true_captured_fraction": true_coverage,
                    "interval_brackets_truth": bracket,
                    "limitation": "Conditional check on this staged finite population; not a calibrated confidence guarantee for real missing evidence",
                },
                "errors": details,
                "unscored": {
                    "launched": "No independently observed background job in in-process stages",
                },
            }
            entities = {
                e["entity_id"]: e
                for subkind in ("artifact", "read")
                for e in store.entities(subkind, native=False)
            }
            truth_pairs = {
                (m["payload_sha256"], m["prior_sha256"])
                for m in key["mutations"]
                if m.get("payload_sha256")
                and m.get("prior_sha256")
                and m["payload_sha256"] != m["prior_sha256"]
            }
            lineage_rows = store.query(
                "SELECT * FROM relations WHERE kind='built_on' AND method='registry_previous_sha256'"
            )
            predicted_pairs = {
                (
                    entities[r["subject_id"]]["attrs"]["sha256"],
                    entities[r["object_id"]]["attrs"]["sha256"],
                )
                for r in lineage_rows
                if r["outcome"] == "supported"
            }
            result["built_on"] = {
                "precision": len(predicted_pairs & truth_pairs) / len(predicted_pairs)
                if predicted_pairs
                else None,
                "recall": len(predicted_pairs & truth_pairs) / len(truth_pairs)
                if truth_pairs
                else None,
                "confident_errors": len(predicted_pairs - truth_pairs),
            }
            truth_reads = {
                (row["ts"], row["route"], row["name"], row["status"], sha256_text(row["payload"]))
                for row in key["audit"]
                if row["kind"] == "read" and isinstance(row.get("payload"), str)
            }
            read_rows = store.query(
                "SELECT * FROM relations WHERE kind='read' AND method='served_payload_sha256'"
            )
            predicted_reads = set()
            for relation in read_rows:
                observation = entities[relation["subject_id"]]
                attrs = observation["attrs"]
                predicted_reads.add(
                    (
                        observation["time_lower"],
                        attrs["route"],
                        attrs["name"],
                        attrs["status"],
                        entities[relation["object_id"]]["attrs"]["sha256"],
                    )
                )
            result["read"] = {
                "precision": len(predicted_reads & truth_reads) / len(predicted_reads)
                if predicted_reads
                else None,
                "recall": len(predicted_reads & truth_reads) / len(truth_reads)
                if truth_reads
                else None,
                "confident_errors": len(predicted_reads - truth_reads),
            }
        directory = root / "reports" / uuid4().hex
        directory.mkdir(parents=True)
        path = directory / "validation.json"
        atomic_json(path, result)
        manifest["validation"] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
        }
        manifest["reports"] = {}
        manifest["partitions"].pop("docket", None)
        update_manifest_stage(
            manifest,
            "validate",
            params={"truth_input_sha256": sha256_file(truth / "bindings.jsonl")},
        )
    shutil.copyfile(path, root / "validation.json")
    return result
