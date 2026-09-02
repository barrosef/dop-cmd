# PRD-0005 — Testes E2E e relatórios

- **Status:** Implementado (v0.5.0)
- **ADRs relacionados:** [0012](../adr/0012-e2e-playwright-allure-strikes.md)

## 1. Problema

Validar uma demanda de ponta a ponta exige rodar testes de UI (Playwright/pytest)
contra o ambiente local e inspecionar relatórios (Allure). O agente precisa rodar
testes por suíte, por chave Jira (sem saber em quais suítes os testes vivem) ou por
arquivo, com resultados isolados por execução, e precisa de um **freio** contra
loops de retentativa quando algo está cronicamente vermelho.

## 2. Usuários e necessidades

- **Agente de IA:** "rode os E2E da OG-150"; precisa de exit code claro e de um
  limite de tentativas (*strikes*).
- **Desenvolvedor:** quer modo visual (headed), filtro `-k`, gravação de novos
  testes (codegen) e relatórios navegáveis.

## 3. Requisitos funcionais

| ID | Requisito | Comando |
|---|---|---|
| F1 | Rodar E2E por suíte, chave Jira (auto-descobre suítes) ou arquivo. | `dop e2e <alvo…> [-k <filtro>]` |
| F2 | Modo visual (X11) com contexto compartilhado. | `dop e2e <alvo> --headed` |
| F3 | Limitar retentativas via *strikes* (vermelho consecutivo). | `dop e2e … --max-strikes N` / `--reset-strikes` |
| F4 | Gravar interações em teste (codegen). | `dop codegen <suite> [--url] [--out]` |
| F5 | Servir/abrir/limpar relatórios Allure. | `dop report serve\|open\|clean [--keep N]` |

## 4. Regras de negócio

- Alvo é interpretado como **suite** (nome conhecido), **Jira** (regex
  `[A-Z][A-Z0-9]+-\d+`, normalizada p/ filtro, ex.: `OG-150`→`og_150`) ou
  **arquivo** (contém `/` ou termina em `.py`).
- Por Jira, varre `e2e/<suite>/tests/` e roda só as suítes com testes daquela chave,
  na ordem canônica.
- Cada execução vai para `e2e/reports/<jira>/<suite>/run-<N>/` (N sequencial).
- URLs por suíte são injetadas (`E2E_BASE_URL` = FE, `E2E_API_URL` = BE).
- *Strikes*: `red` incrementa o contador; `green` zera; teto 1–10 (default do
  config); histórico das últimas 10 execuções no `.state.json`.
- Exit code 0 = tudo verde; 1 = alguma suíte vermelha.

## 5. Saídas

- Resultados Allure por execução; portal Allure (`dop report serve`).
- Campo `e2e` do `.state.json` (status, contagem de strikes, histórico, report URL).

## 6. Fluxo típico

```bash
dop start osf
dop e2e OG-150               # descobre suítes e roda os testes da OG-150
dop e2e optum-support-fe --headed -k login
dop report open optum-support-fe OG-150
```

## 7. Fora de escopo

- Escrever os testes (o `codegen` apenas grava o esqueleto).
- Execução em CI remoto (é runner local via container `playwright-env`).

## 8. Dívidas conhecidas

- Acoplado ao layout `e2e/<suite>/tests/`, a Allure 3 e a mapas de porta FE/BE
  específicos do workspace. Headed exige X11 no host.
