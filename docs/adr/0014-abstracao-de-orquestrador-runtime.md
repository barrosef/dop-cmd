# ADR-0014 — Abstração de orquestrador de runtime (RuntimeProvider)

- **Status:** Aceito
- **Data:** 2026-05-30
- **Componentes:** `src/dop/runtime/orchestrator/{base,docker_compose,__init__}.py`,
  `config/schema.py`, `runtime/{resolve,e2e,handlers}.py`
- **Relaciona-se a:** [ADR-0011](0011-runtime-docker-compose.md) (que esta decisão
  evolui), [ADR-0004](0004-abstracao-multiplataforma-pr.md) (padrão espelhado)
- **Spec:** `docs/superpowers/specs/2026-05-30-runtime-orchestrator-design.md`

## Contexto

O runtime (ADR-0011) nasceu acoplado: nomes de apps, portas, mapas de URL, ordem de
suites E2E, comandos de build, volumes e o prefixo de projeto Docker estavam
**hard-coded** em `runtime/{resolve,e2e,handlers}.py`, e toda interação era direto
com `docker compose`. Isso impedia reuso em outros workspaces e a futura adoção de
Kubernetes/OKD/Rancher.

## Decisão

Duas mudanças complementares:

1. **Runtime data-driven** — apps lógicos descritos em `[runtime.apps.*]`
   (`service`, `role`, `port`, `depends_on`, `url_env`/`fallback_url_env`,
   `e2e_suite`, `build`) mapeiam para serviços do `docker-compose.yml`. `resolve`,
   `e2e` e `handlers` passam a ler tudo do config; **nenhuma constante Optum** no
   código (breaking change assumido — ver [ADR-0009](0009-remover-stage-gates.md)
   para a postura de remover acoplamentos).

2. **Provider pattern** — uma ABC `RuntimeProvider` (espelhando `PlatformProvider`)
   com `up/stop/restart/status/logs/run_ephemeral/clean`, operando sobre nomes
   lógicos de app. `build_runtime_provider(ws)` seleciona a implementação por
   `runtime.orchestrator`. Implementado: `DockerComposeProvider` (reusa
   `runtime/compose.py`). Selecionáveis mas não implementados: `kubernetes`, `okd`,
   `rancher` → `ValidationError` clara ("ainda não implementado").

Decisões de apoio:

- Knobs específicos do backend ficam em subseção própria
  (`[runtime.docker_compose]`: `compose_file`, `env_files`, `project_name`,
  `ephemeral_runner`, `clean`), mantendo o modelo de app agnóstico.
- E2E/codegen/clean roteiam pelo provider (`run_ephemeral`, `clean`); a lógica de
  negócio do E2E (portas/URLs por suite, run numbering, strikes, Allure) permanece
  no handler.
- O build de FE roda no **host** (não é responsabilidade do provider): o handler
  builda e chama `provider.restart()`.

## Consequências

- ➕ Runtime reutilizável por qualquer workspace via config; zero acoplamento Optum
  no código.
- ➕ Caminho claro para Kubernetes/OKD/Rancher: basta uma nova implementação de
  `RuntimeProvider` + entrada na factory.
- ➕ Testabilidade: provider e resolução cobertos por testes com `dry_run`
  (sem Docker real). 22 testes novos.
- ➖ Breaking change no schema: o `config.toml` do workspace precisou ser expandido
  (exemplo versionado em `docs/examples/config.optum.toml`).
- ➖ `report serve` agora sobe a infra (`mongodb`+`allure`) via `provider.up([])`,
  e não só `allure` — efeito colateral menor, aceitável.
