"""Streaming citations. JSONL line hashes exclude LF and a preceding CR only."""

import csv
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from evidencegraph.ids import fragment_reference_id
from evidencegraph.provenance import sha256_bytes
from evidencegraph.schema import Citation, Witness
from evidencegraph.strict_json import finite_json_float, reject_json_constant, unique_json_object

MAX_ROW_BYTES = 32 * 1024 * 1024


def citation(
    witness: str,
    kind: Literal["event", "message", "jsonl_line", "json_path", "csv_row", "file"],
    locator: str,
    payload: bytes,
    **extra,
) -> Citation:
    return Citation(
        citation_id=fragment_reference_id(witness, extra.get("transcript_id") or "", kind, locator),
        witness_id=witness,
        locator_kind=kind,
        locator=locator,
        content_sha256=sha256_bytes(payload),
        preview=payload.decode("utf-8", errors="replace")[:160],
        **extra,
    )


def jsonl_rows(path: Path, witness: str) -> Iterator[tuple[dict, Citation]]:
    with path.open("rb") as stream:
        line_number = 0
        while raw := stream.readline(MAX_ROW_BYTES + 1):
            line_number += 1
            if len(raw) > MAX_ROW_BYTES:
                raise ValueError(f"{path.name}:{line_number}: row exceeds byte limit")
            payload = raw.removesuffix(b"\n").removesuffix(b"\r")
            if not payload.strip():
                continue
            try:
                row = json.loads(
                    payload,
                    object_pairs_hook=unique_json_object,
                    parse_constant=reject_json_constant,
                    parse_float=finite_json_float,
                )
            except (ValueError, UnicodeError) as exc:
                raise ValueError(f"{path.name}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path.name}:{line_number}: expected an object")
            yield row, citation(witness, "jsonl_line", str(line_number), payload)


def csv_rows(path: Path, witness: str) -> Iterator[tuple[dict, Citation]]:
    # CSV row locators count data records, including quoted multiline records, from one.
    with path.open(newline="", encoding="utf-8") as stream:
        for number, row in enumerate(csv.DictReader(stream), 1):
            payload = json.dumps(
                row, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
            yield row, citation(witness, "csv_row", str(number), payload)


def json_citation(witness: str, locator: str, value: object) -> Citation:
    payload = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
    return citation(witness, "json_path", locator, payload)


def json_value_at(value: object, locator: str) -> object:
    if not locator.startswith("$"):
        raise ValueError("JSON locator must begin with $")
    rest = locator[1:]
    while rest:
        match = re.match(r"(?:\.([A-Za-z_][A-Za-z_0-9-]*)|\[(\d+)\])", rest)
        if not match:
            raise ValueError("unsupported JSON locator")
        if match[1] is not None and isinstance(value, dict):
            value = value[match[1]]
        elif match[2] is not None and isinstance(value, list):
            value = value[int(match[2])]
        else:
            raise ValueError("JSON locator does not match source structure")
        rest = rest[match.end() :]
    return value


def resolve_citation(path: Path, witness: Witness, ref: Citation) -> object:
    from evidencegraph.provenance import verify_file_identity
    from evidencegraph.strict_json import load_strict_json

    verify_file_identity(
        path,
        expected_size=witness.size_bytes,
        expected_sha256=witness.sha256,
        subject=ref.citation_id,
    )
    if ref.locator_kind in {"jsonl_line", "csv_row"}:
        reader = jsonl_rows if ref.locator_kind == "jsonl_line" else csv_rows
        for row, actual in reader(path, witness.witness_id):
            if actual.locator == ref.locator:
                if actual.content_sha256 != ref.content_sha256:
                    raise ValueError("citation fragment hash mismatch")
                return row
    elif ref.locator_kind == "json_path":
        value = json_value_at(load_strict_json(path, max_bytes=64 * 1024 * 1024), ref.locator)
        if (
            json_citation(witness.witness_id, ref.locator, value).content_sha256
            != ref.content_sha256
        ):
            raise ValueError("JSON fragment hash mismatch")
        return value
    elif ref.locator_kind in {"event", "message"}:
        from evidencegraph.adapters.inspect_eval import (
            read_transcripts,
            ref_for_event,
            ref_for_message,
        )

        for transcript in read_transcripts([path]):
            if transcript.transcript_id != ref.transcript_id:
                continue
            fragments = (
                [
                    (event, ref_for_event(witness, transcript, event))
                    for event in transcript.events
                    if event.uuid == ref.locator
                ]
                if ref.locator_kind == "event"
                else [
                    (message, ref_for_message(witness, transcript, message))
                    for message in transcript.messages
                    if message.id == ref.locator
                ]
            )
            for fragment, actual in fragments:
                if actual.citation_id == ref.citation_id:
                    if actual.content_sha256 != ref.content_sha256:
                        raise ValueError("native fragment hash mismatch")
                    return fragment.model_dump(mode="json")
    elif ref.locator_kind == "file":
        if witness.sha256 != ref.content_sha256:
            raise ValueError("file citation hash mismatch")
        return {"sha256": witness.sha256, "size_bytes": witness.size_bytes}
    raise ValueError("citation locator absent from source")
