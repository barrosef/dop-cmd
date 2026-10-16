# dop-cmd

`dop` operates a project's environment — the local cluster, the processes that
run beside it, and each component's tests — from one `dop.toml` at the
project's root.

It knows **how** to operate an environment and never **which** components
exist. Everything project-specific (the components, their repositories, how
each is brought up, observed and tested, and how the shared environment is
driven) lives in `dop.toml`, committed in the project. If it is not in
`dop.toml`, `dop` does not know it.

A small, dependency-free Python CLI: standard library only, Python ≥ 3.11,
Linux and macOS (Windows is out of scope: process groups, `tail -f`).

## The six verbs

| Verb | What it does |
|---|---|
| `dop up [component…]` | brings up the environment and the named components (default: all) with their declared `needs` closure |
| `dop down` | stops the app processes and the local cluster; data is kept |
| `dop status` | shows the environment and every component; changes nothing |
| `dop logs <component>` | follows a component's logs |
| `dop test <component> [--layer unit\|integration\|contract]` | runs a component's test layer (default `unit`) as `dop.toml` declares it |
| `dop reset` | starts the local environment over; all local data is lost |

Every verb takes `--dry-run` (print `WOULD RUN <cmd>` / `WOULD WAIT <what>`,
change nothing, exit 0), `-v`, and `--config <path>`.

## The `dop.toml` model

Found by walking up from the current directory; `DOP_CONFIG=<path>` overrides.
One file, one project. All paths are relative to the file. **Unknown keys
anywhere are a configuration error (exit 2)**, and so are an unknown `kind`, an
unknown `needs`, a dependency cycle, a `process` without `run`, a `cluster`
without `workloads`, and a test layer other than `unit`, `integration` or
`contract`. Keys must fit the `kind` (`none` takes only `repo`, `kind`, `needs`,
`setup`, `test`; `process` no `workloads`/`logs`; `cluster` no `run`/`env`),
`workloads` are `deploy|deployment|sts|statefulset/<name>`, `needs` has no
duplicates, every `repo` must be an existing directory, and an environment must
declare `kubeconfig`, `namespace`, `up`, `down` and the five `cluster_*` commands.

```toml
[project]
name = "dop"
state_dir = ".dop"                    # lock, pid files and process logs; gitignored at the root
default_environment = "local"         # the only one today; the table is plural so a later GCP verb adds a row, not a migration

[environments.local]
kubeconfig = "k3d kubeconfig write dop-local"      # stdout = path; every make/kubectl runs with KUBECONFIG=<path>
namespace = "dop-local"
cluster_exists = "k3d cluster list dop-local"      # exit 0 = exists
cluster_running = "k3d cluster list dop-local -o json"   # dop reads serversRunning > 0
cluster_up = "make -C repos/dop-infra cluster-up"
cluster_start = "k3d cluster start dop-local"
cluster_stop = "make -C repos/dop-infra cluster-stop"
prepare = ["make -C repos/dop-infra images", "make -C repos/dop-infra image-runner"]   # after cluster_up only
up = "make -C repos/dop-infra up"
down = "make -C repos/dop-infra down"              # used by reset only (deletes the namespace and its data)
status = "make -C repos/dop-infra status"          # appended verbatim under -v
workloads = ["sts/postgres", "sts/nats", "sts/firebase", "deploy/secretmanager"]   # the environment's own readiness
wait_timeout = "240s"
addresses = ["http://api.localtest.me:8080", "http://auth.localtest.me:8080", "http://traces.localtest.me:8080"]

[components.app]
repo = "repos/dop-app"
kind = "process"
needs = ["bff"]
setup = "pnpm install --frozen-lockfile"
run = "pnpm -C artifacts/dop dev"
env = { PORT = "5173", BASE_PATH = "/", VITE_API_BASE_URL = "http://api.localtest.me:8080", VITE_FIREBASE_AUTH_EMULATOR_URL = "http://auth.localtest.me:8080", VITE_FIREBASE_PROJECT_ID = "dop-local", VITE_FIREBASE_API_KEY = "fake-api-key" }
ready = "http://localhost:5173"
address = "http://localhost:5173"
[components.app.test]
unit = ["pnpm typecheck", "pnpm -C artifacts/dop test"]

[components.bff]
repo = "repos/dop-api"
kind = "cluster"
needs = ["core"]
workloads = ["deploy/dop-api"]
logs = "make -C repos/dop-infra logs C=dop-api"
ready = "http://api.localtest.me:8080/healthz"
address = "http://api.localtest.me:8080"
setup = "uv sync"
[components.bff.test]
unit = ["make test"]

[components.core]
repo = "repos/dop-core"
kind = "cluster"
workloads = ["deploy/dop-core-serve", "deploy/dop-core-worker", "deploy/dop-core-sched"]
logs = "make -C repos/dop-infra logs C=dop-core"
[components.core.test]
unit = ["make test"]
[components.core.test.integration]
forwards = ["svc/postgres 5432:5432", "svc/nats 4222:4222"]
commands = ["make test-integration"]
[components.core.test.contract]
forwards = ["svc/nats 4222:4222", "svc/firebase 9199:9199", "svc/secretmanager 8085:9090"]
commands = ["make test-contract-integration", "make proto-breaking"]

[components.cli]
repo = "repos/dop-cli"
kind = "none"
[components.cli.test]
unit = ["uv run --with pytest --with-editable . python -m pytest tests -p no:playwright -q"]
```

