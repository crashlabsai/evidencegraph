# Prototype handoff — 2026-09-11

This is an independent Git repository, with a small `main` foundation and all work
on `prototype`. The cloud GitHub integration rejected repository creation, and the
Mac connection timed out. Its history is therefore backed up in Crossledger as
`evidencegraph-foundation` and `evidencegraph-prototype`, with tag
`eg-prototype-v0.1`. Those branches are storage and review targets; the intended
destination remains the separate private `crashlabsai/evidencegraph` repository.

From this independent checkout, use an authenticated GitHub client that can create
repositories:

```sh
gh repo create crashlabsai/evidencegraph --private
git remote add origin https://github.com/crashlabsai/evidencegraph.git
git push --set-upstream origin main prototype
git push origin eg-prototype-v0.1
gh pr create --repo crashlabsai/evidencegraph --base main --head prototype --title 'Build the evidencegraph forensic prototype' --body-file docs/prototype-pr.md
```

To recover on another machine before that move:

```sh
git clone --single-branch --branch evidencegraph-prototype https://github.com/crashlabsai/crossledger.git evidencegraph
cd evidencegraph
git remote rename origin backup
git switch -c prototype
git branch main fb7341f
git fetch backup tag eg-prototype-v0.1
uv sync --locked
uv run python scripts/demo.py cases/demo --seed 7
```

Completed checks: 53 tests pass; lint, formatting, type checking, wheel and source
distribution builds pass. The public corpus reconstruction script completed from
a new download. The full 14,591-revision case and scripted registry case exported
and passed bundle verification with docket recomputation. The readable dockets,
metrics, source inventory and retained bundle hashes are under `docs/cases/`.

Remaining acceptance work: create the separate GitHub repository; collect and
validate the Mac Docker background-process case with an observed clock bound and
additional public host launch observations; obtain a named investigator's review.
Live paid scanning is disabled pending enforceable provider cost accounting.
The implemented Scout control is offline and spent USD 0. 
