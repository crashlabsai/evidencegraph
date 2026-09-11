import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evidencegraph.case import add_witness, ingest, init_case
from evidencegraph.provenance import sha256_bytes
from evidencegraph.schema import CaseConfig, TrustDomain


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def wiki_fixture(root: Path) -> Path:
    root.mkdir()
    pages = []
    revisions = []
    events = []
    start = datetime(2026, 5, 24, tzinfo=UTC)
    for page_index in range(3):
        key = f"dse~Page{page_index}"
        pages.append(
            {
                "page_key": key,
                "page_id": key.replace("~", "/"),
                "wiki": "dse",
                "name": f"Page{page_index}",
                "page_family": "coordination",
                "page_family_source": "synthetic",
                "page_family_confidence": "high",
                "n_revs": 4,
                "n_revs_before": 0,
                "n_deletions": int(page_index == 0),
                "n_recreations": int(page_index == 0),
                "bucket": "P",
            }
        )
        for seq in range(1, 5):
            body = ("hello café" if seq == 2 else f"shared content {seq}").encode()
            timestamp = (start + timedelta(seconds=page_index * 10 + seq)).isoformat()
            row = {
                "rev_id": f"{key}@{seq}",
                "page_key": key,
                "wiki": "dse",
                "name": f"Page{page_index}",
                "seq": seq,
                "body": body.decode("latin-1"),
                "body_encoding": "utf8" if seq == 2 else "ascii",
                "body_len": len(body),
                "body_sha256": sha256_bytes(body),
                "label": ["AgentOne", "Human", ""][page_index],
                "ip16": "10.20",
                "time": timestamp,
                "time_grade": ["reqlog", "rclog", "write_date"][page_index],
                "uncertainty_seconds": 1,
                "winning_clock": "revision.pref_ts",
                "write_date": timestamp,
                "archived_at": timestamp,
                "request_time": timestamp if page_index == 0 else None,
                "success_time": None,
                "recent_changes_time": timestamp if page_index == 1 else None,
                "diff_base": f"{key}@{seq - 1}" if seq > 1 else None,
                "diff_base_reason": None if seq > 1 else "page_created",
            }
            revisions.append(row)
            events.append(
                {
                    "event_id": "save:" + row["rev_id"],
                    "event_type": "save",
                    "wiki": "dse",
                    "page_key": key,
                    "revision_ref": row["rev_id"],
                    "time": timestamp,
                    "time_grade": row["time_grade"],
                }
            )
    events.append(
        {
            "event_id": "delete-1",
            "event_type": "delete",
            "wiki": "dse",
            "page_key": "dse~Page0",
            "time": start.isoformat(),
            "time_grade": "reqlog",
            "page_held": True,
        }
    )
    events[-2].update({"relation_type": "first_recreation_of", "related_event_id": "delete-1"})
    labels = [
        {
            "label": name,
            "stored_revisions": 4,
            "is_human_handle": name == "Human",
            "save_requests": 8,
            "save_request_source": "edit_actors.jsonl",
        }
        for name in ["", "AgentOne", "Human"]
    ]
    for name, rows in [
        ("pages", pages),
        ("revisions", revisions),
        ("events", events),
        ("labels", labels),
    ]:
        write_jsonl(root / f"{name}.jsonl", rows)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "facts": {
                    "held_revisions": {"value": 12},
                    "held_pages": {"value": 3},
                    "unpublished_ip_count": {"value": 999},
                },
                "counts": {
                    "revisions": {"value": 12},
                    "pages": {"value": 3},
                    "labels": {"value": 3},
                },
                "checks": [
                    {"name": "published revisions", "expected": 12, "actual": 999, "ok": False}
                ],
            }
        )
    )
    return root


@pytest.fixture
def wiki_case(tmp_path):
    source = wiki_fixture(tmp_path / "source")
    case = tmp_path / "case"
    init_case(
        case,
        CaseConfig(
            title="Synthetic wiki", trust_domains=(TrustDomain(id="archive", label="One archive"),)
        ),
    )
    add_witness(case, source, adapter="collusion-wiki", trust_domain="archive")
    ingest(case)
    return case
