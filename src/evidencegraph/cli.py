"""The eg command line. All evidence mutations publish under the case lock."""

import json
from pathlib import Path
from typing import Annotated

import typer

from evidencegraph import case as lifecycle
from evidencegraph.manifest import read_manifest
from evidencegraph.schema import CaseConfig, ClockBound, TrustDomain
from evidencegraph.store import Store

app = typer.Typer(no_args_is_help=True, help="Evidence graphs for agent incident forensics.")
case_app = typer.Typer(no_args_is_help=True)
witness_app = typer.Typer(no_args_is_help=True)
lab_app = typer.Typer(no_args_is_help=True)
app.add_typer(case_app, name="case")
app.add_typer(witness_app, name="witness")
app.add_typer(lab_app, name="lab")


def output(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False, default=str, allow_nan=False))


@case_app.command("init")
def init(
    case: Path,
    title: str = "Untitled investigation",
    trust_domain: Annotated[list[str] | None, typer.Option("--trust-domain")] = None,
    independent: Annotated[list[str] | None, typer.Option("--independent")] = None,
    clock_bound: Annotated[list[str] | None, typer.Option("--clock-bound")] = None,
):
    domains = {}
    for item in trust_domain or []:
        key, sep, label = item.partition("=")
        if not sep or not key:
            raise typer.BadParameter("trust domains use id=label")
        domains[key] = {"id": key, "label": label, "related_to": {}}
    for item in independent or []:
        a, b = item.split(":", 1)
        if a not in domains or b not in domains:
            raise typer.BadParameter("independence requires declared domains")
        domains[a]["related_to"][b] = "independent"
        domains[b]["related_to"][a] = "independent"
    bounds = []
    for item in clock_bound or []:
        a, b, seconds = item.split(":")
        bounds.append(ClockBound(clock_a=a, clock_b=b, bound_seconds=float(seconds)))
    lifecycle.init_case(
        case,
        CaseConfig(
            title=title,
            trust_domains=tuple(TrustDomain.model_validate(v) for v in domains.values()),
            clock_bounds=tuple(bounds),
        ),
    )
    typer.echo(f"Initialized {case}")


@witness_app.command("add")
def add(
    case: Path,
    path: Path,
    adapter: Annotated[str, typer.Option()],
    trust_domain: Annotated[str, typer.Option()],
    origin: str | None = None,
):
    output(
        lifecycle.add_witness(case, path, adapter=adapter, trust_domain=trust_domain, origin=origin)
    )


@witness_app.command("describe")
def describe(case: Path, witness_id: str):
    output(lifecycle.describe_witness(case, witness_id))


@app.command()
def ingest(case: Path, witness: str | None = None):
    output(lifecycle.ingest(case, witness))


@app.command()
def query(case: Path, sql: str):
    with Store(case, read_manifest(case)) as store:
        statements = store.connection.extract_statements(sql)
        if len(statements) != 1 or str(statements[0].type) != "StatementType.SELECT":
            raise typer.BadParameter("query accepts a single read-only SELECT")
        # Keep lazy Parquet scans readable while preventing arbitrary file/network IO.
        paths = [
            str((case / entry["path"]).absolute())
            for partition in read_manifest(case)["partitions"].values()
            for entry in partition.values()
        ]
        store.connection.execute("SET allowed_paths = ?", [paths])
        store.connection.execute("SET enable_external_access=false")
        output(store.query(sql))


@app.command()
def identity(case: Path):
    from evidencegraph.identity import identify

    output(identify(case))


@app.command()
def lineage(case: Path):
    from evidencegraph.lineage import lineage as run

    output(run(case))


@app.command()
def facts(case: Path, manifest: Path | None = None):
    from evidencegraph.docket.facts import audit_facts

    if manifest:
        from evidencegraph.provenance import sha256_file

        if not any(
            w["sha256"] == sha256_file(manifest) for w in read_manifest(case)["witnesses"].values()
        ):
            raise typer.BadParameter("add the manifest as a witness before auditing")
    output(audit_facts(case))


@app.command()
def coverage(case: Path, level: float = 0.95, seed: int = 0):
    from evidencegraph.coverage.estimate import estimate

    output(estimate(case, level=level, seed=seed))


@app.command()
def docket(case: Path):
    from evidencegraph.docket.render import render

    typer.echo(str(render(case)))


@app.command()
def reconcile(case: Path, substrate: Annotated[str, typer.Option()], bound: float | None = None):
    from evidencegraph.reconcile.engine import reconcile_case

    output(reconcile_case(case, substrate, bound=bound))


@app.command()
def cite(case: Path, ref_id: str):
    from evidencegraph.refs import resolve_citation
    from evidencegraph.schema import Citation, Witness
    from evidencegraph.store import safe_path

    manifest = read_manifest(case)
    with Store(case, manifest) as store:
        refs = store.query("SELECT * FROM citations WHERE citation_id=?", [ref_id])
    if len(refs) != 1:
        raise typer.BadParameter("unknown or ambiguous citation")
    ref = refs[0]
    witness = manifest["witnesses"][ref["witness_id"]]
    path = safe_path(case, witness["snapshot_path"])
    row = resolve_citation(path, Witness.model_validate(witness), Citation.model_validate(ref))
    output({"citation": ref, "witness": witness, "source_row": row})


@lab_app.command("stage")
def stage(
    out: Path,
    seed: int = 0,
    spoof: str | None = None,
    drop: Annotated[list[str] | None, typer.Option("--drop")] = None,
):
    from evidencegraph.lab.stage import construct_stage

    output(construct_stage(out, seed=seed, spoof=spoof, drop=drop or []))


@lab_app.command("import-mac")
def import_mac(collection: Path, out: Path):
    from evidencegraph.lab.stage import import_mac_collection

    output(import_mac_collection(collection, out))


@app.command()
def validate(case: Path, truth: Annotated[Path, typer.Option()]):
    from evidencegraph.validate.score import validate_case

    output(validate_case(case, truth))


@app.command("export")
def export_case(case: Path, dest: Path):
    from evidencegraph.export.bagit import export_bundle

    output(export_bundle(case, dest))


@app.command()
def verify(dest: Path, recompute: bool = False):
    from evidencegraph.export.bagit import verify_bundle

    output(verify_bundle(dest, recompute=recompute))


@app.command("scan-validation")
def scan_validation(
    case: Path, truth: Annotated[Path, typer.Option()], dest: Annotated[Path, typer.Option()]
):
    from evidencegraph.scanners.validation import validation_csv

    output(validation_csv(case, truth, dest))


@app.command()
def scan(
    case: Path,
    scanner: Annotated[str, typer.Option()],
    validation: Annotated[Path, typer.Option()],
    model: str = "mockllm/claims",
    max_usd: float = 0,
):
    from evidencegraph.scanners.validation import run_scan

    output(run_scan(case, scanner=scanner, validation=validation, model=model, max_usd=max_usd))


@app.command("scout-db")
def scout_db(case: Path):
    from evidencegraph.scanners.validation import scout_database

    output(scout_database(case))


if __name__ == "__main__":
    app()
