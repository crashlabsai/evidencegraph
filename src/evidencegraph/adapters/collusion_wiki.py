"""Published collusion.wiki semantics, discovered from the actual September export.

Save events are derived from stored revisions, not an independent request log.
Grades describe corroboration; winning_clock describes the selected clock.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from evidencegraph.adapters.base import AdapterOutput, Builder
from evidencegraph.provenance import sha256_bytes, sha256_text
from evidencegraph.refs import csv_rows, json_citation, jsonl_rows
from evidencegraph.schema import Clock, EntityKind, Outcome, TimeClaim, Witness
from evidencegraph.strict_json import load_strict_json

FILES = {
    "revisions.jsonl",
    "pages.jsonl",
    "events.jsonl",
    "labels.jsonl",
    "records.jsonl",
    "links.jsonl",
    "site-coverage.csv",
    "coverage-gaps.csv",
    "other-wikis.json",
    "shortener-logs.json",
    "manifest.json",
}
RAW_CLOCKS = (
    "request_time",
    "success_time",
    "recent_changes_time",
    "write_date",
    "archived_at",
    "rcs_date",
)


def interval(value: str, uncertainty: float = 0) -> tuple[str, str]:
    timestamp = datetime.fromisoformat(value)
    if timestamp.utcoffset() is None:
        raise ValueError(f"timezone required: {value}")
    if uncertainty < 0:
        raise ValueError("negative timestamp uncertainty")
    timestamp = timestamp.astimezone(UTC)
    return (
        (timestamp - timedelta(seconds=uncertainty)).isoformat(),
        (timestamp + timedelta(seconds=uncertainty)).isoformat(),
    )


def time_fields(row: dict, witness: Witness) -> dict:
    if not row.get("time"):
        return {}
    # Missing uncertainty is unknown, not an invented ±1 second or exact time.
    uncertainty = row.get("uncertainty_seconds")
    lower, upper = interval(row["time"], uncertainty or 0)
    return {
        "time_lower": lower,
        "time_upper": upper,
        "time_grade": row.get("time_grade", "unknown"),
        "time_uncertainty_s": uncertainty,
        "winning_clock_id": f"{witness.witness_id}:{row.get('winning_clock') or 'published_time'}",
    }


class CollusionWiki:
    def __init__(self, family_confidence: float = 0.8):
        self.family_confidence = family_confidence

    def describe(self, path: Path) -> dict:
        if path.name not in FILES:
            raise ValueError(f"unsupported collusion.wiki export file: {path.name}")
        return {"kind": "prior_analysis" if path.name == "manifest.json" else "archive_export"}

    def ingest(self, path: Path, witness: Witness) -> AdapterOutput:
        builder = Builder(witness)
        clocks: set[str] = set()
        primary_keys: set[str] = set()
        if path.suffix == ".json":
            data = load_strict_json(path, max_bytes=32 * 1024 * 1024)
            ref = json_citation(witness.witness_id, "$", data)
            builder.cite(ref)
            yield from builder.drain()
            return
        if path.suffix == ".csv":
            for _row, ref in csv_rows(path, witness.witness_id):
                yield "citations", ref
            return
        for row, ref in jsonl_rows(path, witness.witness_id):
            builder.cite(ref)
            attrs = {k: v for k, v in row.items() if k not in {"body", "text", "hunks"}}
            attrs["source_file"] = path.name
            attrs["source_line"] = int(ref.locator)
            spec = {
                "revisions.jsonl": ("message", "revision", "rev_id"),
                "pages.jsonl": ("substrate", "page", "page_key"),
                "events.jsonl": ("action", "wiki_event", "event_id"),
                "labels.jsonl": ("identity", "label", "label"),
                "records.jsonl": ("message", "record", "id"),
                "links.jsonl": ("substrate", "link", "url"),
            }[path.name]
            kind, subkind, key_field = spec
            if key_field not in row:
                raise ValueError(f"{path.name}:{ref.locator}: missing {key_field}")
            key = str(row[key_field])
            if key in primary_keys:
                raise ValueError(f"duplicate {key_field}: {key}")
            primary_keys.add(key)
            if subkind == "label":
                attrs["anonymous"] = key == ""
                attrs["authenticated"] = False
            if subkind == "revision":
                # Export JSON strings preserve original bytes through Latin-1.
                # body_encoding classifies those original bytes, not the JSON string.
                payload = row["body"].encode("latin-1")
                if sha256_bytes(payload) != row["body_sha256"]:
                    raise ValueError(f"revision body hash mismatch at {ref.locator}")
                attrs["body_hash_verified"] = True
                decoded = payload.decode(
                    "utf-8" if row.get("body_encoding") == "utf8" else "latin-1"
                )
                attrs["normalized_sha256"] = sha256_text(" ".join(decoded.split()))
            if subkind == "record" and row.get("text") is not None:
                attrs["normalized_sha256"] = sha256_text(" ".join(row["text"].split()))
            entity = builder.entity(
                cast(EntityKind, kind), subkind, key, ref, attrs, **time_fields(row, witness)
            )
            if row.get("time"):
                clock_values = [(row.get("winning_clock") or "published_time", row["time"], True)]
                clock_values.extend((k, row[k], False) for k in RAW_CLOCKS if row.get(k))
                for clock, value, winning in clock_values:
                    clock_id = f"{witness.witness_id}:{clock}"
                    if clock_id not in clocks:
                        yield (
                            "clocks",
                            Clock(clock_id=clock_id, witness_id=witness.witness_id, label=clock),
                        )
                        clocks.add(clock_id)
                    uncertainty = row.get("uncertainty_seconds") or 0
                    lower, upper = interval(value, uncertainty)
                    yield (
                        "time_claims",
                        TimeClaim(
                            entity_id=entity.entity_id,
                            witness_id=witness.witness_id,
                            clock_id=clock_id,
                            lower=lower,
                            upper=upper,
                            grade=row.get("time_grade", "unknown"),
                            uncertainty_s=uncertainty,
                            winning=winning,
                            citation_id=ref.citation_id,
                            note="Published uncertainty"
                            if row.get("uncertainty_seconds") is not None
                            else "Uncertainty unspecified; endpoints are reported times, not accuracy bounds",
                        ),
                    )
            if subkind in {"revision", "page", "wiki_event"} and row.get("wiki"):
                wiki = builder.entity(
                    "substrate",
                    "wiki",
                    row["wiki"],
                    ref,
                    {"wiki": row["wiki"], "observed_as_reference": True},
                )
                builder.edge("located_on", entity, wiki, ref)
            if subkind == "revision":
                page = builder.entity(
                    "substrate",
                    "page",
                    row["page_key"],
                    ref,
                    {"wiki": row["wiki"], "observed_as_reference": True},
                )
                builder.edge("located_on", entity, page, ref)
                label = builder.entity(
                    "identity",
                    "label",
                    row["label"],
                    ref,
                    {
                        "anonymous": row["label"] == "",
                        "authenticated": False,
                        "observed_as_reference": True,
                    },
                )
                builder.edge(
                    "authored",
                    label,
                    entity,
                    ref,
                    method="claimed_label",
                    outcome=Outcome.AMBIGUOUS,
                    rationale="Unauthenticated preference label; blank labels do not identify actors",
                )
                if row.get("ip16"):
                    prefix = builder.entity(
                        "identity",
                        "network_prefix",
                        row["ip16"],
                        ref,
                        {"observed_as_reference": True, "authenticated": False},
                    )
                    builder.edge(
                        "sent",
                        prefix,
                        entity,
                        ref,
                        method="published_ip16",
                        rationale="Published network prefix; shared infrastructure does not identify an actor",
                    )
                artifact = builder.entity(
                    "artifact_version",
                    "body",
                    row["body_sha256"],
                    ref,
                    {"sha256": row["body_sha256"], "source_bytes_verified": True},
                )
                builder.edge("member_of", entity, artifact, ref, method="body_sha256")
            elif subkind == "page" and row.get("page_family"):
                family = builder.entity("substrate", "page_family", row["page_family"], ref)
                confidence = row.get("page_family_confidence")
                # Published confidence is commonly a textual grade, not a probability.
                confident = (
                    confidence == "high"
                    or isinstance(confidence, (float, int))
                    and confidence >= self.family_confidence
                )
                builder.edge(
                    "member_of",
                    entity,
                    family,
                    ref,
                    method="published_page_family",
                    outcome=Outcome.SUPPORTED if confident else Outcome.AMBIGUOUS,
                )
            elif subkind == "record":
                for origin in row.get("origins", []):
                    site = builder.entity("substrate", "site", origin["site"], ref)
                    builder.edge(
                        "located_on",
                        entity,
                        site,
                        ref,
                        method="published_origin",
                        rationale="Publisher-selected text origin; not proof of independent acquisition or authorship",
                    )
            yield from builder.drain()
