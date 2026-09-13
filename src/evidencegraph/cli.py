"""The eg command line. All evidence mutations publish under the case lock."""

import json
from pathlib import Path
from typing import Annotated

import typer

from evidencegraph import case as lifecycle
from evidencegraph.manifest import read_manifest
from evidencegraph.schema import CaseConfig, ClockBound, TrustDomain
from evidencegraph.store import Store

app = typer.Typer(
    no_args_is_help=True,
    help=(
        "Evidence graphs for agent incident forensics. Typical order: case init, "
        "witness add, ingest, reconcile, coverage, docket, export, verify. "
        "See docs/getting-started.md for a worked example."
    ),
)
case_app = typer.Typer(no_args_is_help=True, help="Create a case and declare its assumptions.")
witness_app = typer.Typer(
    no_args_is_help=True, help="Acquire evidence files as hashed, immutable witnesses."
)
lab_app = typer.Typer(
    no_args_is_help=True, help="Stage synthetic incidents and import lab collections."
)
app.add_typer(case_app, name="case")
app.add_typer(witness_app, name="witness")
app.add_typer(lab_app, name="lab")

Case = Annotated[Path, typer.Argument(help="Case directory created by `eg case init`")]


def output(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False, default=str, allow_nan=False))


@case_app.command("init")
def init(
    case: Annotated[Path, typer.Argument(help="New case directory to create")],
    title: Annotated[str, typer.Option(help="Title printed on the docket")] = (
        "Untitled investigation"
    ),
    trust_domain: Annotated[
        list[str] | None,
        typer.Option(
            "--trust-domain",
            help="Declare a trust domain as ID=LABEL, once per domain, for example registry=Host",
        ),
    ] = None,
    independent: Annotated[
        list[str] | None,
        typer.Option(
            "--independent",
            help="Declare two domains independent as A:B; only then can a cross-domain match become supported",
        ),
    ] = None,
    clock_bound: Annotated[
        list[str] | None,
        typer.Option(
            "--clock-bound",
            help="Declare a clock relation as CLOCK_A:CLOCK_B:SECONDS; registry rules read the runner:container pair",
        ),
    ] = None,
    authentic_records: Annotated[
        list[str] | None,
        typer.Option(
            "--authentic-records",
            help="Domain whose records the investigated actors could not fabricate or edit",
        ),
    ] = None,
):
    """Create a case directory with its trust, independence and clock declarations."""
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
    for item in authentic_records or []:
        if item not in domains:
            raise typer.BadParameter("authentic records require a declared domain")
        domains[item]["authentic_records"] = True
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
    case: Case,
    path: Annotated[
        Path, typer.Argument(help="Evidence file, or a directory the adapter knows how to scan")
    ],
    adapter: Annotated[
        str,
        typer.Option(help="inspect-eval, lab-public, collusion-wiki or reference-list"),
    ],
    trust_domain: Annotated[str, typer.Option(help="Declared domain id this evidence belongs to")],
    origin: Annotated[
        str | None, typer.Option(help="Where the file came from, if not its current path")
    ] = None,
):
    """Snapshot and hash evidence before anything reads it. Prints the witness ids."""
    output(
        lifecycle.add_witness(case, path, adapter=adapter, trust_domain=trust_domain, origin=origin)
    )


@witness_app.command("describe")
def describe(
    case: Case, witness_id: Annotated[str, typer.Argument(help="Witness id from `witness add`")]
):
    """Show a witness's metadata and what its ingestion produced."""
    output(lifecycle.describe_witness(case, witness_id))


@app.command()
def ingest(
    case: Case,
    witness: Annotated[
        str | None, typer.Option(help="Ingest one witness id; default is every witness")
    ] = None,
):
    """Parse witnesses into cited graph tables. Skips witnesses already ingested under the current case.json."""
    output(lifecycle.ingest(case, witness))


@app.command()
def query(
    case: Case,
    sql: Annotated[
        str,
        typer.Argument(
            help="One read-only SELECT over entities, relations, citations, witnesses, docket_answers and the other tables"
        ),
    ],
):
    """Run a read-only SQL query against the graph (DuckDB)."""
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
def identity(case: Case):
    """Derive identity hypotheses (shared labels, shared network prefixes); never authenticates actors."""
    from evidencegraph.identity import identify

    output(identify(case))


@app.command()
def lineage(case: Case):
    """Derive textual ancestry and byte-equality relations between revisions and records."""
    from evidencegraph.lineage import lineage as run

    output(run(case))


@app.command()
def facts(
    case: Case,
    manifest: Annotated[
        Path | None,
        typer.Option(help="Publisher manifest to audit; it must already be an ingested witness"),
    ] = None,
):
    """Recompute a publisher's manifest facts from the graph and report exact, differing and not-computable ones."""
    from evidencegraph.docket.facts import audit_facts

    if manifest:
        from evidencegraph.provenance import sha256_file

        if not any(
            w["sha256"] == sha256_file(manifest) for w in read_manifest(case)["witnesses"].values()
        ):
            raise typer.BadParameter("add the manifest as a witness before auditing")
    output(audit_facts(case))


@app.command()
def coverage(
    case: Case,
    level: Annotated[float, typer.Option(help="Bootstrap interval level")] = 0.95,
    seed: Annotated[int, typer.Option(help="Bootstrap seed")] = 0,
):
    """Estimate what fraction of the declared record population has supported attributions. Run after reconcile."""
    from evidencegraph.coverage.estimate import estimate

    output(estimate(case, level=level, seed=seed))


