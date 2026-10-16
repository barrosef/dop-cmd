# ADR-0016 — Readaptation: one `dop.toml` per project, six verbs

- **Status:** Accepted
- **Date:** 2026-10-16
- **Components:** `src/dop/config.py`, `src/dop/cli.py`, `src/dop/core/process.py`, `src/dop/kube.py`, `src/dop/verbs/`

## Context and problem statement

Version 0.7.1 was built for another client: multi-workspace configuration in
the user's home, git and pull-request automation, a docker-compose runtime,
build-tool-driven unit and integration tests, browser end-to-end tests with
their report server, and issue-tracker grouping. None of it describes how the
`dop` project runs: a k3d cluster provided by `dop-infra`, a cockpit process
started beside it, and four components each with its own test commands.

The project needs one daily entry point for its operating environment, for the
owner and for every agent, without the tool hard-coding the project.

## Decision drivers

- The tool must not know which components exist; the project must.
- Wrap the environment's own tooling (`dop-infra`'s Makefile, `k3d`,
  `kubectl`); do not change it.
- No dependencies to install beyond Python ≥ 3.11.
- Never touch the machine's current kubectl context, which holds other
  clusters.
- Errors must be one line and an exit code, never a traceback.

## Considered options

1. Keep 0.7.1 and add a k3d runtime behind its orchestrator abstraction.
2. Continue from the former client's later branches.
3. Rewrite on the 0.7.1 import, keeping only what is general, driven by one
   project file.

## Decision outcome

Option 3.

- **One `dop.toml` per project**, at the project root, found by walking up from
  the current directory, `DOP_CONFIG` overriding. Tables `[project]`,
  `[environments.<name>]`, `[components.<name>]`; component `kind` is
  `cluster | process | none`; `needs` gives a dependency closure;
  `test.<layer>` (`unit | integration | contract`) is a list of commands or
  `{ forwards?, commands }`. Unknown keys anywhere, unknown kinds or needs,
  cycles, a `process` without `run`, a `cluster` without `workloads` are
  configuration errors.
- **Six verbs**: `up`, `down`, `status`, `logs`, `test`, `reset`. Global
  `--dry-run`, `-v`, `--config`.
- **Exit codes**: `0` done, `1` failed, `2` usage or configuration error
  (including a missing tool, named), `3` partial.
- **Commands are shell strings** run by `core/process.py` in one of three
  modes: `stream`, `capture` (failure output redacted), `detach` (new session,
  output to a log file, pid returned; stopped by process-group kill).
- **`KUBECONFIG`** comes from the environment's `kubeconfig` command and is set
  on every `kubectl`/`make` the tool runs.
- Kept from 0.7.1: `core/security.py`, `core/logging_utils.py`, and ADRs 0001,
  0002, 0006, 0007 (amended). Removed: every other verb, module, document and
  test, and ADRs 0003–0005 and 0008–0015.
- Package `dop-cmd` version `1.0.0`, no dependencies, script
  `dop = dop.cli:main`, installed with `uv tool install --editable repos/dop-cmd`.

### Consequences

- Good: adding or changing a component is a `dop.toml` edit in the project, not
  a release of the tool.
- Good: the tool is small, stdlib-only and testable with fakes.
- Bad: `dop up bff|core` runs the image the manifests name, not the working
  copy; taking a source change to the cluster has no verb yet.
- Bad: the behaviour against a real cluster is proved only by live validation;
  the unit tests run on fakes.
- Neutral: `dop-cli` still installs a script named `dop`; the two must not
  share a path until that is decided.
