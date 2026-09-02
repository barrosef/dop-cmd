from __future__ import annotations

import os

import gitlab

from ..config.schema import WorkspaceConfig
from ..core.errors import ProcessError, ValidationError
from .base import PlatformProvider, PRResult


class GitLabPlatform(PlatformProvider):
    def __init__(self, workspace: WorkspaceConfig):
        self._ws = workspace
        token_env = workspace.credentials.token_env or "GITLAB_TOKEN"
        token = os.environ.get(token_env)
        if not token:
            raise ValidationError(f"Missing GitLab token. Set env var: {token_env}")
        url = workspace.platform_config.gitlab_url or "https://gitlab.com"
        self._gl = gitlab.Gitlab(url, private_token=token)
        self._namespace = workspace.platform_config.namespace
        if not self._namespace:
            raise ValidationError("GitLab platform requires platform_config.namespace in workspace config.")

    def _get_project(self, repo_name: str):
        path = f"{self._namespace}/{repo_name}"
        try:
            return self._gl.projects.get(path)
        except gitlab.GitlabGetError as exc:
            raise ValidationError(f"GitLab project not found: {path}") from exc

    def create_pr(
        self,
        *,
        repo_name: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
        reviewers: list[str] | None = None,
        dry_run: bool = False,
        logger=None,
    ) -> PRResult:
        if dry_run:
            return PRResult(
                repo_name=repo_name,
                source_branch=source_branch,
                target_branch=target_branch,
                pr_id=None,
                web_url=None,
                has_conflict=False,
                merge_status=None,
            )
        project = self._get_project(repo_name)
        try:
            mr = project.mergerequests.create(
                {
                    "title": title,
                    "description": description,
                    "source_branch": source_branch,
                    "target_branch": target_branch,
                }
            )
            has_conflict = mr.merge_status in ("cannot_be_merged", "cannot_be_merged_recheck")
            return PRResult(
                repo_name=repo_name,
                source_branch=source_branch,
                target_branch=target_branch,
                pr_id=str(mr.iid),
                web_url=mr.web_url,
                has_conflict=has_conflict,
                merge_status=mr.merge_status,
            )
        except gitlab.GitlabCreateError as exc:
            raise ProcessError(f"GitLab MR creation failed: {exc}") from exc

    def get_pr_status(
        self,
        pr_id: str,
        *,
        repo_name: str | None = None,
        dry_run: bool = False,
        logger=None,
    ) -> PRResult:
        if dry_run:
            return PRResult(
                repo_name=repo_name or "",
                source_branch="",
                target_branch="",
                pr_id=pr_id,
                web_url=None,
                has_conflict=False,
                merge_status=None,
            )
        if not repo_name:
            raise ValidationError("repo_name required for GitLab get_pr_status")
        project = self._get_project(repo_name)
        mr = project.mergerequests.get(int(pr_id))
        has_conflict = mr.merge_status in ("cannot_be_merged", "cannot_be_merged_recheck")
        return PRResult(
            repo_name=repo_name,
            source_branch=mr.source_branch,
            target_branch=mr.target_branch,
            pr_id=str(mr.iid),
            web_url=mr.web_url,
            has_conflict=has_conflict,
            merge_status=mr.merge_status,
        )

    def list_prs(
        self,
        *,
        repo_name: str,
        source_branch: str,
        target_branch: str,
        dry_run: bool = False,
        logger=None,
    ) -> list[PRResult]:
        if dry_run:
            return []
        project = self._get_project(repo_name)
        mrs = project.mergerequests.list(
            state="opened",
            source_branch=source_branch,
            target_branch=target_branch,
        )
        return [
            PRResult(
                repo_name=repo_name,
                source_branch=mr.source_branch,
                target_branch=mr.target_branch,
                pr_id=str(mr.iid),
                web_url=mr.web_url,
                has_conflict=mr.merge_status in ("cannot_be_merged", "cannot_be_merged_recheck"),
                merge_status=mr.merge_status,
            )
            for mr in mrs
        ]