@app.command()
def docket(case: Case):
    """Answer the frozen questions and write DOCKET.md and docket.json into the case."""
    from evidencegraph.docket.render import render

    typer.echo(str(render(case)))


@app.command()
def reconcile(
    case: Case,
    substrate: Annotated[str, typer.Option(help="registry or wiki-saves")],
    bound: Annotated[
        float | None,
        typer.Option(help="Override the declared runner:container clock bound in seconds"),
    ] = None,
):
    """Match independently observed records to transcript claims, abstaining when evidence is insufficient."""
    from evidencegraph.reconcile.engine import reconcile_case

    output(reconcile_case(case, substrate, bound=bound))


@app.command()
def cite(
    case: Case,
    ref_id: Annotated[str, typer.Argument(help="Citation id, for example from docket.json")],
):
    """Resolve a citation to its source row or native event and verify its hash."""
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
    out: Annotated[
        Path, typer.Argument(help="New directory for public/ and private/ stage output")
    ],
    seed: Annotated[int, typer.Option(help="Scenario seed")] = 0,
    spoof: Annotated[
        str | None, typer.Option(help="Append fabricated write receipts as LABEL:COUNT")
    ] = None,
    drop: Annotated[
        list[str] | None,
        typer.Option("--drop", help="Withhold this agent's transcript from public/ (A, B, C or D)"),
    ] = None,
):
    """Build a synthetic incident with real Inspect logs, an independent registry and private truth."""
    from evidencegraph.lab.stage import construct_stage

    output(construct_stage(out, seed=seed, spoof=spoof, drop=drop or []))


@lab_app.command("collect-docker")
def collect_docker(
    out: Annotated[
        Path, typer.Argument(help="New directory for public evidence and private collection truth")
    ],
    seed: Annotated[int, typer.Option(help="Scripted scenario seed")] = 7,
    timeout_seconds: Annotated[
        float, typer.Option(help="Whole-run deadline in seconds, followed by bounded cleanup")
    ] = 120,
    docker_binary: Annotated[
        str, typer.Option(help="Docker executable, injectable for testing")
    ] = "docker",
):
    """Collect a real background-process incident on a Linux Docker host or Docker Desktop."""
    from evidencegraph.lab.docker import collect_docker as collect

    try:
        report = collect(
            out, seed=seed, timeout_seconds=timeout_seconds, docker_binary=docker_binary
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    output(report)
    if report["status"] != "ok":
        for failure in report["failures"]:
            typer.echo(f"{failure['step']}: {failure['error']}", err=True)
        for command in report["cleanup_commands"]:
            typer.echo(command, err=True)
        raise typer.Exit(1)


@lab_app.command("import-mac")
def import_mac(
    collection: Annotated[
        Path, typer.Argument(help="Public replica evidence directory containing ledger.jsonl")
    ],
    out: Annotated[Path, typer.Argument(help="New case directory")],
):
    """Import a public Mac replica collection as a case; declare a measured clock bound before reconciling."""
    from evidencegraph.lab.stage import import_mac_collection

    output(import_mac_collection(collection, out))


@app.command()
def validate(
    case: Case,
    truth: Annotated[Path, typer.Option(help="Private truth directory from `lab stage`")],
):
    """Score supported conclusions against private host truth. The graph never reads this directory."""
    from evidencegraph.validate.score import validate_case

    output(validate_case(case, truth))


@app.command("export")
def export_case(
    case: Case, dest: Annotated[Path, typer.Argument(help="New bundle directory outside the case")]
):
    """Write a portable BagIt bundle with evidence, graph, docket and checksums."""
    from evidencegraph.export.bagit import export_bundle

    output(export_bundle(case, dest))


@app.command()
def verify(
    dest: Annotated[Path, typer.Argument(help="Bundle directory from `export`")],
    recompute: Annotated[
        bool, typer.Option(help="Also recompute the docket from the bundled graph and compare")
    ] = False,
):
    """Check a bundle's checksums, inventory and graph invariants."""
    from evidencegraph.export.bagit import verify_bundle

    output(verify_bundle(dest, recompute=recompute))


@app.command("scan-validation")
def scan_validation(
    case: Case,
    truth: Annotated[Path, typer.Option(help="Private truth directory")],
    dest: Annotated[Path, typer.Option(help="New CSV of held-out scanner keys")],
):
    """Write the held-out validation keys the offline Scout control is scored against."""
    from evidencegraph.scanners.validation import validation_csv

    output(validation_csv(case, truth, dest))


@app.command()
def scan(
    case: Case,
    scanner: Annotated[str, typer.Option(help="Only claimed-writes is available")],
    validation: Annotated[Path, typer.Option(help="CSV from `scan-validation`")],
    model: Annotated[str, typer.Option(help="Only the offline mockllm/claims control runs")] = (
        "mockllm/claims"
    ),
    max_usd: Annotated[float, typer.Option(help="Must stay 0; live paid scanning is disabled")] = 0,
):
    """Run the offline Scout scanner control and score it. No paid model calls are possible here."""
    from evidencegraph.scanners.validation import run_scan

    output(run_scan(case, scanner=scanner, validation=validation, model=model, max_usd=max_usd))


@app.command("scout-db")
def scout_db(case: Case):
    """Insert the ingested Inspect transcripts into a Scout transcript database inside the case."""
    from evidencegraph.scanners.validation import scout_database

    output(scout_database(case))


if __name__ == "__main__":
    app()
