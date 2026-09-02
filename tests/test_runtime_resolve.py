import pytest
from dop.config.schema import WorkspaceConfig, RuntimeConfig, AppConfig
from dop.runtime.resolve import expand_apps, infer_urls


def _ws() -> WorkspaceConfig:
    apps = {
        "lifesupport-api": AppConfig(
            name="lifesupport-api", service="lifesupport-api", role="backend",
            port=8082, debug_port=5005, aliases=["ls"],
            url_env="LIFESUPPORT_URL", fallback_url_env="AZURE_LIFESUPPORT_URL",
        ),
        "optum-support-be": AppConfig(
            name="optum-support-be", service="optum-support-be", role="backend",
            port=8080, debug_port=5006, aliases=["osb"],
            url_env="OPTUM_SUPPORT_BE_URL", fallback_url_env="AZURE_OPTUM_SUPPORT_BE_URL",
        ),
        "optum-support-fe": AppConfig(
            name="optum-support-fe", service="optum-support-fe", role="frontend",
            port=5173, aliases=["osf"], depends_on=["optum-support-be"],
            e2e_suite="optum-support-fe",
        ),
    }
    aliases = {"ls": "lifesupport-api", "osb": "optum-support-be", "osf": "optum-support-fe"}
    runtime = RuntimeConfig(apps=apps, aliases=aliases)
    return WorkspaceConfig(name="test", root="/tmp/test", runtime=runtime)


def test_expand_aliases():
    assert expand_apps(_ws(), ["osf", "osb"]) == {"optum-support-fe", "optum-support-be"}


def test_expand_auto_deps():
    result = expand_apps(_ws(), ["osf"])
    assert result == {"optum-support-fe", "optum-support-be"}


def test_expand_no_deps():
    assert expand_apps(_ws(), ["osf"], no_deps=True) == {"optum-support-fe"}


def test_infer_urls_local(monkeypatch):
    monkeypatch.setenv("AZURE_LIFESUPPORT_URL", "https://azure.example.com")
    env = infer_urls(_ws(), {"lifesupport-api", "optum-support-be"})
    assert env["LIFESUPPORT_URL"] == "http://lifesupport-api:8082"
    assert env["OPTUM_SUPPORT_BE_URL"] == "http://optum-support-be:8080"


def test_infer_urls_fallback(monkeypatch):
    monkeypatch.setenv("AZURE_LIFESUPPORT_URL", "https://azure.example.com")
    env = infer_urls(_ws(), {"optum-support-be"})
    assert env["LIFESUPPORT_URL"] == "https://azure.example.com"


def test_expand_unknown_app_raises():
    with pytest.raises(Exception):
        expand_apps(_ws(), ["unknown-app"])
