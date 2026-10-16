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

A referência de campos desta versão foi removida; o modelo atual está no `README.md` e no ADR-0016.

## Amendment — 2026-10-16 (ADR-0016)

Still holds: TOML parsed with `tomllib` into typed dataclasses; discovery from
the current directory; `DOP_CONFIG` overrides.

Changed by the readaptation: there are no workspaces and no user-level config.
**One `dop.toml` per project**, committed at the project root and found by
walking up from the current directory (`src/dop/config.py`). The loader is no
longer lenient: unknown keys at any level are a configuration error (exit 2).
The schema is `[project]`, `[environments.<name>]` and `[components.<name>]`
(see ADR-0016 and the README). The `--workspace` flag and
`~/.config/dop/config.toml` are gone.
