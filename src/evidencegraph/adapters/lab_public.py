"""Only public registry observations; the adapter has no truth-store capability."""

import re
from pathlib import Path

from evidencegraph.adapters.base import AdapterOutput, Builder
from evidencegraph.provenance import sha256_file, sha256_text
from evidencegraph.refs import citation, json_citation, jsonl_rows
from evidencegraph.schema import Clock, Witness
from evidencegraph.strict_json import load_strict_json


class LabPublic:
    def describe(self, path: Path) -> dict:
        kinds = {
            "ledger.jsonl": "ledger",
            "population.json": "population_declaration",
            "registry-reads.json": "read_log",
            "registry-refresh.json": "refresh_log",
        }
        if path.name in kinds:
            return {"kind": kinds[path.name]}
        if re.fullmatch(r"[0-9a-f]{64}", path.name):
            return {"kind": "artifact_store"}
        raise ValueError(f"unsupported public lab artifact: {path.name}")

    def ingest(self, path: Path, witness: Witness) -> AdapterOutput:
        builder = Builder(witness)
        clock_id = f"{witness.witness_id}:container"
        yield "clocks", Clock(clock_id=clock_id, witness_id=witness.witness_id, label="container")
        if witness.kind == "artifact_store":
            if sha256_file(path) != path.name:
                raise ValueError("content-addressed artifact hash mismatch")
            ref = citation(witness.witness_id, "file", path.name, path.read_bytes())
            builder.entity(
                "artifact_version",
                "artifact",
                path.name,
                ref,
                {"sha256": path.name, "bytes_verified": True},
            )
            yield from builder.drain()
            return
        if path.name == "ledger.jsonl":
            rows = jsonl_rows(path, witness.witness_id)
        else:
            value = load_strict_json(path, max_bytes=32 * 1024 * 1024)
            values = value if isinstance(value, list) else [value]
            rows = (
                (
                    row,
                    json_citation(
                        witness.witness_id, f"$[{i}]" if isinstance(value, list) else "$", row
                    ),
                )
                for i, row in enumerate(values)
            )
        seen = set()
        artifacts = {}
        pending = []
        for index, (row, ref) in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError("public registry observations must be objects")
            time = row.get("ts")
            timing = (
                {
                    "time_lower": time,
                    "time_upper": time,
                    "time_grade": "container_recorded",
                    "time_uncertainty_s": 0,
                    "winning_clock_id": clock_id,
                }
                if time
                else {}
            )
            if path.name == "ledger.jsonl":
                key = row["id"]
                if key in seen:
                    raise ValueError(f"duplicate registry id: {key}")
                seen.add(key)
                if row.get("payload") is not None and sha256_text(row["payload"]) != row.get(
                    "sha256"
                ):
                    raise ValueError("registry payload hash mismatch")
                entity = builder.entity("action", "registry_mutation", key, ref, row, **timing)
                if row.get("sha256"):
                    artifact = builder.entity(
                        "artifact_version",
                        "artifact",
                        row["sha256"],
                        ref,
                        {"sha256": row["sha256"], "observed_as_reference": True},
                    )
                    artifacts[row["sha256"]] = artifact
                    builder.edge(
                        "member_of", entity, artifact, ref, method="registry_payload_sha256"
                    )
                    if row.get("previous_sha256") and row["previous_sha256"] != row["sha256"]:
                        pending.append((artifact, row["previous_sha256"], ref))
            elif path.name == "population.json":
                if row.get("count") != len(row.get("event_ids", [])) or len(
                    set(row.get("event_ids", []))
                ) != row.get("count"):
                    raise ValueError("invalid population enumeration")
                builder.entity("task", "population", row["namespace"], ref, row)
            elif path.name == "registry-refresh.json":
                builder.entity("task", "refresh", str(index), ref, row, **timing)
            else:
                attrs = {k: v for k, v in row.items() if k != "payload"}
                digest = (
                    sha256_text(row["payload"]) if isinstance(row.get("payload"), str) else None
                )
                attrs["payload_sha256"] = digest
                entity = builder.entity("action", "read", str(index), ref, attrs, **timing)
                if digest:
                    artifact = builder.entity(
                        "artifact_version",
                        "artifact",
                        digest,
                        ref,
                        {"sha256": digest, "observed_as_reference": True},
                    )
                    builder.edge(
                        "read",
                        entity,
                        artifact,
                        ref,
                        method="served_payload_sha256",
                        rationale="Registry read observation records these served bytes; reader identity not published",
                    )
            yield from builder.drain()
        for artifact, previous, ref in pending:
            if previous in artifacts:
                builder.edge(
                    "built_on",
                    artifact,
                    artifacts[previous],
                    ref,
                    method="registry_previous_sha256",
                    rationale="Host-recorded prior version at the same registry name; does not prove semantic adaptation",
                )
        yield from builder.drain()
