# Evidence sufficiency stress results

Paired development checks on one scripted scenario per seed; views share truth and are not independent trials.

Each row below is one view of the first seed. All seeds and private scoring inventories are in `results.json`. A fabricated claim not contradicted remains unresolved; it is not accepted as true.

| Evidence condition | Correct supported / 12 | Confident errors | Fabricated claims contradicted | Old token rule errors | Claim-only false accepts |
|---|---:|---:|---:|---:|---:|
| clean | 12 / 12 | 0 | 0 / 0 | 0 | 0 |
| fabricated | 12 / 12 | 0 | 2 / 2 | 0 | 2 |
| missing_transcript | 9 / 12 | 0 | 2 / 2 | 0 | 2 |
| copied_public_receipt | 9 / 12 | 0 | 0 / 1 | 0 | 1 |
| full_receipt_relay | 0 / 12 | 0 | 0 / 1 | 1 | 1 |
| false_exclusivity | 9 / 12 | 1 | 0 / 1 | 1 | 1 |
| shared_tokens_no_attack | 0 / 12 | 0 | 0 / 0 | 0 | 0 |
| missing_tokens | 0 / 12 | 0 | 0 / 0 | 0 | 0 |
| missing_clock_bound | 0 / 12 | 0 | 0 / 0 | 0 | 0 |
| dependent_witnesses | 0 / 12 | 0 | 0 / 2 | 0 | 2 |
| incomplete_capture | 12 / 12 | 0 | 0 / 2 | 0 | 2 |
| capture_window_edge | 12 / 12 | 0 | 0 / 2 | 0 | 2 |

Contract checks: 36/36 passed.

`false_exclusivity` is an intentionally false declaration on exactly the same receipt-relay evidence. Its error is the expected negative control, not evidence of safe behavior under that declaration.

Default abstention also removes correct attributions when receipt isolation is unknown. The clean `shared_tokens_no_attack` control makes this cost visible.

## Limits

- No real attackers, model calls, human investigators, or production incident logs.
- No accuracy, time-saving, calibrated-coverage, or containment-effectiveness estimate.
- False exclusivity is deliberately invalid and must produce a confident error.
- Bundle recomputation verifies the docket against the bundled graph, not authenticity of the original capture or truth labels.

Run `uv run python scripts/incident_stress.py NEW_DIRECTORY` to regenerate. Paths, timestamps, tokens, and native UUIDs change; scenario decisions and scores should reproduce.
