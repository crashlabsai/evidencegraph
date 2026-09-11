Build a question-driven evidence graph for two concrete forensic cases: the public DseWiki export and a staged registry incident with private host truth. The `eg` CLI snapshots sources, writes cited Parquet graph tables, reconciles claims with explicit abstention, estimates coverage, renders compact dockets, and exports verifiable BagIt directories.

The real-data reconstruction ingests all 14,591 revisions and the accompanying pages, events, labels, off-wiki records and links. It verifies source body hashes and reports which manifest facts reproduce, differ, or cannot be reconstructed. Published save events are treated as dependent revision-derived observations, so capture-recapture does not identify the incident population.

Across ten scripted seeds, dropping one transcript leaves its three records unmatched. All nine supported write attributions are correct, both fabricated receipts are contradicted, and there are no false spoof accusations. An offline Scout control repeats the fabricated claims and is correctly scored with two false positives. No paid model calls were made.

Validation: 53 passing tests; lint, formatting and basedpyright pass; wheel and source distribution build; a fresh public-corpus download reconstructs successfully; both case bundles verify with docket recomputation. See `docs/cases/` for readable dockets, exact metrics and source hashes.

Remaining acceptance work is explicit in `docs/handoff.md`: Mac Docker launch/process validation and host observations, a measured clock bound, a named investigator review, and enforceable cost accounting before live scanning. Raw wiki bodies and private truth are excluded from Git.
