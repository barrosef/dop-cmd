# Design — `dop aaa` / `dop it` + `test_root` (ADR-16)

- **Data:** 2026-06-08
- **Repo alvo:** `dop-cli` (versão atual: `0.5.1`)
- **Fonte:** ADR-16 (`/opt/wks/csptech/optum/docs/ADR/ADR-16-camadas-de-teste-aaa-it-e2e-seed.md`)
- **Status:** Aprovado para planejamento

## Contexto

O `dop` é a CLI única do ecossistema Optum. Hoje cobre `dop e2e` (Playwright via
container efêmero `playwright-env`) e `dop report` (Allure). O ADR-16 introduz três
camadas de teste sob um diretório único `test/`:

```
test/
  aaa/<repo>/    # unit AAA (Maven, surefire, *Test)
  it/<repo>/     # integração (Maven, failsafe + Testcontainers, *IT)
  e2e/<suite>/   # Playwright (migrado de e2e/)
```

A UI Allure (`:5252`, servida por `allure-ui` a partir de `reports/`) deve mostrar
projects separados: `e2e-<suite>`, `aaa-<repo>`, `it-<repo>`, todos no **mesmo root de
reports**.

## Decisões (definidas com o usuário)

1. **Maven roda no host** para `aaa` e `it` — sem container novo, sem mexer em
   `ephemeral_runner`. `aaa` não precisa de Docker; `it` usa Testcontainers nativo no
   Docker do host (evita DinD frágil). Decisão alinhada à recomendação do ADR/prompt.
2. **Escopo = só `dop-cli`** + um documento listando as mudanças necessárias no
   workspace Optum (compose, config, migração). As mudanças do workspace **não** são
   aplicadas neste trabalho.
3. **Migrar e2e para `e2e-<suite>`** — simetria total com o ADR. O report gerado do e2e
   passa de `reports/<suite>` para `reports/e2e-<suite>`. (Custo aceito: muda URLs e o
   `index.html` do `:5252`; a migração dos reports existentes vai no doc do workspace.)

## Arquitetura

Um **root de reports unificado** servido em `:5252`:

```
reports_root = <ws_root>/<test_root>/reports        # Optum: test/e2e/reports
```

Os três tipos de project Allure coexistem nesse root:
- `e2e-<suite>`  — gerado por `dop e2e`
- `aaa-<repo>`   — gerado por `dop aaa`
- `it-<repo>`    — gerado por `dop it`

Como `aaa`/`it` rodam Maven **no host** (não passam pelo container `playwright-env`),
os literais `/e2e/...` do lado-container só dizem respeito ao fluxo `e2e` e ficam
**intactos** (o mount do compose passa a `./test/e2e:/e2e`, então o caminho interno
continua `/e2e`).

## Componentes

### 1. Config — `src/dop/config/schema.py` + `loader.py`

Três campos novos em `WorkspaceConfig`, opcionais, com default que não quebra outros
workspaces:

| Campo       | Default      | Significado                                   |
|-------------|--------------|-----------------------------------------------|
| `test_root` | `"e2e"`      | Root da camada e2e (Optum: `"test/e2e"`)      |
| `aaa_root`  | `"test/aaa"` | Root dos projetos unit AAA                    |
| `it_root`   | `"test/it"`  | Root dos projetos de integração               |

Para workspaces legados, `aaa_root`/`it_root` simplesmente não existem em disco →
`all` itera nada → no-op. O `loader._parse_workspace` lê os três de `data.get(...)`
com os mesmos defaults.

### 2. e2e — `src/dop/runtime/handlers.py` (mudanças mínimas)

- Trocar as 3 ocorrências de `_ws_root(ws) / "e2e"` (em `handle_e2e` ~256,
  `_generate_allure3_reports` ~369, `handle_report` clean ~447) por
  `_ws_root(ws) / ws.test_root`.
- Project do report gerado: `reports_root / suite` → `reports_root / f"e2e-{suite}"`.
- **Não mudam:** o staging cru (`reports/<jira>/<suite>/run-N/results`), os literais
  `/e2e/...` do container, e a lógica de strikes.

### 3. `handle_aaa()` e `handle_it()` — novos em `handlers.py`

Espelham o estilo de `handle_e2e`:

1. **Resolver alvos:** alvo explícito (nome de repo) ou `all` (varre
   `<root>/*/pom.xml`, onde `<root>` = `aaa_root` ou `it_root`). Alvo desconhecido →
   `ValidationError`.
2. **Rodar Maven no host**, `cwd` = dir do projeto, usando `./mvnw` se existir senão
   `mvn`:
   - `aaa`: `mvn test` (+ `-Dtest=<filtro>` quando `-k`).
   - `it`:  `mvn -Pit verify` (+ `-Dit.test=<filtro>` quando `-k`).
3. **Publicar Allure:** copiar `target/allure-results` → agregado persistente
   `<projeto>/.allure-results` (merge; `--fresh-report` zera antes) e gerar o report
   `aaa-<repo>` / `it-<repo>` no `reports_root` unificado.
4. **Exit code:** `0` se todos verdes; `!= 0` se qualquer `mvn` falhar. **Sem strikes.**

