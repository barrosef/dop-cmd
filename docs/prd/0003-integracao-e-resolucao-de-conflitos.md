# PRD-0003 — Integração diária e resolução de conflitos

- **Status:** Implementado (v0.5.0)
- **ADRs relacionados:** [0008](../adr/0008-fluxo-git-operacional.md)

## 1. Problema

Num modelo de branches longas (base `OG-GLOBAL` → integração `desenv`), é preciso
sincronizar diariamente o que entrou na base para a `desenv`, e lidar com conflitos
em dois pontos: feature→base (rebase) e base→`desenv` (merge auxiliar). Esse
trabalho é recorrente, mecânico e arriscado se feito sem padrão.

## 2. Usuários e necessidades

- **Agente/Operador de integração:** quer um comando diário que crie os PRs
  base→`desenv` apenas onde houver novidade, sinalizando conflitos.
- **Desenvolvedor:** quer um caminho guiado para resolver conflitos manualmente e
  publicar o resultado.

## 3. Requisitos funcionais

| ID | Requisito | Comando |
|---|---|---|
| F1 | Para cada repo com commits novos em `base..desenv`, criar PR `base → desenv` (pular se não há commits ou já existe PR ativo). Reportar conflitos. | `dop integrate-desenv [--repo <r>]` |
| F2 | Preparar branch auxiliar `merge-conflicts-desenv-from-OG-GLOBAL` a partir de `origin/desenv` e tentar merge da base; listar arquivos em conflito. | `dop prepare-merge-conflicts [--repo <r>]` |
| F3 | Após resolução manual, validar merge concluído, push da branch auxiliar e criar PR → `desenv`. | `dop finish-merge-conflicts [--repo <r>]` |
| F4 | Resolver conflito de feature: rebase da feature branch sobre a base; em conflito, listar arquivos e parar para resolução manual. | `dop solve-conflict <JIRA> [--repo]` |

## 4. Regras de negócio

- **Comandos de integração (F1–F3) são independentes de Jira** e **não alteram
  `.state.json`** de nenhuma demanda — são manutenção de repositório.
- **F4 depende de Jira** e opera sobre `state.repos[<repo>].branch`.
- Nunca push direto em branches longas; conflitos param o fluxo e instruem o humano.
- Merge/rebase reportam `(success, conflicts)`; estados pendentes (`MERGE_HEAD`,
  `rebase-merge/`, `rebase-apply/`) são detectados antes de prosseguir.
- `finish-merge-conflicts` exige estar na branch auxiliar, sem merge pendente, com
  commits a publicar; senão erro claro.
- Todos respeitam `--dry-run`.

## 5. Saídas

- PRs base→`desenv` (F1) e da branch auxiliar→`desenv` (F3).
- Resumo em stdout por repo (criado/skip/conflito), com a próxima ação sugerida.

## 6. Fluxos típicos

**Integração diária:**
```bash
dop integrate-desenv
# repos com conflito → dop prepare-merge-conflicts --repo <r>
#   ...humano resolve, git add + git commit...
dop finish-merge-conflicts --repo <r>
```

**Conflito de feature:**
```bash
dop solve-conflict OG-123 --repo lifesupport-api
#   ...humano resolve, git add + git rebase --continue...
dop git-push OG-123 --repo lifesupport-api --force
```

## 7. Fora de escopo

- Resolução automática de conflitos (sempre manual).
- Merge/aprovação de PRs.

## 8. Dívidas conhecidas

- Nomes `desenv` e `merge-conflicts-desenv-from-OG-GLOBAL` são constantes em
  `cli.py` (específicos do Optum) — generalizar para o config é débito técnico.
