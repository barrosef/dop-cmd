# Design — `dop aaa` / `dop it` em container (ADR-16, v0.7)

- **Data:** 2026-06-08
- **Repo alvo:** `dop-cli` (passa de 0.6.0 → **0.7.0**)
- **Workspace afetado:** `/opt/wks/csptech/optum` (imagem + serviço compose + config + poms piloto)
- **Status:** Aprovado para planejamento

## Contexto

`dop aaa`/`dop it` (v0.6) rodam Maven **no host**. O host tem **JDK21 e não tem `mvn`**,
então a execução no host é inviável. Mudança: rodar Maven num **container Java efêmero**
(via `docker compose run --rm`), espelhando o que `handle_e2e` faz com `playwright-env`,
mas com um runner Java dedicado e configurável.

## Decisões (com o usuário)

1. **Imagem:** nova `docker/java-test/Dockerfile` = base do dev (`eclipse-temurin:17-jdk-jammy`)
   + Maven (`apt-get install maven`) + `netcat-openbsd`. Handler ainda prefere `./mvnw` se o
   projeto tiver.
2. **Config:** container-only (sem caminho host — host não tem mvn). **Um** runner compose
   configurável (`java_runner`) usado por aaa e it.
3. **Testcontainers (`dop it`):** serviço `java-test` com `network_mode: host` + mount de
   `/var/run/docker.sock` → Testcontainers sobe MySQL/Mongo no host e o teste alcança via
   `localhost:<porta-mapeada>`.
4. **Escopo:** plumbing (dop-cli + compose + config) **+ poms piloto mínimos** que provam o
   caminho ponta-a-ponta.

## Arquitetura

```
dop aaa lifesupport-api
  └─ docker compose run --rm -w /workspace/test/aaa/lifesupport-api <java_runner.service> mvn test
       container: temurin17 + maven; monta a RAIZ do workspace em /workspace; reusa m2-cache
  └─ test/aaa/lifesupport-api/target/allure-results   (host lê via bind mount)
  └─ _publish_allure_project → test/e2e/reports/aaa-lifesupport-api → :5252
```

O container monta a **raiz do workspace** em `/workspace`, preservando o layout relativo:
`test/aaa/<repo>` → `/workspace/test/aaa/<repo>` e `repos/<repo>` → `/workspace/repos/<repo>`,
de modo que o parent `../../../repos/<repo>/pom.xml` e o `build-helper add-source` resolvam
corretamente. `mvn` roda com `-w /workspace/<root_rel>/<repo>`.

## Componentes

### A. `dop-cli` — config (`schema.py` + `loader.py`)

`JavaRunnerConfig {service: str, profile: str | None = None}`; campo
`java_runner: JavaRunnerConfig | None = None` em `DockerComposeConfig`. `loader` parseia
`dc_raw.get("java_runner")`. Default `None` → `dop aaa/it` sem `java_runner` configurado
levanta `ValidationError` clara.

### B. `dop-cli` — compose builder (`runtime/compose.py`)

`build_run_command` ganha `workdir: str | None = None`; quando presente, insere `-w <workdir>`
após `run --rm` (e após os `-e`), antes do service.

### C. `dop-cli` — provider (`runtime/orchestrator/base.py` + `docker_compose.py`)

Novo método (abstrato em `base`, implementado em `docker_compose`):
`run_service(service, args, *, profile=None, workdir=None, env=None, dry_run=False, logger=None) -> int`.
Monta o `compose run` via `build_run_command` e executa com **output ao vivo** (subprocess
sem captura, `guard_text` no comando), retornando o exit code do container (= exit do `mvn`).
`dry_run` loga e retorna 0.

### D. `dop-cli` — handlers (`runtime/handlers.py`)

- `_handle_maven_layer` passa a receber `root_rel: str` (ex.: `ws.aaa_root`), computa
  `root = _ws_root(ws)/root_rel` (host) e `workdir = f"/workspace/{root_rel}/{repo}"`
  (container). Escolhe `./mvnw` se `(<root>/<repo>/mvnw)` existir senão `mvn`. Chama
  `provider.run_service(runner.service, [mvn]+maven_args(+filtro), profile=runner.profile,
  workdir=workdir, dry_run=, logger=)`. Allure inalterado.
- `handle_aaa`/`handle_it` passam `root_rel=ws.aaa_root` / `ws.it_root`.
- **Remove** `_run_maven` (host) — agora código morto — e seus testes.
- `_resolve_test_targets` e `_publish_allure_project` permanecem.

### E. Workspace Optum — imagem + serviço + config

