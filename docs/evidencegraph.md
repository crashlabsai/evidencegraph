# Evidencegraph prototype contract

An outcome describes a particular relation with particular witnesses. `supported`
means the declared rule selects one claim; it does not prove intent or authenticate
an actor. `ambiguous` retains competing or unauthenticated explanations. `unmatched`
means the acquired corpus contains no matching action. `contradicted` requires
specified conflicting evidence. `not_assessable` means a necessary field, clock,
population or witness is unavailable.

## Storage and provenance

Each source is copied and hashed before parsing, then parsed only through its case
snapshot. Changing the original file cannot change the graph. Witness ids use the
source SHA-256; conflicting semantics for identical bytes are rejected. Ingestion
streams bounded JSONL records into 2,048-row Parquet batches. DuckDB creates views
over only the partitions named in a single manifest snapshot.

Mutations hold an OS file lock. New partitions are immutable; the stage commits by
atomically replacing `manifest.json`. Exceptions leave the previous manifest intact.
Interrupted generations can leave unreferenced files, but readers and exports ignore
them. Ingest checks cached partition hashes and the analyzer source identity before
skipping, including the case configuration hash. `case.json` supports
`handle_patterns` (each regex captures one claimed name) and the numeric
`family_confidence` threshold. Re-run ingest after changing either. Derived stages
invalidate downstream coverage and dockets. Root-level
`DOCKET.md`, `docket.json`, and `validation.json` are convenience copies; the manifest
names the authoritative immutable report files.

Entities are witness-local. A revision's page reference and the separately published
page row remain two observations; `native_entities` excludes implicit references.
Use `revisions` for the convenient revision view and query native rows when counting
published pages or labels. No silent actor merging occurs.

JSONL citations count physical lines from one, including blank lines in the locator;
their hashes exclude the line ending. CSV locators count data records from one and
hash a canonical JSON object of the decoded fields. JSON paths hash canonical JSON.
Inspect locators retain Scout transcript ids and native message/event ids. Full body
bytes remain in the snapshot, never in a clipped preview used for matching.

## Forensic rules

Registry matching checks request/receipt agreement, key, version hash, event id,
clock bound and declared witness independence. Multiple valid candidates abstain.
The R3 check excludes all-late candidates before payload separation. The 2B earliest
creation rule is opt-in because registry writes are not idempotent `mkdir` operations.
Launch calls never yield created keys. Optional listing/removal protocol hooks are
reserved; the prototype does not run the old board shell evaluator or R1/R2 rules.

Tool/sandbox consistency remains within the runner trust domain. It tolerates
recorded truncation, keeps multiple candidate events, and treats empty outputs as
not comparable. It does not independently establish spoofing. Missing successful
registry receipts are contradicted only against an independently declared complete
population covering the tool event's time window.

Identity links based on names or masked networks stay ambiguous. A supported
`same_actor_as` edge requires both endpoint identities to carry authentication.
Text-hash equality establishes reproduction of bytes; normalized whitespace is a
weaker match. RCS diff bases establish textual ancestry, not invention or motive.

## Coverage and validation

Declared record coverage counts only supported outcomes in its numerator. Ambiguous,
contradicted and unmatched outcomes remain in the denominator. Unassessable records
are explicitly excluded. Label coverage uses nonempty labels as clusters, not actors.
Binary percentile bootstraps describe exchangeable resampling of observed units;
they do not cover systematic missingness, clustered dropped sessions, or wrong rules.

Chapman's estimate is accompanied by its assumptions and a parametric hypergeometric
bootstrap. When captures depend on each other, the population is not identified.
Only the observed union is a lower bound; the conditional model interval is not a
bound on the true incident population. Zero overlap returns no finite estimate.

The staged registry independently records dispatch tokens, native event bindings,
mutations and reads. The validator joins these host bindings without using graph
matches as its answer key. The offline Scout control deliberately repeats write
claims and must fail on injected spoofed events. These are engineering controls,
not a performance estimate for any real model or a claim of real-world robustness.

## What remains outside this release

- Real Mac Docker launch/job/host-clock validation and publication of the additional
  independent host observations needed to answer launch questions from public evidence.
- Provider-enforced cost accounting before a live Scout scan; `--max-usd` alone is
  not a reliable hard cap. The implemented CLI runs the offline control only.
- A named investigator's review, corrections and any decision to redistribute the
  public corpus in a bundle. 
- Million-row performance characterization, incremental removal/replacement of a
  witness, cryptographic acquisition attestations, and a production redaction workflow.

BagIt checks prove the bundle's internal hashes and references agree. Anyone who
can replace the whole bundle can also replace its checksum manifests. Retain the
payload-manifest hash separately to detect wholesale replacement. Recompute mode
rebuilds the docket from bundled graph rows; it does not independently re-run the
upstream acquisition, model scanner, or every inference stage.
