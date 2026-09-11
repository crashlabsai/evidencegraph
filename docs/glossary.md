# Glossary

**Case.** A directory holding `case.json` (your declarations), snapshotted evidence,
immutable graph partitions, and reports. Every command takes it as the first argument.

**Witness.** One acquired evidence file, hashed before parsing and stored read-only.
Its id combines the content hash with the adapter and filename, so the same bytes
observed under two names are two witnesses. Identical bytes can never sit in two
trust domains.

**Trust domain.** Who recorded a witness: the Inspect runner, a registry host, a
publisher's export. Declared in `case.json`. Relations between two domains are
`independent`, `same` or `unknown`; only `independent` pairs can yield a supported
cross-source match.

**Authentic records.** A per-domain declaration that its recorder was outside the
investigated actors' control, so a native event in it cannot have been fabricated by
them. It lets a unique matching receipt be supported without a receipt token; the
docket names the assumption.

**Clock bound.** The declared maximum disagreement, in seconds, between two clocks.
The registry rules read the `runner:container` pair. A declared bound is an
assumption; a measured one is evidence.

**Adapter.** A reader for one evidence format: `inspect-eval` for Inspect logs,
`lab-public` for independent registry observations, `collusion-wiki` for the DseWiki
export, `reference-list` for dated network-prefix lists.

**Entity.** A witness-local observation: a tool event, a ledger record, a read, a
revision, a handle. Entities from different witnesses are never merged silently.

**Relation.** A cited edge between two entities with an outcome, the method that
decided it, its candidates, and the trust relation between the witnesses involved.
Kinds include `produced` (record to the action that produced it), `executed`,
`launched`, `corroborated_by`, `built_on`, `reproduced`, `same_actor_as`.

**Outcome or status.** `supported`, `ambiguous`, `contradicted`, `unmatched` or
`not_assessable`. See [getting-started.md](getting-started.md#reading-the-docket).

**Citation.** A locator into an immutable witness: a JSONL line, a CSV record, a JSON
path, or a native Inspect event or message id, with the hash of the cited fragment.
`eg cite` resolves it and re-verifies the hash.

**Receipt.** The JSON a registry returns to a write call and the transcript records
as the tool result: `accepted`, `name`, `namespace`, `sha256`, `event_id`. Every
field is public once the ledger is published.

**Receipt token.** A secret the registry returns only to the caller, whose hash it
publishes in the ledger as `receipt_token_sha256`. A transcript receipt carrying the
matching token is bound to the record; a copied receipt cannot carry it.

**Population declaration.** The registry's completeness claim: the exact set of
record ids it accepted during a stated window. Absence is evidence only against a
complete population whose window covers the whole claim.

**Substrate.** The kind of thing being written: the registry namespace and name. The
reconciliation engine is written against a small substrate protocol so other
mutation stores can be added.

**Reconcile.** The stage that matches each independent record to at most one
transcript claim, in a fixed decision order, abstaining whenever the evidence does
not separate candidates.

**Coverage.** What fraction of the declared population has a supported attribution.
The bootstrap interval describes resampling variability only.

**Docket.** The one-page answer table (`DOCKET.md`) and its full JSON (`docket.json`)
with numbers, citations, gaps and assumptions per question.

**Bundle.** A BagIt directory with the evidence, graph, docket and checksum
manifests. `eg verify --recompute` re-derives the docket from the bundled graph and
compares.

**Private truth.** Host-side records of who really did what, used only by
`eg validate`. The graph reader never opens it; the staged incident keeps it under
`private/`.

**Scout control.** An offline scanner that repeats every write claim a transcript
makes and is scored against actual writes. It checks the plumbing and must fail on
fabricated claims; it says nothing about any real model.

**R3 and 2B.** Names carried over from the ported engine. R3 excludes candidates that
start after the record plus the clock bound. 2B is an opt-in rule for idempotent
creations observed by a later listing, where the earliest creation is the effective
one.
