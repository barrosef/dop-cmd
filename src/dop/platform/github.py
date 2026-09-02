from __future__ import annotations

import os

from github import Github, GithubException

from ..config.schema import WorkspaceConfig
from ..core.errors import ProcessError, ValidationError
from .base import PlatformProvider, PRResult


class GitHubPlatform(PlatformProvider):
    def __init__(self, workspace: WorkspaceConfig):
        self._ws = workspace
        token_env = workspace.credentials.token_env or "GITHUB_TOKEN"
        token = os.environ.get(token_env)
        if not token:
            raise ValidationError(f"Missing GitHub token. Set env var: {token_env}")
        self._gh = Github(token)
        self._org = workspace.platform_config.org
        if not self._org:
            raise ValidationError("GitHub platform requires platform_config.org in workspace config.")

    def _get_repo(self, repo_name: str):
        full_name = f"{self._org}/{repo_name}"
        try:
            return self._gh.get_repo(full_name)
        except GithubException as exc:
            raise ValidationError(f"GitHub repo not found: {full_name}") from exc

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
        repo = self._get_repo(repo_name)
        try:
            pr = repo.create_pull(
                title=title,
                body=description,
                head=source_branch,
                base=target_branch,
            )
            if reviewers:
                pr.create_review_request(reviewers=reviewers)
            has_conflict = pr.mergeable is False
            return PRResult(
                repo_name=repo_name,
                source_branch=source_branch,
                target_branch=target_branch,
                pr_id=str(pr.number),
                web_url=pr.html_url,
                has_conflict=has_conflict,
                merge_status=str(pr.mergeable_state) if pr.mergeable_state else None,
            )
        except GithubException as exc:
            raise ProcessError(f"GitHub PR creation failed: {exc}") from exc

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
            raise ValidationError("repo_name required for GitHub get_pr_status")
        repo = self._get_repo(repo_name)
        pr = repo.get_pull(int(pr_id))
        return PRResult(
            repo_name=repo_name,
            source_branch=pr.head.ref,
            target_branch=pr.base.ref,
            pr_id=str(pr.number),
            web_url=pr.html_url,
            has_conflict=pr.mergeable is False,
            merge_status=str(pr.mergeable_state) if pr.mergeable_state else None,
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
        repo = self._get_repo(repo_name)
        pulls = repo.get_pulls(state="open", head=source_branch, base=target_branch)
        return [
            PRResult(
                repo_name=repo_name,
                source_branch=p.head.ref,
                target_branch=p.base.ref,
                pr_id=str(p.number),
                web_url=p.html_url,
                has_conflict=p.mergeable is False,
                merge_status=str(p.mergeable_state) if p.mergeable_state else None,
            )
            for p in pulls
        ]
