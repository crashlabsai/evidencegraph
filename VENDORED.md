# Reused source

Origin: [crashlabsai/crossledger, commit 9a38525](https://github.com/crashlabsai/crossledger/tree/9a38525),
declared MIT in its `pyproject.toml`. Copyright remains with the original authors.
This repository is also MIT licensed. No incident dataset bytes are vendored.

| Origin | Destination | Treatment |
|---|---|---|
| `provenance.py`, `strict_json.py` | Same module names | Copied; package/build identity renamed |
| `schema.py` | `schema.py` | Outcome, trust, population and coverage vocabulary adapted; graph models new |
| `manifest.py` | `manifest.py` | OS case-lock functions extracted; graph stage records redesigned |
| `publication.py`, `acquisition.py` | `publication.py`, `acquire.py` | Reimplemented around immutable generations; regular-copy/hash-before-read rules retained |
| `transcripts/scout_import.py` | `adapters/inspect_eval.py`, `ids.py` | Scout reader and reference identity helpers extracted; Witness citations replace board Artifact/EvidenceRef |
| `consistency/tool_sandbox.py` | `reconcile/consistency.py` | Shell/output/window helpers copied; findings mapped to graph edges |
| `reconcile/engine.py` | `reconcile/engine.py` | Decision order ported behind a substrate protocol; board evaluator excluded; 2B explicitly opt-in for idempotent effects |
| `coverage/estimate.py` | `coverage/declared.py` | Denominator/abstention semantics retained; binary bootstrap vectorized |
| `export/bundle.py` | `export/bagit.py` | Path/inventory/hash rules adapted; new graph verifier, no board semantic verifier |
| `replica/collection.py` | `lab/collection.py` | Copied; durable JSON helper import changed |
| `replica/lab_server.py` | `lab/registry.py` | Registry initialization/read/write methods extracted; sockets, process spawning and control server excluded |
| `pilot/board.py` helpers | `lab/io.py` | Durable JSON and UTC helpers adapted |
| `tests/conftest.py` | `lab/logs.py`, `tests/` | Native Inspect writer pattern adapted |
| `replica/report.py::host_key`, `replica/evidence.py` | `validate/`, `lab/stage.py` | Host dispatch-to-UUID join and public/private layout adapted |

The abandoned investigation runtime, paid comparison harness, board path assumptions,
and actor identity regexes are not part of the graph engine.
