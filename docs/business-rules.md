# `dop` — business rules

**Date:** 2026-09-28 · **Authority:** this document · **Branch:** `k3s` (orphan — nothing is
inherited from the earlier tools that carried the name)

> What `dop` must do, stated so that the architect specifies against it, the reviewer attacks it and
> the QA team judges the tool by it. The earlier `dop-cli` / `dop-cmd` are **not** a reference: a
> behaviour they had is neither a requirement nor a regression. Where this document is silent, the
> tool does nothing.

---

## 1. What `dop` is for

One operator — a human or an agent — works on **several demands at once**, each touching several
repositories, each needing its own running copy of the applications it changes, its own tests and
its own reports, **without one demand ever seeing another's code or state.**

`dop` is the only way that environment is touched: brought up, fed, observed, tested, torn down. It
does **no governance**: no branch, commit, push, pull request, card or task-manager call. The git it
reads is `git worktree list` (where a demand's code is) and, for a companion's label only (B31),
`git status --porcelain` and the checkout's branch and sha — all read-only.

## 2. The environment

- **B1 — One cluster.** A local k3s cluster (k3d) named in the workspace config. *(How the context
  is enforced is B25, which supersedes the "active context" check first written here.)*
  Client production contexts live in the same kubeconfig — this rule is what keeps them safe.
- **B2 — One entry port.** The browser reaches everything through one host port (the cluster load
  balancer, `8080` today). No service gets a port of its own. Ports are for attaching a debugger
  only.
- **B3 — Every service by name.** A demand's application answers at
  `<app>.<demand>.<domain>:<port>` (`optum-support-fe.suopt-1530.localhost:8080`); shared services
  at `<service>.<domain>:<port>` (`reports.localhost:8080`, `allure.localhost:8080`). Lower case
  always. The domain is `localhost`, so no DNS or hosts-file setup exists. The scheme lives in the
  workspace config; the tool never hard-codes a host or a port.
- **B4 — One namespace per demand.** A demand present in the environment is one namespace,
  `<prefix>-<demand>` (lower case), labelled with the demand key. Shared services live in one
  shared namespace. Nothing of one demand is reachable from another demand's namespace.
- **B5 — Databases stay remote.** No database or queue runs in the cluster. Applications reach the
  remote ones exactly as today; credentials come from the workspace's env file and are never
  printed.
- **B6 — The cluster never sees the host disk.** Artifacts enter the node by copy
  (`docker cp` into the node container) under `/workspace/<namespace>/<app>/`. Workloads mount that
  directory (`hostPath`, `type: Directory`, never a file, never `DirectoryOrCreate`). A copy
  replaces the directory's content; it does not survive recreation of the node, so everything is
  re-copied after one.

## 3. The demand

- **B7 — A demand is named, never guessed.** A demand key (`SUOPT-1530`) is given by the operator,
  matched `^[A-Za-z]+-\d+$`, upper-cased. Nothing is inferred from the working directory, a branch
  or a state file.
- **B8 — A demand's code is its worktree.** For each repository, the demand's code is the worktree
  whose branch contains the key as a whole token (`git worktree list`). None → that repository is
  not part of the demand. More than one → that unit fails naming the candidates; other units run.
- **B9 — A demand's applications** are the configured applications whose repository has a worktree
  for the demand (B8). The operator can narrow them (`--app`); never widen beyond them.
- **B10 — A demand enters and leaves explicitly.** `dop up --tasks K` brings it into the
  environment; `dop down --tasks K` removes its namespace and its copied artifacts. Nothing else
  creates or destroys a demand. A demand that shipped but was never brought down stays present —
  that is visible in `dop status`, not hidden.

## 4. How every command behaves

- **B11 — No filter means everything present.** A command with no filter acts on every demand
  present in the environment (B4 labels are the only source). Nothing present → the command says
  so and exits *partial* (3).
- **B12 — Filters only narrow.** `--tasks K…`, `--app A…` mean the same thing on every command;
  they intersect, and several values on one filter unite. The only command a filter can *add* to is
  `up`, and only by the demands it names.
- **B13 — Units, not runs.** A command acts on units — *(demand, app)*, *(demand, suite)*,
  *(demand, repo)* — and every unit ends **done**, **skipped** (out of reach, with the reason) or
  **failed** (attempted and failed, with the reason). One unit never stops another. The command
  ends with a summary listing every non-done unit.
- **B14 — One exit contract.** `0` all done · `3` nothing failed, something skipped or nothing to
  do · `1` something failed · `2` usage or configuration error, nothing attempted.
- **B15 — `--dry-run` everywhere.** It resolves the same units, prints what would be done to each,
  changes nothing, and never claims an outcome it cannot know.
- **B16 — Nothing destructive by surprise.** Nothing replaces a running artifact before the new one
  has been verified present and non-empty. Nothing deletes a report run.

## 5. What the commands are

| Command | Units | What it does |
|---|---|---|
| `dop env up` | shared services | Creates/updates the shared namespace (reports, Allure). Idempotent. |
| `dop up --tasks K… [--app]` | (demand, app) | Creates the demand's namespace with its config and the workloads of its applications (B9), from the workspace's manifests. Idempotent. |
| `dop build [--tasks] [--app]` | (demand, app) | Builds each application's artifact **in the demand's worktree**, in a build container on the host (the cluster cannot see the worktree). |
| `dop deploy [--tasks] [--app]` | (demand, app) | Copies the built artifact into the node (B6) and makes the workload serve it (back-ends restart; front-ends serve the new files). An absent or empty artifact fails the unit and replaces nothing. |
| `dop down --tasks K…` | demand | Deletes the namespace and the copied artifacts. Requires `--tasks`. |
| `dop status [--tasks] [--app]` | (demand, app) | Readiness and address of every workload; a cluster that cannot be read fails the units, never reports them "down". |
| `dop log [--tasks] [--app]` | (demand, app) | Logs of the scope; follows by default. |
| `dop test aaa\|it [--tasks] [--repo]` | (demand, repo) | Unit / integration tests on the **demand's worktree**, in a runner container on the host (the test project is mounted so that its source path resolves to the demand's worktree). Integration tests get Docker for Testcontainers. |
| `dop test e2e [--tasks] [--app] [-k]` | (demand, suite) | Playwright against the **demand's own running applications** (B3 addresses). Suites belong to apps in config. |
| `dop report [--tasks] [--publish]` | — | Prints the reports address; every test run publishes to its demand's report, keeping every previous run. |