`docker/java-test/Dockerfile`:
```dockerfile
FROM eclipse-temurin:17-jdk-jammy
RUN apt-get update && apt-get install -y --no-install-recommends maven netcat-openbsd ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
ENV MAVEN_OPTS="-Dmaven.repo.local=/root/.m2/repository"
```

`docker-compose.yml` serviço:
```yaml
  java-test:
    build: { context: ./docker/java-test }
    profiles: ["test"]
    network_mode: host
    working_dir: /workspace
    volumes:
      - .:/workspace:rw
      - m2-cache:/root/.m2/repository
      - /var/run/docker.sock:/var/run/docker.sock
    env_file: [docker/.env]
    environment:
      MAVEN_OPTS: "-Dmaven.repo.local=/root/.m2/repository"
```

`~/.config/dop/config.toml`:
```toml
[workspaces.optum.runtime.docker_compose.java_runner]
service = "java-test"
profile = "test"
```

### F. Workspace Optum — poms piloto mínimos

Esquema ADR-16 (parent = pom do app via `relativePath`, `build-helper` add-source da
`src/main/java` do app — o que, por si, **compila o app inteiro** e prova "compila contra
`repos/<repo>`"):

- `test/aaa/lifesupport-api/` — parent `com.optum:lifesupport:1.0.0-SNAPSHOT`
  (`../../../repos/lifesupport-api/pom.xml`); `build-helper` + `allure-junit5` + `aspectjweaver`
  + surefire; **1 teste** JUnit5/AssertJ trivial (a compilação do app já é o smoke).
- `test/it/optum-support-be/` — parent `com.optum:optum-support:0.0.1-SNAPSHOT`; perfil `it`
  + `maven-failsafe-plugin`; deps Testcontainers (`testcontainers`, `junit-jupiter`, `mysql`);
  **1 `*IT`** que sobe um `MySQLContainer` e assere `isRunning()` (prova
  `docker.sock`+`network_mode: host`). Fallback documentado: `TESTCONTAINERS_RYUK_DISABLED=true`.

## Fluxo de dados

Ver bloco em **Arquitetura**. `it` é idêntico trocando `aaa`→`it`, `mvn test`→`mvn -Pit verify`,
`-Dtest`→`-Dit.test`, project `aaa-<repo>`→`it-<repo>`.

## Tratamento de erros

- `java_runner` ausente na config → `ValidationError`.
- Runner falha (build da imagem, erro de compilação, teste vermelho) → exit ≠ 0; Allure ainda
  publica (consistente com o comportamento atual).
- Testcontainers/Ryuk: fallback `TESTCONTAINERS_RYUK_DISABLED=true` no env do serviço.
- Sem segredos logados (`guard_text` no comando; output streama, não é interpolado em log).

## Testes (dop-cli, TDD, mockados — sem Docker/Maven real)

1. Config: `java_runner` default `None`; parse com valores.
2. `build_run_command` com `workdir` insere `-w <workdir>` na posição correta.
3. `run_service` monta o argv certo (service, profile, `-w`, env) e retorna o returncode
   (patch `subprocess.run`); `dry_run` não chama subprocess.
4. `_handle_maven_layer`/`handle_aaa`/`handle_it`: patcham `build_runtime_provider` (provider
   mock) e `_publish_allure_project`; assertam `run_service` chamado com
   `workdir=/workspace/test/aaa/<repo>`, `mvn` vs `./mvnw`, filtro `-k`→`-Dtest`/`-Dit.test`,
   project `aaa-<repo>`/`it-<repo>`, exit codes, e `ValidationError` quando `java_runner` é None.

## Validação ponta-a-ponta (workspace)

`docker compose --profile test build java-test` → `dop aaa lifesupport-api` (verde, project
`aaa-lifesupport-api` no `:5252`) → `dop it optum-support-be` (Testcontainers sobe, verde,
`it-optum-support-be`), tudo **sem JDK/Maven no host**.

## Critérios de aceitação

1. `dop aaa lifesupport-api` roda Maven em container (sem mvn no host), piloto compila contra
   `repos/lifesupport-api`, publica `aaa-lifesupport-api` no `:5252`.
2. `dop it optum-support-be` roda em container com Testcontainers (docker.sock + network host),
   publica `it-optum-support-be`.
3. Sem `java_runner` configurado → erro claro.
4. `dop e2e` inalterado (continua usando `ephemeral_runner`/playwright-env).

## Fora de escopo (YAGNI)

- Caminho de execução no host (removido).
- Conteúdo real dos testes SUOPT-3184/3188 (só smoke mínimo aqui).
- Suporte a outros orquestradores além de `docker_compose`.
