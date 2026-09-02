# Architecture Decision Records (ADR)

Decisões de arquitetura do `dop`, reconstruídas por engenharia reversa do código e
do histórico Git. Formato baseado em [MADR](https://adr.github.io/madr/).

> **Nota de proveniência:** estes ADRs documentam decisões *já tomadas e implementadas*.
> Onde o código diverge do `README.md` da raiz, o **código** é a fonte de verdade.
> A numeração é interna a esta base de conhecimento e não corresponde 1:1 ao
> antigo "ADR-06" citado nos prompts (que vivia no repositório `optum`).
>
> O "ADR-16" citado em `docs/workspace-migration-adr16.md` e nas specs de
> `docs/superpowers/` é um documento **do lado da workspace**, fora deste índice.

| # | Título | Status |
|---|---|---|
| [0001](0001-cli-unica-com-dispatch-por-handlers.md) | CLI única com dispatch por handlers | Aceito |
| [0002](0002-configuracao-multiworkspace-toml.md) | Configuração multi-workspace via TOML + auto-detecção por CWD | Aceito |
| [0003](0003-estado-como-fonte-de-verdade.md) | Estado por demanda como fonte de verdade (`.state.json`) | Aceito |
| [0004](0004-abstracao-multiplataforma-pr.md) | Abstração multi-plataforma de Pull Requests | Aceito |
| [0005](0005-abstracao-de-autenticacao-git.md) | Abstração de autenticação Git (token/SSH) | Aceito |
| [0006](0006-redacao-de-segredos.md) | Redação de segredos e *guard* anti env-dump | Aceito |
| [0007](0007-dry-run-everywhere.md) | `--dry-run` em todas as operações com efeito colateral | Aceito |
| [0008](0008-fluxo-git-operacional.md) | Fluxo Git operacional (feature branches, integração, conflitos) | Aceito |
| [0009](0009-remover-stage-gates.md) | Remoção dos *stage gates* em favor de fluxo conversacional | Aceito |
| [0010](0010-agrupamento-de-jiras-por-alias.md) | Agrupamento de múltiplas Jiras via aliases | Aceito |
| [0011](0011-runtime-docker-compose.md) | Runtime local via `docker compose` | Aceito |
| [0012](0012-e2e-playwright-allure-strikes.md) | E2E com Playwright + Allure 3 e mecanismo de *strikes* | Aceito |
| [0013](0013-unificacao-da-cli-dop.md) | Renomeação `iadev`→`dop` e unificação de `dop-devops` | Aceito |
| [0014](0014-abstracao-de-orquestrador-runtime.md) | Abstração de orquestrador de runtime (RuntimeProvider) + runtime data-driven | Aceito |
| [0015](0015-separacao-dop-cmd-e-dop-cli.md) | Separação `dop-cmd` (ferramenta de ambiente) e `dop-cli` (nome da plataforma) | Aceito |

## Convenção de status

- **Proposto** — em discussão, não implementado.
- **Aceito** — decidido e implementado no código atual.
- **Substituído** — decisão antiga trocada por outra (referencia a substituta).
