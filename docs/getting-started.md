# Getting started with your own evidence

This guide takes you from two kinds of files you already have to a cited docket you
can hand to a colleague. Every command below is exercised by
`tests/test_cli.py::test_documented_recipe_runs_end_to_end`, so it runs as written.

## What you bring

1. **Transcripts.** Inspect `.eval` logs of the agents under investigation. The
   `inspect-eval` adapter reads them through Scout's public API and cites native
   event and message ids.
2. **An independent record of what actually happened.** Something written by a
   system the agents could not edit: a registry or artifact store ledger, a read log,
   a process or dispatch log. The `lab-public` adapter reads the registry format
   described below. If your independent log has a different shape, converting it to
   that format is usually a short script; the fields that matter are listed here.

If you only have transcripts, the tool still ingests them, checks tool calls against
sandbox executions within the runner, and cites everything. It cannot attribute
writes or contradict claims without the second source, and it will say so.

## What you get

`DOCKET.md`, a one-page table of the frozen questions with a status and a one-line
finding each, backed by `docket.json`, which carries the numbers, citations, gaps and
assumptions behind every line. Every citation resolves with `eg cite` back to the
exact source bytes, which were hashed before anything parsed them. A BagIt bundle
packages evidence, graph and docket with checksums for handoff.

## The recipe

```sh
uv sync --locked

# 1. Declare assumptions. Two domains, declared independent, with a bound on how far
#    the runner's clock and the registry's clock may disagree.
uv run eg case init cases/mine --title "My incident" \
  --trust-domain registry="Artifact registry" \
  --trust-domain runner="Inspect runner" \
  --independent registry:runner \
  --clock-bound runner:container:1

# 2. Acquire evidence. Files are hashed and snapshotted before they are read.
uv run eg witness add cases/mine path/to/registry --adapter lab-public --trust-domain registry
uv run eg witness add cases/mine path/to/transcripts --adapter inspect-eval --trust-domain runner

# 3. Build the graph, reconcile claims against records, estimate coverage, answer.
uv run eg ingest cases/mine
uv run eg reconcile cases/mine --substrate registry
uv run eg coverage cases/mine
uv run eg docket cases/mine
cat cases/mine/DOCKET.md

# 4. Inspect, then package for handoff.
uv run eg query cases/mine "SELECT outcome, method, count(*) n FROM relations WHERE kind='produced' GROUP BY 1,2"
uv run eg cite cases/mine ref-...            # any citation id from docket.json
uv run eg export cases/mine cases/mine-bundle
uv run eg verify cases/mine-bundle --recompute
```

Try it first on a synthetic incident that ships with the repo:

```sh
uv run eg lab stage cases/stage --seed 7 --spoof B:2 --drop A
# then use cases/stage/public as the registry path and cases/stage/public/transcripts
# as the transcripts path in the recipe above
```

Or run `uv run python scripts/demo.py cases/demo --seed 7`, which does all of this and
also scores the answers against the stage's private truth.

### About the declarations

- **Trust domains** name who recorded what. Nothing crosses domains as "supported"
  unless you declared the pair `--independent`. Two copies of the same file are not
  independent, and the tool refuses identical bytes in two domains.
- **Clock bound** is the most the two clocks may disagree, in seconds. The registry
  rules read the `runner:container` pair. Without it, timing questions are
  `not_assessable`. Measure it if you can; a declared guess is an assumption the
  docket carries.
- **`--authentic-records runner`** declares that the runner's logs were written by a
  recorder the agents could not edit. Only declare it when that is true of your
  harness. Without it, and without receipt tokens (below), a matching write claim is
  `ambiguous`, because every field of a receipt is public once the ledger is and a
  forged transcript event could copy it.

If you change any declaration later, `eg ingest` re-parses under the new
`case.json`, and every derived stage must be re-run. Rendering or exporting with a
stale stage is refused rather than silently mixing assumptions.

## Collect with Docker

Use a Linux Docker host with cgroup v2, uv, and host Python 3.13. Docker Desktop
on a Mac is an optional collection path. Pull the exact runtime image first:

