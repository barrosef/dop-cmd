# src/dop/runtime/handlers.py
from __future__ import annotations
import os
import subprocess
from pathlib import Path

from ..config.schema import WorkspaceConfig
from ..core.errors import ValidationError
from ..core.process import run_command
from .resolve import expand_apps, infer_urls
from .compose import write_env_runtime
from .orchestrator import build_runtime_provider


# --------------------------------------------------------------------------
# Path / env helpers
# --------------------------------------------------------------------------
def _ws_root(ws: WorkspaceConfig) -> Path:
    return Path(ws.root)


def _dc(ws: WorkspaceConfig):
    dc = ws.runtime.docker_compose
    if dc is None:
        raise ValidationError("Missing [runtime.docker_compose] config.")
    return dc


def _primary_env_file(ws: WorkspaceConfig) -> Path:
    return _ws_root(ws) / _dc(ws).env_files[0]


def _env_runtime_file(ws: WorkspaceConfig) -> Path:
    return _primary_env_file(ws).parent / ".env.runtime"


def _validate_env_file(ws: WorkspaceConfig) -> None:
    ef = _primary_env_file(ws)
    if not ef.exists():
        example = ef.parent / ".env.example"
        raise ValidationError(
            f"Environment file not found: {ef}\n"
            f"Copy {example} -> {ef} and fill in credentials."
        )


# --------------------------------------------------------------------------
# E2E suite URL helpers (derived from config)
# --------------------------------------------------------------------------
def _fe_for_suite(ws: WorkspaceConfig, suite: str):
    return next((a for a in ws.runtime.apps.values() if a.e2e_suite == suite), None)


def _suite_base_url(ws: WorkspaceConfig, suite: str) -> str:
    fe = _fe_for_suite(ws, suite)
    return f"http://localhost:{fe.port if fe else 5173}"


def _suite_api_url(ws: WorkspaceConfig, suite: str) -> str:
    fe = _fe_for_suite(ws, suite)
    if fe:
        for dep in fe.depends_on:
            be = ws.runtime.apps.get(dep)
            if be and be.role == "backend":
                return f"http://localhost:{be.port}"
    return "http://localhost:8080"


# --------------------------------------------------------------------------
# Front-end build (host)
# --------------------------------------------------------------------------
def _build_frontends(ws: WorkspaceConfig, requested: set[str], *, dry_run: bool = False, logger=None) -> None:
    for app_name in sorted(requested):
        app = ws.runtime.apps.get(app_name)
        if not app or app.build is None:
            continue
        app_path = _ws_root(ws) / app.build.dir
        artifact_path = app_path / app.build.artifact
        if not (app_path / "node_modules").is_dir():
            print(f"  ⚠ {app_name}: node_modules missing, running npm install first...")
            run_command(["npm", "install"], cwd=app_path, dry_run=dry_run, logger=logger)
        print(f"  Building {app_name}...")
        run_command(app.build.command.split(), cwd=app_path, dry_run=dry_run, logger=logger)
        if not dry_run and artifact_path.is_dir():
            print(f"  ✔ {app_name}: built → {artifact_path}")
        elif not dry_run:
            raise ValidationError(
                f"{app_name}: build succeeded but {app.build.artifact}/ not found at {artifact_path}"
            )


