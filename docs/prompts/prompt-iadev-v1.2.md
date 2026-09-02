# Prompt para Codex — Refatoração: `conflict-solved` com Push Automático

## Contexto

Este prompt descreve uma refatoração no script `scripts/mcp/cli.py`, função `handle_conflict_solved`.

O fluxo IA-First atual possui uma etapa de resolução de conflitos onde, após o humano
resolver os conflitos manualmente nos repos afetados e emitir o comando:

```bash
python scripts/mcp/cli.py conflict-solved <JIRA-KEY>
```

O script deve automaticamente:
1. Validar se os conflitos foram de fato resolvidos via Azure DevOps API
2. Realizar os pushes necessários se resolvidos, ou retornar erro claro se não resolvidos
3. Atualizar o `.state.json` corretamente ao final

**Problema atual identificado:**

A implementação corrente em `handle_conflict_solved` (arquivo `scripts/mcp/cli.py`) possui
um bug: após confirmar que os conflitos foram resolvidos, o loop de push filtra apenas
repos com status `"pending"`:

```python
# Bug: ignora repos com status "conflict-resolution"
if status.get("status") != "pending":
    continue
```

Isso significa que os repos que tiveram conflitos (`status == "conflict-resolution"`) —
que são exatamente os que precisam ser pusheados — são ignorados. Por isso o humano
precisava fazer o push manualmente.

---

## Escopo dos Arquivos

| Arquivo | Ação |
|---|---|
| `scripts/mcp/cli.py` | Modificar `handle_conflict_solved` |
| `scripts/mcp/state.py` | Verificar/confirmar comportamento existente (somente leitura) |
| `scripts/mcp/pr_ops.py` | Verificar/confirmar `refresh_pr_results` (somente leitura) |
| `scripts/mcp/devops.py` | Verificar `_update_repo_statuses_from_results` (somente leitura) |

---

## Requisito 1 — Validação de Conflitos com Retorno de Erro Claro

**Arquivo:** `scripts/mcp/cli.py` → `handle_conflict_solved`

**Comportamento atual quando conflitos ainda estão presentes:**
```python
if has_conflict:
    _delete_clean_local_branches(refreshed, dry_run=args.dry_run, logger=logger)
    logger.warn("Conflicts still present. Stage remains conflict-resolution.")
    # continua execução e retorna 0 (sucesso!) ← erro
```

**Comportamento esperado:**
- Identificar quais repos/PRs ainda têm conflito (campo `has_conflict == True` em cada PR de `refreshed`)
- Emitir mensagem de erro clara e amigável listando:
  - Nome do repo
  - Branch de origem
  - Branch de destino
  - URL do PR (se disponível)
- Retornar exit code **não-zero** (ex: `return 1`) para sinalizar falha ao Codex
- **Não deletar** branches limpas se ainda há conflitos pendentes
- **Não avançar** stage

Exemplo de mensagem de erro esperada:
```
ERRO: Conflitos ainda presentes após conflict-solved. Pushes NÃO realizados.

Repos com conflito:
  - lifesupport-api  | branch: OG-101-ajuste-os  →  OG-GLOBAL  | PR: https://...
  - optumsupport-be  | branch: OG-101-ajuste-os  →  desenv      | PR: https://...

Ação necessária:
  1. Resolva os conflitos manualmente nos repos listados
  2. Execute novamente: python scripts/mcp/cli.py conflict-solved <JIRA-KEY>
```

---

## Requisito 2 — Push de Repos `conflict-resolution` (Bug Fix)

**Arquivo:** `scripts/mcp/cli.py` → `handle_conflict_solved`

**Comportamento atual (bug):**
```python
else:  # sem conflitos
    for repo_name, status in repos_state.items():
        if status.get("status") != "pending":  # ← ignora "conflict-resolution"
            continue
        # push...
        status["status"] = "done"
```

**Comportamento esperado:**
- Fazer push de todos os repos com status **`"conflict-resolution"` OU `"pending"`**
- A condição de filtro deve ser:
  ```python
  if status.get("status") not in ("pending", "conflict-resolution"):
      continue
  ```
- O restante da lógica de push permanece igual (buscar branches locais, chamar `push_branch`)

**Verificação de rastreamento no `.state.json`:**

O campo `repos` em `.state.json` já rastreia corretamente os repos conflitados:
```json
{
  "repos": {
    "lifesupport-api": { "status": "conflict-resolution" },
    "optumsupport-be":  { "status": "conflict-resolution" },
    "optumsupport-fe":  { "status": "done" }
  }
}
```

Esse valor é definido em `devops.py → _update_repo_statuses_from_results` quando o
`pr-publish` detecta `has_conflict == True` em algum PR do repo. Confirme que esse
comportamento está correto antes de prosseguir com a modificação em `cli.py`.

---

## Requisito 3 — Atualização de Status para `done` Após Push

**Arquivo:** `scripts/mcp/cli.py` → `handle_conflict_solved`

**Comportamento atual:** repos com `"conflict-resolution"` são ignorados no push loop,
então nunca chegam a receber status `"done"`.

**Comportamento esperado:**
- Após push bem-sucedido de um repo (independente de ter sido `"pending"` ou `"conflict-resolution"`):
  ```python
  status["status"] = "done"
  ```
- Se branch não encontrado localmente para um repo `"conflict-resolution"`: logar warning
  e marcar como `"done"` mesmo assim (seguindo o padrão já usado para repos `"pending"`)
- Ao final, quando todos os repos estiverem `"done"`, chamar:
  ```python
  advance_stage(state, "done")
  ```