```sh
docker info --format '{{.CgroupVersion}}'  # must print 2
docker pull python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17
uv sync --locked
uv run eg lab collect-docker cases/docker --seed 7 --timeout-seconds 120
cat cases/docker/collection.json
```

Use a new output directory. A successful collection exits 0 and records
`status: "ok"`. It stages the A/D/B/C script, launches B's chosen script version,
overwrites that registry name after launch, ends B's recorded actions, refreshes
the cache, drains the background job, and lets C read and copy its output. The job
polls until the cache payload changes; collection fails if C does not observe the
refreshed payload. No paid model calls are made.

The supervisor uses a read-only filesystem and runtime, isolated networking,
512 MiB of memory, 2 CPUs, and a 64-process limit. Startup checks the limits as
reported by the container's cgroup files. Job processes run under unprivileged
UIDs and cannot read the root-only `/truth` volume. Socket authorization uses
Linux peer credentials and process start times. A Docker daemon reporting cgroup
v2 is insufficient if the host cannot actually start a limited container.

The output separates `public/` ledger, artifacts, read/refresh observations and
native transcripts from `private/` audit records, dispatch bindings, process
identities and Docker diagnostics. Raw host send/receive and local monotonic
brackets for every control request are in `private/control-requests.jsonl`.
Startup and shutdown are the first and last control requests. The runtime inside
the pinned Python 3.12 image uses only the standard library.

To check the flat runtime imports independently of collection, run this from the
repository root on a working Docker host:

```sh
docker run --rm --network=none --read-only \
  --mount "type=bind,src=$PWD/src/evidencegraph/lab/runtime,dst=/app,readonly" \
  --workdir=/app \
  python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17 \
  python -S -c 'import server, collection, runtime_io, registry, client, registry_client'
```

The working directory makes the flat files importable. The helper is named
`runtime_io` because `import io` resolves to Python's built-in module. `-S`
disables site packages so this check cannot depend on an installed Evidencegraph.

On failure, the command exits 1 and writes `collection.json` with the failing
step, errors, retained resources, and any manual cleanup commands. The operational
deadline includes startup and scenario requests. A separate cleanup grace of up
to 30 seconds drains or kills jobs, confirms that the supervisor has stopped,
exports available truth, and removes resources. Export first tries `docker cp`
from the container, including a stopped container,
then a disposable container that mounts the truth volume read-only. The truth
volume is retained if both exports fail or fail integrity checks; the report prints its exact
`docker volume rm NAME` command. If Docker itself prevents removal, the report
also names the resource and removal error. Failed runs never claim a complete
public population.

The collector does not yet publish the typed host streams, derive clock bounds,
or provide `import-docker`. The existing forensic rules and in-process demo remain
unchanged. Preserve private output for future grading; never add it or generated
transcripts to git. On a Mac, sleep or VM clock drift can invalidate timing
measurements; raw brackets alone do not establish clock validity.

The default test suite exercises controller success and failure with an executable
Docker shim. It does not prove kernel isolation or real container execution:

```sh
uv run pytest -q tests/test_lab_docker.py tests/test_lab_server.py tests/test_runtime_subset.py tests/test_collection.py
```

## Input contracts

### Transcripts (`inspect-eval`)

Any Inspect `.eval` file. What the rules look at:

- **Write claims.** A `ToolEvent` whose `function` is `registry_write`, whose
  arguments contain a string `name` and string `payload`, and whose result parses as a
  JSON receipt with `accepted: true`, the same `name`, a string `namespace`, a string
  `event_id`, and `sha256` equal to the SHA-256 of the payload. Optional
  `receipt_token` binds the claim to the ledger (see below). Errored, pending or
  truncated events are never claims.
- **Launch claims.** A `start_job` tool whose result has `started: true` and a
  `job_id`, with `arguments.version` naming the script digest. These produce
  `launched` edges that stay `ambiguous` until an independent host observation
  exists; a launch never counts as producing a later record.
