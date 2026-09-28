"""`test aaa`, `test it`, `test e2e`, `report` (B21-B23, B29, B33, B35)."""

from __future__ import annotations

from pathlib import Path

from dop.outcome import Status
from dop.verbs import registry

from .conftest import make_ctx, worktree_dir

VERBS = {name: mod.VERB for name, mod in registry().items()}


def _run(ctx, verb_name):
    return registry()[verb_name].run(ctx)


def _one(summary):
    assert len(summary.results) == 1
    return summary.results[0]


def _log(fake, tool):
    return fake.log(tool)


def _runs(fake, tool, argv0):
    return [a for a in fake.log(tool) if a and a[0] == argv0]


# ---------------------------------------------------------------------------------------------
# fixtures: a test project / suite living under the workspace's own test tree


def _aaa_project(root: Path, repo: str, *, with_results: bool = False) -> Path:
    proj = root / "test" / "aaa" / repo
    proj.mkdir(parents=True)
    (proj / "pom.xml").write_text("<project/>")
    if with_results:
        results = proj / "target" / "allure-results"
        results.mkdir(parents=True)
        (results / "one.json").write_text("{}")
    return proj


def _it_project(root: Path, repo: str) -> Path:
    proj = root / "test" / "it" / repo
    proj.mkdir(parents=True)
    (proj / "pom.xml").write_text("<project/>")
    return proj


def _e2e_suite(root: Path, suite: str, *, env_keys=("BASE_URL",)) -> Path:
    d = root / "test" / "e2e" / suite
    d.mkdir(parents=True)
    body = "\n".join(f'import os\nos.environ["{k}"]' for k in env_keys)
    (d / "test_smoke.py").write_text(body)
    return d


# ---------------------------------------------------------------------------------------------
# test aaa / test it (B22, B29, B35)