**Estado final esperado no `.state.json`:**
```json
{
  "stage": "done",
  "repos": {
    "lifesupport-api": { "status": "done" },
    "optumsupport-be":  { "status": "done" },
    "optumsupport-fe":  { "status": "done" }
  }
}
```

---

## Pseudocódigo da Função Refatorada

```python
def handle_conflict_solved(args, logger) -> int:
    state, original = _load_state(args.jira_key, args.dry_run)
    require_stage(state, "conflict-resolution")

    pr_results = state.get("prs")
    if not pr_results:
        raise ValidationError("No PRs recorded. Run mcp-devops pr-create before resolving conflicts.")

    _set_azure_defaults_from_prs(pr_results)
    refreshed = refresh_pr_results(pr_results, dry_run=args.dry_run, logger=logger)
    state["prs"] = refreshed

    conflict_by_repo, branches_by_repo = _collect_repo_conflicts(refreshed)
    has_conflict = any(conflict_by_repo.values())
    repos_state = state.setdefault("repos", {})

    # Atualiza status por repo com base no refresh
    for repo_name, status in repos_state.items():
        if repo_name not in conflict_by_repo:
            continue
        if conflict_by_repo[repo_name]:
            status["status"] = "conflict-resolution"
        else:
            if status.get("status") in ("pending", "conflict-resolution"):
                # será atualizado para "done" após push (abaixo)
                pass

    if has_conflict:
        # [NOVO] Construir mensagem de erro clara com detalhes por repo/PR
        conflict_details = _build_conflict_error_message(refreshed, conflict_by_repo)
        logger.error(conflict_details)
        append_command_log(state, _command_string(args), user=getpass.getuser())
        save_state(args.jira_key, state, original, dry_run=args.dry_run)
        return 1  # [NOVO] Retornar código de erro, não 0

    else:
        # [CORRIGIDO] Incluir "conflict-resolution" além de "pending"
        for repo_name, status in repos_state.items():
            if status.get("status") not in ("pending", "conflict-resolution"):  # ← FIX
                continue
            branches = branches_by_repo.get(repo_name, set())
            if not branches:
                logger.warn(f"No branches found for repo {repo_name}; marking done.")
                status["status"] = "done"
                continue
            local_branches = set(list_local_branches(repo_name, dry_run=args.dry_run, logger=logger))
            for branch in branches:
                if branch not in local_branches:
                    logger.warn(f"Branch {branch} not found in {repo_name}; skipping push.")
                    continue
                push_branch(repo_name, branch, state=state, dry_run=args.dry_run, logger=logger)
            status["status"] = "done"  # [MANTIDO] Atualiza após push bem-sucedido

        _delete_clean_local_branches(refreshed, dry_run=args.dry_run, logger=logger)
        advance_stage(state, "done")

    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(args.jira_key, state, original, dry_run=args.dry_run)
    logger.info("conflict-solved recorded.")
    return 0
```

---

## Função Auxiliar Nova: `_build_conflict_error_message`

Implemente uma função privada que constrói a mensagem de erro legível:

```python
def _build_conflict_error_message(pr_results: list[dict], conflict_by_repo: dict[str, bool]) -> str:
    """
    Retorna mensagem de erro amigável listando repos e PRs ainda com conflito.
    """
```

A função deve:
- Filtrar apenas PRs onde `has_conflict == True`
- Para cada PR conflitante, incluir: `repo_name`, `source_branch`, `target_branch`, `web_url`
- Agrupar por repo
- Incluir instrução de próximo passo para o humano

---

## Build e Testes

Após implementação, verificar:

1. **Cenário sem conflito:** `handle_conflict_solved` faz push dos repos `conflict-resolution`
   e `pending`, atualiza todos para `done`, avança stage para `done`, retorna `0`

2. **Cenário com conflito ainda presente:** retorna `1`, loga mensagem detalhada, NÃO faz push,
   stage permanece em `conflict-resolution`

3. **Cenário misto** (alguns resolvidos, outros não): retorna `1`, loga apenas os ainda conflitantes,
   não faz push de nenhum (comportamento seguro — não push parcial)

4. **Dry-run:** todos os push simulados, estado atualizado, nenhuma chamada real ao Git/Azure

Execute os testes existentes em `scripts/mcp/tests/` após a implementação:
```bash
cd /opt/wks/csptech/optum
python -m pytest scripts/mcp/tests/ -v
```

---

## Resumo das Mudanças

| # | Local | Tipo | Descrição |
|---|---|---|---|
| 4.1 | `cli.py` → `handle_conflict_solved` | Bug fix + melhoria | Retornar exit code 1 + mensagem detalhada quando conflitos ainda presentes |
| 4.1 | `cli.py` → nova `_build_conflict_error_message` | Nova função | Mensagem de erro amigável por repo/PR |
| 4.2 | `cli.py` → `handle_conflict_solved` | Verificação | Rastreamento em `.state.json` já existe (`repos.status = "conflict-resolution"`); confirmar comportamento correto |
| 4.3 | `cli.py` → `handle_conflict_solved` | Bug fix | Incluir repos `"conflict-resolution"` no loop de push (condição `not in ("pending", "conflict-resolution")`) |
| 4.3 | `cli.py` → `handle_conflict_solved` | Mantido | `status["status"] = "done"` após push bem-sucedido |

---

## Referências

- Contrato principal: `agent-rules.md` § 10.1 (Conflicts) — atualizado
- Scripts afetados: `scripts/mcp/cli.py`
- Estrutura `.state.json`: `scripts/mcp/state.py` → `default_state()`
- Rastreamento de conflito por repo: `scripts/mcp/devops.py` → `_update_repo_statuses_from_results()`
- Refresh de PRs: `scripts/mcp/pr_ops.py` → `refresh_pr_results()`