## 6. Configuration

The workspace declares, in one file: the cluster (context name, load-balancer node container), the
address scheme (domain, port, namespace prefix), the manifests directory, the env file, the
applications (name, repository, kind back-end/front-end, artifact directory inside the worktree,
build image and command, e2e suite) and the runner images. An unknown key, a missing required key,
an application naming an unknown repository, or a duplicated name is a configuration error (exit
2). Nothing in the tool names an Optum application.

## 7. What `dop` does not do

No governance (§1). No database. No Docker Compose. No inference of a demand. No port per service.
No deletion of history. No knowledge of any particular application.

---

## 8. Rules added after review (v2, 28/09) — binding, they amend §2–§6

**Wiring**
- **B17 — Front-end bundles are addressed at build time.** Each front-end declares in config the
  build variables that carry back-end addresses and which app each one points at. `build` sets
  them to that app's address in the demand (B3) when the app is in the demand, else to the app's
  declared fallback. After building, the bundle is scanned for the addresses config declares as
  forbidden (e.g. `localhost:8090`); a hit fails the unit.
- **B18 — Back-ends are wired in the namespace.** Config declares, per back-end, the environment
  keys that carry another app's address. `up` writes them into the demand's config: the
  in-namespace Service when the callee is in the demand, else its fallback. The tool names no key.
- **B19 — Companions.** A demand's apps (B9) are its worktree apps **plus** the companions config
  declares for them (a back-end's front-end, a front-end's back-end). A companion is built from the
  **main checkout**, labelled `source=trunk`, and shown as such by `status`. A demand with no app is
  skipped with the reason; no namespace is created.
- **B20 — Schedulers are off in a demand** unless config switches them on for that app. Config
  declares, per app, the environment that turns scheduling off; an app with none declared is shown
  by `status` as "scheduler not controllable".

**Where things run**
- **B21 — Runners run on the host.** `build`, `test aaa|it|e2e` run in containers on the host, never
  in the cluster. The e2e runner uses the host network and is given, per hostname it needs, an
  explicit `127.0.0.1` mapping; every address a suite reads comes from config templates rendered
  with the scheme — no default in any suite may be relied on.
- **B22 — Tests use the demand's test code and their own build directory.** The test tree is the
  demand's worktree of the workspace repository when it has one, else the main checkout. Each
  (demand, repo) test project is copied to `<workspace>/.dop/runs/<DEMAND>/<layer>-<repo>/` and run
  there with the demand's worktree mounted at the path its source root expects. `it` units report
  that they test the test project's own code (their poms have no application source root).

**Reports**
- **B23 — Reports live on the host.** Results are written under the workspace's report directory
  on the host, per demand, per run; nothing prunes them. After each run `dop` regenerates that
  demand's report on the host and copies the static site into the node, where the shared
  `reports` service (nginx) serves it at `reports.<domain>:<port>`. The in-cluster Allure API
  service is not used.

