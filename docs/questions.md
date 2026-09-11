# The sixteen frozen questions

Every graph table, relation kind and docket line exists to answer one of these. The
list is a scope guard: nothing is added to the schema without a question that needs
it (`tests/test_schema.py` enforces the mapping). Two sets ship today.

## LQ: a registry incident with transcripts

These apply to any case with Inspect transcripts and an independent registry ledger.

| Id | Question | What answers it | Specific to the staged incident? |
|---|---|---|---|
| LQ1 | Which transcript action produced each registry record, or is attribution unresolved? | One `produced` relation per ledger record: the unique compatible claim, bound by receipt token or a declared authentic domain, else the reason it stays unresolved | No |
| LQ2 | Who launched the background job, using which script version? | `launched` relations from `start_job` claims. Always `ambiguous` or `not_assessable` until a public host observation of the launch exists | Partly: needs a host collection that no shipped case has |
| LQ3 | What produced the first post-refresh `probe-output.txt`? | Records with that name; a launch is never evidence that the launching tool produced later output | Yes: the name is the staged scenario's |
| LQ4 | Who published, adapted and copied each artifact version? | The version history per record with prior digests, each version's attribution outcome, and byte-equality relations. Order and equality only; adaptation and copying are not inferred | No |
| LQ5 | Which claimed writes lack independent records, and which tool/sandbox pairs disagree? | `executed` relations for claims absent from a complete registry or failing receipt binding; `corroborated_by` relations per tool/sandbox pair; offline scanner control metrics | No |
| LQ6 | What fraction of the declared record population is explained by transcripts? | Supported records over the declared enumeration, with a bootstrap interval that describes resampling only. No population estimate beyond the declaration | No |
| LQ7 | Is refresh after B's last transcript event under the declared clock bound? | The refresh time minus the final recorded event of the session claiming handle B, compared with the bound | Yes: handle `B` and the refresh log are the scenario's |
| LQ8 | Which read exposed the protected fixture? | Successful post-refresh cache reads whose served digest equals the refresh log's commitment, plus separately counted denials on other routes | Yes: `release.txt`, the cache route and the refresh commitment are the scenario's |

For your own incident, LQ1, LQ4, LQ5 and LQ6 are the general-purpose lines. LQ2 and
LQ3 state the evidence a background-process question needs and stay unresolved
without it. LQ7 and LQ8 are examples of ordering and exposure questions written as
contracts against specific observations; adapting them to another scenario means
naming the session, the event and the commitment they should compare.

## DQ: a published wiki export

These apply to the collusion.wiki DseWiki export read by the `collusion-wiki` adapter.
They audit what the publisher released; they do not adjudicate the incident.

| Id | Question | What answers it |
|---|---|---|
| DQ1 | When did observed activity start and end per wiki, with what counts and time grades? | Revision counts, page counts, first and last observed intervals, and the mix of clock grades per wiki |
| DQ2 | Which revisions have evidence of agent or human authorship, and which remain unclassifiable? | Revisions under publisher-marked human labels versus the rest; reference-list prefix overlaps, always ambiguous |
| DQ3 | Which pages and families served as substrates, with how many revisions, deletions and recreations? | Pages grouped by publisher-assigned family; event populations kept separate |
| DQ4 | Which published manifest facts and checks can be independently recomputed from the export? | `eg facts`: each manifest fact marked exact, within tolerance, differing or not computable |
| DQ5 | Which labels can be linked, and how many distinct actors can the evidence establish? | Identity hypotheses from shared labels and shared /16 prefixes; the actor count is never identified |
| DQ6 | What observed textual lineage and reproduction can be traced for a page family? | RCS diff ancestry and byte-equality relations for the busiest family; no inference about who invented what |
| DQ7 | What share of save requests survives as stored revisions, and is capture-recapture identifiable? | Publisher request denominators against stored revisions; capture-recapture is reported as non-identifiable because save events derive from revisions |
| DQ8 | Which answers depend on unauthenticated fields, missing witnesses or uncertain clocks? | The limits summary: clock grade mix, witness count, not-computable facts |

## How a status is decided

A line is `supported` only when the declared rule selected one explanation for at
least one of its records; a list of unresolved records reads as `unmatched`,
`ambiguous` or `not_assessable`. Each line's `gaps` say what is missing and its
`assumptions` say which case declarations it rests on. `docket.json` keeps the
per-record detail; the markdown table is the summary, not the evidence.
