# ADR-0003 — Estado por demanda como fonte de verdade (`.state.json`)

- **Status:** Aceito
- **Componentes:** `src/dop/core/state.py`, `src/dop/core/fs.py`

## Contexto

O fluxo IA-First é conduzido por um agente e por humanos em sessões intermitentes.
É preciso um registro persistente, auditável e idempotente de o que já foi feito
para cada demanda (Jira): quais repos foram tocados, que branches/PRs existem,
qual o histórico de E2E e quais comandos foram executados.

## Decisão

Cada demanda tem um arquivo **`.state.json`** em
`<root>/<demands_dir>/<JIRA>/.state.json`. Toda operação de demanda segue o ciclo
**carregar → modificar → salvar**:

```python
state, original = load_state(jira_key, path, jira_base_url=..., create=not dry_run)
# ... muta state ...
append_command_log(state, _command_string(args), user=getpass.getuser())
save_state(path, state, original, dry_run=...)
```

Campos rastreados (ver [`reference/state-schema.md`](../reference/state-schema.md)):
`jiraKey`, `jiraUrl`, `linkedJiraKeys`, `createdAt`, `repos{}`, `prs[]`,
`commands_log[]`, `e2e{}`, `runtime{}`.

Decisões de apoio:

- **Append-only auditável:** `commands_log` e `prs` são apenas-acréscimo;
  `save_state` verifica que `commands_log` não foi truncado/alterado
  (`assert_commands_log_append_only`) e levanta `StateError` se for.
- **Escrita atômica:** `fs.write_json` escreve em arquivo `.tmp` e faz *rename*
  atômico, evitando corromper o estado se o processo morrer no meio.
- **Compatibilidade retroativa:** `ensure_state_defaults()` preenche campos
  ausentes (migração v0.4 → v0.5), tornando estados antigos legíveis.
- **Histórico E2E rotativo:** `e2e.history` mantém as últimas 10 execuções.

## Consequências

- ➕ Idempotência: comandos podem ser re-executados; o estado reflete a realidade.
- ➕ Auditoria: quem rodou o quê e quando fica registrado e protegido de adulteração.
- ➕ Recuperação: o estado é o ponto de retomada entre sessões do agente.
- ➖ O estado pode divergir da realidade do Git se o usuário operar fora do `dop`
  (mitigado por comandos que re-consultam o git/plataforma, ex.: `list_prs`).