Rules of the model:

- `kind` is one of `cluster`, `process`, `none`. A `cluster` component is
  brought up by the environment's `up`; `dop` waits for its `workloads` and
  probes `ready`. What runs is the image the manifests name, **not the working
  copy**. A `process` component is started by `dop` from `repo` with `run` and
  `env`, detached, with its pid and log under `state_dir`. A `none` component
  has no runtime.
- `needs` lists component names; the closure is computed, dependencies first.
  A `cluster` component implicitly needs the environment.
- `setup` (optional, any kind) is an idempotent install command run in `repo`
  before `run` and before any test layer.
- `test.<layer>` is either a list of commands or a table
  `{ forwards?, commands }`. Commands run in order inside `repo`; the first
  failure stops the layer and fails the verb. `forwards` are
  `kubectl port-forward -n <namespace> <resource> <ports>` entries that `dop`
  opens for the run and closes afterwards.
- Every `make` and `kubectl` runs with `KUBECONFIG` set to the path the
  environment's `kubeconfig` command prints. `dop` never changes the machine's
  current kubectl context.
- `wait_timeout` is a duration: `"240s"`, `"4m"`, `"1h"`.

## Install

From the project root, after cloning it with its submodules:

```bash
uv tool install --editable repos/dop-cmd      # puts `dop` on ~/.local/bin
```

On a machine without the clone:

```bash
uv tool install git+ssh://git@github.com/barrosef/dop-cmd.git
```

The distribution is `dop-cmd`; the package and the executable are `dop`.
`dop-cli` installs a console script also named `dop`: do not put both on the
same path — whichever was installed last wins.

Development:

```bash
uv run --with pytest python -m pytest -q
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | done |
| `1` | failed (a command failed, a forward did not answer, another `dop` holds the lock) |
| `2` | usage or configuration error, including a tool the commands need (`k3d`, `kubectl`, `make`, `pnpm`, `uv`, …) missing from `PATH` |
| `3` | partial: several units were asked and some failed or were skipped |

A failing component command is never retried, skipped or masked.

## What it is not

`dop` does not touch git, pull requests, cards or deploys, and it does not take
a source change to the cluster (build, tag, rollout). It does not decide which
components a project has. It wraps the project's own tooling (the environment's
Makefile, `k3d`, `kubectl`) and does not replace it.

## Decisions

See [`docs/adr/`](docs/adr/README.md); [ADR-0016](docs/adr/0016-readaptation-dop-toml.md)
records the readaptation to `dop.toml`.
