from dop.config.schema import (
    RuntimeConfig, AppConfig, AppBuildConfig,
    DockerComposeConfig, EphemeralRunnerConfig,
    WorkspaceConfig,
)


def test_app_config_minimal():
    app = AppConfig(name="optum-support-be", service="optum-support-be",
                    role="backend", port=8080)
    assert app.aliases == []
    assert app.depends_on == []
    assert app.url_env is None
    assert app.fallback_url_env is None
    assert app.e2e_suite is None
    assert app.build is None
    assert app.debug_port is None


def test_app_build_config():
    b = AppBuildConfig(dir="repos/optum-support-fe", command="npx vite build")
    assert b.artifact == "dist"


def test_runtime_config_defaults():
    rc = RuntimeConfig()
    assert rc.orchestrator == "docker_compose"
    assert rc.infra == []
    assert rc.default_max_strikes == 3
    assert rc.apps == {}
    assert rc.aliases == {}
    assert rc.docker_compose is None


def test_docker_compose_config_defaults():
    dc = DockerComposeConfig()
    assert dc.compose_file == "docker-compose.yml"
    assert dc.env_files == ["docker/.env"]
    assert dc.project_name == ""
    assert dc.ephemeral_runner is None
    assert dc.clean == {}


def test_ephemeral_runner_config():
    er = EphemeralRunnerConfig(service="playwright-env", profile="e2e")
    assert er.service == "playwright-env"
    assert er.profile == "e2e"


def test_workspace_config_has_runtime():
    ws = WorkspaceConfig(name="test", root="/tmp/test")
    assert isinstance(ws.runtime, RuntimeConfig)


def test_workspace_test_roots_defaults():
    ws = WorkspaceConfig(name="test", root="/tmp/test")
    assert ws.test_root == "e2e"
    assert ws.aaa_root == "test/aaa"
    assert ws.it_root == "test/it"


def test_workspace_test_roots_override():
    ws = WorkspaceConfig(name="ws", root="/tmp/ws",
                         test_root="custom/e2e", aaa_root="custom/aaa", it_root="custom/it")
    assert ws.test_root == "custom/e2e"
    assert ws.aaa_root == "custom/aaa"
    assert ws.it_root == "custom/it"


def test_java_runner_config_default_none():
    from dop.config.schema import DockerComposeConfig
    dc = DockerComposeConfig()
    assert dc.java_runner is None


def test_java_runner_config_values():
    from dop.config.schema import DockerComposeConfig, JavaRunnerConfig
    jr = JavaRunnerConfig(service="java-test", profile="test")
    dc = DockerComposeConfig(java_runner=jr)
    assert dc.java_runner.service == "java-test"
    assert dc.java_runner.profile == "test"
