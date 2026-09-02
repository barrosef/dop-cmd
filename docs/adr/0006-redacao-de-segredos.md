# ADR-0006 — Redação de segredos e *guard* anti env-dump

- **Status:** Aceito
- **Componentes:** `src/dop/core/security.py`, `core/process.py`, `core/logging_utils.py`, `cli.py`

## Contexto

A ferramenta executa subprocessos (git, az), grava logs e imprime erros. Tokens,
PATs, *Bearer tokens* e variáveis de ambiente sensíveis podem aparecer em qualquer
dessas saídas. Num fluxo conduzido por IA, vazar um segredo para o log/transcript é
crítico.

## Decisão

Centralizar a defesa em `core/security.py`:

- **`guard_text(text)`** — levanta `SecurityViolationError` se o texto contiver
  `os.environ` (impede dump acidental do ambiente inteiro).
- **`redact(text)`** — chama `guard_text` e aplica regex para mascarar:
  - tokens GitHub (`gh[pors]_…` → preserva prefixo);
  - PATs genéricos (`pat_…`);
  - `Bearer …`;
  - atribuições `TOKEN|SECRET|KEY|PASSWORD = …`;
  - campos JSON `"token|secret|key|password": "…"`.
- **`redact_many(lines)`** — redige uma sequência.

Pontos de aplicação:

- `core/process.run_command()` valida o comando com `guard_text` e **redige
  stdout/stderr ao levantar `ProcessError`**.
- `core/logging_utils.Logger` redige toda mensagem antes de escrever em arquivo/console.
- `cli.SecureArgumentParser` redige mensagens de erro de parsing.
- `cli.main()` redige qualquer exceção antes de imprimir `ERROR: …`.

## Consequências

- ➕ Defesa em profundidade: segredos são removidos no ponto de saída, não confiando
  em "lembrar de não logar".
- ➕ O prefixo do token é preservado na redação (ajuda a diagnosticar *qual tipo* de
  credencial estava errada sem revelá-la).
- ➖ A redação é baseada em padrões; segredos com formato incomum podem escapar — por
  isso a regra "**nunca logar `git_env()`**" ([ADR-0005](0005-abstracao-de-autenticacao-git.md))
  permanece como defesa primária.
- ➖ `guard_text` bloqueia a *string* literal `os.environ`, o que é uma heurística
  específica contra o ataque mais provável (agente tentando dumpar o ambiente).
