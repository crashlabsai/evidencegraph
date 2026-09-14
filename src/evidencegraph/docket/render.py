"""A compact reading surface with the full tables and citations in docket.json."""

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from evidencegraph.case import configuration
from evidencegraph.derive import configuration_sha256, require_current_configuration
from evidencegraph.docket.answers import answers
from evidencegraph.manifest import atomic_json, update_manifest_stage
from evidencegraph.provenance import sha256_file
from evidencegraph.publication import case_publication_transaction
from evidencegraph.store import PartitionWriter, Store

LABELS = {
    "DQ1": "Activity",
    "DQ2": "Authorship",
    "DQ3": "Substrates",
    "DQ4": "Fact audit",
    "DQ5": "Identity",
    "DQ6": "Lineage",
    "DQ7": "Retention",
    "DQ8": "Limits",
    "LQ1": "Write attribution",
    "LQ2": "Job launch",
    "LQ3": "Background output",
    "LQ4": "Versions",
    "LQ5": "Integrity",
    "LQ6": "Coverage",
    "LQ7": "Refresh timing",
    "LQ8": "Fixture access",
}


def escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def relevant_questions(docket: dict) -> list[str]:
    """The question set the ingested evidence can speak to: DQ for wiki revisions, LQ for
    registry records; both when both were ingested."""
    relevant = {"DQ" if any(c["subkind"] == "revision" for c in docket["entity_counts"]) else "LQ"}
    if any(c["subkind"] == "registry_mutation" for c in docket["entity_counts"]):
        relevant.add("LQ")
    return [a["question_id"] for a in docket["answers"] if a["question_id"][:2] in relevant]


def compute_docket(root: Path, manifest: dict) -> dict:
    require_current_configuration(root, manifest)
    config = configuration(root)
    with Store(root, manifest) as store:
        rows = answers(store, config, manifest)
        counts = store.query("SELECT subkind,count(*) n FROM native_entities GROUP BY 1 ORDER BY 1")
    validation = None
    if manifest.get("validation"):
        from evidencegraph.store import safe_path

        validation = json.loads(safe_path(root, manifest["validation"]["path"]).read_text())
    return {
        "schema_version": "0.1",
        "title": config.title,
        "answers": [r.model_dump(mode="json") for r in rows],
        "entity_counts": counts,
        "witnesses": [
            {
                k: w[k]
                for k in ("witness_id", "filename", "sha256", "trust_domain", "coverage_claim")
            }
            for w in sorted(manifest["witnesses"].values(), key=lambda w: w["witness_id"])
        ],
        "validation": validation,
    }