### 4. Allure multi-project — `_generate_allure3_reports` generalizado

Extrair um helper reutilizável:

```python
def _publish_allure_project(
    *, project: str, results_dir: Path, reports_root: Path,
    src: Path | None = None, config_file: Path | None = None,
    report_name: str | None = None, fresh: bool = False, dry_run: bool = False,
) -> None
```

Faz: merge de `src` → `results_dir` (reusa `_merge_allure_results`), `allure generate`
de `results_dir` → `reports_root/project` (com `--config` se `config_file` existir),
e imprime `http://localhost:5252/<project>/index.html`.

- `e2e`: chamado por suíte → `project=f"e2e-{suite}"`, `results_dir=<suite>/.allure-results`,
  `config_file=<suite>/allurerc.yml`, `src=produced[suite]`.
- `aaa`/`it`: chamado por repo → `project=f"aaa-{repo}"`/`f"it-{repo}"`,
  `results_dir=<projeto>/.allure-results`, `src=<projeto>/target/allure-results`,
  sem `config_file`.

### 5. Parser — `src/dop/cli.py`

Dois subparsers novos espelhando `e2e` (sem `--headed`/`--max-strikes`):

```
dop aaa <repo|all> [-k FILTER] [--fresh-report]
dop it  <repo|all> [-k FILTER] [--fresh-report]
```

`--dry-run` já é global. Cada um faz `set_defaults(func=_make_rt_func(_rt_aaa/_rt_it))`.

### 6. Doc das mudanças do workspace Optum (entregável, não aplicado)

Documento curto (em `docs/`) listando o que o workspace precisa:
- `mv e2e test/e2e` (nada é git; preserva `reports/` e histórico).
- `docker-compose.yml`: mounts `./e2e:/e2e` → `./test/e2e:/e2e` (playwright-env), e
  `./e2e/reports` → `./test/e2e/reports` (allure, allure-ui).
- `~/.config/dop/config.toml`: `test_root = "test/e2e"` no `[workspaces.optum]`.
- Scaffolding dos poms piloto (`test/aaa/lifesupport-api`, `test/it/optum-support-be`)
  conforme ADR-16 — fora do escopo do dop-cli.
- Migração dos reports existentes para o naming `e2e-<suite>` (renomear dirs gerados).

## Fluxo de dados

```
dop aaa lifesupport-api
  └─ resolve <ws>/test/aaa/lifesupport-api/pom.xml
  └─ host: (./mvnw|mvn) test            (cwd = projeto)
  └─ target/allure-results ──merge──▶ <projeto>/.allure-results
  └─ allure generate ──▶ <ws>/test/e2e/reports/aaa-lifesupport-api
  └─ http://localhost:5252/aaa-lifesupport-api/
  └─ exit 0 verde / !=0 vermelho
```

## Tratamento de erros

- Config ausente de `test_root`/`aaa_root`/`it_root` → usa defaults (sem erro).
- Root aaa/it inexistente em disco + `all` → no-op verde (nada a rodar).
- Alvo explícito sem `pom.xml` → `ValidationError`.
- `mvn`/`mvnw` ausente no host → erro de processo propagado (exit != 0).
- `allure generate` falhando → warning (não derruba o exit code do teste), como hoje.
- Sem log de segredos (usar logger existente / `redact`).

## Testes (TDD)

Seguindo `tests/` existente (`test_cli_parsing.py`, `test_config_schema_v05.py`,
`test_runtime_handlers.py`), com Maven/IO mockados (nenhum `mvn` real):

1. Parsing: `dop aaa <repo>`, `dop aaa all`, `-k`, `--fresh-report`; idem `it`.
2. Config: parse de `test_root`/`aaa_root`/`it_root` (default e override).
3. e2e: report agora em `e2e-<suite>`; `_ws_root/test_root` respeitado; `dop e2e`
   inalterado quando `test_root` não setado.
4. Resolução de alvos aaa/it: `all` varre poms; explícito válido/ inválido.
5. Comando Maven: host, `./mvnw` vs `mvn`, filtro `-k` → `-Dtest`/`-Dit.test`,
   `-Pit verify` para it.
6. Publish Allure: project name correto, merge chamado, `--config` só no e2e.
7. Exit code: vermelho quando um `mvn` falha; verde caso contrário.

## Critérios de aceitação (do prompt/ADR-16)

1. `dop e2e` continua funcional em workspaces sem `test_root` (default `"e2e"`).
2. Com `test_root="test/e2e"`, `dop e2e` opera a partir de `test/e2e/`.
3. `dop aaa lifesupport-api` roda os unit tests e aparece como `aaa-lifesupport-api`.
4. `dop it optum-support-be` roda os `*IT` com Testcontainers e aparece como
   `it-optum-support-be`.
5. `dop aaa all` / `dop it all` iteram todos os projetos existentes.
6. Saída `!= 0` quando há teste falhando.

## Fora de escopo (YAGNI)

- Strikes para aaa/it (só e2e, ADR-09 §12.4).
- Execução em CI/remoto; cobertura mínima (JaCoCo); unit de front-end.
- Aplicar as mudanças no workspace Optum (vão documentadas, não executadas).
