# PRD-0004 — Runtime local de aplicações

- **Status:** Implementado (v0.5.0)
- **ADRs relacionados:** [0011](../adr/0011-runtime-docker-compose.md)

## 1. Problema

Validar uma mudança exige subir um conjunto de serviços (front-ends, back-ends,
infra) com as portas e URLs entre eles corretamente configuradas. Fazer isso à mão
é demorado e propenso a erros de configuração (porta ocupada, URL de BE errada,
esquecer de subir a dependência).

## 2. Usuários e necessidades

- **Agente/Desenvolvedor:** quer "subir o app X" e ter as dependências e URLs
  resolvidas automaticamente; quer status legível e logs fáceis.

## 3. Requisitos funcionais

| ID | Requisito | Comando |
|---|---|---|
| F1 | Subir apps (por nome ou alias), auto-incluindo o BE do qual um FE depende; checar portas; buildar FEs; injetar URLs de BE. | `dop start <apps…> [--no-deps]` |
| F2 | Parar apps (ou todos). | `dop stop [apps…]` |
| F3 | Reiniciar apps. | `dop restart <apps…> [--no-deps]` |
| F4 | Rebuildar FE (build + restart do nginx). | `dop rebuild <fe-apps…>` |
| F5 | Mostrar status dos containers (apps + infra) com marcadores ✔/✗. | `dop status` / `dop ps` |
| F6 | Seguir logs de apps. | `dop log <apps…> [-f] [-n <tail>] [--since]` |
| F7 | Limpar volumes/caches Docker. | `dop clean [--m2] [--node-modules] [--allure] [--all]` |

## 4. Regras de negócio

- Aliases resolvem para nomes canônicos = nomes de serviço no `docker-compose.yml`
  (ex.: `osf`→`optum-support-fe`, `osb`→`optum-support-be`, `pfe`, `cef`, `ls`).
- Ao subir um FE, seu BE (de `runtime.fe_deps`) é incluído, salvo `--no-deps`.
- URLs de BE são **inferidas**: serviço Docker local se o BE está no ar; senão
  *fallback* para variável de ambiente Azure. Gravadas em `docker/.env.runtime`.
- `start` exige `docker/.env` presente; checa portas livres via `lsof`.
- Infra (`mongodb`, `allure`) sobe junto; `start` usa `--wait` (health checks).
- Tudo respeita `--dry-run` (os construtores de comando não executam por si).

## 5. Configuração

Definida em `[workspaces.<w>.runtime]` (`compose_file`, `env_file`,
`compose_timeout`, `default_max_strikes`), `[…runtime.apps.<app>]`
(`repo`, `kind`=java|vite|vue-cli, `port`, `debug_port`, `aliases`, `dev_cmd`) e
`[[…runtime.fe_deps]]` (`fe`, `be`). Ver [`reference/configuration.md`](../reference/configuration.md).

## 6. Fluxo típico

```bash
dop start osf            # sobe optum-support-fe + optum-support-be + infra
dop status
dop log osf -f
dop rebuild osf          # após mudar o FE
dop stop                 # para tudo
```

## 7. Fora de escopo / dependências

- Não provisiona nuvem; é estritamente local.
- Depende de `docker`/`docker compose`, `lsof`, `npm`/`npx` e nginx no host.

## 8. Dívidas conhecidas

- ~~Portas e nomes de serviço têm constantes específicas do workspace Optum~~ —
  **resolvido (2026-05-30)**: o runtime tornou-se data-driven e o orquestrador foi
  abstraído atrás de `RuntimeProvider` (docker_compose; k8s/okd/rancher futuros).
  Ver [ADR-0014](../adr/0014-abstracao-de-orquestrador-runtime.md) e
  [`reference/configuration.md`](../reference/configuration.md).