**Deploy and safety**
- **B24 — Deploy replaces contents, not directories.** The artifact is staged inside the node, then
  the mounted directory's contents are made exactly equal to it (stale files removed); the
  directory itself is never swapped. A back-end unit is done when its rollout finished and the new
  pod is Ready; a front-end unit when the files are in place.
- **B25 — Every cluster call names the context.** The configured context is passed on every call;
  the current context is never read. A context that does not exist is exit 2.
- **B26 — `dop` owns only what it labelled.** Demand namespaces carry `app.kubernetes.io/managed-by=dop`
  and `dop/demand=<KEY>`. Discovery (B11) and `down` act only on those; anything else fails the unit.
- **B27 — One writer per unit.** A unit is locked (`<workspace>/.dop/locks/`) while a command acts
  on it; a second command on the same unit fails that unit naming the holder.

**Precision**
- **B28 — Token.** The key matches a branch case-insensitively, as a token delimited by the start,
  the end, or one of `/ - _ .`. A worktree whose path no longer exists fails the unit naming it.
- **B29 — `--repo`** narrows test units by repository. A repository with no test project for the
  layer is skipped with that reason.
- **B30 — Secrets.** Application and e2e credentials come from the workspace env file, by keys
  config names; they reach the cluster as a Secret and are never printed, logged or put in a
  report.

**Cut from today, recorded:** none of B17–B30 is cut. Out of scope: parallel `env up` on two
machines; a cluster recreated with a host mount (R5, deferred by the manager).

## 9. Rules added after the second review (v3, 28/09)

- **B31 — Every build is the demand's own.** A worktree app builds in its worktree (unique to the
  demand). A companion (B19) is copied from the main checkout into
  `<workspace>/.dop/builds/<DEMAND>/<app>/` and built there, so no two demands ever share a build
  output. The companion's label is the main checkout's actual branch and short sha, plus `dirty`
  when it has local changes — never the word `trunk` unchecked.
- **B32 — Schedulers are an accepted risk.** B20 stands, but no application can switch scheduling off
  today (76 `@Scheduled`, no conditional `@EnableScheduling`). `status` shows "scheduler: on
  (not controllable)" unless *every* scheduler of that app is covered by config; partial control is
  never shown as off.
- **B33 — Suites are checked for addresses.** `test e2e` reads the suite's sources for environment
  reads of URL-like keys; one not covered by the suite's `suite_env` fails the unit naming the key.
- **B34 — Stale locks are broken.** A lock records pid and host; a lock whose process is dead on this
  host is broken and the fact is reported.
- **B35 — Integration runners use the host network**, so Testcontainers started beside them are
  reachable.
- **Cut today, recorded:** headed e2e (display/noVNC per demand) — not built; `dop test e2e` is
  headless only.

### Objections → rules

| Review 1 | Rule | | Review 2 | Rule |
|---|---|---|---|---|
| 1 FE bundle addresses | B17 | | 1 mapping | this table |
| 2 BE wiring | B18 | | 2 shared companion build | B31 |
| 3 companions / zero apps | B19 | | 3 schedulers | B32 (accepted risk) |
| 4 reports home | B23 | | 4 ADR-03/05, headed | spec step 3b; headed cut |
| 5 e2e runner location | B21 | | 5 shared contracts | spec step 1 |
| 6 test isolation | B22 | | 6 render ↔ base seam | spec step 1 patch targets |
| 7 `it` claims | B22 | | 7 B1 vs B25 | B1 amended |
| 8 deploy semantics | B24 | | 8 suite defaults | B33 + spec step 3 |
| 9 context | B25 | | 9 stale locks | B34 |
| 10 ownership | B26 | | 10 trunk label | B31 |
| 11 schedulers | B20 | | 11 secrets for step 5 | spec step 5 pass criterion |
| 12 concurrency | B27 | | 12 dead manifests | spec step 3 |
| 13 token / missing path | B28 | | 13 Testcontainers | B35 |
| 14 `--repo` | B29 | | | |
| 15 manifests/secrets/k8s untracked | B30 + spec step 3 | | | |

### Step-1 choices accepted by the architect (28/09)

`Runner.run` takes a `command`; verbs lock through `ctx.lock(unit)`; `Scope.skipped` carries every
result decided during resolution; required `dop.toml` keys are those the core enforces; a front-end
build variable's fallback is that front-end's own `calls` entry for the target app; the base layout
`demand/<dir>/`, `demand/{backends,frontends}/<app>/` (+ shared dirs by kind) is a contract with the
workspace; a named demand that is not present is skipped with the reason on every verb except `up`
and `down`.

