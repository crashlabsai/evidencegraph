# Working in this repository

This repository is public. Internal build notes, plans, brainstorms and review
write-ups are never committed or pushed. Keep them in `internal/` (human notes)
or `.context/` (agent-to-agent files); both are gitignored. Only condensed,
public-facing documents belong in git: `README.md`, `ROADMAP.md` and `docs/`.

Before committing, check `git status` for stray notes, case outputs, transcripts
(`*.eval`) or private truth directories. None of those belong in history.
