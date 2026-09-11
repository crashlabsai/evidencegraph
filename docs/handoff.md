# Prototype handoff — 2026-09-11

Independent repository: [`crashlabsai/evidencegraph`](https://github.com/crashlabsai/evidencegraph)
(private). Foundation on `main` (`fb7341f`); prototype work on `prototype`
(`846320b`), tagged `eg-prototype-v0.1`. Review:
[PR #1](https://github.com/crashlabsai/evidencegraph/pull/1).

Crossledger still holds backup branches `evidencegraph-foundation` and
`evidencegraph-prototype` plus the same tag, in case the dedicated remote is
unavailable.

Clone and run:

```sh
git clone --branch prototype https://github.com/crashlabsai/evidencegraph.git
cd evidencegraph
uv sync --locked
uv run python scripts/demo.py cases/demo --seed 7
```

Completed checks: 53 tests pass; lint, formatting, type checking, wheel and source
distribution builds pass. The public corpus reconstruction script completed from
a new download. The full 14,591-revision case and scripted registry case exported
and passed bundle verification with docket recomputation. The readable dockets,
metrics, source inventory and retained bundle hashes are under `docs/cases/`.

Remaining acceptance work: collect and validate the Mac Docker background-process
case with an observed clock bound and additional public host launch observations;
obtain a named investigator's review. Live paid scanning is disabled pending
enforceable provider cost accounting. The implemented Scout control is offline
and spent USD 0. 
