# The local viewer

`eg serve` opens a read-only web viewer over one case, or over the `data/` directory
of an exported bundle, so the docket can be read, and every line checked, without
the terminal.

```sh
uv run eg serve cases/demo/case --open      # http://127.0.0.1:8765/
uv run eg serve cases/demo/bundle           # a bundle from `eg export` works too
```

The viewer never mutates a case. Every request re-reads the published
`manifest.json`, so a stage run from another terminal appears on the next reload.

## Pages

**Docket.** Status tiles, then two panels an investigator reads first: the
evidence still missing, with every gap linked to the questions it blocks, and the
declared assumptions the supported lines rest on. Below them, each frozen question
shows its status, one-line finding and, without expanding, its first gap or the
number of assumptions it carries. Open a line for the full question text, what the
status means, the per-record evidence (outcomes, methods, matched actions,
rationale, and for unresolved records the change that would resolve them) and its
citations. The page also carries the coverage estimate with its interval, the
validation summary when a private-truth score exists, the witness inventory, and
every known gap. `DOCKET.md` and `docket.json` are one click away, and the viewer
shows whether the bundled `docket.json` still matches the hash the manifest records.

**Citation drawer.** Clicking any citation resolves it the way `eg cite` does: the
snapshot's size and SHA-256 are checked against the manifest, the cited fragment is
re-hashed, and only then is the source row or native Inspect event shown. A
tampered snapshot produces a visible verification failure with the bytes withheld.
The drawer lists the entities built on the fragment, links to the relations that
cite it, and prints the exact `eg cite` command to reproduce the lookup.

**Witnesses.** Every acquired file grouped by trust domain, with the declared
relations between domains, whether a domain is declared authentic, each file's
hash, size, adapter, coverage claim and ingestion state. A witness page re-verifies
the snapshot and breaks its entities down by subkind.

**Relations.** The cited edges of the graph, filterable by kind, outcome, method
and run. Hovering a method shows what would change an unresolved outcome, using the
same table as [getting-started.md](getting-started.md#reading-the-docket).

**Entities.** Witness-local observations, filterable by subkind, kind and witness,
with a text search over keys and attributes. An entity page shows its attributes,
time claims, citation, and every relation in which it is subject or object.

**Query.** A SQL console with the same rules as `eg query`: one read-only SELECT,
no file or network access, results capped at a chosen row limit and downloadable
as CSV or JSON. The table list shows columns and which question each table serves.

**Case.** The declarations every conclusion is bound to (trust domains,
independence, authentic records, exclusive receipt tokens, clock bounds, handle
patterns), the pipeline with the exact command for each stage not yet run, the
stage history with whether each stage still matches the current case.json and
analyzer build, and the report hashes. A changed declaration or an analyzer upgrade
is flagged on every page until the stages are re-run.

## Security posture

- Binds to `127.0.0.1` by default. Binding another interface requires
  `--host` and, for the request to be accepted, `--allowed-host` naming the
  hostname browsers will send.
- Rejects requests whose `Host` header is not the bound host or a loopback name,
  so a web page elsewhere cannot drive the browser to read the case through DNS
  rebinding.
- Serves no evidence bytes directly; source rows are reachable only through
  citation resolution, which verifies hashes first.
- SQL runs with DuckDB external access disabled and reads limited to the case's own
  Parquet partitions, exactly as `eg query`.
- Sends a strict Content-Security-Policy; the page uses no inline script or style
  and inserts all data as text, never as HTML.

## Limits

The viewer displays; it does not run stages. Resolving a citation into an Inspect
`.eval` file re-parses that log, so the first lookup on a large transcript can take
a moment. The viewer is a reading surface for one investigator at a time on one
machine; it is not a multi-user service and has no authentication of its own.
