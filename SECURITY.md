# Security and unsafe conclusions

Evidencegraph is an evidence-accounting prototype. A wrong **supported**
attribution or a false contradiction can mislead an investigation. Treat that
class of failure as a security-relevant report.

## Please report privately when

- The tool marks a claim **supported** on evidence that does not justify it
- It emits a **contradicted** or accusatory finding from incomplete capture
- A citation resolves to the wrong bytes, row, or event
- Bundle verification can be made to pass while conclusions are stale or substituted
- You find credential leakage, path traversal, or unsafe handling of case files

Prefer a **private** GitHub security advisory on this repository when the org has
them enabled. Otherwise email the maintainers via the GitHub org contact for
[crashlabsai](https://github.com/crashlabsai). Do not file a public issue for a
novel attribution bypass until maintainers have a chance to respond.

## Out of scope for private reporting

- Feature requests, docs clarity, and performance
- Failures already listed under known limits in the README and `docs/evidencegraph.md`
- Offline Scout / mock controls (they are not model evaluations)

## Maintainer expectations

- Confirm receipt within a few business days when possible
- Prefer a regression test and a docs note over a silent fix
- Do not ask reporters to withhold high-level descriptions of already-fixed public
  counterexamples once a release notes them
