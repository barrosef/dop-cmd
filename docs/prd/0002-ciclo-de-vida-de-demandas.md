# PRD-0002 — Ciclo de vida de demandas (Jira → branch → PR)

- **Status:** Implementado (v0.5.0)
- **ADRs relacionados:** [0001](../adr/0001-cli-unica-com-dispatch-por-handlers.md),
  [0003](../adr/0003-estado-como-fonte-de-verdade.md),
  [0004](../adr/0004-abstracao-multiplataforma-pr.md),
  [0008](../adr/0008-fluxo-git-operacional.md),
  [0010](../adr/0010-agrupamento-de-jiras-por-alias.md)

## 1. Problema

Tocar uma demanda significa: descobrir os repos impactados, atualizar branches
longas, criar feature branches consistentes em vários repos, commitar, abrir PRs
para os alvos corretos em N plataformas, e comunicar os PRs ao time. Feito à mão,
é repetitivo, inconsistente entre repos e fácil de esquecer um passo.

## 2. Usuários e necessidades

- **Agente de IA:** precisa de um comando para "abrir" a demanda e outro para
  "publicar" cada repo, sem decidir detalhes de baixo nível.
- **Desenvolvedor:** quer que branches/PRs sigam a convenção do time
  automaticamente; quer poder intervir (resolver conflito, ajustar branch).
- **Revisor:** quer uma mensagem pronta para o Teams com todos os PRs.

## 3. Requisitos funcionais

| ID | Requisito | Comando |
|---|---|---|
| F1 | Inicializar demanda: registrar repos impactados, atualizar branches longas, criar feature branch e push inicial. | `dop demand-init <JIRA> --repos <csv> [--linked <csv>] [--branch <nome>]` |
| F2 | Agrupar Jiras relacionadas sob uma chave mestre (aliases). | `dop link <mestre> <chave…>` / `--linked` no init |
| F3 | Atualizar (pull) todas as branches longas conhecidas. | `dop update-repos [--repo <r>]` |
| F4 | Push da feature branch (com `--force-with-lease` opcional pós-rebase). | `dop git-push <JIRA> [--repo] [--branch] [--force]` |
| F5 | Commit pendente + push + criar PR(s) para todos os `pr_targets`, reusando PR ativo existente. | `dop pr-publish <JIRA> --repo <r> --title <t> [--body] [--target] [--force-new-pr]` |
| F6 | Gerar mensagem de Teams resumindo os PRs. | `dop teams-message <JIRA>` |
| F7 | Inspecionar o estado da demanda. | `dop show <JIRA>` |
| F8 | Resolver conflito de feature via rebase sobre a base. | `dop solve-conflict <JIRA> [--repo]` (ver [PRD-0003](0003-integracao-e-resolucao-de-conflitos.md)) |

## 4. Regras de negócio

- A chave Jira é validada contra `jira_key_pattern` do workspace; aliases são
  resolvidos para a mestre antes de qualquer operação.
- Repos omitidos em `--repos` são marcados como `skipped` no estado.
- `pr-publish` cria PR para **cada** branch em `pr_targets` do repo, salvo `--target`.
- PR ativo existente para a mesma `source→target` é **reaproveitado** (evita
  duplicatas), exceto com `--force-new-pr`.
- Ao final do `pr-publish`, o repo volta para a `base_branch` (UX consistente).
- Toda operação registra `commands_log` (usuário + timestamp) e respeita `--dry-run`.

## 5. Saídas / artefatos

- `.state.json` atualizado (`repos`, `prs`, `linkedJiraKeys`, `commands_log`).
- `03-pr-team-message.md` (mensagem de Teams) com link Jira, repos, branches e flag
  de conflito por PR.
- Logs por execução em `<JIRA>/logs/`.

## 6. Fluxo típico

```bash
dop demand-init OG-123 --repos lifesupport-api,optum-support-be --linked OG-124
# ... agente/humano implementa a mudança ...
dop pr-publish OG-123 --repo lifesupport-api --title "OG-123: ajuste X"
dop pr-publish OG-123 --repo optum-support-be --title "OG-123: ajuste X"
dop teams-message OG-123
```

## 7. Fora de escopo

- Aprovar/mergear PRs (humano na plataforma).
- Gerar o código/conteúdo da mudança (responsabilidade do agente/dev).
- Integração base→`desenv` (ver [PRD-0003](0003-integracao-e-resolucao-de-conflitos.md)).

## 8. Oportunidades futuras

- Tornar nomes de branch/targets totalmente data-driven (hoje há constantes).
- Anexar a descrição do PR a partir de um documento de plano por repo.
