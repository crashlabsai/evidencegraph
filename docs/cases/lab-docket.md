# Staged registry incident — seed 7

Evidence docket · prototype · source assertions and independently corroborated actions remain distinct.

| Question | Status | Finding |
|---|---|---|
| LQ1 Write attribution | supported | 9 supported, 3 unmatched |
| LQ2 Job launch | not_assessable | No background job in the in-process stage |
| LQ3 Background output | not_assessable | No background output in this stage |
| LQ4 Versions | supported | Version history and content copies are cited separately from claimed base versions |
| LQ5 Integrity | supported | 2 claimed writes contradicted by the complete independent registry; 0 matching claims lack a receipt binding; runner consistency reported separately |
| LQ6 Coverage | supported | 9 of 12 declared records have a supported transcript attribution; the declared enumeration is the population, no estimate beyond it |
| LQ7 Refresh timing | ambiguous | Refresh minus final recorded B transcript event (tool): -0.008s, declared clock bound ±1s |
| LQ8 Fixture access | supported | 1 post-refresh cache reads served the refresh-committed bytes; 1 protected-route denials observed |

| Coverage population | Observed fraction | Conditional interval / limit |
|---|---|---|
| record | 9/12 = 75.0% | 95%: 50.0%–100.0%; exchangeable-record bootstrap |

Scout control (mockllm/claims): 9 true positives, 2 false positives, 0 false negatives; API spend $0.00.

Witness inventory (full hashes, intervals and citations in [lab-docket.json](lab-docket.json)):

| Sources | Trust domain | Files |
|---|---|---:|
| Content-addressed artifacts | registry | 11 |
| Source files | registry | 4 |
| Inspect transcripts | runner | 3 |

Known gaps: Mac Docker collection is required for independently observed launch/process attribution. B is an unauthenticated transcript handle; a measured Mac host-clock bound is still required for the Docker case. Bootstrap does not quantify missing-witness or attribution uncertainty. A contradiction requires complete registry coverage and declared independent trust domains.

Validation (this scripted fixture only): produced precision 1.0; confident errors 0.
Not a real-world accuracy estimate. Full scoring and exclusions:
[validation.json](lab-validation.json); method notes in [lab-validation.md](lab-validation.md).

Resolve a citation with `eg cite CASE REF_ID`; query the full graph with `eg query CASE SQL`.
