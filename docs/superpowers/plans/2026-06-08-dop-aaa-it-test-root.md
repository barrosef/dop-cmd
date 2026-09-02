# `dop aaa` / `dop it` + `test_root` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `dop aaa <repo|all>` and `dop it <repo|all>` subcommands (host Maven, unit/integração) and a per-workspace `test_root`/`aaa_root`/`it_root` config, unifying all three test layers under one Allure reports root (`e2e-<suite>`, `aaa-<repo>`, `it-<repo>`).

**Architecture:** Config gains three optional path fields. `dop e2e` is parameterized by `test_root` and its generated report is renamed to `e2e-<suite>`. Two new handlers run Maven **on the host** per project, then publish results into the shared `<test_root>/reports` tree via a generalized Allure helper. No new container, no `ephemeral_runner` change — so container-side `/e2e/...` literals stay intact.

**Tech Stack:** Python 3.13, `argparse`, `dataclasses`, `unittest`/`pytest`, `subprocess`, Maven (host), Allure CLI.

---

## Context for the implementer (read before starting)

- All paths below are relative to the `dop-cli` repo root: `/opt/wks/dbo/dop/repos/dop-cli`.
- Run tests with: `python -m pytest tests/ -v` (the repo uses both `unittest`-style classes and bare `pytest` functions; follow the style of the file you're editing).
- `run_command(cmd, *, cwd=None, dry_run=False, env=None, logger=None)` lives in `src/dop/core/process.py`. It **captures** output and **raises `ProcessError`** on non-zero exit. We deliberately do **not** use it to run Maven (we want live output and a return code, not an exception on test-failure). We add a dedicated `_run_maven` helper for that.
- `ValidationError` and `ProcessError` are in `src/dop/core/errors.py`.
- Existing handler test patterns are in `tests/test_runtime_handlers.py` (`_ws()` factory, `_args(**kw)`, `patch("dop.runtime.handlers.build_runtime_provider", ...)`).
- The shared Allure UI (`:5252`) serves `<test_root>/reports`. Every layer's generated report dir must land there.

---

## File Structure

- `src/dop/config/schema.py` — add `test_root`, `aaa_root`, `it_root` to `WorkspaceConfig`.
- `src/dop/config/loader.py` — parse the three new fields.
- `src/dop/runtime/handlers.py` — parameterize e2e by `test_root`; rename e2e report to `e2e-<suite>`; add `_publish_allure_project`, `_run_maven`, `_resolve_test_targets`, `_handle_maven_layer`, `handle_aaa`, `handle_it`.
- `src/dop/cli.py` — add `aaa` and `it` subparsers; import the two new handlers.
- `tests/test_config_schema_v05.py` — config field tests.
- `tests/test_runtime_handlers.py` — handler + helper tests.
- `tests/test_cli_parsing.py` — parser tests.
- `docs/workspace-migration-adr16.md` — workspace-side changes (delivered, not applied).

---

## Task 1: Config fields `test_root` / `aaa_root` / `it_root`

**Files:**
- Modify: `src/dop/config/schema.py:86-100` (`WorkspaceConfig`)
- Modify: `src/dop/config/loader.py:125-140` (`_parse_workspace` return)
- Test: `tests/test_config_schema_v05.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config_schema_v05.py`:

```python
def test_workspace_test_roots_defaults():
    ws = WorkspaceConfig(name="test", root="/tmp/test")
    assert ws.test_root == "e2e"
    assert ws.aaa_root == "test/aaa"
    assert ws.it_root == "test/it"


def test_workspace_test_roots_override():
    ws = WorkspaceConfig(name="optum", root="/tmp/optum",
                         test_root="test/e2e", aaa_root="test/aaa", it_root="test/it")
    assert ws.test_root == "test/e2e"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_schema_v05.py::test_workspace_test_roots_defaults -v`
Expected: FAIL — `TypeError`/`AttributeError` (no `test_root` field).

- [ ] **Step 3: Add the fields**

In `src/dop/config/schema.py`, inside `WorkspaceConfig` (add right after `root: str`):

```python
@dataclass
class WorkspaceConfig:
    name: str
    root: str
    test_root: str = "e2e"
    aaa_root: str = "test/aaa"
    it_root: str = "test/it"
    demands_dir: str = "docs/RFC"
```

(Leave the rest of `WorkspaceConfig` unchanged.)

- [ ] **Step 4: Parse them in the loader**

In `src/dop/config/loader.py`, in the `return WorkspaceConfig(...)` call, add after `root=data["root"],`:

```python
        root=data["root"],
        test_root=data.get("test_root", "e2e"),
        aaa_root=data.get("aaa_root", "test/aaa"),
        it_root=data.get("it_root", "test/it"),
        demands_dir=data.get("demands_dir", "docs/RFC"),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_config_schema_v05.py -v`
Expected: PASS (all, including the two new tests).

- [ ] **Step 6: Commit**

```bash
git add src/dop/config/schema.py src/dop/config/loader.py tests/test_config_schema_v05.py
git commit -m "feat(config): add test_root/aaa_root/it_root per-workspace (ADR-16)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Generalize Allure publishing (`_publish_allure_project`)

Extract a single-project publisher and make `_generate_allure3_reports` (e2e) call it. No behavior change yet beyond the refactor (the `e2e-` prefix lands in Task 3).

**Files:**
- Modify: `src/dop/runtime/handlers.py:359-402` (`_generate_allure3_reports`)
- Test: `tests/test_runtime_handlers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime_handlers.py`:

```python
def test_publish_allure_project_generates(tmp_path):
    results = tmp_path / "proj" / ".allure-results"
    results.mkdir(parents=True)
    (results / "x-result.json").write_text("{}")
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    with patch("dop.runtime.handlers.run_command") as rc:
        handlers._publish_allure_project(
            project="aaa-demo", results_dir=results, reports_root=reports_root,
        )
    rc.assert_called_once()
    cmd = rc.call_args.args[0]
    assert cmd[:2] == ["allure", "generate"]
    assert str(reports_root / "aaa-demo") in cmd


def test_publish_allure_project_dry_run_skips(tmp_path):
    results = tmp_path / "p" / ".allure-results"
    results.mkdir(parents=True)
    (results / "r.json").write_text("{}")
    with patch("dop.runtime.handlers.run_command") as rc:
        handlers._publish_allure_project(
            project="it-demo", results_dir=results, reports_root=tmp_path / "rep",
            dry_run=True,
        )
    rc.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime_handlers.py::test_publish_allure_project_generates -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_publish_allure_project'`.

- [ ] **Step 3: Add `_publish_allure_project` and refactor `_generate_allure3_reports`**

In `src/dop/runtime/handlers.py`, add this new function immediately above `_generate_allure3_reports`:

```python
def _publish_allure_project(
    *,
    project: str,
    results_dir: Path,
    reports_root: Path,
    src: Path | None = None,
    config_file: Path | None = None,
    report_name: str | None = None,
    fresh: bool = False,
    dry_run: bool = False,
) -> None:
    """Aggregate *src* into *results_dir* and (re)generate an Allure report
    for *project* under *reports_root* (the shared :5252 UI root)."""
    import shutil
    report_dir = reports_root / project
    report_name = report_name or project

    if dry_run:
        if src is not None:
            print(f"  [dry-run] {project}: agregaria resultados de {src} em {results_dir}")
        print(f"  [dry-run] Would regenerate Allure report for {project}")
        return

    if src is not None:
        copied = _merge_allure_results(src, results_dir, fresh=fresh)
        if copied:
            mode = "fresh" if fresh else "merge"
            print(f"  {project}: {copied} resultado(s) agregados ({mode})")

    if not results_dir.is_dir() or not any(results_dir.iterdir()):
        print(f"  ⚠ {project}: sem resultados em {results_dir}; pulando geração")
        return
    try:
        if report_dir.is_dir():
            shutil.rmtree(report_dir)
        cmd = ["allure", "generate", str(results_dir),
               "--output", str(report_dir), "--report-name", report_name]
        if config_file is not None and config_file.is_file():
            cmd += ["--config", str(config_file)]
        run_command(cmd, cwd=results_dir.parent)
        print(f"  Allure: http://localhost:5252/{project}/index.html")
    except Exception as e:
        print(f"  ⚠ Allure generate failed for {project}: {e}")
```

Now replace the body of `_generate_allure3_reports` so its per-suite loop delegates to the helper. Replace the whole function with:

```python
def _generate_allure3_reports(
    e2e_root: Path,
    *,
    suites: list,
    produced: dict | None = None,
    fresh: bool = False,
    dry_run: bool = False,
) -> None:
    produced = produced or {}
    reports_root = e2e_root / "reports"
    for suite in suites:
        suite_dir = e2e_root / suite
        _publish_allure_project(
            project=f"e2e-{suite}",
            results_dir=suite_dir / ".allure-results",
            reports_root=reports_root,
            src=produced.get(suite),
            config_file=suite_dir / "allurerc.yml",
            report_name=f"e2e-{suite}",
            fresh=fresh,
            dry_run=dry_run,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runtime_handlers.py -v`
Expected: PASS (new tests + existing ones).

- [ ] **Step 5: Commit**

```bash
git add src/dop/runtime/handlers.py tests/test_runtime_handlers.py
git commit -m "refactor(allure): extract _publish_allure_project; e2e report -> e2e-<suite>

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Parameterize `dop e2e` and `dop report clean` by `test_root`

**Files:**
- Modify: `src/dop/runtime/handlers.py:256` (`handle_e2e`: `e2e_root`)
- Modify: `src/dop/runtime/handlers.py:447` (`handle_report` clean: `reports_root`)
- Test: `tests/test_runtime_handlers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime_handlers.py`:

```python
def test_handle_report_clean_uses_test_root(tmp_path):
    ws = _ws()
    ws.root = str(tmp_path)
    ws.test_root = "test/e2e"
    runs = tmp_path / "test/e2e/reports/SUOPT-1/optum-support-fe"
    for n in range(1, 8):
        (runs / f"run-{n}").mkdir(parents=True)
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=MagicMock()):
        rc = handlers.handle_report(ws, _args(report_action="clean", keep=5), dry_run=False)
    assert rc == 0
    # 7 runs, keep last 5 -> 2 oldest removed -> 5 remain (only works if clean
    # resolved the path under test/e2e/reports, i.e. honored ws.test_root)
    assert sum(1 for _ in runs.iterdir()) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime_handlers.py::test_handle_report_clean_uses_test_root -v`
Expected: FAIL — code still hardcodes `<root>/e2e/reports`, so it never finds the runs under `test/e2e/reports`; nothing is removed and 7 remain, so `assert ... == 5` fails.

- [ ] **Step 3: Apply the three `test_root` edits**

In `src/dop/runtime/handlers.py`, `handle_e2e` (~line 256):

```python
    e2e_root = _ws_root(ws) / ws.test_root