### Step-2 choices accepted by the architect (28/09)

Maven cache is the docker volume `dop-maven-cache`; a back-end rollout waits 180 s. `up` renders and
applies once per demand, outside the unit lock — two concurrent `up` on the same demand are not
serialized (known, not handled today). Reports are synced to
`<node_root>/<shared_namespace>/reports/<DEMAND>/<project>/`, which the `reports` service serves.

- **B36 — Build credentials.** A build that needs a private registry declares, per app, host
  credential files (`build.credentials = { "<path in container>" = "<host path>" }`). They are
  mounted read-only, never copied into a build directory or an artifact, never printed. An absent
  file fails the unit naming the path, not the content.

## 10. Decisions after QA (v4, 28/09) — binding

- **B8 amended.** Only *linked* worktrees are a demand's code. The main checkout is never a demand's
  worktree, whatever its branch; it is only the companion source (B31), labelled with its real
  branch, sha and `dirty`. A companion may therefore carry another demand's uncommitted code — the
  label says so; that is an accepted, visible risk.
- **B27 amended.** Read-only verbs (`status`, `log`, `report`) take no lock. `down` takes every app
  lock of the demand. The unit identity includes the verb family (`test aaa` and `test it` differ)
  and is escaped so distinct names never collide.
- **B37 — Render is total.** `up` always renders the demand's whole config (every app of the demand,
  every key any app of the workspace declares) regardless of `--app`; `--app` narrows which
  workloads are applied, never what config says. A patch target that does not match fails the
  demand. Cluster-scoped kinds in the demand base are refused.
- **B38 — Dry-run writes nothing**, anywhere: no overlay, no secret file, no seed, no lock.
- **B39 — Secret files are transient.** The rendered secret file exists only for the duration of the
  `apply` and is deleted afterwards, success or failure; `.dop/` is git-ignored by the workspace.
- **B40 — Ownership is checked at delete time.** `down` deletes by label selector
  (`dop/demand=<KEY>`, `app.kubernetes.io/managed-by=dop`), never by computed name; a namespace
  that exists under the computed name without those labels fails `up` and `down` for that demand.
- **B41 — Artifacts are validated.** A back-end artifact must contain exactly one runnable jar; a
  front-end artifact must contain `index.html`. Build fails when it produced no valid artifact.
- **B42 — Builds run as the invoking user** (uid:gid), never root.
- **B43 — Credential mounts are confined.** A credential's container path may not lie inside the
  build directory or the Maven cache; host paths must be absolute or `~`-prefixed.
- **Accepted risk:** a pod in one demand can reach another demand through the ingress by host name
  (hairpin); B4's isolation is at the Service/NetworkPolicy level, not the ingress.
- **B18 amended.** A `calls` entry may declare `form = "browser"`: its key then gets the demand's
  public address (B3) instead of the in-namespace Service — for addresses handed to a person, such as
  the base of links in an e-mail.
- **B18 amended (callee running).** Render is total (B37), so an applied app's key can point at a
  callee that is part of the demand yet was never itself brought up — `--app` narrowed a previous
  or this same `up` to leave it out. `up` warns per unit rather than failing it: "`KEY` points at
  local `<callee>`, which is not up — run `dop up --tasks K --app <callee>` (or without `--app`)";
  a dry-run, unable to check anything live, warns instead that it "would point at local `<callee>`,
  not in this apply". `status` is the tool that verifies wiring is actually live: its wiring
  section checks a local callee's own Deployment too, and a missing one, or one with zero ready
  replicas, is shown `[local <callee> — NOT RUNNING]` and fails the unit with that reason.
- **B36 amended (secret environment).** A build may also declare `build.secret_env = ["KEY", …]`:
  those host environment variables are passed into the build container by name only — never on the
  command line, never printed; a missing one fails the unit naming the key.
- **B44 — `dop report --publish` copies already-generated report sites into the shared `reports`
  service (B23).** Every top-level directory under the workspace's report root that holds its own
  `index.html` is one published project site (`aaa-<repo>`, `it-<repo>`, `e2e-<suite>`); each is
  copied whole, one `Node.sync` per project, into its own name under the node's `reports` directory
  — exact content, never touching another project's entry or a raw per-demand results directory
  (no `index.html` of its own, so never published). The hand-written landing page — whatever plain
  files sit at the report root beside those directories, its `index.html` and the screenshots it
  references — is staged on its own and written into the node's `reports` directory overwriting
  only files of the same name; nothing already there, including a project's directory, is ever
  removed. Each project and the landing page succeed or fail independently. `--publish` takes no
  lock (B27 amended: `report` is read-only). `--dry-run` lists what would be copied, with size, and
  writes nothing (B38).
