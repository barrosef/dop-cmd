from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RepoConfig:
    name: str
    dir: str
    base_branch: str
    pr_targets: list[str]
    primary: bool = True
    azure_org: str | None = None
    azure_project: str | None = None
    long_branches: list[str] | None = None  # override; se None, usa workspace.long_branches


@dataclass
class CredentialsConfig:
    login_env: str | None = None
    token_env: str | None = None
    ssh_key_env: str | None = None


@dataclass
class PlatformConfig:
    org_env: str | None = None
    project_env: str | None = None
    reviewers_env: str | None = None
    org: str | None = None
    gitlab_url: str = "https://gitlab.com"
    namespace: str | None = None


@dataclass
class AppBuildConfig:
    """Build de front-end no host (nginx serve o artefato)."""
    dir: str
    command: str
    artifact: str = "dist"


@dataclass
class AppConfig:
    """App lógico mapeado para um serviço do orquestrador."""
    name: str
    service: str               # nome do serviço no docker-compose.yml
    role: str                  # "frontend" | "backend"
    port: int
    aliases: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    url_env: str | None = None          # env var onde injetar a URL deste app (BE)
    fallback_url_env: str | None = None  # env var de fallback (Azure) quando fora do ar
    e2e_suite: str | None = None        # diretório da suite em e2e/ (FE)
    build: AppBuildConfig | None = None
    debug_port: int | None = None


@dataclass
class EphemeralRunnerConfig:
    """Serviço usado para execuções efêmeras (e2e/codegen)."""
    service: str
    profile: str | None = None


@dataclass
class JavaRunnerConfig:
    """Serviço usado para rodar Maven (aaa/it) em container efêmero."""
    service: str
    profile: str | None = None


@dataclass
class DockerComposeConfig:
    compose_file: str = "docker-compose.yml"
    env_files: list[str] = field(default_factory=lambda: ["docker/.env"])
    project_name: str = ""              # prefixo de volume (docker compose project)
    ephemeral_runner: EphemeralRunnerConfig | None = None
    java_runner: JavaRunnerConfig | None = None
    clean: dict[str, list[str]] = field(default_factory=dict)  # categoria -> volumes


@dataclass
class RuntimeConfig:
    orchestrator: str = "docker_compose"
    infra: list[str] = field(default_factory=list)
    default_max_strikes: int = 3
    apps: dict[str, AppConfig] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)  # derivado de apps[*].aliases
    docker_compose: DockerComposeConfig | None = None


@dataclass
class WorkspaceConfig:
    name: str
    root: str
    test_root: str = "e2e"
    aaa_root: str = "test/aaa"
    it_root: str = "test/it"
    demands_dir: str = "docs/RFC"
    platform: str = "azure_devops"
    auth_method: str = "token"
    jira_base_url: str = "https://atlassian.net/browse"
    jira_key_pattern: str = r"^[A-Z][A-Z0-9]+-\d+$"
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    platform_config: PlatformConfig = field(default_factory=PlatformConfig)
    repos: dict[str, RepoConfig] = field(default_factory=dict)
    pr_doc_prefix: str = "99-pr-00"
    pr_doc_suffix_map: dict[str, str] = field(default_factory=dict)
    long_branches: list[str] = field(default_factory=lambda: ["master", "main", "desenv", "hml", "OG-GLOBAL"])
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
