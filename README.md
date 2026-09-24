# Evidencegraph

A research prototype for reconstructing what AI agents actually did during an
incident, from their transcripts and from records an independent system kept. It
preserves source bytes, builds a cited graph, distinguishes supported actions from
ambiguous claims, and writes a one-page evidence docket another investigator can
check line by line. MIT licensed. Not a finished product, and never a claim about
intent or authenticated identity.

## The problem

When several agents act on shared infrastructure, a transcript is only the agent's
own account of events. A tool result can be fabricated, a genuine receipt can be
copied into another agent's log, clocks disagree, and a transcript can simply be
missing. Grep and SQL over the logs will find matches, but they will not tell you
which matches are evidence and which are coincidence, nor keep track of the
assumptions each conclusion rests on.

Evidencegraph reconciles transcript claims against independently captured records
under declared trust and clock assumptions, and it abstains, visibly, whenever the
evidence does not separate the explanations. Every line of its output cites
immutable source bytes and lists the assumptions it depends on.

## Who it is for

People who already have native Inspect transcripts plus a separately collected
artifact, registry or process log, need a small number of factual answers with
citations, and must hand the reconstruction to someone else. If you only want to
browse or scan transcripts, Inspect's viewer and Scout already do that well.

## What you bring and what you get

You bring `.eval` transcripts and an independent ledger of what happened (formats in
[docs/getting-started.md](docs/getting-started.md)). You get:

- `DOCKET.md`: a table of frozen questions, each with a status and a one-line finding.
- `docket.json`: the numbers, per-record outcomes, citations, gaps and assumptions
  behind every line.
- `eg cite`: any citation resolves to the exact source row or native event, hash
  re-verified.
- A BagIt bundle whose docket can be recomputed from the bundled graph by whoever
  receives it.

## Try it in five minutes

```sh
uv sync --locked
uv run python scripts/demo.py cases/demo --seed 7
cat cases/demo/case/DOCKET.md
uv run eg verify cases/demo/bundle --recompute
```

The demo stages a synthetic incident with real Inspect logs and an independent
registry, withholds one agent's transcript, appends two fabricated write receipts to
another's, reconciles, scores the answers against private truth the graph never
reads, and exports a bundle. No Docker, credentials or paid model calls. Use a new
output directory each run.

## Use it on your own logs

```sh
uv run eg case init cases/mine --title "My incident" \
  --trust-domain registry="Artifact registry" --trust-domain runner="Inspect runner" \
  --independent registry:runner --clock-bound runner:container:1
uv run eg witness add cases/mine path/to/registry --adapter lab-public --trust-domain registry
uv run eg witness add cases/mine path/to/transcripts --adapter inspect-eval --trust-domain runner
uv run eg ingest cases/mine
uv run eg reconcile cases/mine --substrate registry
uv run eg coverage cases/mine
uv run eg docket cases/mine
uv run eg export cases/mine cases/mine-bundle
```

[docs/getting-started.md](docs/getting-started.md) explains each declaration, the
exact input contracts for both adapters, how to read the docket, and which missing
observation would resolve an unresolved line. `uv run eg --help` describes every
command.

## Read it in a browser

```sh
uv run eg serve cases/demo/case --open
```

`eg serve` starts a read-only viewer on `http://127.0.0.1:8765/` over a case or an
exported bundle: the docket with every per-record outcome, assumption and gap;
one-click citation resolution with the source hash re-verified; witnesses by trust
domain; entity and relation browsing; a read-only SQL console; and the pipeline
status with the command for each stage not yet run. It never mutates the case.
See [docs/viewer.md](docs/viewer.md).

## Optional review suggestions

```sh
uv run eg suggest claims cases/mine              # offline keyword baseline
uv run eg suggest show cases/mine write-claims:baseline
```