```

In `handle_report`, the `clean` branch (~line 447):

```python
        reports_root = _ws_root(ws) / ws.test_root / "reports"
```

(Leave the container-side literals `f"/e2e/reports/..."` and `f"/e2e/{suite}"` **unchanged** — they map through the compose mount.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runtime_handlers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dop/runtime/handlers.py tests/test_runtime_handlers.py
git commit -m "feat(e2e): drive e2e/report roots from ws.test_root (ADR-16)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Maven host runner + target resolution helpers

**Files:**
- Modify: `src/dop/runtime/handlers.py` (add `_run_maven`, `_resolve_test_targets` near the other helpers, e.g. after `_check_port_available`)
- Test: `tests/test_runtime_handlers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime_handlers.py`:

```python
def test_run_maven_returns_code(tmp_path):
    fake = MagicMock()
    fake.returncode = 1
    with patch("dop.runtime.handlers.subprocess.run", return_value=fake) as sr:
        code = handlers._run_maven(["mvn", "test"], cwd=tmp_path)
    assert code == 1
    sr.assert_called_once()


def test_run_maven_dry_run(tmp_path):
    with patch("dop.runtime.handlers.subprocess.run") as sr:
        code = handlers._run_maven(["mvn", "test"], cwd=tmp_path, dry_run=True)
    assert code == 0
    sr.assert_not_called()


