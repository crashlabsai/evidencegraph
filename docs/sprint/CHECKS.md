# Validation record — 2026-09-14

| Check | Recorded result |
|---|---|
| Initial baseline suite | 70 passed; separate full-token relay probe still produced one wrong supported attribution |
| Final `uv run pytest -q` | 77 passed in 56.99 seconds; one existing Scout/DuckDB deprecation warning |
| `uv run ruff check` | Passed |
| `uv run ruff format --check` | Passed |
| `uv run basedpyright` | 0 errors, 0 warnings, 0 notes |
| `git diff --check` | Passed |
| Three-seed evidence stress run | 36/36 specified contract outcomes; three deliberately false-declaration cases each retain one expected error |
| Representative bundle exports | Clean, missing-transcript and full-relay cases each verified with graph-to-docket recomputation |
| Copied-event citations | Both the registry row and the native event resolve through `eg cite`; source fingerprint matches the recorded stress run |
| Report content | 139-word abstract, 1011-word main body before references, required dual-use and LLM-use appendices, template title footnote retained, sole author Ryan Junejo (CrashLabs) |
| PDF review | Official-template DOCX rendered with LibreOffice and the template's Old Standard TT font; five pages total including references and appendices; all reference URLs visible and clickable; no blank pages |

The stress result records the full source-tree fingerprint and runtime dependency
closure. Its seed-0 relay ablation reproduces the pre-change probe's 10 supported,
9 correct, 1 wrong attribution pattern. The new default has 0 supported, 10 ambiguous
and 2 unmatched records on that view. This is a correctness and abstention check,
not an estimate of generalization or human usefulness.

Report rendering was checked separately from analyzer tests. Optional plotting,
DOCX and PDF libraries do not participate in reconciliation. All case evidence and
scoring truth in the local archive are synthetic. No real incident was re-adjudicated,
no external organization was contacted, and no submission was filed.
