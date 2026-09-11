# Prototype handoff — 2026-09-11

Repository: [`crashlabsai/evidencegraph`](https://github.com/crashlabsai/evidencegraph),
MIT licensed. Default branch `main`; the first packaged prototype is tagged
`eg-prototype-v0.1`, with its early work on branch `prototype`.

Clone and run (default branch):

```sh
git clone https://github.com/crashlabsai/evidencegraph.git
cd evidencegraph
uv sync --locked
uv run python scripts/demo.py cases/demo --seed 7
```

To reproduce the tagged prototype exactly: `git checkout eg-prototype-v0.1`.

Completed checks at the prototype tag: 53 tests; lint, formatting, type checking,
wheel and source distribution builds. The public corpus reconstruction script
completed from a new download. The full 14,591-revision case and scripted registry
case exported and passed bundle verification with docket recomputation. Readable
dockets, metrics, source inventory and retained bundle hashes are under
`docs/cases/`.

Remaining acceptance work: collect and validate the Mac Docker background-process
case with an observed clock bound and additional public host launch observations;
obtain independent review of the findings. Live paid scanning is disabled pending
enforceable provider cost accounting. The implemented Scout control is offline and
spent USD 0.

## Correctness pass — 2026-09-11

The nine reproduced defects in [critical-review.md](critical-review.md) are fixed on
`main`; its closing section records what changed per finding and what remains.
70 tests pass (53 prior plus regression tests converted from the review probes and a
test of the documented recipe); lint, formatting and type checks pass. Both recorded
cases under `docs/cases/`, the seed-7 registry incident and the wiki reconstruction,
were regenerated with the fixed engine and their bundle hashes updated. The Mac Docker collection, a matched LLM comparison and independent
review remain open.

## Release notes for maintainers

1. Keep CI green on `main`.
2. `.gitignore` excludes `/cases/`, `/.context/`, environment files and secrets.
3. Do not commit raw corpus bodies, private truth or credentials; payload-manifest
   hashes are enough to reference a bundle.
4. Keep Issues enabled and security advisories available for misleading-conclusion
   reports.