def test_resolve_test_targets_all(tmp_path):
    for repo in ("alpha", "beta"):
        (tmp_path / repo).mkdir()
        (tmp_path / repo / "pom.xml").write_text("<project/>")
    (tmp_path / "no-pom").mkdir()
    assert handlers._resolve_test_targets(tmp_path, ["all"]) == ["alpha", "beta"]


def test_resolve_test_targets_explicit_missing(tmp_path):
    with pytest.raises(Exception):
        handlers._resolve_test_targets(tmp_path, ["ghost"])


def test_resolve_test_targets_empty_root_all(tmp_path):
    assert handlers._resolve_test_targets(tmp_path / "nope", ["all"]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime_handlers.py::test_run_maven_returns_code -v`
Expected: FAIL — `AttributeError` (`_run_maven` / `subprocess` not present on module).

- [ ] **Step 3: Add the helpers and the `subprocess` import**

At the top of `src/dop/runtime/handlers.py`, add `import subprocess` next to `import os`:

```python
import os
import subprocess
from pathlib import Path
```

Add these helpers (e.g. right after `_check_port_available`):

```python
def _run_maven(cmd: list[str], *, cwd: Path, dry_run: bool = False, logger=None) -> int:
    """Run a Maven command on the host, streaming output. Returns the exit code
    (does NOT raise on test failure, so callers can still publish Allure)."""
    from ..core.security import guard_text
    display = " ".join(cmd)
    guard_text(display)
    if dry_run:
        if logger:
            logger.info(f"WOULD RUN: {display} (cwd={cwd})")
        return 0
    if logger:
        logger.info(f"RUN: {display} (cwd={cwd})")
    result = subprocess.run(cmd, cwd=str(cwd))
    return result.returncode


def _resolve_test_targets(root: Path, targets: list[str]) -> list[str]:
    """Resolve *targets* (repo names or ``all``) to project dirs under *root*
    that contain a ``pom.xml``. Unknown explicit target -> ValidationError."""
    available = sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and (d / "pom.xml").is_file()
    ) if root.is_dir() else []
    if not targets:
        raise ValidationError("No target specified. Use: <repo|all>")
    if "all" in targets:
        return available
    resolved: list[str] = []
    for t in targets:
        if t not in available:
            raise ValidationError(
                f"No test project '{t}' in {root} "
                f"(available: {', '.join(available) or 'none'})"
            )
        resolved.append(t)
    return resolved
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runtime_handlers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dop/runtime/handlers.py tests/test_runtime_handlers.py
git commit -m "feat(test): add host Maven runner + target resolver helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `handle_aaa` / `handle_it` handlers