`eg suggest` asks a decision model, such as TypeSafe's Jev, bounded questions about each
cited transcript message ("what does this say about a write?", "how relevant is it to
my question?"). The answers are stored beside the graph as a review queue with full
probability distributions. They never change a relation, coverage population or
docket line, and the docket is byte-identical with or without them. Network providers
need `--allow-network` and a hard `--max-requests` budget; every exchange is recorded
and replayable offline. `eg serve` shows the queue on its Suggestions page. See
[docs/suggestions.md](docs/suggestions.md), including the pilot comparison and where
the model failed.

## Reading a docket

| Status | Meaning |
|---|---|
| `supported` | The declared rule selected exactly one explanation under the case's assumptions |
| `ambiguous` | Competing or unbound explanations remain |
| `contradicted` | Specific conflicting evidence exists |
| `unmatched` | Nothing in the acquired evidence corresponds |
| `not_assessable` | A required field, clock, population or witness is missing; `gaps` says which |

`supported` never means intent, actor authentication, or a bound on hidden activity.
Each supported line lists the case declarations it rests on; treat them as part of
the finding. The questions themselves are explained in
[docs/questions.md](docs/questions.md), and the terms in
[docs/glossary.md](docs/glossary.md).

## Cases in the repository

- **Receipt relay and evidence sufficiency.** Twelve paired development conditions
  expose a false attribution in the previous token rule and test the corrected
  default, missing clocks, dependent witnesses, incomplete capture, and deliberately
  false declarations. Run `uv run python scripts/incident_stress.py cases/stress`.
  See [the incident-response work pack](docs/sprint/README.md).
- **A staged registry incident** with private host truth. Ten seeds of one scripted
  scenario are checked against that truth: all nine supported attributions correct,
  both fabricated receipts contradicted, no false accusations, and a copied genuine
  receipt left unattributed rather than misattributed. These are software checks on
  one fixture, not estimates of real-world forensic accuracy. See
  [docs/cases/lab-validation.md](docs/cases/lab-validation.md).
- **The public collusion.wiki DseWiki export**: 14,591 revisions reconstructed and
  audited against the publisher's own manifest. This audits what was published; it
  does not adjudicate the incident. Bodies are not in git because no corpus licence
  has been identified. See [docs/cases/dsewiki.md](docs/cases/dsewiki.md).

## Known limits

- **A matching receipt is not enough for write attribution.** Every receipt field is
  copyable, including the token if another transcript discloses it. A matching token
  stays `ambiguous` by default. Support requires declared `--authentic-records`, or a
  matching token plus `--exclusive-receipts` for that transcript domain. The latter
  asserts that tokens could not be relayed into another event; hashes cannot verify
  it. A false declaration can still produce a false attribution.
- **Absence is evidence only inside complete capture.** A claimed write is
  contradicted only when a complete independent population's window contains the
  whole tool call widened by the clock bound; otherwise the tool abstains.
- **Conclusions are bound to `case.json`.** Changing a trust or clock declaration
  or upgrading the analyzer blocks rendering and export until the stages are re-run.
- **Handles and network prefixes never authenticate anyone.** Identity lines stay
  ambiguous by design.
- **Background launch and process questions** need a host-side collection with public
  launch and process observations that no shipped case has yet; they stay
  `not_assessable`.
- **Live paid scanning is disabled.** The Scout path is an offline control that
  repeats claims and must fail on fabricated ones; it evaluates no real model.
- **Wiki save events derive from stored revisions**, so they cannot support
  independent capture-recapture. No population estimate is made from transcript
  claims either.

A full independent review of the prototype, the defects it reproduced, and how each
was fixed is in [docs/critical-review.md](docs/critical-review.md). The rule contract
is [docs/evidencegraph.md](docs/evidencegraph.md).

## Documentation map

| Document | Contents |
|---|---|
| [docs/getting-started.md](docs/getting-started.md) | Bring your own evidence: recipe, input contracts, reading the docket |
| [docs/viewer.md](docs/viewer.md) | The local read-only viewer: what each page shows, security posture |
| [docs/questions.md](docs/questions.md) | The sixteen frozen questions and the evidence each needs |
| [docs/glossary.md](docs/glossary.md) | Witness, trust domain, receipt token, docket and the rest |
| [docs/evidencegraph.md](docs/evidencegraph.md) | Status vocabulary and forensic rules |
| [docs/suggestions.md](docs/suggestions.md) | Optional model review suggestions: boundaries, records, pilot results |
| [docs/typesafe-assessment.md](docs/typesafe-assessment.md) | Why and how a decision model may assist review, and what it must never decide |
| [docs/cases/lab-validation.md](docs/cases/lab-validation.md) | Staged incident and its ground-truth scores |
| [docs/cases/dsewiki.md](docs/cases/dsewiki.md) | Public export reconstruction findings |
| [docs/critical-review.md](docs/critical-review.md) | Independent review and the resolution of each finding |
| [docs/handoff.md](docs/handoff.md) | Release history and remaining acceptance work |
| [SECURITY.md](SECURITY.md) | How to report a misleading conclusion |
| [CONTRIBUTING.md](CONTRIBUTING.md) | What contributions help most |

## Development

```sh
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run basedpyright
uv run pytest -q
```

Feedback from investigators and evaluation researchers is the most useful input:
open an issue with the *Investigator / reviewer feedback* template and say whether a
docket would beat your current log and SQL workflow on a real case. See
[VENDORED.md](VENDORED.md) for the Crossledger source port.
