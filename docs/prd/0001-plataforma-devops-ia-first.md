# PRD-0001 — Plataforma DevOps IA-First (visão de produto)

- **Status:** Implementado (v0.5.0)
- **Tipo:** PRD guarda-chuva

## 1. Resumo

O `dop` é a **camada de ferramentas determinística** sobre a qual um agente de IA
(e humanos) conduzem o ciclo de desenvolvimento de software de ponta a ponta. Ele
transforma intenções de alto nível ("trabalhe na OG-123", "suba o ambiente", "rode
os E2E") em operações Git/PR/Docker/teste seguras, auditáveis e reproduzíveis.

## 2. Problema

Fluxos conduzidos por IA precisam executar ações no mundo real (mexer em repos,
abrir PRs, subir serviços). Deixar o agente rodar comandos de shell crus é:

- **Inseguro** — risco de vazar tokens, fazer push destrutivo, dumpar o ambiente.
- **Não-determinístico** — o mesmo objetivo gera comandos diferentes a cada vez.
- **Não-auditável** — sem registro do que foi feito por demanda.
- **Frágil entre projetos** — cada workspace tem repos, plataformas e branches
  distintos.

## 3. Objetivos

1. Oferecer **comandos de alto nível** que encapsulam o fluxo do time.
2. Ser **seguro por padrão**: redação de segredos, `--force-with-lease`, sem
   merge automático, anti env-dump.
3. Ser **auditável**: estado por demanda + logs com redação.
4. Ser **previsível**: `--dry-run` em tudo; operações idempotentes.
5. Ser **portável**: multi-workspace, multi-plataforma (Azure/GitHub/GitLab),
   multi-org.

## 4. Não-objetivos

- Não é uma plataforma de CI/CD remota (não substitui pipelines do Azure/GitHub).
- Não faz merge/aprovação automática de PRs (decisão humana).
- Não gerencia infraestrutura de nuvem.

## 5. Capacidades (detalhadas nos PRDs filhos)

| Capacidade | PRD |
|---|---|
| Ciclo de vida de demandas (Jira → branch → PR → Teams) | [PRD-0002](0002-ciclo-de-vida-de-demandas.md) |
| Integração diária e resolução de conflitos | [PRD-0003](0003-integracao-e-resolucao-de-conflitos.md) |
| Runtime local (`docker compose`) | [PRD-0004](0004-runtime-local.md) |
| Testes E2E e relatórios (Playwright/Allure) | [PRD-0005](0005-testes-e2e-e-relatorios.md) |

## 6. Princípios de produto (invariantes)

Estes invariantes são imutáveis e protegidos por ADRs:

- **Estado é fonte de verdade** ([ADR-0003](../adr/0003-estado-como-fonte-de-verdade.md)).
- **`--dry-run` em tudo** ([ADR-0007](../adr/0007-dry-run-everywhere.md)).
- **Segredos nunca vazam** ([ADR-0006](../adr/0006-redacao-de-segredos.md), [ADR-0005](../adr/0005-abstracao-de-autenticacao-git.md)).
- **Fluxo conversacional, sem gates rígidos** ([ADR-0009](../adr/0009-remover-stage-gates.md)).

## 7. Métricas de sucesso (propostas)

- Tempo do "intenção → PR aberto" reduzido vs. fluxo manual.
- Zero incidentes de vazamento de credencial em logs/transcripts.
- Re-execução de qualquer comando sem efeito colateral indesejado (idempotência).

## 8. Estado atual e dívidas conhecidas

- ✅ Todas as capacidades acima implementadas na v0.5.0.
- ⚠️ `README.md` da raiz desatualizado (comandos de gate inexistentes).
- ⚠️ Constantes específicas do Optum embutidas no código (branches de integração,
  portas, nomes de serviço) — ver dívidas em [ADR-0008](../adr/0008-fluxo-git-operacional.md)
  e [ADR-0011](../adr/0011-runtime-docker-compose.md).
