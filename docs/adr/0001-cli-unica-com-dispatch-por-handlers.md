# ADR-0001 — CLI única com dispatch por handlers

- **Status:** Aceito
- **Data:** reconstruído (impl. em `cli.py`)
- **Componentes:** `src/dop/cli.py`

## Contexto

O `dop` precisa expor dezenas de subcomandos heterogêneos (ciclo de vida de
demanda, integração, runtime, E2E) numa interface coesa, fácil de estender e
testável sem efeitos colaterais.

## Decisão

Usar **`argparse` com subparsers** num único *entry point* (`dop.cli:main`). Cada
subcomando registra um `func` via `set_defaults(func=..., command=...)`, e o
`main()` despacha de forma uniforme:

```python
return args.func(args, logger, workspace, auth)
```

Decisões de apoio:

- **Assinatura uniforme de handler:** `(args, logger, workspace, auth) -> int`
  (retorno é o *exit code*). Handlers de runtime têm assinatura própria
  `(workspace, args, dry_run, logger)` e são adaptados por um *wrapper*
  (`_make_rt_func`) — ver `cli.py:705`.
- **`SecureArgumentParser`:** subclasse de `ArgumentParser` cujo `error()`
  passa a mensagem por `redact()` e levanta `ValidationError` em vez de chamar
  `sys.exit`, garantindo que erros de parsing não vazem segredos (`cli.py:605`).
- **Tratamento central de exceções:** `main()` captura `MCPError` (redige e imprime
  `ERROR: …`, exit 1) e qualquer `Exception` inesperada (redige), evitando
  *tracebacks* crus para o usuário.
- **Contexto resolvido uma vez no `main()`:** workspace, logger, auth e resolução
  de alias da Jira são montados antes do dispatch, então handlers recebem tudo pronto.

## Consequências

- ➕ Adicionar um comando = adicionar um subparser + um handler. Baixo acoplamento.
- ➕ Handlers são funções puras-ish (recebem dependências), fáceis de testar
  (ver `tests/test_cli_parsing.py`, `tests/test_handlers.py`).
- ➕ Política de segurança (redação) centralizada no parser e no `main()`.
- ➖ O arquivo `cli.py` concentra muitos handlers de demanda/integração (~825 linhas);
  handlers de runtime já foram extraídos para `runtime/handlers.py`. Há espaço para
  extrair também os de demanda no futuro.
