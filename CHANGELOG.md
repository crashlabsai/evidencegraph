# Changelog

## Unreleased (incident-response stress pack)

- Complete receipt relays now remain ambiguous by default; token-based attribution
  requires explicit per-domain `exclusive_receipt_tokens` or an authentic recorder
- Dockets name the token-exclusivity assumption and CLI exposes `--exclusive-receipts`
- Analyzer upgrades invalidate old derived conclusions before rendering or export
- Twelve paired evidence conditions, prior-rule ablation, claim-only positive control,
  separate truth scoring, reproducible dockets and verified representative bundles
- Incident-response protocol, evidence collection checklist, readiness assessment and
  clearly marked research draft; all evaluation is scripted development work

## Unreleased (correctness + public readiness)

- Receipt-token binding (or explicit `authentic_records`) required for supported
  write attribution; copied public receipts no longer promote alone
- Temporal absence / execution-window checks use the full tool interval
- Derived stages, dockets, and export refuse stale `case.json` assumptions
- LQ7 uses the final recorded transcript event of any type; LQ8 splits observed parts
- Per-pair tool/sandbox outcomes; capture-recapture lower bound removed for registry
- Witness ids include adapter + filename so identical empty logs can coexist
- Public README positioning, `SECURITY.md`, issue templates, handoff updates
- Regression tests from the 2026-09-11 critical review probes
- Onboarding docs for outside users: getting-started recipe with input contracts,
  the sixteen questions explained, a glossary, and help text on every CLI command;
  the documented recipe runs as a test
- `eg serve`: a local read-only web viewer over a case or exported bundle, with the
  docket and its per-record evidence, citation resolution with hash re-verification,
  witness inventory, entity and relation browsing, a read-only SQL console and
  pipeline status; standard library only, no build step

## eg-prototype-v0.1 — 2026-09-11

- First packaged prototype: DseWiki reconstruction path and scripted registry lab
- `eg` CLI: case lifecycle, ingest, reconcile, coverage, docket, export, verify
- Offline Scout mock control; live paid scanning disabled
- MIT license; corpus bodies and private truth excluded from git
