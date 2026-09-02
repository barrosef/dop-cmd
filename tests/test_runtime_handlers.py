import argparse
from unittest.mock import MagicMock, patch
import pytest
from dop.core.errors import ValidationError
from dop.config.schema import (
    WorkspaceConfig, RuntimeConfig, AppConfig,
    DockerComposeConfig, EphemeralRunnerConfig, JavaRunnerConfig,
)
from dop.runtime import handlers


def _ws() -> WorkspaceConfig:
    apps = {
        "optum-support-be": AppConfig(name="optum-support-be", service="optum-support-be",
                                      role="backend", port=8080,
                                      url_env="OPTUM_SUPPORT_BE_URL"),
        "optum-support-fe": AppConfig(name="optum-support-fe", service="optum-support-fe",
                                      role="frontend", port=5173, aliases=["osf"],
                                      depends_on=["optum-support-be"],
                                      e2e_suite="optum-support-fe"),
    }
    dc = DockerComposeConfig(
        ephemeral_runner=EphemeralRunnerConfig(service="playwright-env", profile="e2e"),
        clean={"maven": ["m2-cache"]},
    )
    rt = RuntimeConfig(apps=apps, aliases={"osf": "optum-support-fe"},
                       infra=["mongodb", "allure"], docker_compose=dc)
    return WorkspaceConfig(name="t", root="/tmp/ws", runtime=rt)


def _args(**kw):
    return argparse.Namespace(**kw)


def test_handle_stop_delegates_to_provider():
    ws = _ws()
    provider = MagicMock()
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider):
        rc = handlers.handle_stop(ws, _args(apps=["osf"]), dry_run=True)
    assert rc == 0
    provider.stop.assert_called_once()


def test_handle_clean_maps_flags_to_categories():
    ws = _ws()
    provider = MagicMock()
    provider.clean.return_value = ["m2-cache"]
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider):
        rc = handlers.handle_clean(ws, _args(m2=True, node_modules=False, allure=False, all=False), dry_run=True)
    assert rc == 0
    provider.clean.assert_called_once_with(["maven"], dry_run=True, logger=None)


def test_handle_clean_no_flags_raises():
    ws = _ws()
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=MagicMock()):
        with pytest.raises(Exception):
            handlers.handle_clean(ws, _args(m2=False, node_modules=False, allure=False, all=False))


def test_suite_urls_from_config():
    ws = _ws()
    assert handlers._suite_base_url(ws, "optum-support-fe") == "http://localhost:5173"
    assert handlers._suite_api_url(ws, "optum-support-fe") == "http://localhost:8080"


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
    # gera num tmp e troca atomicamente: --output aponta pro tmp, resultado final no dir servido
    assert str(reports_root / ".aaa-demo.tmp-gen") in cmd
    assert "--report-name" in cmd
    assert (reports_root / "aaa-demo").is_dir()
    assert not (reports_root / ".aaa-demo.tmp-gen").exists()


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


def test_publish_allure_project_skips_when_no_results(tmp_path):
    # results_dir does not exist -> generate is skipped, run_command never called
    with patch("dop.runtime.handlers.run_command") as rc:
        handlers._publish_allure_project(
            project="aaa-empty",
            results_dir=tmp_path / "missing" / ".allure-results",
            reports_root=tmp_path / "reports",
        )
    rc.assert_not_called()


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



def test_resolve_test_targets_all(tmp_path):
    for repo in ("alpha", "beta"):
        (tmp_path / repo).mkdir()
        (tmp_path / repo / "pom.xml").write_text("<project/>")
    (tmp_path / "no-pom").mkdir()
    assert handlers._resolve_test_targets(tmp_path, ["all"]) == ["alpha", "beta"]


def test_resolve_test_targets_explicit_missing(tmp_path):
    with pytest.raises(ValidationError):
        handlers._resolve_test_targets(tmp_path, ["ghost"])


def test_resolve_test_targets_explicit_present(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "pom.xml").write_text("<project/>")
    assert handlers._resolve_test_targets(tmp_path, ["alpha"]) == ["alpha"]


def test_resolve_test_targets_empty_root_all(tmp_path):
    assert handlers._resolve_test_targets(tmp_path / "nope", ["all"]) == []


