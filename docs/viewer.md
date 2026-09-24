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
relations between domains, each file's hash, size, adapter, coverage claim and
ingestion state. Source files come first; content-addressed artifacts are folded
behind a count. One button re-verifies every snapshot against its recorded size and
SHA-256 and marks each row. A witness page does the same for one file and breaks
its entities down by subkind.

**Relations.** The cited edges of the graph, unresolved ones first, filterable by
kind, outcome, method and run. Unresolved rows state what would change their
outcome, using the same table as
[getting-started.md](getting-started.md#reading-the-docket).

**Entities.** Witness-local observations, filterable by subkind, kind and witness,
with a text search over keys and attributes. An entity page leads with every
relation in which the entity is subject or object, then its attributes, time claims
and citation.

**Suggestions.** Optional review suggestions from `eg suggest`, which are not evidence
(see [suggestions.md](suggestions.md)). Each published run appears as a card with its
model, provider, query and disposition counts, plus a warning when the evidence,
question wording, review policy or suggestion code changed after the run. A run whose
files changed since publication is listed as unreadable rather than shown. Without a
choice, the page opens the current model run for write claims. The queue is in the
same order as `eg suggest show`. It can be filtered by disposition, answer, role and
transcript, and searched by message text. A filter hides rows but never renumbers
them. Opening a message shows the exact redacted text the provider received,
re-hashed against its recorded request, beside every probability each question
returned. The page also says why the policy placed the message where it did. **Open
verified source** resolves the same message through the citation drawer. **Recheck
recorded answers** re-validates every recorded response offline, as
`eg verify --recompute` does, and contacts no provider. Read marks (`x`, or the
number beside a message) are kept in the browser only. `j` and `k` move through the
queue, and `s` opens the source.

**Query.** A SQL console with the same rules as `eg query`: one read-only SELECT,
no file or network access, results capped at a chosen row limit and downloadable
as CSV or JSON. The table list shows columns and which question each table serves.

**Case.** The pipeline first, with the next command to run ready to copy. The
reconciliation steps follow from the evidence ingested: a registry ledger needs the
registry substrate, a wiki export needs wiki-saves plus the facts, identity and
lineage stages, and a case with only transcripts has nothing to reconcile. Then
the declarations every conclusion is bound to (trust domains with what each holds,
independence, authentic records, exclusive receipt tokens, clock bounds), the stage
history with whether each stage still matches the current case.json and analyzer
build, and the report hashes. A changed declaration or an analyzer upgrade is
flagged on every page until the stages are re-run.

## Security posture

- Binds to `127.0.0.1` by default. Binding another interface requires
  `--host` and, for the request to be accepted, `--allowed-host` naming the
  hostname browsers will send.
- Rejects requests whose `Host` header is not the bound host or a loopback name,
  so a web page elsewhere cannot drive the browser to read the case through DNS
  rebinding.
- Serves no evidence bytes directly; source rows are reachable only through
  citation resolution, which verifies hashes first. The Suggestions page shows only
  the redacted request text a suggestion run recorded, after re-hashing it against
  its content address.
- SQL runs with DuckDB external access disabled and reads limited to the case's own
  Parquet partitions, exactly as `eg query`.
- Sends a strict Content-Security-Policy; the page uses no inline script or style
  and inserts all data as text, never as HTML.

## Limits

The viewer displays; it does not run stages. Resolving a citation into an Inspect
`.eval` file re-parses that log, so the first lookup on a large transcript can take
a moment. The viewer is a reading surface for one investigator at a time on one
machine; it is not a multi-user service and has no authentication of its own.
