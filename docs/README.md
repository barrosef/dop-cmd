# dop — Base de Conhecimento

> Documentação gerada por engenharia reversa do código-fonte.
> Versão analisada: **0.5.0** · Data: **2026-05-30**

O **dop** (*DevOps Pipeline CLI*) é uma ferramenta de linha de comando, escrita em
Python, que orquestra um fluxo de desenvolvimento **IA-First** em ambientes
**multi-workspace** e **multi-plataforma** (Azure DevOps, GitHub, GitLab).

Ele unifica, numa única CLI, quatro grandes capacidades:

1. **Ciclo de vida de demandas** (Jira → branches → commits → Pull Requests).
2. **Integração e resolução de conflitos** entre branches longas.
3. **Runtime local** de aplicações via `docker compose` (front-ends e back-ends).
4. **Testes E2E** com Playwright e relatórios Allure.

## Como navegar esta documentação

| Pasta | Conteúdo |
|---|---|
| [`architecture/`](architecture/overview.md) | Visão geral da arquitetura, mapa de módulos e fluxos de dados. |
| [`adr/`](adr/README.md) | *Architecture Decision Records* — decisões técnicas reconstruídas a partir do código e do histórico Git. |
| [`prd/`](prd/README.md) | *Product Requirement Documents* — propósito, usuários e requisitos de cada capacidade do produto. |
| [`reference/`](reference/cli-commands.md) | Referências detalhadas: comandos da CLI, schema de configuração e schema de estado. |
| [`prompts/`](prompts/) | Prompts históricos de desenvolvimento IA-First (preservados como artefatos). |

## Resumo de uma frase

> Uma CLI que serve de "painel de controle" para um agente de IA (e para humanos)
> conduzirem demandas de software de ponta a ponta — do código ao PR ao teste E2E —
> com **estado auditável**, **operações idempotentes**, **suporte a `--dry-run`** e
> **redação de segredos** em todo lugar.

## Estado atual (0.5.0)

- ✅ CLI unificada `dop` (entry point único; `dop-devops` foi fundido).
- ✅ Fluxo **conversacional** sem *stage gates* (o modelo de gates monotônicos foi
  removido na v0.5 — ver [ADR-0009](adr/0009-remover-stage-gates.md)).
- ✅ Suporte a Azure DevOps (via `az` CLI), GitHub (PyGithub) e GitLab (python-gitlab).
- ✅ Runtime `docker compose` com front-ends servidos por nginx e Playwright/Allure 3.
- ⚠️ O `README.md` da raiz do projeto está **parcialmente desatualizado**: cita
  comandos de gate (`context-approved`, `plan-approved`, `build-passed`,
  `change-approved`, `dop-devops …`) que **não existem mais** no código. A
  referência canônica de comandos é [`reference/cli-commands.md`](reference/cli-commands.md).
