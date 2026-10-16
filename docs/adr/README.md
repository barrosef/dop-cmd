# Architecture Decision Records (ADR)

Decisions of `dop`, in [MADR](https://adr.github.io/madr/) form. The numbering
is internal to this repository. Gaps in the numbering are ADRs of version 0.7.1
removed by the readaptation ([ADR-0016](0016-readaptation-dop-toml.md)); git
history keeps them.

| # | Title | Status |
|---|---|---|
| [0001](0001-cli-unica-com-dispatch-por-handlers.md) | Single CLI with handler dispatch | Accepted, amended 2026-10-16 |
| [0002](0002-configuracao-multiworkspace-toml.md) | TOML configuration with discovery from the current directory | Accepted, amended 2026-10-16 |
| [0006](0006-redacao-de-segredos.md) | Secret redaction and the anti env-dump guard | Accepted, amended 2026-10-16 |
| [0007](0007-dry-run-everywhere.md) | `--dry-run` on every side effect | Accepted, amended 2026-10-16 |
| [0016](0016-readaptation-dop-toml.md) | Readaptation: one `dop.toml` per project, six verbs | Accepted |

## Status convention

- **Proposed** — under discussion, not implemented.
- **Accepted** — decided and implemented in the current code.
- **Superseded** — replaced by another decision (which it names).