def _make_project(root, repo, *, mvnw=False):
    p = root / repo
    p.mkdir(parents=True)
    (p / "pom.xml").write_text("<project/>")
    if mvnw:
        (p / "mvnw").write_text("#!/bin/sh\n")
    return p


def _ws_with_java_runner(tmp_path):
    ws = _ws()
    ws.root = str(tmp_path)
    ws.test_root = "test/e2e"
    ws.aaa_root = "test/aaa"
    ws.it_root = "test/it"
    ws.runtime.docker_compose.java_runner = JavaRunnerConfig(service="java-test", profile="test")
    return ws


def test_handle_aaa_runs_in_container_and_publishes(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    _make_project(tmp_path / "test/aaa", "lifesupport-api")
    provider = MagicMock(); provider.run_service.return_value = 0
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project") as pub:
        rc = handlers.handle_aaa(ws, _args(targets=["lifesupport-api"], k=None, fresh_report=False))
    assert rc == 0
    kw = provider.run_service.call_args.kwargs
    args0 = provider.run_service.call_args.args
    assert args0[0] == "java-test"
    assert kw["workdir"] == "/workspace/test/aaa/lifesupport-api"
    assert kw["profile"] == "test"
    cmd = args0[1]
    assert cmd[0] == "mvn" and "test" in cmd
    assert pub.call_args.kwargs["project"] == "aaa-lifesupport-api"


def test_handle_aaa_uses_mvnw_when_present_and_k_filter(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    _make_project(tmp_path / "test/aaa", "demo", mvnw=True)
    provider = MagicMock(); provider.run_service.return_value = 0
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project"):
        handlers.handle_aaa(ws, _args(targets=["demo"], k="FooTest", fresh_report=False))
    cmd = provider.run_service.call_args.args[1]
    assert cmd[0] == "./mvnw"
    assert "-Dtest=FooTest" in cmd


def test_handle_it_runs_pit_verify_in_container(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    _make_project(tmp_path / "test/it", "optum-support-be")
    provider = MagicMock(); provider.run_service.return_value = 0
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project") as pub:
        rc = handlers.handle_it(ws, _args(targets=["optum-support-be"], k=None, fresh_report=False))
    assert rc == 0
    kw = provider.run_service.call_args.kwargs
    cmd = provider.run_service.call_args.args[1]
    assert kw["workdir"] == "/workspace/test/it/optum-support-be"
    assert "-Pit" in cmd and "verify" in cmd
    assert pub.call_args.kwargs["project"] == "it-optum-support-be"


def test_handle_it_k_filter_uses_it_test(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    _make_project(tmp_path / "test/it", "demo")
    provider = MagicMock(); provider.run_service.return_value = 0
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project"):
        handlers.handle_it(ws, _args(targets=["demo"], k="FooIT", fresh_report=False))
    cmd = provider.run_service.call_args.args[1]
    assert "-Dit.test=FooIT" in cmd


def test_handle_aaa_red_when_container_fails(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    _make_project(tmp_path / "test/aaa", "demo")
    provider = MagicMock(); provider.run_service.return_value = 1
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project") as pub:
        rc = handlers.handle_aaa(ws, _args(targets=["demo"], k=None, fresh_report=False))
    assert rc == 1
    pub.assert_called_once()


def test_handle_aaa_all_multi_repo_mixed(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    _make_project(tmp_path / "test/aaa", "a-pass")
    _make_project(tmp_path / "test/aaa", "z-fail")
    provider = MagicMock(); provider.run_service.side_effect = [0, 1]
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project") as pub:
        rc = handlers.handle_aaa(ws, _args(targets=["all"], k=None, fresh_report=False))
    assert rc == 1
    assert provider.run_service.call_count == 2
    assert pub.call_count == 2


def test_handle_aaa_all_empty_root_is_noop_green(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    provider = MagicMock()
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project"):
        rc = handlers.handle_aaa(ws, _args(targets=["all"], k=None, fresh_report=False))
    assert rc == 0
    provider.run_service.assert_not_called()


def test_handle_aaa_without_java_runner_raises(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    ws.runtime.docker_compose.java_runner = None
    _make_project(tmp_path / "test/aaa", "demo")
    provider = MagicMock()
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider):
        with pytest.raises(ValidationError):
            handlers.handle_aaa(ws, _args(targets=["demo"], k=None, fresh_report=False))
