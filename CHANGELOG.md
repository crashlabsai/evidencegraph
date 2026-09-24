# Changelog

## Unreleased (review suggestions pilot)

- `eg suggest claims|search|show|evaluate`: optional model suggestions that order LQ5
  transcript review, stored under `manifest["suggestions"]`, outside every forensic
  table; the docket is byte-identical with suggestions enabled, adversarial, garbage
  or removed
- Providers behind one byte-level interface: a local keyword baseline (default), a
  TypeSafe HTTP client gated by `--allow-network` and a hard `--max-requests` budget,
  and offline replay of recorded runs
- Local redaction and bounding of every packet; strict, non-repairing answer
  validation; `unscored` kept distinct from negative answers
- Redaction version 2: a value already redacted by JSON key is no longer matched again
  as a free-text assignment (version 1 sent `"[redacted]]"` and counted it twice)
- Runs prepare under the case lock, infer without it and refuse to publish onto
  changed evidence; exact request/response bytes are content-addressed, exported and
  re-validated by `eg verify --recompute`
- `suggest/` is excluded from the forensic analyzer identity; runs carry their own
  build id, question hashes and policy version
- Review policy v2 routes write-claim answers to review, uncertain or background,
  keeping only concentrated "no write" answers in the background and noting possible
  relays
- `eg lab claims-corpus` and `scripts/suggest_experiment.py`: a labelled synthetic
  corpus with dev/held-out splits and a baseline-versus-Jev comparison; results and
  failure cases in `docs/suggestions.md`
- `scripts/stress_suggestions.py`: review suggestions on copies of the incident-stress
  cases, beside each injected receipt's forensic outcome, with checks that the docket
  is unchanged and no receipt token is sent; see `docs/sprint/rebench-2026-09-24.md`
- `eg serve` gains a Suggestions page: every published run, its review queue in
  `eg suggest show` order with filters and text search, the exact redacted text each
  provider received beside its full answer distribution, stale reasons, an offline
  recheck, and a link from every message to its hash-verified source. Read marks stay
  in the browser; the viewer still writes nothing to the case, and a changed run file
  or request blob is reported rather than raised

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
