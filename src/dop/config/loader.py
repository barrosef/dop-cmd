from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .schema import (
    WorkspaceConfig, RepoConfig, CredentialsConfig, PlatformConfig,
    RuntimeConfig, AppConfig, AppBuildConfig,
    DockerComposeConfig, EphemeralRunnerConfig, JavaRunnerConfig,
)

CONFIG_DEFAULT_PATH = Path.home() / ".config" / "dop" / "config.toml"


def config_path() -> Path:
    override = os.environ.get("DOP_CONFIG")
    return Path(override) if override else CONFIG_DEFAULT_PATH


def load_config() -> dict[str, WorkspaceConfig]:
    """Retorna dict {workspace_name: WorkspaceConfig}."""
    path = config_path()
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return {
        name: _parse_workspace(name, data)
        for name, data in raw.get("workspaces", {}).items()
    }


def _parse_workspace(name: str, data: dict) -> WorkspaceConfig:
    repos: dict[str, RepoConfig] = {}
    for repo_name, repo_data in data.get("repos", {}).items():
        repos[repo_name] = RepoConfig(
            name=repo_name,
            dir=repo_data["dir"],
            base_branch=repo_data["base_branch"],
            pr_targets=repo_data.get("pr_targets", []),
            primary=repo_data.get("primary", True),
            azure_org=repo_data.get("azure_org"),
            azure_project=repo_data.get("azure_project"),
            long_branches=repo_data.get("long_branches"),
        )

    creds_data = data.get("credentials", {})
    credentials = CredentialsConfig(
        login_env=creds_data.get("login_env"),
        token_env=creds_data.get("token_env"),
        ssh_key_env=creds_data.get("ssh_key_env"),
    )

    plat_data = data.get("platform_config", {})
    platform_config = PlatformConfig(
        org_env=plat_data.get("org_env"),
        project_env=plat_data.get("project_env"),
        reviewers_env=plat_data.get("reviewers_env"),
        org=plat_data.get("org"),
        gitlab_url=plat_data.get("gitlab_url", "https://gitlab.com"),
        namespace=plat_data.get("namespace"),
    )

    pr_doc_suffix_map = data.get("pr_doc_suffix_map", {})
    long_branches = data.get("long_branches", ["master", "main", "desenv", "hml", "OG-GLOBAL"])

    rt_raw = data.get("runtime", {})
    apps_raw = rt_raw.get("apps", {})
    apps: dict[str, AppConfig] = {}
    aliases: dict[str, str] = {}
    for app_name, app_data in apps_raw.items():
        build_raw = app_data.get("build")
        build = None
        if build_raw is not None:
            build = AppBuildConfig(
                dir=build_raw["dir"],
                command=build_raw["command"],
                artifact=build_raw.get("artifact", "dist"),
            )
        app = AppConfig(
            name=app_name,
            service=app_data["service"],
            role=app_data["role"],
            port=app_data["port"],
            aliases=app_data.get("aliases", []),
            depends_on=app_data.get("depends_on", []),
            url_env=app_data.get("url_env"),
            fallback_url_env=app_data.get("fallback_url_env"),
            e2e_suite=app_data.get("e2e_suite"),
            build=build,
            debug_port=app_data.get("debug_port"),
        )
        apps[app_name] = app
        for alias in app.aliases:
            aliases[alias] = app_name

    dc_raw = rt_raw.get("docker_compose")
    docker_compose = None
    if dc_raw is not None:
        runner_raw = dc_raw.get("ephemeral_runner")
        ephemeral_runner = None
        if runner_raw is not None:
            ephemeral_runner = EphemeralRunnerConfig(
                service=runner_raw["service"],
                profile=runner_raw.get("profile"),
            )
        jr_raw = dc_raw.get("java_runner")
        java_runner = None
        if jr_raw is not None:
            java_runner = JavaRunnerConfig(
                service=jr_raw["service"],
                profile=jr_raw.get("profile"),
            )
        docker_compose = DockerComposeConfig(
            compose_file=dc_raw.get("compose_file", "docker-compose.yml"),
            env_files=dc_raw.get("env_files", ["docker/.env"]),
            project_name=dc_raw.get("project_name", ""),
            ephemeral_runner=ephemeral_runner,
            java_runner=java_runner,
            clean=dc_raw.get("clean", {}),
        )

    runtime = RuntimeConfig(
        orchestrator=rt_raw.get("orchestrator", "docker_compose"),
        infra=rt_raw.get("infra", []),
        default_max_strikes=rt_raw.get("default_max_strikes", 3),
        apps=apps,
        aliases=aliases,
        docker_compose=docker_compose,
    )

    return WorkspaceConfig(
        name=name,
        root=data["root"],
        test_root=data.get("test_root", "e2e"),
        aaa_root=data.get("aaa_root", "test/aaa"),
        it_root=data.get("it_root", "test/it"),
        demands_dir=data.get("demands_dir", "docs/RFC"),
        platform=data.get("platform", "azure_devops"),
        auth_method=data.get("auth_method", "token"),
        jira_base_url=data.get("jira_base_url", "https://atlassian.net/browse"),
        jira_key_pattern=data.get("jira_key_pattern", r"^[A-Z][A-Z0-9]+-\d+$"),
        credentials=credentials,
        platform_config=platform_config,
        repos=repos,
        pr_doc_prefix=data.get("pr_doc_prefix", "99-pr-00"),
        pr_doc_suffix_map=pr_doc_suffix_map,
        long_branches=long_branches,
        runtime=runtime,
    )
