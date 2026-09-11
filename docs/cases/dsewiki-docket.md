# DseWiki: published evidence reconstruction

Evidence docket · prototype · source assertions and independently corroborated actions remain distinct.

| Question | Status | Finding |
|---|---|---|
| DQ1 Activity | supported | 14,591 revisions across 4 wikis; reported revision uncertainty ±1s |
| DQ2 Authorship | ambiguous | 31 revisions under publisher-marked human labels; 14,560 lack authenticated classification |
| DQ3 Substrates | supported | 4,579 stored pages; 49 publisher-assigned families |
| DQ4 Fact audit | supported | 2 differs, 89 exact, 95 not_computable |
| DQ5 Identity | ambiguous | 3,102 nonempty labels; actor count not identifiable |
| DQ6 Lineage | supported | Observed lineage for relay-coordination; originating actor remains unknown |
| DQ7 Retention | ambiguous | Request coverage unavailable per wiki; DSE label retention is conditional on publisher totals |
| DQ8 Limits | supported | Unauthenticated actors; dependent export witnesses; unpublished raw request logs |

| Coverage population | Observed fraction | Conditional interval / limit |
|---|---|---|
| Published DSE totals for 3,015 nonempty labels | 13,222/39,364 = 33.6% | Unaudited publisher denominator; no independent population estimate |

Revision time grades: reqlog 14,482; rclog 103; write_date 6.

Witness inventory (full hashes, intervals and citations in [dsewiki-docket.json](dsewiki-docket.json)):

| Sources | Trust domain | Files |
|---|---|---:|
| azure-20260907.json | microsoft-servicetags | 1 |
| chatgpt-user.json | openai-allowlist | 1 |
| Source files | publisher-export | 11 |

Known gaps: events.jsonl save rows are generated from revisions, not all save requests. No authenticated actor identities; human labels are publisher assertions. Observed write-date cut, not true swarm start/end; reported accuracy is not independently calibrated. Diff ancestry and identical text do not establish who invented a technique or causal copying.

Resolve a citation with `eg cite CASE REF_ID`; query the full graph with `eg query CASE SQL`.