def test_aaa_no_project_is_skipped(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"))
    summary = _run(make_ctx(ws, tasks=["K-1"], repos=["api"]), "test aaa")
    r = _one(summary)
    assert r.status is Status.SKIPPED
    assert "no aaa test project" in r.reason


def test_aaa_dry_run_touches_nothing(ws, root, fake):
    _aaa_project(root, "api")
    fake.present("K-1")
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"))
    summary = _run(make_ctx(ws, tasks=["K-1"], repos=["api"], dry_run=True), "test aaa")
    r = _one(summary)
    assert r.status is Status.PLANNED
    assert _log(fake, "docker") == []
    assert not (ws.state_dir / "runs").exists()


def test_aaa_happy_path_stages_mounts_and_publishes(ws, root, fake):
    _aaa_project(root, "api", with_results=True)
    fake.present("K-1")
    wt = worktree_dir(root, "api", "K-1")
    fake.worktrees(root / "repos/api", (wt, "K-1"))
    # The fake allure container does not really write a site/: stand one up so the sync leg
    # (node.sync of the *generated* site) has something real to copy and can be asserted on.
    site = ws.paths.reports / "K-1" / "aaa-api" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<html/>")
    summary = _run(make_ctx(ws, tasks=["K-1"], repos=["api"]), "test aaa")
    r = _one(summary)
    assert r.status is Status.DONE

    staged = ws.state_dir / "runs" / "K-1" / "aaa-api"
    assert (staged / "pom.xml").is_file()

    runs = _runs(fake, "docker", "run")
    assert len(runs) == 2  # mvn test, then allure generate (B23)
    argv = runs[0]
    assert "maven:3" in argv
    assert f"{staged}:/w/test/aaa/api" in argv
    assert f"{wt}:/w/repos/api:ro" in argv
    assert argv[-3:] == ["mvn", "-q", "test"]
    assert "--network" not in argv  # aaa does not need Testcontainers (B35 is `it` only)

    results_dir = ws.paths.reports / "K-1" / "aaa-api" / "run-1" / "results"
    assert (results_dir / "one.json").is_file()

    # regenerate_report ran allure and synced the site into the node (B23)
    assert any("allure:2" in a for a in runs)
    cp_calls = [a for a in _log(fake, "docker") if a and a[0] == "cp"]
    assert any("site" in a[1] for a in cp_calls)
    assert r.reason == ""  # no warning: publish succeeded


def test_aaa_failure_is_reported_without_stopping_the_unit(ws, root, fake):
    _aaa_project(root, "api")
    fake.present("K-1")
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"))
    (fake.state / "docker_fail").write_text("run")
    summary = _run(make_ctx(ws, tasks=["K-1"], repos=["api"]), "test aaa")
    r = _one(summary)
    assert r.status is Status.FAILED
    assert r.reason.startswith("mvn aaa exit 1")


def test_it_uses_host_network_and_docker_socket(ws, root, fake):
    _it_project(root, "api")
    fake.present("K-1")
    wt = worktree_dir(root, "api", "K-1")
    fake.worktrees(root / "repos/api", (wt, "K-1"))
    summary = _run(make_ctx(ws, tasks=["K-1"], repos=["api"]), "test it")
    r = _one(summary)
    assert r.status is Status.DONE
    assert "tests the test project's own code" in r.reason

    runs = _runs(fake, "docker", "run")
    assert len(runs) == 1
    argv = runs[0]
    assert "--network" in argv and argv[argv.index("--network") + 1] == "host"
    assert "/var/run/docker.sock:/var/run/docker.sock" in argv
    assert argv[-3:] == ["mvn", "-q", "verify"]


def test_repo_filter_still_applies_to_test_aaa(ws, root, fake):
    _aaa_project(root, "api")
    _aaa_project(root, "be")
    fake.present("K-1")
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"))
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    summary = _run(make_ctx(ws, tasks=["K-1"], repos=["be"]), "test aaa")
    assert [r.unit.repo for r in summary.results] == ["be"]


# ---------------------------------------------------------------------------------------------
# test e2e (B21, B33)


def test_e2e_unmapped_url_key_fails_the_unit(ws, root, fake):
    _e2e_suite(root, "fe", env_keys=("BASE_URL", "OTHER_SERVICE_URL"))
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    summary = _run(make_ctx(ws, tasks=["K-1"]), "test e2e")
    r = _one(summary)
    assert r.status is Status.FAILED
    assert "OTHER_SERVICE_URL" in r.reason


def test_e2e_missing_suite_dir_is_skipped(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    summary = _run(make_ctx(ws, tasks=["K-1"]), "test e2e")
    r = _one(summary)
    assert r.status is Status.SKIPPED


def test_e2e_dry_run_touches_nothing(ws, root, fake):
    _e2e_suite(root, "fe")
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    summary = _run(make_ctx(ws, tasks=["K-1"], dry_run=True), "test e2e")
    r = _one(summary)
    assert r.status is Status.PLANNED
    assert _log(fake, "docker") == []


def test_e2e_happy_path_wires_env_hosts_and_k(ws, root, fake):
    _e2e_suite(root, "fe")
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    ctx = make_ctx(ws, tasks=["K-1"])
    ctx.options["k"] = "test_login"
    summary = _run(ctx, "test e2e")
    r = _one(summary)
    assert r.status is Status.DONE

    runs = _runs(fake, "docker", "run")
    assert len(runs) == 1
    argv = runs[0]
    assert "playwright:1" in argv
    assert "--network" in argv and argv[argv.index("--network") + 1] == "host"
    assert "--add-host" in argv
    hosts = [argv[i + 1] for i, a in enumerate(argv) if a == "--add-host"]
    assert "fe.k-1.localhost:127.0.0.1" in hosts
    assert "-e" in argv and "BASE_URL" in argv
    assert argv[-4:] == ["pytest", "--alluredir=/results", "-k", "test_login"]

    # no allure results were produced by the fake container: nothing to publish, no warning
    assert r.reason == ""


# ---------------------------------------------------------------------------------------------
# report (B23)


def test_report_prints_address_of_every_generated_project(ws, fake):
    fake.present("K-1")
    (ws.paths.reports / "K-1" / "aaa-api" / "site").mkdir(parents=True)
    ctx = make_ctx(ws, tasks=["K-1"])
    summary = _run(ctx, "report")
    r = _one(summary)
    assert r.status is Status.DONE
    # D14: the address `report` prints is where the node's `reports` service actually serves
    # from (business-rules.md Step-2 choices: synced to `.../reports/<DEMAND>/<project>/`, no
    # `/site` segment -- `node.sync` copies `site`'s *contents* straight there).
    assert ctx.out.getvalue().strip() == "http://reports.localhost:8080/K-1/aaa-api/"


def test_report_nothing_generated_is_skipped(ws, fake):
    fake.present("K-1")
    ctx = make_ctx(ws, tasks=["K-1"])
    summary = _run(ctx, "report")
    r = _one(summary)
    assert r.status is Status.SKIPPED
    assert ctx.out.getvalue() == ""


def test_b33_scan_ignores_installed_libraries(tmp_path):
    from dop.verbs._testing import scan_env_url_keys
    (tmp_path / "conftest.py").write_text('import os\nos.environ["E2E_BASE_URL"]\n')
    lib = tmp_path / ".venv/lib/site-packages/requests"; lib.mkdir(parents=True)
    (lib / "x.py").write_text('import os\nos.environ.get("CURL_CA_BUNDLE_URL")\n')
    assert scan_env_url_keys(tmp_path) == {"E2E_BASE_URL"}
