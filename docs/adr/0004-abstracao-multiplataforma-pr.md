# ADR-0004 — Abstração multi-plataforma de Pull Requests

- **Status:** Aceito
- **Componentes:** `src/dop/platform/{base,azure,github,gitlab}.py`

## Contexto

Diferentes workspaces hospedam código em Azure DevOps, GitHub ou GitLab. As
operações de PR (criar, listar, consultar status/conflito) variam em API e
nomenclatura (GitLab usa *Merge Request*), mas o fluxo do `dop` deve ser idêntico.

## Decisão

Definir uma **ABC `PlatformProvider`** (`platform/base.py`) com a interface mínima:

```python
create_pr(*, repo_name, source_branch, target_branch, title, description,
          reviewers=None, dry_run=False, logger=None) -> PRResult
get_pr_status(pr_id, *, repo_name=None, ...) -> PRResult
list_prs(*, repo_name, source_branch, target_branch, ...) -> list[PRResult]
```

e um **DTO uniforme `PRResult`** (`repo_name`, `source_branch`, `target_branch`,
`pr_id`, `web_url`, `has_conflict`, `merge_status`). Uma *factory*
`build_platform_provider(workspace)` instancia a implementação certa a partir de
`workspace.platform`.

Implementações:

- **GitHub** (`github.py`): PyGithub; conflito = `pr.mergeable is False`.
- **GitLab** (`gitlab.py`): python-gitlab; conflito = `merge_status in
  {cannot_be_merged, cannot_be_merged_recheck}`; usa `iid` como `pr_id`.
- **Azure DevOps** (`azure.py`): **não usa SDK Python — usa a CLI `az repos pr`**
  e faz parsing do JSON. Implementação mais rica:
  - org/project resolvidos por prioridade: config por repo → variáveis de ambiente
    → derivados do URL do remote git;
  - suporta formatos modernos (`dev.azure.com`) e legados (`visualstudio.com`);
  - tolera versões de `az` sem suporte a `--project`;
  - detecta "PR já existe" (TF401179) e reusa o PR existente;
  - limita descrição a 4000 caracteres;
  - sufixa o título do PR com o nome do repo (`pr_doc_suffix_map`) para
    desambiguar PRs multi-repo.

## Consequências

- ➕ Handlers de demanda/integração são agnósticos de plataforma.
- ➕ `--dry-run` é uniforme: cada provider retorna `PRResult` "esqueleto" sem chamar
  a rede.
- ➕ Reuso de PR existente evita duplicatas (importante no Azure, que permite
  múltiplos PRs source→target).
- ➖ Azure depende do binário `az` no PATH e autenticado (acoplamento a ferramenta
  externa, não a uma lib). É uma escolha pragmática dada a maturidade do `az repos`.
- ➖ A detecção de conflito depende de campos que algumas plataformas computam de
  forma assíncrona (`mergeable` pode ser `null` logo após criar o PR).
