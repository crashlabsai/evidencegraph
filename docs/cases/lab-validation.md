# Staged incident validation

The scripted demo explicitly declares `exclusive_receipt_tokens` for its runner:
these scenarios fabricate new receipts but do not relay genuine tokens. The broader
[incident stress pack](../sprint/README.md) tests that boundary, including a complete
receipt relay that must remain ambiguous without this declaration. Legacy case
configurations lacking the field now default to no token-exclusivity assumption.

The registry stage uses scripted A/D/B/C actions, genuine Inspect `.eval` containers,
independent durable registry mutations, and dispatch-to-event UUID bindings. No model
API or Docker is used. It publishes a script, copies it, overwrites its version,
performs denied and cached reads, refreshes the cache, and copies served fixture bytes.
Two fabricated write receipts are appended to B's transcript; A's transcript is dropped.
The registry hands each caller a receipt token and publishes only the token's hash;
its refresh log commits to the digest the cache serves; and it keeps collecting for
1.1 s before the first and after the last action so the declared window covers every
call widened by the declared 1 s clock bound.

Across the ten fixed seeds 0–9, each stage has 12 accepted mutations and three
captured transcripts. The checked result for every seed is:

| Measure | Result |
|---|---:|
| Correct supported production attributions (all via receipt binding) | 9 / 9 |
| Confident production errors | 0 |
| Dropped-transcript records left unmatched | 3 / 3 |
| Captured spoofed events contradicted | 2 / 2 |
| False spoof accusations | 0 |
| Reads that served the protected fixture (LQ8 as a completed answer) | 1 / 1, 0 errors |
| True captured record fraction | 0.75 |
| Coverage interval contains that fraction | 10 / 10 seeds |

A separate scenario appends to B's transcript a write event whose receipt copies A's
public ledger row field for field, with A's transcript dropped. The claim matches
key, digest, event id and timestamp, but carries no receipt token, so the record stays
`ambiguous` (`receipt_binding_missing`) and validation reports zero confident errors.
The forgery is not itself proven from public evidence: the scorer records that spoof
as captured but not contradicted. Before this rule the same input produced a
confidently wrong supported attribution.

Recall among records with captured native tool events is 1.0; recall over the full
record population is 0.75. The scorer reports both so dropping transcripts cannot
silently improve the headline recall. Version-ancestry and served-byte read relations
are separately checked against private mutations and read observations.

The seed-7 release demo also runs Scout's `llm_scanner` with `AnswerStructured` and a
scripted `mockllm` control. It deliberately repeats all claimed successful writes:
9 true positives, 2 false positives, 0 false negatives, precision 9/11 overall and
3/5 on the spoofed transcript. The graph's missing-write check identifies both
fabrications. This verifies the positive control; it is not an evaluation of a real
model. API spend is exactly USD 0. Native transcripts are also inserted into Scout's
Parquet database, whose files are included in the synthetic evidence bundle.

Truth is never a graph witness. The source of the key is the in-process registry's
private dispatch bindings, not inferred graph matches or agent-authored labels.
The public stage contains public observations; validation records a digest of its
private binding input without copying that input into the graph. A leak check
rejects private truth filenames and dispatch/actor metadata in public registry rows.

The coverage bootstrap is conditional on exchangeable observed records. Checking
that it contains the known captured fraction is not proof of calibrated uncertainty
for unknown populations, correlated missingness, deceptive agents, or production
telemetry. The incident choices repeat a small scenario across seeds; these ten
runs establish software correctness on this fixture, not broad forensic accuracy.

The [lab docket](lab-docket.md) gives the reading surface. Reproduce it with:

```sh
uv run python scripts/demo.py cases/lab-demo --seed 7
uv run eg verify cases/lab-demo/bundle --recompute
```

The exact checked seed-7 metrics are in [lab-validation.json](lab-validation.json).
Verified bundle payload-manifest hashes are in [bundle-hashes.json](bundle-hashes.json).

For the background-process case, the retained Crossledger repository provides
`python -m replica runtime` and `python -m replica rehearse`. Import a **public**
`evidence/stage-2` directory with `eg lab import-mac`. No clock bound is invented on
import: declare a measured bound before temporal reconciliation. A replica ledger
without receipt-token commitments yields no supported attribution unless the runner
domain is declared `authentic_records`; the docket then names that assumption.
Additional public host launch observations are needed to support independent launch
attribution; private audit records remain restricted to validation. This Mac run has
not been performed in the cloud VM, and LQ2/LQ3 remain explicit evidence gaps.
