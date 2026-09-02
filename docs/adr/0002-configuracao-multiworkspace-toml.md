# ADR-0002 — Configuração multi-workspace via TOML + auto-detecção por CWD

- **Status:** Aceito
- **Componentes:** `src/dop/config/{schema,loader,discovery}.py`

## Contexto

A ferramenta precisa atender múltiplos projetos/clientes (workspaces), cada um com
seus repositórios, plataforma de PR, método de autenticação, branches longas e
configuração de runtime. Hard-coding por projeto é insustentável.

## Decisão

Um único arquivo **TOML** descreve todos os workspaces:

- Caminho: `$DOP_CONFIG` ou `~/.config/dop/config.toml` (`loader.config_path()`).
- Parsing via `tomllib` (stdlib do Python 3.11+), mapeado para **dataclasses**
  fortemente tipadas (`schema.py`): `WorkspaceConfig`, `RepoConfig`,
  `CredentialsConfig`, `PlatformConfig`, `RuntimeConfig`, `AppConfig`, `FeDependency`.
- Campos opcionais têm *defaults* nas dataclasses; o loader é lenient (campos
  ausentes caem no default).

**Seleção de workspace** (`discovery.py`):

- `--workspace <nome>` seleciona explicitamente.
- Sem flag, `find_workspace_by_cwd()` escolhe o workspace cujo `root` é ancestral
  do diretório atual. Erro se zero ou múltiplos casarem.

Isso permite rodar `dop …` de dentro de qualquer subdiretório do workspace sem
flags extras.

## Consequências

- ➕ Um operador/agente alterna entre projetos só mudando de diretório.
- ➕ Schema tipado: erros de configuração viram `ValidationError` cedo, não falhas
  obscuras de runtime.
- ➕ Override por repo (ex.: `azure_org`, `azure_project`, `long_branches`) e por app.
- ➖ Auto-detecção exige que os `root` dos workspaces não se sobreponham.
- ➖ Segredos **não** ficam no TOML — apenas *nomes de variáveis de ambiente*
  (`*_env`), o que adia o problema de segredos para o ambiente (ver
  [ADR-0006](0006-redacao-de-segredos.md)).

## Detalhes do schema

A referência completa de campos está em
[`reference/configuration.md`](../reference/configuration.md).
