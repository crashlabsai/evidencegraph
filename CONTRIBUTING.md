# Contributing

This is a small research prototype. The most useful contributions are:

1. **Incorrect or overconfident conclusions** — wrong `supported` / false
   contradiction / bad citation (see [SECURITY.md](SECURITY.md) if novel and sharp)
2. **Investigator feedback** — whether a docket helps a real workflow (use the
   review issue template)
3. **Minimal adapters or fixtures** for public, redistributable evidence formats
4. **Docs fixes** that prevent misreading fixture scores as real-world accuracy

Start with [docs/getting-started.md](docs/getting-started.md) to run the tool on
your own evidence, and [docs/evidencegraph.md](docs/evidencegraph.md) for the rules
a change must respect.

## Dev loop

```sh
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run basedpyright
uv run pytest -q
```

Prefer a failing regression test next to a rule change. Do not commit raw
collusion.wiki bodies, private host truth, API credentials, or generated
`cases/` trees. Do not enable live paid scanning without provider-enforced
cost controls.

Open small PRs against `main`. Match existing style; avoid drive-by refactors.
