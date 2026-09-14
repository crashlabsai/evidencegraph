# Incident-response work pack

**Finding: a copied receipt token can make a forensic tool attribute a real write
to the wrong native event.** EvidenceGraph now leaves that match ambiguous unless
the case explicitly justifies recorder authenticity or token exclusivity.

This uses the [AI Incident Response Sprint](https://apartresearch.com/sprints/ai-incident-response-sprint-2026-09-11-to-2026-09-13)
Track 1 direction on fabricated evidence to improve an existing prototype. It is a
working development artifact, not evidence of production readiness or a submitted
entry. The sole author is Ryan Junejo, affiliated with CrashLabs. The report is an
AI-assisted draft requiring the author's own review and writing.

## Review in fifteen minutes

1. Read [the results table](RESULTS.md). Compare `full_receipt_relay` with
   `false_exclusivity`: identical evidence, different assumptions, different answer.
2. Run the commands below. Inspect the relay docket and its `receipt_possession_only`
   relations. A hash verifies possession of the token, not which event received it.
3. Compare `clean` with `shared_tokens_no_attack`. The safer default also withholds
   twelve correct attributions when token exclusivity is not established.
4. Read [the collection checklist](evidence-checklist.md), which says what to acquire
   before making stronger findings, and [the limitations](readiness.md).

```sh
uv sync --locked
uv run python scripts/incident_stress.py cases/my-incident-stress --seeds 0 1 2
cat cases/my-incident-stress/RESULTS.md
cat cases/my-incident-stress/cases/0-full_receipt_relay/DOCKET.md
uv run eg verify cases/my-incident-stress/bundles/0-full_receipt_relay --recompute
uv run eg query cases/my-incident-stress/cases/0-full_receipt_relay \
  "SELECT outcome, method, candidates, citation_ids FROM relations WHERE kind='produced'"
# Resolve one of the returned citations:
# uv run eg cite cases/my-incident-stress/cases/0-full_receipt_relay CITATION_ID
```

No Docker, external target, model API, or credential is used. Use a fresh output
directory. The recorded three-seed run has **36/36 contract checks passing** and
three verified representative bundles. Its twelve conditions reuse three scripted
worlds; 36 is not a sample of independent incidents. The intentionally false
declaration produces one wrong supported attribution in each seed, as specified.

## Contents

| Artifact | Purpose |
|---|---|
| [RESULTS.md](RESULTS.md), [results.json](results.json) | Measured per-condition scores, controls, provenance and truth inventories |
| [protocol.md](protocol.md) | Threat model, transformations, scoring and validity limits |
| [evidence-checklist.md](evidence-checklist.md) | Concrete evidence requests and sufficiency conditions |
| [readiness.md](readiness.md) | What works, what has not been evaluated, and a bounded follow-up study |
| [report-draft.md](report-draft.md) | Editable research draft, abstract and required dual-use appendix |
| `report-draft.docx` / `report-draft.pdf` | Official-template renders; kept local (gitignored), rebuild with the commands below |
| [provenance.json](provenance.json), [relay-before.json](relay-before.json) | Starting commit, template hashes and the executed pre-fix counterexample |

The raw local run is `cases/incident-response/` (gitignored). It retains the source
evidence, private **synthetic** scoring truth, every case and three portable bundles.
A local `.context/incident-response-pack.zip` may hold that run for review; it is not
in git. The Markdown draft is the source of truth in this repository.

The [validation record](CHECKS.md) lists the completed software and artifact checks.
To rebuild the optional report assets, download the DOCX export of the
[official template](https://docs.google.com/document/d/1PQBlhI3tM5vb51x7jBWXBQMYg6hkiU_x8RaCws4kjl4/copy?usp=sharing)
to a local path and run:

```sh
uv run scripts/plot_incident_stress.py docs/sprint/results.json docs/sprint/figures
uv run scripts/build_sprint_report.py docs/sprint/report-draft.md \
  path/to/official-template.docx docs/sprint/report-draft.docx
libreoffice --headless --convert-to pdf --outdir docs/sprint docs/sprint/report-draft.docx
```

These two scripts declare their optional dependencies for `uv`; no report-generation
packages are added to the analyzer. LibreOffice can render the DOCX to PDF. Install
the template's [Old Standard TT](https://fonts.google.com/specimen/Old+Standard+TT)
font before converting, otherwise the title, abstract heading and footnote fall
back to a substitute face.
The Markdown is the editable source in git. DOCX/PDF outputs stay local. The report
remains a draft even when all technical checks pass.

## What is new

Before this work, EvidenceGraph already had native Inspect ingestion, a registry
fixture, cited graphs, dockets, BagIt export, the public wiki audit and 70 tests.
It also incorporated Crossledger code; [VENDORED.md](../../VENDORED.md) records that
provenance. The new work is the complete-receipt counterexample, explicit
token-exclusivity gate, upgrade invalidation, twelve-condition stress pack and
its evidence-collection guidance. Receipt replay itself is not claimed as a new
security discovery.

EvidenceGraph was selected because these existing offline paths directly support
the experiment. Crossledger's README describes useful reconciliation infrastructure
but leaves its investigator study and held-out evaluation pending. This was a
scope decision, not a measured comparison between the two tools.
