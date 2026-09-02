# ADR-0011 — Runtime local via `docker compose`

- **Status:** Aceito — **evoluído por [ADR-0014](0014-abstracao-de-orquestrador-runtime.md)** (runtime tornou-se data-driven + abstração de orquestrador)
- **Componentes:** `runtime/{compose,resolve,handlers}.py`, `config` (`RuntimeConfig`, `AppConfig`)
- **Commits:** `d1c9985`, `595d98b`, `5f847ae`, `0eeb961`, `a82fa9c`

## Contexto

Além de Git/PR, o agente precisa **subir o ambiente local** para validar mudanças:
múltiplos back-ends (Java) e front-ends (Vite/Vue-CLI), além de infra (MongoDB) e
relatórios (Allure). Fazer isso à mão é propenso a erro (portas, URLs entre
serviços, ordem de subida).

## Decisão

Modelar o runtime no config (`[workspaces.<w>.runtime]`) e orquestrar via
`docker compose`:

- **`compose.py`** — *construtores puros* de comandos (`build_up_command`,
  `build_stop_command`, `build_logs_command`, `build_run_command`,
  `build_ps_command`) e `write_env_runtime`. Não executam nada (testáveis).
- **`resolve.py`** — `expand_apps()` resolve aliases (ex.: `osf`, `pfe`, `cef`,
  `osb`, `ls`) para nomes canônicos (= nomes de serviço no compose) e **auto-inclui
  o back-end** do qual um front-end depende (via `fe_deps`), salvo `--no-deps`.
  `infer_urls()` computa as URLs de back-end: serviço Docker local se o BE está no
  ar, senão *fallback* para uma variável de ambiente Azure.
- **`handlers.py`** — `start` valida o `.env`, checa portas (`lsof`), builda
  front-ends no host (nginx serve o `dist/`), escreve `docker/.env.runtime` com as
  URLs inferidas e sobe `mongodb`, `allure` + apps com `--wait`. Há também
  `stop`, `restart`, `rebuild` (rebuild FE + restart nginx), `status`/`ps`
  (parsing de `compose ps --format json` com marcadores ✔/✗), `log` (segue logs via
  `os.execvp`) e `clean` (remove volumes: m2, node_modules, allure, all).

Decisões de apoio:

- **FEs servidos por nginx** com build no host (não dentro do container) — escolha
  feita em `0eeb961` para acelerar o ciclo.
- **`playwright-env` usa `network_mode: host`** e portas `localhost` (`a82fa9c`) para
  os testes alcançarem os serviços.

## Consequências

- ➕ "Subir o stack para a demanda X" vira `dop start osf` (com BE incluído).
- ➕ Construtores de comando separados da execução → cobertos por testes
  (`tests/test_runtime_compose.py`, `test_runtime_resolve.py`).
- ➕ URLs entre serviços são inferidas, não hard-coded pelo usuário.
- ➖ ~~Forte acoplamento ao `docker compose` e ao layout de repos/portas do workspace
  Optum~~ — **resolvido em [ADR-0014](0014-abstracao-de-orquestrador-runtime.md)**:
  o runtime é data-driven e o orquestrador é abstraído atrás de `RuntimeProvider`.
- ➖ Depende de ferramentas do host: `docker`, `lsof`, `npm`/`npx`, `nginx`.