def markdown(docket: dict) -> str:
    relevant = set(relevant_questions(docket))
    rows = [a for a in docket["answers"] if a["question_id"] in relevant]
    by_id = {a["question_id"]: a for a in rows}
    lines = [
        f"# {escape(docket['title'])}",
        "",
        "Evidence docket · prototype · source assertions and independently corroborated actions remain distinct.",
        "",
        "| Question | Status | Finding |",
        "|---|---|---|",
    ]
    lines += [
        f"| {a['question_id']} {LABELS[a['question_id']]} | {a['outcome']} | {escape(a['headline'])} |"
        for a in rows
    ]
    coverage = by_id.get("LQ6", {}).get("numbers", {}).get("coverage", [])
    retention = by_id.get("DQ7", {}).get("numbers", {}).get("dse_label_retention_totals", {})
    if coverage or retention.get("requests"):
        lines += [
            "",
            "| Coverage population | Observed fraction | Conditional interval / limit |",
            "|---|---|---|",
        ]
        for estimate in coverage[:3]:
            interval = estimate["interval"]
            interval_text = (
                f"{estimate['interval_level']:.0%}: {interval[0]:.1%}–{interval[1]:.1%}"
                if interval
                else "unavailable"
            )
            fraction = (
                f"{estimate['estimate']:.1%}" if estimate["estimate"] is not None else "unavailable"
            )
            lines.append(
                f"| {escape(estimate['sampling_unit'])} | {estimate['n_supported']}/{estimate['n_sampled']} = {fraction} | {interval_text}; exchangeable-record bootstrap |"
            )
        if retention.get("requests"):
            requests, stored = retention["requests"], retention["stored_revisions"]
            lines.append(
                f"| Published DSE totals for {retention['labels']:,} nonempty labels | {stored:,}/{requests:,} = {stored / requests:.1%} | Unaudited publisher denominator; no independent population estimate |"
            )
    grade_mix = by_id.get("DQ1", {}).get("numbers", {}).get("grade_mix")
    if grade_mix:
        lines += [
            "",
            "Revision time grades: "
            + "; ".join(f"{escape(grade)} {count:,}" for grade, count in grade_mix.items())
            + ".",
        ]
    metrics = by_id.get("LQ5", {}).get("numbers", {}).get("scanner_validation", [])
    if metrics:
        totals = {key: 0 for key in ("true_positive", "false_positive", "false_negative")}
        for metric in metrics:
            values = json.loads(metric["validation"])
            for key in totals:
                totals[key] += values[key]
        spend = sum(m["spend_usd"] for m in metrics)
        models = ", ".join(sorted({m["model"] for m in metrics}))
        lines += [
            "",
            f"Scout control ({escape(models)}): {totals['true_positive']} true positives, {totals['false_positive']} false positives, {totals['false_negative']} false negatives; API spend ${spend:.2f}.",
        ]
    lines += [
        "",
        "Witness inventory (full hashes, intervals and citations in `docket.json`):",
        "",
        "| Sources | Trust domain | Files |",
        "|---|---|---:|",
    ]
    groups = defaultdict(list)
    for witness in docket["witnesses"]:
        name = witness["filename"]
        category = (
            "Content-addressed artifacts"
            if re.fullmatch(r"[0-9a-f]{64}", name)
            else "Inspect transcripts"
            if name.endswith(".eval")
            else "Source files"
        )
        groups[(witness["trust_domain"], category)].append(name)
    for (domain, category), names in sorted(groups.items()):
        label = ", ".join(names) if category == "Source files" and len(names) <= 3 else category
        lines.append(f"| {escape(label)} | {escape(domain)} | {len(names)} |")
    priority = ("DQ7", "DQ2", "DQ1", "DQ6", "LQ2", "LQ7", "LQ6", "LQ5")
    gaps = list(
        dict.fromkeys(by_id[q]["gaps"][0] for q in priority if q in by_id and by_id[q]["gaps"])
    )
    lines += ["", "Known gaps: " + " ".join(escape(g) + "." for g in gaps[:4]), ""]
    if docket.get("validation"):
        value = docket["validation"]
        lines += [
            f"Validation: produced precision {value.get('produced', {}).get('precision')}; confident errors {value.get('produced', {}).get('confident_errors')}. Full scoring and exclusions: `validation.json`.",
            "",
        ]
    lines += [
        "Resolve a citation with `eg cite CASE REF_ID`; query the full graph with `eg query CASE SQL`.",
        "",
    ]
    return "\n".join(lines)


def render(root: Path) -> Path:
    with case_publication_transaction(root) as manifest:
        manifest["partitions"].pop("docket", None)
        docket = compute_docket(root, manifest)
        destination = root / "reports" / uuid4().hex
        destination.mkdir(parents=True)
        atomic_json(destination / "docket.json", docket)
        (destination / "DOCKET.md").write_text(markdown(docket))
        writer = PartitionWriter(root, "docket")
        from evidencegraph.schema import DocketAnswer

        for answer in docket["answers"]:
            writer.add("docket_answers", DocketAnswer.model_validate(answer))
        manifest["partitions"]["docket"] = writer.finish()
        manifest["reports"] = {
            name: {
                "path": (destination / name).relative_to(root).as_posix(),
                "sha256": sha256_file(destination / name),
            }
            for name in ("docket.json", "DOCKET.md")
        }
        update_manifest_stage(
            manifest, "docket", params={"case_config_sha256": configuration_sha256(root)}
        )
    for name in ("docket.json", "DOCKET.md"):
        shutil.copyfile(destination / name, root / name)
    return root / "DOCKET.md"
