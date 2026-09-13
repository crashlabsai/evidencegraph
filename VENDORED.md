# Reused source

Origin: [crashlabsai/crossledger, commit 9a38525](https://github.com/crashlabsai/crossledger/tree/9a38525),
declared MIT in its `pyproject.toml`. Copyright remains with the original authors.
This repository is also MIT licensed. No incident dataset bytes are vendored.
The origin repository is private at the time of writing, so that link may not
resolve for readers; the table below records what was copied and how it was changed
so the provenance can be audited from this repository alone.

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
| `replica/collection.py` | `lab/runtime/collection.py`, `lab/collection.py` | Durable collector moved into the stdlib runtime; old module remains a compatibility import |
| `replica/lab_server.py` | `lab/runtime/registry.py`, `lab/registry.py` | Shared registry initialization/read/write methods, including Evidencegraph receipt commitments; old module remains a compatibility import |
| `replica/lab_server.py` | `lab/runtime/server.py` | Socket supervisor and unprivileged process lifecycle adapted; dispatch takes a captured peer identity, checks process start times, and records opaque principals separately from private identity mappings |
| `replica/registry_client.py` | `lab/runtime/client.py`, `lab/runtime/registry_client.py` | Stdlib socket client with a stdin command interface and script import compatibility |
| `replica/lab.py` | `lab/docker.py` | Docker flags and per-request exec pattern adapted; added whole-run deadline, request brackets, effective-limit verification, stopped-container copy export, volume fallback, and explicit failure/cleanup reports |
| `replica/rehearsal.py::BACKGROUND` | `lab/docker.py::BACKGROUND` | Cache-polling job retained as the refresh gate; failure to observe the changed payload now fails explicitly |
| `pilot/board.py` helpers | `lab/runtime/runtime_io.py`, `lab/io.py` | Durable JSON and UTC helpers adapted; atomic JSON moved from manifest into this stdlib-only helper, with compatibility imports |
| `tests/conftest.py` | `lab/logs.py`, `tests/` | Native Inspect writer pattern adapted |
| `replica/report.py::host_key`, `replica/evidence.py` | `validate/`, `lab/stage.py` | Host dispatch-to-UUID join and public/private layout adapted |

The abandoned investigation runtime, paid comparison harness, board path assumptions,
and actor identity regexes are not part of the graph engine.
