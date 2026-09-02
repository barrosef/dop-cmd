# ADR-0010 — Agrupamento de múltiplas Jiras via aliases

- **Status:** Aceito
- **Componentes:** `core/state.py` (`write_alias`, `resolve_alias`, `add_linked_jira`), `cli.py`

## Contexto

Uma única mudança de código frequentemente atende várias Jiras relacionadas
(ex.: uma feature e seu bug). Manter `.state.json` separado por chave duplicaria
estado e branches. É preciso tratar várias chaves como **uma demanda lógica**.

## Decisão

Eleger uma **chave mestre** e apontar as demais para ela via arquivos de **alias**:

- `write_alias(demands_root, alias_key, master_key)` cria
  `<demands_root>/<alias_key>/.alias` contendo a chave mestre.
- `resolve_alias(demands_root, jira_key)` lê o `.alias` (se existir) e devolve a
  chave mestre; senão devolve a própria chave.
- O `cli.main()` resolve o alias **antes de qualquer operação**, de modo que
  `dop <cmd> OG-101` opera sobre o `.state.json` da mestre `OG-100`.
- `add_linked_jira(state, key)` registra a chave em `state.linkedJiraKeys`
  (sem duplicar, ignorando a própria mestre).
- Os comandos `demand-init --linked …` e `link <mestre> <chaves…>` criam os aliases
  e atualizam o estado.

## Consequências

- ➕ Uma só fonte de estado/branches para um conjunto de Jiras.
- ➕ Transparência: comandos aceitam qualquer chave do grupo; a resolução é implícita.
- ➕ A mensagem Teams agrega todas as chaves (`OG-100 + OG-101`).
- ➖ Aliases vivem no filesystem (`.alias`); mover/renomear demandas exige cuidado.
