# ADR-16 — Workspace Optum: setup das camadas de teste (aaa/it/e2e)

Estas mudanças vivem no workspace `/opt/wks/csptech/optum` (não é git). O `dop` v0.7 já
expõe `dop e2e`/`dop aaa`/`dop it` e lê `test_root`/`aaa_root`/`it_root` + `java_runner`.

> **Estado em 2026-06-08:** itens 1–4 e 5 (imagem/serviço/config) e os pilotos **já foram
> aplicados** neste workspace e validados ponta-a-ponta. Este doc serve de referência/replay.

> **Modelo de execução (v0.7): `dop aaa`/`dop it` rodam Maven EM CONTAINER**, não no host
> (o host tem JDK21 e não tem `mvn`). Reports unificados em `<test_root>/reports`
> (`test/e2e/reports`); projects Allure `e2e-<suite>`, `aaa-<repo>`, `it-<repo>` no `:5252`.

## 1. Migrar `e2e/` → `test/e2e/`
```bash
cd /opt/wks/csptech/optum && mkdir -p test && mv e2e test/e2e
```
(Preserva `reports/` e histórico Allure; specs usam paths relativos.)

## 2. `docker-compose.yml` — mounts do e2e (caminho interno do container fica `/e2e`)
- `playwright-env`: `./e2e:/e2e` → `./test/e2e:/e2e`
- `allure`: `./e2e/reports:/app/projects` → `./test/e2e/reports:/app/projects`
- `allure-ui`: `./e2e/reports:/usr/share/nginx/html:ro` → `./test/e2e/reports:/usr/share/nginx/html:ro`

Recriar os containers que mudaram de mount:
```bash
docker compose -f docker-compose.yml --project-directory . up -d --force-recreate --no-deps allure allure-ui
```

## 3. Runner Java em container (imagem + serviço + config)

### 3a. Imagem `docker/java-test/Dockerfile`
```dockerfile
FROM eclipse-temurin:17-jdk-jammy
RUN apt-get update && apt-get install -y --no-install-recommends \
        maven netcat-openbsd ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
ENV MAVEN_OPTS="-Dmaven.repo.local=/root/.m2/repository"
```
(Maven 3.6.3 + Java 17.)

### 3b. Serviço `java-test` no `docker-compose.yml`
```yaml
  java-test:
    build: { context: ./docker/java-test, dockerfile: Dockerfile }
    profiles: ["test"]
    network_mode: host                      # Testcontainers alcança os containers via localhost
    working_dir: /workspace
    volumes:
      - .:/workspace:rw                      # raiz do workspace (test/ + repos/ no mesmo layout)
      - m2-cache:/root/.m2/repository        # reusa cache Maven dos apps
      - /var/run/docker.sock:/var/run/docker.sock   # Testcontainers (sibling containers)
    env_file: [docker/.env]
    environment:
      MAVEN_OPTS: "-Dmaven.repo.local=/root/.m2/repository"
```
Build: `docker compose --profile test build java-test`

### 3c. `~/.config/dop/config.toml` — `[workspaces.optum]`
```toml
test_root = "test/e2e"      # aaa_root/it_root usam defaults test/aaa, test/it

[workspaces.optum.runtime.docker_compose.java_runner]
service = "java-test"
profile = "test"
```

## 4. Renomear reports e2e existentes → `e2e-<suite>`
```bash
cd /opt/wks/csptech/optum/test/e2e/reports
for d in optum-support-fe providers-front-end; do
  [ -d "$d" ] && [ ! -d "e2e-$d" ] && mv "$d" "e2e-$d"
done
```

## 5. Poms piloto (smoke) e ACHADOS do ADR-16

Os pilotos provam o caminho do runner. **Dois achados importantes surgiram na validação —
o esquema "parent = pom do app" do ADR-16 precisa de revisão** (eram riscos já previstos no
ADR §Custos/riscos):

- **🔴 Parent-pom via relativePath NÃO funciona:** `repos/<repo>/pom.xml` tem packaging
  `jar` (Spring Boot app, não multi-módulo). Maven exige `<packaging>pom</packaging>` num
  parent → erro `must be "pom" but is "jar"`. **Os pilotos usam `spring-boot-starter-parent:2.5.7`
  como parent** (sem `build-helper` add-source do app). Consequência: o smoke **não compila
  as fontes do app**. Para o conteúdo real de **SUOPT-3184/3188**, definir outra estratégia
  de "compilar contra o app" (ex.: depender do jar do app, ou um módulo agregador `pom`).
- **🟡 Versões:** Spring Boot 2.5.7 traz JUnit Platform 1.7.2 — incompatível com `allure-junit5`
  novo e com Testcontainers atuais. Pilotos fixam `<junit-jupiter.version>5.8.2</junit-jupiter.version>`
  e `allure-junit5:2.20.1`.
- **🟡 Testcontainers + MySQL:** é preciso `mysql:mysql-connector-java` no classpath de teste
  (o probe de readiness do `MySQLContainer` usa o driver JDBC).

Pilotos atuais (smoke, em `/opt/wks/csptech/optum`):
- `test/aaa/lifesupport-api/` — JUnit5/AssertJ trivial; publica `aaa-lifesupport-api`.
- `test/it/optum-support-be/` — `MySQLContainer` via Testcontainers (perfil `it`, failsafe);
  publica `it-optum-support-be`. **Confirma que docker.sock + network_mode host funcionam.**

## 6. Docs / memória do workspace
- `CLAUDE.md` / `agent-rules.md`: `dop aaa`/`dop it` na tabela de ferramentas; paths `test/`;
  gate das três camadas (verde local pré-PR). Refs `e2e/...` → `test/e2e/...`.

## Validação ponta-a-ponta (confirmada 2026-06-08)
```bash
dop --workspace optum aaa lifesupport-api   # mvn test em container → aaa-lifesupport-api (green, :5252 200)
dop --workspace optum it  optum-support-be  # mvn -Pit verify + Testcontainers → it-optum-support-be (green, :5252 200)
dop --workspace optum e2e <suite>           # inalterado, a partir de test/e2e/
```
Pré-requisito de host: **apenas Docker** (a toolchain Java/Maven roda no container `java-test`;
Testcontainers usa docker.sock + network_mode host).