**Files:**
- Modify: `src/dop/runtime/handlers.py` (add `_handle_maven_layer`, `handle_aaa`, `handle_it` after `handle_e2e`)
- Test: `tests/test_runtime_handlers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime_handlers.py`:

```python
def _make_project(root, repo, *, mvnw=False):
    p = root / repo
    p.mkdir(parents=True)
    (p / "pom.xml").write_text("<project/>")
    if mvnw:
        (p / "mvnw").write_text("#!/bin/sh\n")
    return p


def test_handle_aaa_runs_mvn_test_and_publishes(tmp_path):
    ws = _ws()
    ws.root = str(tmp_path)
    ws.test_root = "test/e2e"
    ws.aaa_root = "test/aaa"
    _make_project(tmp_path / "test/aaa", "lifesupport-api")
    with patch("dop.runtime.handlers._run_maven", return_value=0) as rm, \
         patch("dop.runtime.handlers._publish_allure_project") as pub:
        rc = handlers.handle_aaa(
            ws, _args(targets=["lifesupport-api"], k=None, fresh_report=False))
    assert rc == 0
    cmd = rm.call_args.args[0]
    assert cmd[0] in ("mvn", "./mvnw")
    assert "test" in cmd
    assert pub.call_args.kwargs["project"] == "aaa-lifesupport-api"


def test_handle_aaa_uses_mvnw_when_present_and_k_filter(tmp_path):
    ws = _ws(); ws.root = str(tmp_path); ws.aaa_root = "test/aaa"; ws.test_root = "test/e2e"
    _make_project(tmp_path / "test/aaa", "demo", mvnw=True)
    with patch("dop.runtime.handlers._run_maven", return_value=0) as rm, \
         patch("dop.runtime.handlers._publish_allure_project"):
        handlers.handle_aaa(ws, _args(targets=["demo"], k="FooTest", fresh_report=False))
    cmd = rm.call_args.args[0]
    assert cmd[0] == "./mvnw"
    assert "-Dtest=FooTest" in cmd


def test_handle_it_runs_pit_verify(tmp_path):
    ws = _ws(); ws.root = str(tmp_path); ws.it_root = "test/it"; ws.test_root = "test/e2e"
    _make_project(tmp_path / "test/it", "optum-support-be")
    with patch("dop.runtime.handlers._run_maven", return_value=0) as rm, \
         patch("dop.runtime.handlers._publish_allure_project") as pub:
        rc = handlers.handle_it(
            ws, _args(targets=["optum-support-be"], k=None, fresh_report=False))
    assert rc == 0
    cmd = rm.call_args.args[0]
    assert "-Pit" in cmd and "verify" in cmd
    assert pub.call_args.kwargs["project"] == "it-optum-support-be"


def test_handle_aaa_red_when_mvn_fails(tmp_path):
    ws = _ws(); ws.root = str(tmp_path); ws.aaa_root = "test/aaa"; ws.test_root = "test/e2e"
    _make_project(tmp_path / "test/aaa", "demo")
    with patch("dop.runtime.handlers._run_maven", return_value=1), \
         patch("dop.runtime.handlers._publish_allure_project"):
        rc = handlers.handle_aaa(ws, _args(targets=["demo"], k=None, fresh_report=False))
    assert rc == 1


def test_handle_aaa_all_empty_root_is_noop_green(tmp_path):
    ws = _ws(); ws.root = str(tmp_path); ws.aaa_root = "test/aaa"; ws.test_root = "test/e2e"
    with patch("dop.runtime.handlers._run_maven") as rm, \
         patch("dop.runtime.handlers._publish_allure_project"):
        rc = handlers.handle_aaa(ws, _args(targets=["all"], k=None, fresh_report=False))
    assert rc == 0
    rm.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime_handlers.py::test_handle_aaa_runs_mvn_test_and_publishes -v`
