from dop.config.schema import WorkspaceConfig, RuntimeConfig, AppConfig
from dop.runtime.e2e import (
    resolve_e2e_target, normalize_jira_filter, find_suites_for_jira,
    next_run_number, suite_order,
)


def _ws() -> WorkspaceConfig:
    apps = {
        "optum-support-be": AppConfig(name="optum-support-be", service="optum-support-be",
                                      role="backend", port=8080),
        "optum-support-fe": AppConfig(name="optum-support-fe", service="optum-support-fe",
                                      role="frontend", port=5173, e2e_suite="optum-support-fe"),
        "providers-front-end": AppConfig(name="providers-front-end", service="providers-front-end",
                                         role="frontend", port=5174, e2e_suite="providers-front-end"),
    }
    return WorkspaceConfig(name="test", root="/tmp/test", runtime=RuntimeConfig(apps=apps))


def test_suite_order_from_config():
    assert suite_order(_ws()) == ["optum-support-fe", "providers-front-end"]


def test_normalize_jira_filter():
    assert normalize_jira_filter("OG-150") == "og_150"
    assert normalize_jira_filter("SUOPT-3144") == "suopt_3144"


def test_resolve_target_suite():
    result = resolve_e2e_target("optum-support-fe", known_suites=["optum-support-fe"])
    assert result == {"kind": "suite", "suites": ["optum-support-fe"], "filter": None}


def test_resolve_target_jira():
    result = resolve_e2e_target("OG-150", known_suites=["optum-support-fe"])
    assert result["kind"] == "jira"
    assert result["filter"] == "og_150"


def test_resolve_target_file():
    result = resolve_e2e_target("tests/test_smoke.py", known_suites=["optum-support-fe"])
    assert result["kind"] == "file"


def test_find_suites_for_jira(tmp_path):
    suite1 = tmp_path / "optum-support-fe" / "tests"
    suite1.mkdir(parents=True)
    (suite1 / "test_og_150_login.py").touch()
    suite2 = tmp_path / "providers-front-end" / "tests"
    suite2.mkdir(parents=True)
    (suite2 / "test_smoke.py").touch()

    found = find_suites_for_jira("og_150", e2e_root=tmp_path,
                                 suite_order=["optum-support-fe", "providers-front-end"])
    assert found == ["optum-support-fe"]


def test_next_run_number(tmp_path):
    reports = tmp_path / "OG-150" / "optum-support-fe"
    reports.mkdir(parents=True)
    (reports / "run-1").mkdir()
    (reports / "run-2").mkdir()
    assert next_run_number(reports) == 3


def test_next_run_number_empty(tmp_path):
    reports = tmp_path / "OG-150" / "optum-support-fe"
    reports.mkdir(parents=True)
    assert next_run_number(reports) == 1
