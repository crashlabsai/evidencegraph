# Development protocol: receipt relay and evidence sufficiency

Status: development checks documented after observing the relay counterexample;
not a preregistration or held-out evaluation. The starting revision is
`efbd4192767672e2ca3634c9ebe9d9a3c98fcddd`. Exact current source and dependency identities
are recorded in [results.json](results.json).

## Question and threat model

When can an independently observed registry mutation be attributed to a particular
native transcript event? The investigated party can fabricate transcript events,
copy published ledger fields, or obtain another transcript's complete receipt.
The registry remains an independent observation of mutations. Some views remove
that independence or the completeness and clock assumptions to test abstention.

Each seed stages twelve accepted mutations by four scripted labels, plus reads and
a cache refresh. These are real in-process registry operations and native Inspect
containers, without model inference. Public observations omit the host's dispatch
bindings. Private bindings join each mutation to its true native event UUID and are
read only by the scorer, after predictions. Labels never establish actor identity.

Constructing a full receipt relay reads A's **public** transcript, copies its token
into a new B event, then withholds A. It never consults private truth to construct
the forged observation. No production credential or exploit is involved.

## Paired conditions

| Condition | Evidence change | Explicit declarations | Required behavior |
|---|---|---|---|
| clean | None | Independent, 0.05 s bound, exclusive tokens | 12 correct supported |
| fabricated | Add two successful receipts for nonexistent records | As clean; complete capture | 12 correct supported; 2 fabricated claims contradicted |
| missing_transcript | Fabricated view with A removed | As clean | 9 correct supported; 3 unmatched; 2 contradictions |
| copied_public_receipt | Copy A's public ledger receipt into B, remove A | As clean | Copied claim ambiguous; 9 correct supported |
| full_receipt_relay | Also copy the genuine token | Exclusivity **unset** | 10 matching claims ambiguous; 2 records unmatched |
| false_exclusivity | Exactly the same bytes as full receipt relay | Exclusivity falsely set | Negative control: 10 supported, 9 correct, 1 wrong |
| shared_tokens_no_attack | Exactly the same bytes as clean | Exclusivity unset | 12 ambiguous; measures abstention cost |
| missing_tokens | Strip tokens from native tool results and corresponding messages | As clean | 12 ambiguous despite retained commitments |
| missing_clock_bound | Exactly the same bytes as clean | Clock bound absent | 12 not assessable |
| dependent_witnesses | Exactly the same bytes as fabricated | Domains declared the same | 12 ambiguous; no absence-based accusations |
| incomplete_capture | Fabricated view with completeness false | Other declarations as clean | 12 correct supported; fabricated claims not assessable |
| capture_window_edge | Add invented receipts at the last call; end capture there | Other declarations as clean | 12 correct supported; fabricated claims not assessable |

The registry collects for 0.1 seconds before and after normal actions; the declared
clock bound is 0.05 seconds. The edge condition ends capture at the final genuine
call's completion. Every real mutation is still in the window, but the added claims'
possible completion times extend beyond it after widening by the bound.

Views are generated once per seed. The relay/false-exclusivity and
clean/shared-token pairs have identical public-inventory hashes. Conditions change
the declared evidence available to the rule, not the underlying accepted mutations.

## Controls and scoring

The **implicit-token-exclusivity ablation** runs the same reconciler on the same
graph, with exclusivity implicitly granted to every witness. This reproduces the
previous token promotion rule while holding candidate selection, clock handling
and trust relations constant. It is an ablation, not another forensic product.

The **accept-success-claims control** accepts every well-formed successful receipt.
It measures false acceptance of fabricated event claims. Its unit is a claim;
attribution metrics use registry records. Those denominators are not compared as
though they measured the same task. Neither control calls an LLM or measures Scout,
SQL experts, or human performance.

Attribution is correct only when the predicted native event UUID equals the
private host dispatch binding. Report correct supported counts, wrong supported
counts, record-population recall, recall among captured genuine events, and
abstention. Precision is null when no attribution is supported. Fabricated-event
contradictions are scored separately against the injected UUIDs; an unproven
fabrication is a miss, not an accepted fact. False accusations are reported.

Run seeds 0, 1 and 2. Seeds vary payloads; labels, action structure and transforms
are fixed. Results are software checks, not an accuracy estimate. Do not pool 36
views into a confidence interval or call them independent attacks. The clean,
unknown-exclusivity control is essential: default abstention also discards useful
true attribution. The intentionally false declaration must remain in the table.

## Reproduction and verification

`scripts/incident_stress.py` is the entry point. It refuses existing output paths,
retains every view, creates native cases through normal ingestion, scores with the
separate truth store, and records source identity before and after execution.
The three representative bundles are clean, missing-transcript and full-relay cases
for the first seed. Each is exported and verified with docket recomputation.

Verification checks the included hashes and recomputes the docket **from the graph**.
It does not re-authenticate original capture, prove witness independence, replay all
inference from raw bytes, or authenticate the unsigned bundle manifest. Rerunning
the stress command does independently regenerate inputs and run the full pipeline.
Expected decisions and scores reproduce; UUIDs, receipt tokens, timestamps and
byte hashes vary between runs. Private truth contains only synthetic data.
