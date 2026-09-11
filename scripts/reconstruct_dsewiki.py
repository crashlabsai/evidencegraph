"""Acquire the pinned public corpus, then reconstruct and audit its evidence graph."""

import argparse
import gzip
import hashlib
import urllib.request
import zipfile
from pathlib import Path

from evidencegraph.case import add_witness, ingest, init_case
from evidencegraph.coverage.estimate import estimate
from evidencegraph.docket.facts import audit_facts
from evidencegraph.docket.render import render
from evidencegraph.identity import identify
from evidencegraph.lineage import lineage
from evidencegraph.provenance import sha256_file
from evidencegraph.reconcile.engine import reconcile_case
from evidencegraph.schema import CaseConfig, TrustDomain

BASE = "https://collusion.wiki/explorer/download/"
ZIP_SHA256 = "eb68aa12d26bf189d8bfc4ce47f4d8af66ae5ba7ebbadd429738297a3cbb25ae"
EXTRA = (
    "records.jsonl.gz",
    "links.jsonl.gz",
    "site-coverage.csv",
    "coverage-gaps.csv",
    "other-wikis.json.gz",
    "shortener-logs.json.gz",
)


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(256 * 1024 * 1024 + 1)
    if len(data) > 256 * 1024 * 1024:
        raise ValueError("download exceeds acquisition limit")
    return data


def reconstruct(case: Path) -> Path:
    if (case / "manifest.json").exists():
        raise ValueError("choose a new case directory")
    raw = case / "raw"
    export = raw / "export"
    export.mkdir(parents=True, exist_ok=True)
    archive = download(BASE + "full-wiki-logs.zip")
    if hashlib.sha256(archive).hexdigest() != ZIP_SHA256:
        raise ValueError(
            "public ZIP changed; inspect the new release before updating the acquisition pin"
        )
    (raw / "full-wiki-logs.zip").write_bytes(archive)
    with zipfile.ZipFile(raw / "full-wiki-logs.zip") as bundle:
        if sum(i.file_size for i in bundle.infolist()) > 256 * 1024 * 1024:
            raise ValueError("expanded archive exceeds limit")
        for name in bundle.namelist():
            if name not in {
                "pages.jsonl",
                "revisions.jsonl",
                "events.jsonl",
                "labels.jsonl",
                "manifest.json",
                "SHA256SUMS",
            }:
                raise ValueError("unexpected archive member")
            (export / name).write_bytes(bundle.read(name))
    for line in (export / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(None, 1)
        if (
            name
            not in {
                "pages.jsonl",
                "revisions.jsonl",
                "events.jsonl",
                "labels.jsonl",
                "manifest.json",
            }
            or sha256_file(export / name) != digest
        ):
            raise ValueError("publisher checksum verification failed")
    for name in EXTRA:
        data = download(BASE + name)
        data = gzip.decompress(data) if name.endswith(".gz") else data
        if len(data) > 256 * 1024 * 1024:
            raise ValueError("expanded extra file exceeds limit")
        (export / name.removesuffix(".gz")).write_bytes(data)
    init_case(
        case,
        CaseConfig(
            title="DseWiki: published evidence reconstruction",
            trust_domains=(
                TrustDomain(
                    id="publisher-export", label="Publisher's public export and prior analysis"
                ),
            ),
        ),
    )
    for path in sorted(export.iterdir()):
        if path.name != "SHA256SUMS":
            add_witness(
                case,
                path,
                adapter="collusion-wiki",
                trust_domain="publisher-export",
                origin=BASE + path.name,
            )
    ingest(case)
    audit_facts(case)
    identify(case)
    lineage(case)
    reconcile_case(case, "wiki-saves")
    estimate(case)
    render(case)
    return case / "DOCKET.md"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    print(reconstruct(parser.parse_args().case))
