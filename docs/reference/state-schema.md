# Referência — Schema do estado (`.state.json`)

> Fonte de verdade: `src/dop/core/state.py` (`default_state`). v0.5.0.
> Localização: `<root>/<demands_dir>/<JIRA>/.state.json`.

## Estrutura

```jsonc
{
  "jiraKey": "OG-100",                       // chave mestre da demanda
  "jiraUrl": "https://atlassian.net/browse/OG-100",
  "linkedJiraKeys": ["OG-101"],              // Jiras agrupadas (aliases)
  "createdAt": "2026-05-30T12:00:00Z",       // ISO-8601 UTC (sufixo Z)

  "repos": {                                  // por repo do workspace
    "lifesupport-api": {
      "impacted": true,                       // true = impactado; false = skipped
      "branch": "OG-100"                      // feature branch (quando impactado)
    },
    "optum-support-fe": { "impacted": false }
  },

  "prs": [                                    // append-only (log de PRs criados/reusados)
    {
      "repo": "lifesupport-api",
      "source_branch": "OG-100",
      "target_branch": "OG-GLOBAL",
      "pr_id": "1234",
      "web_url": "https://dev.azure.com/.../pullrequest/1234",
      "merge_status": "succeeded",
      "has_conflict": false,
      "at": "2026-05-30T12:05:00Z"
    }
  ],

  "commands_log": [                           // append-only + verificado anti-truncamento
    { "command": "demand-init OG-100 --repos lifesupport-api",
      "user": "ed", "at": "2026-05-30T12:00:00Z", "notes": null }
  ],

  "e2e": {                                    // testes E2E
    "max_strikes": 3,
    "strike_count": 0,                        // red → +1; green → 0
    "last_run": {                             // null se nunca rodou
      "status": "green",                      // "green" | "red"
      "exit_code": 0,
      "suites_run": ["optum-support-fe"],
      "filter_used": "og_150",
      "headed": false,
      "report_url": "http://localhost:5050/...",
      "at": "2026-05-30T12:10:00Z"
    },
    "history": [ /* até 10 execuções, mais recentes */ ]
  },

  "runtime": {                                // runtime local
    "apps_up": ["optum-support-fe", "optum-support-be"]
  }
}
```

## Funções principais (`core/state.py`)

| Função | Papel |
|---|---|
| `default_state(jira_key, jira_url?, *, jira_base_url?)` | Estado inicial. |
| `load_state(jira_key, path, *, jira_base_url, create=True)` | Carrega (cria se `create`); retorna `(state, original)`. |
| `save_state(path, state, original, *, dry_run=False)` | Salva (escrita atômica) e valida append-only de `commands_log`. |
| `ensure_state_defaults(state, ...)` | Backfill v0.4 → v0.5. |
| `set_repo_impacted / set_repo_skipped / impacted_repos` | Repos impactados. |
| `append_command_log` / `assert_commands_log_append_only` | Log de comandos. |
| `append_pr_record` | Registro de PR (append-only). |
| `add_linked_jira` | Agrupa Jira (linkedJiraKeys). |
| `write_alias / resolve_alias / alias_path` | Aliases (`.alias`). |
| `record_e2e_run / reset_strikes / get_strike_count` | E2E + strikes. |
| `validate_jira_key` | Valida formato da chave. |
| `now_iso` | Timestamp UTC ISO com `Z`. |

## Invariantes

- `commands_log` e `prs` são **apenas-acréscimo**; truncar `commands_log` levanta
  `StateError` ao salvar.
- Escrita é **atômica** (`.tmp` + rename) — ver `core/fs.write_json`.
- `e2e.history` é limitado a **10** entradas (`_E2E_HISTORY_MAX`).
- Em `--dry-run`, `load_state(create=False)` evita criar arquivo novo.

> Nota: o modelo antigo tinha um campo `stage` (máquina de estados). Ele foi
> **removido** na v0.5 — ver [ADR-0009](../adr/0009-remover-stage-gates.md).
