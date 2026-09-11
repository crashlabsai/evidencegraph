# DseWiki reconstruction

Acquired 2026-09-11 from the [collusion.wiki download index](https://collusion.wiki/explorer/download).
The ZIP SHA-256 is
`eb68aa12d26bf189d8bfc4ce47f4d8af66ae5ba7ebbadd429738297a3cbb25ae`.
Its own `SHA256SUMS` matches the five packaged files. Six further files were
downloaded individually. Raw bodies remain under gitignored `cases/dsewiki/`.
The full file inventory and hashes are in [dsewiki-acquisition.json](dsewiki-acquisition.json).

The [one-page docket](dsewiki-docket.md) is the reading surface. Its underlying graph
contains 14,591 native revisions, 4,579 native pages, 19,913 physical event rows,
3,103 label rows, 13,703 selected text records, and 23,877 link rows. There are
143 site-coverage records and 110 gap records. These populations must not be added
together as an incident activity total.

| Wiki | Revisions | Stored pages | Earliest reported lower bound (UTC) | Latest upper bound (UTC) |
|---|---:|---:|---|---|
| dse | 13,403 | 3,908 | 2026-05-24 06:02:18 | 2026-07-02 17:24:41 |
| probier | 1,013 | 601 | 2026-05-24 11:56:30 | 2026-07-02 17:51:23 |
| fractal | 169 | 68 | 2026-05-24 06:21:15 | 2026-07-01 00:19:40 |
| dorfwiki | 6 | 2 | 2026-06-22 08:42:56 | 2026-06-22 08:46:19 |

These are observed extrema in the publisher's write-date cut, not the swarm's true
start or end. Docket JSON retains both endpoints for the start and end intervals.

## Findings that changed the implementation

- **Archive membership:** `full-wiki-logs.zip` includes pages, revisions, events,
  labels and manifest only. Off-wiki records, links and coverage files are separate.
- **Byte encoding:** every exported revision body reconstructs its original bytes
  using Latin-1 encoding of the JSON string. `body_encoding=utf8` describes those
  original bytes, not the string's JSON encoding. All 14,591 source body hashes
  verified: 14,340 ASCII bodies, 250 UTF-8 bodies, and one Latin-1 body.
- **Clocks:** every revision selects `revision.pref_ts` and reports uncertainty of
  one second. `reqlog` (14,482), `rclog` (103), and `write_date` (6) are corroboration
  grades. Every non-null raw clock is retained separately. Reported uncertainty is
  not a measurement of the physical clocks' error.
- **Join key:** each published save event has a unique `revision_ref` resolving to
  one revision. There are 14,591 of each. This is an internal export association,
  not a request-log capture. The graph marks the witness relationship as `same`.
- **Request denominators:** labels' `save_requests` comes from DSE
  `edit_actors.jsonl`; their `stored_revisions` spans multiple wikis. Retention uses
  DSE revisions in the numerator and reports the denominator as an unaudited
  publisher aggregate. Other wikis' request totals remain unavailable.
- **Recreation multiplicity:** the publisher reports 68 recreation relations but
  the export carries only 67 row-level links, including 63 stored revisions. One
  row can collapse multiple historical relations. Relation-level grades also cannot
  be recovered from the save row's grade. These quantities are `not_computable`
  from the available representation, rather than treated as errors in the report.
- **Names after redaction:** the published rows contain zero non-ASCII page names
  where the manifest records one; the exported labels are not fully in UTF-8 byte
  order although the manifest's check says they are. These two comparisons differ.
  The graph cannot determine whether redaction or a later rendering step explains it.

The audit contains 186 rows: **89 exact, 2 differing, 95 not computable**. This includes
58 named manifest facts, publisher checks, and six physical-count comparison rows;
the categories are comparisons, not 186 independent incident facts. The audit never
uses a publisher check's `actual` or `ok` field as its recomputed answer.

Three publisher-marked human labels account for 31 revisions; all actor identities
remain unauthenticated. The 899 blank-label revisions are excluded from label-author
denominators. Identity hypotheses over the 3,102 nonempty labels never establish
the number of distinct agents. Reference snapshots from
[Microsoft's Azure service tags](https://www.microsoft.com/en-us/download/details.aspx?id=56519)
and [OpenAI's fetch ranges](https://openai.com/chatgpt-user.json) add 177 ambiguous
masked-prefix overlaps. They are September snapshots, not historical ownership
proof for May–July. No ARIN historical attribution was acquired.

The busiest publisher-assigned family is `relay-coordination`. RCS diff ancestry and
content-hash reproduction are queryable with citations. Those links do not establish
who invented a technique, causal copying, off-wiki visits, or intent.

## Reproduction

```sh
uv run python scripts/reconstruct_dsewiki.py cases/reconstruction
uv run eg docket cases/reconstruction
uv run eg export cases/reconstruction cases/reconstruction-bundle
uv run eg verify cases/reconstruction-bundle --recompute
```

The script reproduces the pinned corpus without optional current reference lists.
To add a dated reference list, declare its trust domain at case initialization and
use `eg witness add --adapter reference-list`, then re-run ingest and identity.
These findings have not yet been independently reviewed.
