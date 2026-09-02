# ADR-0005 — Abstração de autenticação Git (token / SSH)

- **Status:** Aceito
- **Componentes:** `src/dop/git/auth/*`, `src/dop/git_askpass.py`, `src/dop/git/operations.py`

## Contexto

Operações remotas de Git (`fetch`, `pull`, `push`) exigem credenciais. Diferentes
workspaces usam HTTPS com token (PAT) ou chaves SSH (RSA / Ed25519). Credenciais
**nunca** podem aparecer em linha de comando (visíveis via `ps`) nem em logs.

## Decisão

Definir a ABC **`GitAuthProvider`** (`auth/base.py`) com dois métodos:

- `git_env() -> dict[str,str]` — variáveis de ambiente para o subprocess git
  (**nunca logar o retorno**).
- `git_command_prefix() -> list[str]` — prefixo de comando (ex.: desabilitar
  credential helper).

Toda operação remota passa por `operations._run_git_remote()`, que injeta
`auth.git_env()` e prefixa `auth.git_command_prefix()`, validando que o prefixo
começa com `git`.

Implementações (escolhidas por `build_auth_provider(workspace)` conforme
`workspace.auth_method`):

- **`TokenAuth`** (HTTPS): seta `GIT_LOGIN`, `GIT_TOKEN`, `GIT_ASKPASS` (apontando
  para `git_askpass.py`), `GIT_TERMINAL_PROMPT=0`, e prefixa
  `git -c credential.helper=` para forçar o uso do askpass. O token é entregue ao
  git **via stdout do helper**, não via argv.
- **`SshRsaAuth`**: seta `GIT_SSH_COMMAND="ssh -i <key> -o IdentitiesOnly=yes -o
  StrictHostKeyChecking=no"` e `GIT_TERMINAL_PROMPT=0`.
- **`SshEd25519Auth`**: herda de `SshRsaAuth`, só muda a chave default
  (`~/.ssh/id_ed25519`).

O **`git_askpass.py`** é um script standalone: o git o invoca passando o texto do
prompt; ele lê `GIT_LOGIN`/`GIT_TOKEN` (com *fallback* `GIT_OPTUM_*`) do ambiente e
imprime usuário ou token conforme o prompt.

## Consequências

- ➕ Sem prompts interativos (`GIT_TERMINAL_PROMPT=0`) → seguro para automação/CI.
- ➕ Token nunca em argv nem em logs → não aparece em `ps` nem vaza.
- ➕ Trocar de token para SSH é só mudar `auth_method` no config.
- ➕ Caminho do askpass é resolvido no factory (`Path(__file__)…/git_askpass.py`),
  evitando *path traversal*.
- ➖ O helper depende de variáveis de ambiente estarem presentes; ausência vira
  `ValidationError` clara.
