# Evidencegraph prototype contract

An outcome describes a particular relation with particular witnesses. `supported`
means the declared rule selects one claim; it does not prove intent or authenticate
an actor. `ambiguous` retains competing or unauthenticated explanations. `unmatched`
means the acquired corpus contains no matching action. `contradicted` requires
specified conflicting evidence. `not_assessable` means a necessary field, clock,
population or witness is unavailable.

A docket answer that summarises many relations takes the status of its strongest
member only when at least one member is supported; a list of unresolved records is
reported as `unmatched`, `ambiguous` or `not_assessable`, never as supported. Each
answer's `numbers` keep the per-part evidence, and `assumptions` name the declared
case assumptions a supported status rests on.

## Storage and provenance

Each source is copied and hashed before parsing, then parsed only through its case
snapshot. Changing the original file cannot change the graph. A witness is one
observation of some bytes: its id hashes the source SHA-256 with the adapter and
filename, so two empty logs with different names are two witnesses, while the same
observation added twice is one. Identical bytes can never be declared in two trust
domains, because two copies of one file are not independent evidence. Ingestion
streams bounded JSONL records into 2,048-row Parquet batches. DuckDB creates views
over only the partitions named in a single manifest snapshot.

Mutations hold an OS file lock. New partitions are immutable; the stage commits by
atomically replacing `manifest.json`. Exceptions leave the previous manifest intact.
Interrupted generations can leave unreferenced files, but readers and exports ignore
them. Ingest checks cached partition hashes and the analyzer source identity before
skipping, including the case configuration hash. `case.json` supports
`handle_patterns` (each regex captures one claimed name), the numeric
`family_confidence` threshold, trust relations, clock bounds and per-domain
`authentic_records`. Every ingested and derived stage records the configuration hash
it ran under; reconciliation, coverage, validation, rendering and export refuse to
run while any stage's hash differs from the current `case.json`, so a changed trust
or clock declaration cannot leave an earlier conclusion in a report. Re-run ingest,
which re-derives nothing by itself, then the derived stages. Derived stages
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
A candidate's whole recorded execution interval must overlap the record within the
clock bound: all-late candidates are excluded by R3, and a synchronous call that
returned before the mutation minus the bound cannot have produced it. A record that
knows only when it was observed (a listing) skips the early-completion exclusion;
that is where the opt-in 2B earliest-creation rule applies, because registry writes
themselves are not idempotent `mkdir` operations. Launch calls never yield created
keys. Optional listing/removal protocol hooks are reserved; the prototype does not
run the old board shell evaluator or R1/R2 rules.

A unique matching receipt is not by itself a supported attribution. Every field of a
receipt is public once the ledger is, so a receipt copied from the ledger into a
forged transcript event matches key, digest, event id and timestamp exactly. The
engine promotes a unique compatible candidate only when one of two things holds:
the registry published a `receipt_token_sha256` commitment, the transcript carries
the matching `receipt_token`, and its domain explicitly declares
`exclusive_receipt_tokens` (`independent_receipt_binding`); or the domain declares
`authentic_records`, meaning its recorder was outside the investigated actors'
control (`independent_receipt`, and the docket names that assumption). Otherwise the
match is `ambiguous`: `receipt_possession_only` for a matching but transferable token,
`attribution_unbound` without a commitment, or `receipt_binding_missing` when a
commitment exists and the claim carries no token. A conflicting token is
`contradicted`. Exclusivity means a token could not have been relayed into another
event; it defaults to false. Falsely declaring it on a relayed receipt still causes
misattribution. See the paired control in [the stress protocol](sprint/protocol.md).

Derived results also retain the analyzer build id. Rendering, validation, further
derivation and export reject stages from an earlier analyzer. Re-ingesting with the
current code invalidates the old derived graph before recomputation.

Tool/sandbox consistency remains within the runner trust domain. Each tool call is
compared with each sandbox execution in its time window separately: same command
and consistent output is `supported`, same command and differing output is
`contradicted`, same command with an empty side is `not_assessable`, and a different
command is `unmatched`. One genuine match never lends support to an unrelated
execution in the same window, and every pair edge cites both events. It does not
independently establish spoofing. Missing successful registry receipts are
contradicted only against an independently declared complete population whose
capture window contains the whole tool execution interval widened by a declared
clock bound; a call that may have completed after capture stopped, or a case with no
declared bound, is `not_assessable`. A claim naming a real record but failing the
receipt binding is reported as an `executed` relation on that record.

Question answers use only observed parts. The refresh-ordering question compares the
refresh with the transcript's final recorded event of any type (a per-transcript
`transcript_final_event` entity with its own citation), not merely its last tool
call. The fixture-exposure question separates successful post-refresh cache reads,
the refresh log's commitment to what the cache serves, reads whose served digest
equals that commitment, and denials observed on non-cache routes; a denial appears
only when one was recorded, and without a refresh commitment the served bytes are
not identified.

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
bootstrap. When captures depend on each other, the population is not identified, and
the conditional model interval is not a bound on the true incident population. The
observed union bounds the population from below only when every counted unit is a
verified member with exact linkage; it is not computed for the registry case at all,
because transcript write claims can be fabricated and the declared enumeration
already is the population. Zero overlap returns no finite estimate.

The staged registry independently records dispatch tokens, native event bindings,
mutations and reads, issues a receipt token per write, publishes each token's hash in
the ledger, and commits in its refresh log to the digest the cache serves. It keeps
collecting for a settle period before the first and after the last action so the
declared window covers every call plus the clock bound. The validator joins private
host bindings without using graph matches as its answer key, and scores fixture
exposure as a completed answer. The offline Scout control deliberately repeats write
claims and must fail on injected spoofed events. These are engineering controls,
not a performance estimate for any real model or a claim of real-world robustness.

## What remains outside this release

- Real Mac Docker launch/job/host-clock validation and publication of the additional
  independent host observations needed to answer launch questions from public evidence.
  A Mac ledger without receipt-token commitments yields no supported attribution
  unless the runner domain is declared `authentic_records`.
- Establishing whether authenticity or token-exclusivity declarations are true.
  Forged records in a domain declared authentic, or relays in a domain declared
  exclusive, violate the assumptions and can cause wrong supported attributions.
- Provider-enforced cost accounting before a live Scout scan; `--max-usd` alone is
  not a reliable hard cap. The implemented CLI runs the offline control only.
- Independent review and corrections, and any decision to redistribute the public
  corpus in a bundle.
- Million-row performance characterization, incremental removal/replacement of a
  witness, cryptographic acquisition attestations, and a production redaction workflow.

BagIt checks prove the bundle's internal hashes and references agree. Anyone who
can replace the whole bundle can also replace its checksum manifests. Retain the
payload-manifest hash separately to detect wholesale replacement. Recompute mode
rebuilds the docket from bundled graph rows; it does not independently re-run the
upstream acquisition, model scanner, or every inference stage.
