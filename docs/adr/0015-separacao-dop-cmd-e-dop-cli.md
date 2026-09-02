# ADR-0015 — Separação `dop-cmd` (ferramenta de ambiente) e `dop-cli` (nome da plataforma)

- **Status:** Aceito
- **Data:** 2026-08-26
- **Spec:** `docs/dop-cmd/2026-08-26-separacao-design.md` (meta-repositório `dop`)

## Contexto

O repositório `dop-cli` acumulava **dois papéis sobrepostos**:

1. A **ferramenta operacional em uso** (v0.7.1) — esta base de código, que acessa a
   workspace diretamente e responde pelo ambiente.
2. O **nome `dop-cli`**, que pertence ao projeto da plataforma DOP.

Com os dois papéis no mesmo repositório, construir a CLI da plataforma significava mexer
no repositório de uma ferramenta em produção, e qualquer conversa sobre "o dop-cli"
era ambígua.

Esta base de código e a plataforma DOP são **projetos distintos**. Esta ferramenta
originou a ideia daquela, mas não é seu ancestral técnico: a plataforma não herda
código, modelo nem decisão daqui. Manter os dois no mesmo repositório sugeria o
contrário.

## Decisão

1. **Mover** esta implementação para um repositório próprio, `dop-cmd`. O nome é mais
   apropriado ao papel: *comando de ambiente*, não interface de um cliente.
2. **Esvaziar** o `dop-cli`, devolvendo o nome ao projeto da plataforma.
3. **Nascer limpo:** o `dop-cmd` recebe os arquivos do `main` do `dop-cli` num commit
   inicial único. A história de decisão permanece acessível no `dop-cli` (tags
   `v0.1.0`, `v0.5.0` e commit `19fa56a`).
4. **Preservar a identidade de execução:** a distribuição passa a chamar-se `dop-cmd`,
   mas o **pacote Python e o executável continuam `dop`**, e a versão continua `0.7.1`.
   Nenhum workspace em uso precisa mudar de comando.
5. **Não ser submodule** do meta-repositório: o `dop-cmd` mora em `repos/dop-cmd` e
   convive com os demais componentes, mas o meta-repo agrega apenas os componentes do
   produto 1.0.

## Consequências

- ➕ A CLI da plataforma pode ser construída do zero sem colocar em risco a ferramenta em
  produção.
- ➕ Cada nome passa a designar um papel só; some a ambiguidade de "o dop-cli".
- ➕ Zero ruptura operacional: `dop <subcomando>` continua funcionando como antes.
- ➖ `dop-cmd` e a futura `dop-cli` disputam o executável `dop` e **não podem coexistir
  no mesmo ambiente Python**. Quando a CLI 1.0 assumir o nome, será em ambiente
  separado ou com o `dop-cmd` já aposentado.
- ➖ Quem instalava de `dop-cli.git` precisa trocar a origem para `dop-cmd.git`.
- ➖ Por não ser submodule, o `dop-cmd` não vem no clone recursivo do meta-repositório;
  exige clone explícito, documentado no README da raiz.
