# Evidencegraph

A working prototype for agent incident forensics. It preserves source bytes, builds
a cited graph, distinguishes supported actions from ambiguous claims, and writes a
compact evidence docket. Its first cases are the public collusion.wiki export and
a scripted registry incident with private host truth.

```sh
uv sync --locked
uv run python scripts/demo.py cases/demo
cat cases/demo/case/DOCKET.md
uv run eg verify cases/demo/bundle --recompute
```

The demo creates real Inspect `.eval` logs, drops one transcript, injects two false
write receipts, runs graph attribution and an offline Scout control, checks the
answers against private truth, and exports a portable BagIt directory. It uses no
Docker, credentials, or paid model calls. Choose a new output directory for each run.

Reconstruct the public wiki corpus:

```sh
uv run python scripts/reconstruct_dsewiki.py cases/wiki
uv run eg query cases/wiki "SELECT wiki,count(*) AS revisions FROM revisions GROUP BY 1"
uv run eg query cases/wiki "SELECT fact_name,status,reason FROM facts WHERE status!='exact'"
```

Raw datasets and generated cases are gitignored. The ZIP is pinned by SHA-256;
separate off-wiki downloads become individually hashed witnesses. No corpus licence
has been identified, so source bodies are not committed here.

Use `uv run eg --help` for individual commands. `case init`, `witness add`, `ingest`,
`reconcile`, `identity`, `lineage`, `coverage`, `facts`, `docket`, `query`, `cite`,
`validate`, `export`, `verify`, `scout-db`, and the offline `scan` path are available.
The [architecture and limitations](docs/evidencegraph.md) describe what each status means.
The [DseWiki findings](docs/cases/dsewiki.md) and [lab validation](docs/cases/lab-validation.md)
record the real-data and ground-truth checks.
The [handoff](docs/handoff.md) records repository recovery and remaining acceptance work.

Every schema field and graph relation maps to one of 16 frozen forensic questions.
Private truth is read only by validation/key generation. The graph reader never
opens it. Citations locate immutable JSONL lines, CSV records, JSON values, or native
Inspect events/messages. Matching handles and IP prefixes do not authenticate actors.

This release does not establish intent, hidden activity, or the true number of
actors. Published wiki save events are derived from stored revisions and cannot
provide independent capture-recapture. Live paid scanning is disabled until a real
spend guard exists. Background-process questions require the separate Mac Docker
collection and additional public host observations; absent evidence stays explicit.

```sh
uv run ruff check
uv run ruff format --check
uv run basedpyright
uv run pytest -q
```

Code is MIT licensed. See [VENDORED.md](VENDORED.md) for the Crossledger source port.