- **Sandbox corroboration.** Tools with a `cmd`, `command` or `code` argument are
  compared with `SandboxEvent`s in their time window, pair by pair.
- **Handles.** A regex over system and user messages, by default `^Handle:\s*(\S+)`,
  records the handle a session claims. Configure `handle_patterns` in `case.json`.
  Handles are never authenticated.
- **Final event.** The last recorded event of any type per transcript is kept, with
  its citation, for questions about what happened after a session ended.

### Independent registry observations (`lab-public`)

A directory, or individual files, in this layout:

| File | Contents |
|---|---|
| `ledger.jsonl` | One accepted mutation per line: `id`, `namespace`, `name`, `sha256`, `ts` (ISO 8601 with timezone), optional `payload`, `previous_sha256`, `path`, `operation`, `receipt_token_sha256` |
| `population.json` | The completeness claim: `namespace`, `count`, `event_ids`, `complete: true`, `started_at`, `stopped_at`, `definition` |
| `registry-reads.json` | List of `{ts, route, name, status, payload}` read observations |
| `registry-refresh.json` | List of `{kind: "cache_refresh", ts, name, source_route, payload_sha256}` |
| `artifacts/<sha256>` | Optional content-addressed bytes; the filename must equal the file's SHA-256 |

Rules that depend on these fields:

- A ledger `id` is what a transcript receipt's `event_id` must name.
- A missing record contradicts a successful claim only when `population.json` says
  `complete: true`, enumerates exactly the ledger's ids, and its window contains the
  whole tool call widened by the clock bound on both sides. Keep collecting for at
  least the bound before and after the incident.
- `receipt_token_sha256` is the registry's commitment to a token it returned only to
  the caller. A transcript receipt carrying the matching `receipt_token` is bound to
  the record; one carrying a different token is contradicted; one carrying none is
  ambiguous. This is what separates a genuine receipt from a copied one without
  assuming the transcript is authentic.
- A refresh entry's `payload_sha256` commits to what the cache serves afterwards, so
  later reads can be matched to it from public evidence.

`registry-reads.json` and `registry-refresh.json` may be empty lists.

## Reading the docket

Each row has a status:

| Status | Meaning |
|---|---|
| `supported` | The declared rule selected exactly one explanation under the case's assumptions. Not proof of intent, not actor authentication. |
| `ambiguous` | Competing or unbound explanations remain. |
| `contradicted` | Specific conflicting evidence exists, for example a claimed write absent from a complete independent record. |
| `unmatched` | Nothing in the acquired evidence corresponds. |
| `not_assessable` | A required field, clock, population or witness is missing. The `gaps` list says which. |

A question about many records is only as strong as its evidence: it reads
`supported` when at least one record is, otherwise the weakest resolution present.
`docket.json` keeps the per-record outcomes, the method that decided each, the
citations, and the assumptions a supported line rests on. Treat the assumptions as
part of the finding.

To find out what would resolve an unresolved line, read its `gaps` and the `method`
of the underlying relations:

| Method | What would change it |
|---|---|
| `clock_bound_missing` | Declare or measure a `runner:container` bound |
| `trust_not_independent` | Declare the two domains independent, if that is true |
| `attribution_unbound` | Add receipt tokens to the registry, or declare the runner authentic if it is |
| `receipt_binding_missing` | The claim lacks the token the registry issued; nothing public resolves it |
| `unseparated_candidates` | Two claims match equally; only more evidence separates them |
| `key_and_version` | No claim matches; the writer's transcript may be missing |

[questions.md](questions.md) explains each question, the evidence it needs, and which
questions are specific to the staged incident. [glossary.md](glossary.md) defines the
terms. [evidencegraph.md](evidencegraph.md) is the full rule contract.

## Scoring against ground truth

If you control the environment, keep a private record that the graph never reads:
which tool event actually produced each record. `eg validate CASE --truth DIR` scores
supported attributions, spoof detection, lineage, reads and fixture exposure against
it and reports every confident error. The staged incident's private directory shows
the expected layout. Scores on a fixture are software checks, not real-world accuracy.