Expected: FAIL — `AttributeError: ... has no attribute 'handle_aaa'`.

- [ ] **Step 3: Implement the handlers**

In `src/dop/runtime/handlers.py`, add after `handle_e2e` (before `_generate_allure3_reports`):

```python
def _handle_maven_layer(
    ws: WorkspaceConfig, args, *, layer: str, root: Path,
    maven_args: list[str], filter_prop: str, dry_run: bool = False, logger=None,
) -> int:
    """Shared driver for aaa/it: run host Maven per project, publish Allure."""
    repos = _resolve_test_targets(root, getattr(args, "targets", []) or [])
    if not repos:
        print(f"No {layer} projects found in {root}")
        return 0

    k = getattr(args, "k", None)
    fresh = getattr(args, "fresh_report", False)
    reports_root = _ws_root(ws) / ws.test_root / "reports"
    all_green = True

    for repo in repos:
        project_dir = root / repo
        mvn = "./mvnw" if (project_dir / "mvnw").is_file() else "mvn"
        cmd = [mvn] + list(maven_args)
        if k:
            cmd.append(f"{filter_prop}={k}")
        if logger:
            logger.info(f"{layer}: {repo}")
        code = _run_maven(cmd, cwd=project_dir, dry_run=dry_run, logger=logger)
        if code == 0:
            print(f"  ✔ {repo}: green")
        else:
            print(f"  ✘ {repo}: red")
            all_green = False
        _publish_allure_project(
            project=f"{layer}-{repo}",
            results_dir=project_dir / ".allure-results",
            reports_root=reports_root,
            src=project_dir / "target" / "allure-results",
            fresh=fresh, dry_run=dry_run, logger=logger,
        )

    print(f"\nResult: {'green' if all_green else 'red'}")
    return 0 if all_green else 1


def handle_aaa(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    return _handle_maven_layer(
        ws, args, layer="aaa", root=_ws_root(ws) / ws.aaa_root,
        maven_args=["test"], filter_prop="-Dtest", dry_run=dry_run, logger=logger,
    )


def handle_it(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    return _handle_maven_layer(
        ws, args, layer="it", root=_ws_root(ws) / ws.it_root,
        maven_args=["-Pit", "verify"], filter_prop="-Dit.test", dry_run=dry_run, logger=logger,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runtime_handlers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dop/runtime/handlers.py tests/test_runtime_handlers.py
git commit -m "feat(test): add handle_aaa/handle_it (host Maven, ADR-16)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: CLI subparsers `aaa` and `it`

**Files:**
- Modify: `src/dop/cli.py:692-703` (import the two handlers) and `src/dop/cli.py:744-756` (add subparsers after `e2e`)
- Test: `tests/test_cli_parsing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_parsing.py` (inside `TestCLIParsing`):

```python
    def test_aaa_parser(self):
        args = cli.build_parser().parse_args(["aaa", "lifesupport-api", "-k", "FooTest"])
        self.assertEqual(args.command, "aaa")
        self.assertEqual(args.targets, ["lifesupport-api"])
        self.assertEqual(args.k, "FooTest")
        self.assertFalse(args.fresh_report)

    def test_aaa_all(self):
        args = cli.build_parser().parse_args(["aaa", "all", "--fresh-report"])
        self.assertEqual(args.targets, ["all"])
        self.assertTrue(args.fresh_report)

    def test_it_parser(self):
        args = cli.build_parser().parse_args(["it", "optum-support-be"])
        self.assertEqual(args.command, "it")
        self.assertEqual(args.targets, ["optum-support-be"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_parsing.py::TestCLIParsing::test_aaa_parser -v`
Expected: FAIL — `SystemExit: 2` (argparse: invalid choice 'aaa').

- [ ] **Step 3: Add the handler imports**

In `src/dop/cli.py`, extend the runtime import block (lines ~692-703):

```python
        handle_e2e as _rt_e2e,
        handle_aaa as _rt_aaa,
        handle_it as _rt_it,
        handle_codegen as _rt_codegen,
```

- [ ] **Step 4: Add the subparsers**

In `src/dop/cli.py`, immediately after the `p_e2e.set_defaults(...)` line (~756), add:

```python
    # ── AAA (unit, Java) ──
    p_aaa = sub.add_parser("aaa", help="Run unit AAA tests (Maven, host)")
    p_aaa.add_argument("targets", nargs="*", help="Repo name(s) or 'all'")
    p_aaa.add_argument("-k", dest="k", default=None, help="Maven -Dtest filter")
    p_aaa.add_argument("--fresh-report", action="store_true", dest="fresh_report",
                       help="Zera o .allure-results do projeto antes de agregar")
    p_aaa.set_defaults(func=_make_rt_func(_rt_aaa), command="aaa")

    # ── IT (integração, Testcontainers) ──
    p_it = sub.add_parser("it", help="Run integration tests (Maven failsafe, host)")
    p_it.add_argument("targets", nargs="*", help="Repo name(s) or 'all'")
    p_it.add_argument("-k", dest="k", default=None, help="Maven -Dit.test filter")
    p_it.add_argument("--fresh-report", action="store_true", dest="fresh_report",
                      help="Zera o .allure-results do projeto antes de agregar")
    p_it.set_defaults(func=_make_rt_func(_rt_it), command="it")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_parsing.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite + commit**

Run: `python -m pytest tests/ -v`
Expected: PASS (entire suite green).

```bash
git add src/dop/cli.py tests/test_cli_parsing.py
git commit -m "feat(cli): add 'dop aaa' and 'dop it' subcommands (ADR-16)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Workspace migration doc (delivered, not applied)

**Files:**
- Create: `docs/workspace-migration-adr16.md`

- [ ] **Step 1: Write the doc**

Create `docs/workspace-migration-adr16.md`:

```markdown
# ADR-16 — Mudanças no workspace Optum (aplicar fora do dop-cli)

Estas mudanças vivem no workspace `/opt/wks/csptech/optum`, não no `dop-cli`.
O `dop` 0.6+ já lê `test_root`/`aaa_root`/`it_root` do config.

## 1. Migrar e2e -> test/e2e
```bash
cd /opt/wks/csptech/optum
mkdir -p test
mv e2e test/e2e
```
(Nada é git; preserva `reports/` e histórico Allure.)

## 2. docker-compose.yml — mounts (manter caminho interno /e2e)
- `playwright-env`: `./e2e:/e2e` -> `./test/e2e:/e2e`
- `allure`: `./e2e/reports:/app/projects` -> `./test/e2e/reports:/app/projects`
- `allure-ui`: `./e2e/reports:/usr/share/nginx/html:ro` -> `./test/e2e/reports:/usr/share/nginx/html:ro`

## 3. ~/.config/dop/config.toml — [workspaces.optum]
```toml
test_root = "test/e2e"
# aaa_root/it_root usam o default "test/aaa" / "test/it"
```

## 4. Renomear reports e2e existentes -> e2e-<suite>
O `dop e2e` agora gera `reports/e2e-<suite>`. Para preservar URLs dos reports já
gerados, renomeie os dirs de report (não os de staging `<jira>/<suite>/run-N`):
```bash
cd /opt/wks/csptech/optum/test/e2e/reports
for d in optum-support-fe providers-front-end; do
  [ -d "$d" ] && [ ! -d "e2e-$d" ] && mv "$d" "e2e-$d"
done
```
(Atualizar o `index.html`/links do :5252 conforme o naming `e2e-`/`aaa-`/`it-`.)

## 5. Scaffolding dos poms piloto (ADR-16)
- `test/aaa/lifesupport-api/pom.xml` (SUOPT-3184): parent = pom do app via
  relativePath `../../../repos/lifesupport-api/pom.xml`; build-helper aponta
  `../../../repos/lifesupport-api/src/main/java`; surefire + allure-junit5 +
  aspectjweaver; testes em `src/test/java`.
- `test/it/optum-support-be/pom.xml` (SUOPT-3188): mesmo esquema + failsafe
  (`-Pit verify`), Testcontainers (MySQL/Mongo efêmeros), Flyway no startup.

## 6. Docs/memória
- `CLAUDE.md` / `agent-rules.md`: tabela de ferramentas (+`dop aaa`/`dop it`),
  paths `test/`, gate das três camadas.
```

- [ ] **Step 2: Commit**

```bash
git add docs/workspace-migration-adr16.md
git commit -m "docs: workspace-side ADR-16 migration steps (compose, config, poms)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Run the full suite: `python -m pytest tests/ -v` — expect all green.
- [ ] Smoke the parser: `python -m dop.cli aaa --help` and `python -m dop.cli it --help` show the new subcommands.
- [ ] Confirm `git log --oneline` shows the per-task commits.

---

## Self-review notes (for the author)

- **Spec coverage:** Config (Task 1) · e2e `test_root` + `e2e-<suite>` (Tasks 2–3) · multi-project Allure (Task 2) · `handle_aaa`/`handle_it` host Maven (Tasks 4–5) · CLI (Task 6) · workspace doc (Task 7). All six acceptance criteria map to tasks.
- **Container literals:** untouched — verified the only edits are the host-side `_ws_root(ws) / "e2e"` occurrences; `/e2e/...` literals left as-is.
- **No strikes for aaa/it:** `_handle_maven_layer` returns `0/1` directly; no strike state touched.
- **Type consistency:** `_publish_allure_project`, `_run_maven`, `_resolve_test_targets`, `_handle_maven_layer`, `handle_aaa`, `handle_it` signatures are identical across the tasks that define and call them.
```