def _check_port_available(port: int) -> None:
    result = subprocess.run(["lsof", "-i", f":{port}", "-t"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        raise ValidationError(f"Port {port} already in use (PIDs: {result.stdout.strip()})")



def _resolve_test_targets(root: Path, targets: list[str]) -> list[str]:
    """Resolve *targets* (repo names or ``all``) to project dirs under *root*
    that contain a ``pom.xml``. If ``all`` is among the targets it takes
    precedence and every available project is returned. Unknown explicit
    target -> ValidationError."""
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


# --------------------------------------------------------------------------
# Lifecycle handlers
# --------------------------------------------------------------------------
def handle_start(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    _validate_env_file(ws)
    requested = expand_apps(ws, args.apps, no_deps=getattr(args, "no_deps", False))

    for app_name in requested:
        app = ws.runtime.apps[app_name]
        _check_port_available(app.port)
        if app.debug_port:
            _check_port_available(app.debug_port)

    _build_frontends(ws, requested, dry_run=dry_run, logger=logger)

    env = infer_urls(ws, requested)
    write_env_runtime(_env_runtime_file(ws), env)

    if logger:
        logger.info(f"Starting: {', '.join(sorted(requested))}")
    provider.up(sorted(requested), wait=True, dry_run=dry_run, logger=logger)

    print(f"\nApps up: {', '.join(sorted(requested))}")
    for k, v in env.items():
        print(f"  {k} -> {v}")
    return 0


def handle_stop(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    apps: list[str] = []
    if getattr(args, "apps", None):
        apps = sorted(expand_apps(ws, args.apps, no_deps=True))
    provider.stop(apps, dry_run=dry_run, logger=logger)
    print(f"Stopped: {', '.join(apps) if apps else 'all'}")
    return 0


def handle_log(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    apps = sorted(expand_apps(ws, args.apps, no_deps=True))
    provider.logs(
        apps,
        follow=getattr(args, "follow", True),
        tail=getattr(args, "tail", None),
        since=getattr(args, "since", None),
        dry_run=dry_run, logger=logger,
    )
    return 0


def handle_rebuild(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    requested = expand_apps(ws, args.apps, no_deps=True)
    fe_apps = {a for a in requested if ws.runtime.apps[a].build is not None}
    if not fe_apps:
        raise ValidationError("No frontend apps to rebuild (apps without a [build] section).")
    _build_frontends(ws, fe_apps, dry_run=dry_run, logger=logger)
    provider.restart(sorted(fe_apps), dry_run=dry_run, logger=logger)
    print(f"✔ Rebuilt and restarted: {', '.join(sorted(fe_apps))}")
    return 0


def handle_status(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    statuses = provider.status(dry_run=dry_run, logger=logger)
    if dry_run:
        return 0

    app_names = set(ws.runtime.apps)
    print("Apps:")
    for s in statuses:
        if s.name not in app_names:
            continue
        if s.up:
            label = (s.state or "up") + (f" ({s.health})" if s.health else "")
            print(f"  ✔ {s.name:<25} {label:<15} :{s.port}")
        else:
            print(f"  ✗ {s.name:<25} down")

    print("\nInfra:")
    for s in statuses:
        if s.name in app_names:
            continue
        mark = "✔" if s.up else "✗"
        label = (s.state or ("up" if s.up else "down"))
        print(f"  {mark} {s.name:<25} {label}")
    return 0


def handle_restart(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    handle_stop(ws, args, dry_run=dry_run, logger=logger)
    handle_start(ws, args, dry_run=dry_run, logger=logger)
    return 0


# --------------------------------------------------------------------------
# E2E / codegen / report / clean
# --------------------------------------------------------------------------
def _merge_allure_results(src_results: Path, dest_results: Path, *, fresh: bool = False) -> int:
    """Copia os arquivos de resultado Allure de *src_results* para *dest_results*.

    Faz a ponte entre o que o pytest grava (``reports/<jira>/<suite>/run-N/results``)
    e o agregado da suíte (``<suite>/.allure-results``) que o ``allure generate`` lê.

    Com ``fresh=True``, limpa o destino antes (contadores só da run atual). É robusto a
    arquivos de origem criados como root pelo container (apenas leitura/cópia). Retorna
    a quantidade de arquivos copiados.
    """
    import shutil

    if src_results.resolve() == dest_results.resolve():
        return 0
    if fresh and dest_results.is_dir():
        for f in dest_results.iterdir():
            if f.is_file():
                f.unlink()
    dest_results.mkdir(parents=True, exist_ok=True)
    if not src_results.is_dir():
        return 0
    count = 0
    for f in src_results.iterdir():
        if not f.is_file():
            continue
        try:
            shutil.copy2(f, dest_results / f.name)
        except OSError:
            shutil.copyfile(f, dest_results / f.name)
        count += 1
    return count


def _x11_grant(logger=None) -> None:
    """Concede acesso X11 ao container (root) via xhost — best-effort (modo headed)."""
    import subprocess
    try:
        subprocess.run(["xhost", "+SI:localuser:root"], capture_output=True, text=True, timeout=5)
        if logger:
            logger.info("X11: acesso concedido ao container (xhost +SI:localuser:root)")
    except Exception as exc:
        if logger:
            logger.warn(f"X11: falha ao conceder acesso via xhost ({exc}); a janela headed pode não aparecer.")


def _x11_revoke(logger=None) -> None:
    """Revoga o acesso X11 concedido por _x11_grant — best-effort."""
    import subprocess
    try:
        subprocess.run(["xhost", "-SI:localuser:root"], capture_output=True, text=True, timeout=5)
        if logger:
            logger.info("X11: acesso revogado (xhost -SI:localuser:root)")
    except Exception:
        pass


def handle_e2e(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    from .e2e import resolve_e2e_target, find_suites_for_jira, next_run_number, suite_order

    provider = build_runtime_provider(ws)
    e2e_root = _ws_root(ws) / ws.test_root
    # NB: host root follows ws.test_root, but the compose mount maps
    # ./<test_root>:/e2e, so container-side paths below stay literal "/e2e/...".
    reports_root = e2e_root / "reports"
    order = suite_order(ws)
    known_suites = [
        d.name for d in e2e_root.iterdir()
        if d.is_dir() and (d / "tests").is_dir()
    ] if e2e_root.is_dir() else []

    targets = getattr(args, "targets", []) or []
    if not targets:
        raise ValidationError("No e2e target specified. Use: dop e2e <suite|JIRA|file>")

    jira_key = None
    suites_to_run: list[str] = []
    pytest_filter = getattr(args, "k", None)
    extra_pytest = list(getattr(args, "extra", []) or [])

    for token in targets:
        resolved = resolve_e2e_target(token, known_suites=known_suites)
        if resolved["kind"] == "suite":
            suites_to_run.extend(resolved["suites"])
        elif resolved["kind"] == "jira":
            jira_key = token.upper()
            jira_filter = resolved["filter"]
            found = find_suites_for_jira(jira_filter, e2e_root=e2e_root, suite_order=order)
            if not found:
                raise ValidationError(f"No tests found for {jira_key} in suites: {', '.join(order)}")
            suites_to_run.extend(found)
            if not pytest_filter:
                pytest_filter = jira_filter
        elif resolved["kind"] == "file":
            extra_pytest.append(resolved.get("path", token))

    # Deduplicate, preserve config suite order
    seen: set[str] = set()
    ordered: list[str] = []
    for s in order:
        if s in suites_to_run and s not in seen:
            ordered.append(s)
            seen.add(s)
    for s in suites_to_run:
        if s not in seen:
            ordered.append(s)
            seen.add(s)

    headed = getattr(args, "headed", False)
    fresh = getattr(args, "fresh_report", False)
    all_green = True
    produced: dict[str, Path] = {}

    if headed and not dry_run:
        _x11_grant(logger=logger)
    try:
        for suite in ordered:
            pytest_args: list[str] = []
            if pytest_filter:
                pytest_args += ["-k", pytest_filter]
            extra_env = {
                "E2E_BASE_URL": _suite_base_url(ws, suite),
                "E2E_API_URL": _suite_api_url(ws, suite),
            }
            if headed:
                pytest_args.append("--headed")
                extra_env["E2E_SHARED_CONTEXT"] = "1"
                extra_env["DISPLAY"] = os.environ.get("DISPLAY", ":0")
            pytest_args += extra_pytest

            report_jira = jira_key or "manual"
            report_dir = reports_root / report_jira / suite
            run_n = next_run_number(report_dir)
            # P3: o host pré-cria run-N/results. Assim os DIRETÓRIOS pertencem ao host;
            # o container (root) escreve os arquivos dentro, e o host consegue copiar e
            # limpar depois (delete depende de permissão no diretório-pai, não na posse
            # do arquivo) — sem precisar rodar o container como --user.
            host_results = report_dir / f"run-{run_n}" / "results"
            if not dry_run:
                host_results.mkdir(parents=True, exist_ok=True)
            produced[suite] = host_results
            results_path = f"/e2e/reports/{report_jira}/{suite}/run-{run_n}/results"
            pytest_args += [f"--alluredir={results_path}"]

            if logger:
                logger.info(f"E2E suite: {suite} (run-{run_n})")
            code = provider.run_ephemeral(
                [f"/e2e/{suite}"] + pytest_args, env=extra_env, dry_run=dry_run, logger=logger,
            )
            if code == 0:
                print(f"  ✔ {suite}: green (run-{run_n})")
            else:
                print(f"  ✘ {suite}: red (run-{run_n})")
                all_green = False

        print(f"\nResult: {'green' if all_green else 'red'}")
        # P1: agrega os resultados da run no .allure-results da suíte e publica.
        _generate_allure3_reports(
            e2e_root, suites=ordered, produced=produced, fresh=fresh, dry_run=dry_run,
            logger=logger,
        )
    finally:
        if headed and not dry_run:
            _x11_revoke(logger=logger)
    return 0 if all_green else 1


def _handle_maven_layer(
    ws: WorkspaceConfig, args, *, layer: str, root_rel: str,
    maven_args: list[str], filter_prop: str, dry_run: bool = False, logger=None,
) -> int:
    """Shared driver for aaa/it: run Maven in the java_runner container, publish Allure."""
    root = _ws_root(ws) / root_rel
    repos = _resolve_test_targets(root, getattr(args, "targets", []) or [])
    if not repos:
        print(f"No {layer} projects found in {root}")
        return 0

    dc = _dc(ws)
    runner = dc.java_runner
    if runner is None:
        raise ValidationError(
            "No java_runner configured in "
            "[runtime.docker_compose.java_runner] (needed for dop aaa/it)."
        )

    provider = build_runtime_provider(ws)
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
        workdir = f"/workspace/{root_rel}/{repo}"
        if logger:
            logger.info(f"{layer}: {repo} (container {runner.service}, workdir {workdir})")
        code = provider.run_service(
            runner.service, cmd, profile=runner.profile, workdir=workdir,
            dry_run=dry_run, logger=logger,
        )
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
        ws, args, layer="aaa", root_rel=ws.aaa_root,
        maven_args=["test"], filter_prop="-Dtest", dry_run=dry_run, logger=logger,
    )


def handle_it(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    return _handle_maven_layer(
        ws, args, layer="it", root_rel=ws.it_root,
        maven_args=["-Pit", "verify"], filter_prop="-Dit.test", dry_run=dry_run, logger=logger,
    )


def _suite_run_results(reports_root: Path, suite: str) -> list[Path]:
    """Todos os diretórios ``reports/<jira>/<suite>/run-N/results`` existentes da suíte.

    Os ``run-N`` são a fonte de verdade dos resultados e2e; o agregado
    ``<suite>/.allure-results`` é estado derivado e reconstruível a partir deles.
    Ordenado por (jira, N) para determinismo.
    """
    if not reports_root.is_dir():
        return []
    found: list[tuple[str, int, Path]] = []
    for jira_dir in reports_root.iterdir():
        if not jira_dir.is_dir() or jira_dir.name.startswith("."):
            continue
        suite_dir = jira_dir / suite
        if not suite_dir.is_dir():
            continue
        for run_dir in suite_dir.iterdir():
            parts = run_dir.name.split("-")
            if (run_dir.is_dir() and len(parts) == 2
                    and parts[0] == "run" and parts[1].isdigit()):
                results = run_dir / "results"
                if results.is_dir():
                    found.append((jira_dir.name, int(parts[1]), results))
    return [p for _, _, p in sorted(found, key=lambda t: (t[0], t[1]))]


def _publish_allure_project(
    *,
    project: str,
    results_dir: Path,
    reports_root: Path,
    src: "Path | list[Path] | None" = None,
    config_file: Path | None = None,
    report_name: str | None = None,
    fresh: bool = False,
    dry_run: bool = False,
    logger=None,
) -> None:
    """Aggregate *src* into *results_dir* and (re)generate an Allure report
    for *project* under *reports_root* (the shared :5252 UI root)."""
    import shutil
    report_dir = reports_root / project
    report_name = report_name or project
    sources: list[Path] = [] if src is None else ([src] if isinstance(src, Path) else list(src))

    if dry_run:
        for s in sources:
            print(f"  [dry-run] {project}: agregaria resultados de {s} em {results_dir}")
        print(f"  [dry-run] Would regenerate Allure report for {project}")
        return

    copied = 0
    for i, s in enumerate(sources):
        copied += _merge_allure_results(s, results_dir, fresh=fresh and i == 0)
    if copied:
        mode = "fresh" if fresh else "merge"
        print(f"  {project}: {copied} resultado(s) agregados ({mode}, {len(sources)} run(s))")

    if not results_dir.is_dir() or not any(results_dir.iterdir()):
        print(f"  ⚠ {project}: sem resultados em {results_dir}; pulando geração")
        return
    # Gera num dir temporário e só troca o report servido depois do sucesso —
    # uma falha no `allure generate` não pode derrubar o report que já está no ar.
    tmp_dir = reports_root / f".{project}.tmp-gen"
    try:
        if tmp_dir.is_dir():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)
        cmd = ["allure", "generate", str(results_dir),
               "--output", str(tmp_dir), "--report-name", report_name]
        if config_file is not None and config_file.is_file():
            cmd += ["--config", str(config_file)]
        run_command(cmd, cwd=results_dir.parent, dry_run=dry_run, logger=logger)
        if report_dir.is_dir():
            shutil.rmtree(report_dir)
        tmp_dir.rename(report_dir)
        print(f"  Allure: http://localhost:5252/{project}/index.html")
    except Exception as e:
        print(f"  ⚠ Allure generate failed for {project}: {e}")
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _generate_allure3_reports(
    e2e_root: Path,
    *,
    suites: list,
    produced: dict | None = None,
    fresh: bool = False,
    dry_run: bool = False,
    logger=None,
) -> None:
    produced = produced or {}
    reports_root = e2e_root / "reports"
    for suite in suites:
        suite_dir = e2e_root / suite
        # Agrega TODAS as runs (não só a atual): o acumulador vira estado derivado
        # e um wipe acidental dele é reparado na publicação seguinte.
        sources = _suite_run_results(reports_root, suite)
        cur = produced.get(suite)
        if cur is not None and cur not in sources:
            sources.append(cur)
        _publish_allure_project(
            project=f"e2e-{suite}",
            results_dir=suite_dir / ".allure-results",
            reports_root=reports_root,
            src=sources,
            config_file=suite_dir / "allurerc.yml",
            report_name=f"e2e-{suite}",
            fresh=fresh,
            dry_run=dry_run,
            logger=logger,
        )


def handle_codegen(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    from ..core.state import now_iso
    provider = build_runtime_provider(ws)
    suite = args.suite
    url = getattr(args, "url", None) or _suite_base_url(ws, suite)
    out = getattr(args, "out", None) or f"tests/recordings/recording_{now_iso()[:10]}.py"
    print(f"Starting codegen for suite '{suite}' → {url}")
    if not dry_run:
        # codegen é inerentemente headed; o processo é substituído (execvp), então não
        # há como revogar automaticamente depois — instruímos o cleanup manual.
        _x11_grant(logger=logger)
        print("  (X11 liberado p/ o container; ao terminar, revogue: xhost -SI:localuser:root)")
    provider.run_ephemeral(
        ["playwright", "codegen", url, "-o", f"/e2e/{suite}/{out}"],
        env={"DISPLAY": os.environ.get("DISPLAY", ":0")},
        dry_run=dry_run, logger=logger, exec_replace=True,
    )
    return 0  # pragma: no cover


def handle_report(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    action = getattr(args, "report_action", None)
    if not action:
        raise ValidationError("Use: dop report serve|open|clean")

    if action == "serve":
        provider = build_runtime_provider(ws)
        provider.up([], wait=True, dry_run=dry_run, logger=logger)  # infra only (mongodb+allure)
        print("✔ Allure serving at http://localhost:5050")
        return 0

    if action == "open":
        import webbrowser
        suite = getattr(args, "suite", None) or ""
        jira = getattr(args, "jira", None) or ""
        url = f"http://localhost:5050/projects/{jira}/{suite}" if jira else "http://localhost:5050"
        webbrowser.open(url)
        return 0

    if action == "clean":
        import shutil
        keep = getattr(args, "keep", 5)
        reports_root = _ws_root(ws) / ws.test_root / "reports"
        cleaned = 0
        if reports_root.is_dir():
            for jira_dir in reports_root.iterdir():
                if not jira_dir.is_dir() or jira_dir.name.startswith(("_", ".")):
                    continue
                for suite_dir in jira_dir.iterdir():
                    if not suite_dir.is_dir():
                        continue
                    runs = sorted(
                        [d for d in suite_dir.iterdir()
                         if d.is_dir() and d.name.startswith("run-") and d.name.split("-")[1].isdigit()],
                        key=lambda d: int(d.name.split("-")[1]),
                    )
                    for r in (runs[:-keep] if len(runs) > keep else []):
                        if not dry_run:
                            shutil.rmtree(r)
                        cleaned += 1
        print(f"✔ Cleaned {cleaned} old runs (keeping last {keep} per suite)")
        return 0
    return 1


def handle_clean(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    categories: list[str] = []
    if getattr(args, "all", False):
        categories = ["all"]
    else:
        if getattr(args, "m2", False):
            categories.append("maven")
        if getattr(args, "node_modules", False):
            categories.append("node_modules")
        if getattr(args, "allure", False):
            categories.append("allure")
    if not categories:
        raise ValidationError("Specify --m2, --node-modules, --allure, or --all")
    removed = provider.clean(categories, dry_run=dry_run, logger=logger)
    print(f"✔ Cleaned {len(removed)} volumes")
    return 0
