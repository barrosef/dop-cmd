from __future__ import annotations

import json
import os
from urllib.parse import urlparse

from ..config.schema import WorkspaceConfig
from ..core.errors import ProcessError, ValidationError
from ..core.process import run_command
from ..git.operations import get_remote_url, repo_path
from .base import PlatformProvider, PRResult


class AzurePlatform(PlatformProvider):
    def __init__(self, workspace: WorkspaceConfig):
        self._ws = workspace

    def _pr_doc_suffix(self, repo_name: str) -> str:
        return self._ws.pr_doc_suffix_map.get(repo_name, repo_name)

    def _pr_title_suffix(self, repo_name: str) -> str:
        return self._pr_doc_suffix(repo_name)

    def _format_pr_title(self, title: str, repo_name: str) -> str:
        suffix = self._pr_title_suffix(repo_name)
        token = f" - {suffix}"
        if title.endswith(token) or title.endswith(f"[{suffix}]") or title.endswith(f"({suffix})"):
            return title
        return f"{title}{token}"

    def _azure_org(self) -> str | None:
        env_name = self._ws.platform_config.org_env or "AZURE_DEVOPS_ORG"
        return os.environ.get(env_name)

    def _azure_project(self) -> str | None:
        env_name = self._ws.platform_config.project_env or "AZURE_DEVOPS_PROJECT"
        return os.environ.get(env_name)

    def _azure_reviewers(self) -> list[str]:
        env_name = self._ws.platform_config.reviewers_env or "AZURE_DEVOPS_REVIEWERS"
        raw = os.environ.get(env_name)
        if not raw:
            return []
        tokens: list[str] = []
        for chunk in raw.replace(",", " ").split():
            cleaned = chunk.strip()
            if cleaned:
                tokens.append(cleaned)
        return tokens

    def _build_pr_command(
        self,
        *,
        repo_name: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
        reviewers: list[str] | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> list[str]:
        remote_repo = self._pr_doc_suffix(repo_name)
        cmd = [
            "az",
            "repos",
            "pr",
            "create",
            "--repository",
            remote_repo,
            "--source-branch",
            source_branch,
            "--target-branch",
            target_branch,
            "--title",
            title,
            "--description",
            description,
            "--output",
            "json",
        ]
        selected_reviewers = reviewers if reviewers is not None else self._azure_reviewers()
        if selected_reviewers:
            cmd.extend(["--reviewers", *selected_reviewers])
        org_value = org or self._azure_org()
        project_value = project or self._azure_project()
        if org_value:
            cmd.extend(["--organization", org_value])
        if project_value:
            cmd.extend(["--project", project_value])
        return cmd

    def _build_pr_show_command(
        self,
        pr_id: int | str,
        *,
        include_project: bool = True,
        org_url: str | None = None,
        project: str | None = None,
    ) -> list[str]:
        cmd = [
            "az",
            "repos",
            "pr",
            "show",
            "--id",
            str(pr_id),
            "--output",
            "json",
        ]
        org_value = org_url or self._azure_org()
        project_value = project or self._azure_project()
        if org_value:
            cmd.extend(["--organization", org_value])
        if include_project and project_value:
            cmd.extend(["--project", project_value])
        return cmd

    def _build_pr_list_command(
        self,
        *,
        repo_name: str,
        source_branch: str,
        target_branch: str,
        include_project: bool = True,
        org: str | None = None,
        project: str | None = None,
    ) -> list[str]:
        remote_repo = self._pr_doc_suffix(repo_name)
        cmd = [
            "az",
            "repos",
            "pr",
            "list",
            "--repository",
            remote_repo,
            "--source-branch",
            source_branch,
            "--target-branch",
            target_branch,
            "--status",
            "active",
            "--output",
            "json",
        ]
        org_value = org or self._azure_org()
        project_value = project or self._azure_project()
        if org_value:
            cmd.extend(["--organization", org_value])
        if include_project and project_value:
            cmd.extend(["--project", project_value])
        return cmd

    @staticmethod
    def _is_project_arg_error(error: Exception) -> bool:
        return "unrecognized arguments: --project" in str(error)

    @staticmethod
    def _is_existing_pr_error(error: Exception) -> bool:
        text = str(error).lower()
        return "pull request already exists" in text or "tf401179" in text

    @staticmethod
    def _parse_pr_payload(stdout: str) -> dict | None:
        if not stdout:
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _org_slug(org_url: str | None) -> str | None:
        if not org_url:
            return None
        parsed = urlparse(org_url)
        if parsed.netloc:
            parts = parsed.path.strip("/").split("/")
            return parts[-1] if parts else None
        return org_url.strip("/").split("/")[-1] or None

    def _derive_web_url(self, repo_name: str, pr_id: int | str | None) -> str | None:
        if not pr_id:
            return None
        org_url = self._repo_azure_org(repo_name) or self._azure_org()
        project = self._repo_azure_project(repo_name) or self._azure_project()
        org_slug = self._org_slug(org_url)
        if not org_slug or not project:
            return None
        remote_repo = self._pr_doc_suffix(repo_name)
        return f"https://dev.azure.com/{org_slug}/{project}/_git/{remote_repo}/pullrequest/{pr_id}"

    def _extract_web_url(self, payload: dict | None, repo_name: str) -> str | None:
        if not payload:
            return None
        web_url = payload.get("webUrl")
        if web_url:
            return web_url
        pr_id = payload.get("pullRequestId")
        repo_web = payload.get("repository", {}).get("webUrl") if isinstance(payload.get("repository"), dict) else None
        if repo_web and pr_id:
            return f"{repo_web}/pullrequest/{pr_id}"
        return self._derive_web_url(repo_name, pr_id)

    @staticmethod
    def _org_project_from_web_url(web_url: str | None) -> tuple[str | None, str | None]:
        if not web_url:
            return None, None
        parsed = urlparse(web_url)
        if not parsed.netloc:
            return None, None
        parts = parsed.path.strip("/").split("/")
        if not parts:
            return None, None
        if "dev.azure.com" in parsed.netloc:
            if len(parts) < 2:
                return None, None
            org = parts[0]
            project = parts[1]
            return f"{parsed.scheme}://{parsed.netloc}/{org}/", project
        if parsed.netloc.endswith("visualstudio.com"):
            org = parsed.netloc.split(".")[0]
            project = parts[0] if parts else None
            if not project:
                return None, None
            return f"{parsed.scheme}://{parsed.netloc}/{org}/", project
        return None, None

    @staticmethod
    def _org_project_from_remote_url(remote_url: str | None) -> tuple[str | None, str | None]:
        if not remote_url:
            return None, None
        if remote_url.startswith("git@ssh.dev.azure.com:"):
            marker = "v3/"
            if marker in remote_url:
                tail = remote_url.split(marker, 1)[1]
                parts = [part for part in tail.split("/") if part]
                if len(parts) >= 2:
                    org = parts[0]
                    project = parts[1]
                    return f"https://dev.azure.com/{org}/", project
        parsed = urlparse(remote_url)
        hostname = parsed.hostname or ""
        if "dev.azure.com" in hostname:
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 2:
                org = parts[0]
                project = parts[1]
                scheme = parsed.scheme or "https"
                return f"{scheme}://{hostname}/{org}/", project
        if hostname.endswith("visualstudio.com"):
            org = hostname.split(".")[0]
            parts = parsed.path.strip("/").split("/")
            project = parts[0] if parts else None
            if project:
                scheme = parsed.scheme or "https"
                return f"{scheme}://{hostname}/{org}/", project
        return None, None

    def _repo_azure_org(self, repo_name: str) -> str | None:
        """Per-repo org from config (takes priority over workspace-level env var)."""
        repo_cfg = self._ws.repos.get(repo_name)
        if repo_cfg and repo_cfg.azure_org:
            raw = repo_cfg.azure_org
            if raw.startswith("http"):
                return raw
            return f"https://dev.azure.com/{raw}/"
        return None

    def _repo_azure_project(self, repo_name: str) -> str | None:
        """Per-repo project from config (takes priority over workspace-level env var)."""
        repo_cfg = self._ws.repos.get(repo_name)
        if repo_cfg and repo_cfg.azure_project:
            return repo_cfg.azure_project
        return None

    def _azure_defaults_for_repo(
        self,
        repo_name: str,
        *,
        dry_run: bool = False,
        logger=None,
    ) -> tuple[str | None, str | None]:
        # Priority: 1) per-repo config  2) workspace env vars  3) git remote URL
        org = self._repo_azure_org(repo_name) or self._azure_org()
        project = self._repo_azure_project(repo_name) or self._azure_project()
        if org and project:
            return org, project
        remote_url = get_remote_url(repo_name, self._ws, dry_run=dry_run, logger=logger)
        remote_org, remote_project = self._org_project_from_remote_url(remote_url)
        if not org:
            org = remote_org
        if not project:
            project = remote_project
        return org, project

    @staticmethod
    def _is_conflict_status(status: str | None) -> bool:
        if not status:
            return False
        return "conflict" in status.lower()

    def _fetch_pr_status(
        self,
        pr_id: int | str,
        *,
        dry_run: bool = False,
        logger=None,
        org_url: str | None = None,
        project: str | None = None,
        cwd=None,
    ) -> tuple[str | None, bool]:
        cmd = self._build_pr_show_command(pr_id, org_url=org_url, project=project)
        try:
            result = run_command(cmd, cwd=cwd, dry_run=dry_run, logger=logger)
        except ProcessError as exc:
            if not self._is_project_arg_error(exc):
                raise
            cmd = self._build_pr_show_command(
                pr_id,
                include_project=False,
                org_url=org_url,
                project=project,
            )
            result = run_command(cmd, cwd=cwd, dry_run=dry_run, logger=logger)
        payload = self._parse_pr_payload(result.stdout)
        merge_status = payload.get("mergeStatus") if payload else None
        return merge_status, self._is_conflict_status(merge_status)

    def _fetch_existing_pr(
        self,
        *,
        repo_name: str,
        source_branch: str,
        target_branch: str,
        dry_run: bool = False,
        logger=None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict | None:
        repo_dir = repo_path(self._ws, repo_name)
        for include_project in (True, False):
            cmd = self._build_pr_list_command(
                repo_name=repo_name,
                source_branch=source_branch,
                target_branch=target_branch,
                include_project=include_project,
                org=org,
                project=project,
            )
            try:
                result = run_command(cmd, cwd=repo_dir, dry_run=dry_run, logger=logger)
                break
            except ProcessError as exc:
                if include_project and self._is_project_arg_error(exc):
                    continue
                raise
        else:
            return None

        payload = self._parse_pr_payload(result.stdout)
        if not isinstance(payload, list) or not payload:
            return None
        pr = payload[0]
        pr_id = pr.get("pullRequestId")
        merge_status = pr.get("mergeStatus")
        has_conflict = self._is_conflict_status(merge_status)
        if pr_id and not dry_run:
            merge_status, has_conflict = self._fetch_pr_status(
                pr_id,
                dry_run=dry_run,
                logger=logger,
                org_url=org,
                project=project,
                cwd=repo_dir,
            )
        return {
            "repo": self._pr_doc_suffix(repo_name),
            "repo_name": repo_name,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "web_url": self._extract_web_url(pr, repo_name),
            "pull_request_id": pr_id,
            "merge_status": merge_status,
            "has_conflict": has_conflict,
        }

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
        if len(description) > 4000:
            raise ValidationError(
                "PR description exceeds 4000 characters. Shorten the PR doc content before creating PRs."
            )
        repo_dir = repo_path(self._ws, repo_name)
        org, project = self._azure_defaults_for_repo(repo_name, dry_run=dry_run, logger=logger)
        title = self._format_pr_title(title, repo_name)
        cmd = self._build_pr_command(
            repo_name=repo_name,
            source_branch=source_branch,
            target_branch=target_branch,
            title=title,
            description=description,
            reviewers=reviewers,
            org=org,
            project=project,
        )
        try:
            result = run_command(cmd, cwd=repo_dir, dry_run=dry_run, logger=logger)
            payload = self._parse_pr_payload(result.stdout)
            web_url = self._extract_web_url(payload, repo_name)
            pr_id = payload.get("pullRequestId") if payload else None
            merge_status = payload.get("mergeStatus") if payload else None
            has_conflict = self._is_conflict_status(merge_status)
            if pr_id and not dry_run:
                merge_status, has_conflict = self._fetch_pr_status(
                    pr_id,
                    dry_run=dry_run,
                    logger=logger,
                    org_url=org,
                    project=project,
                    cwd=repo_dir,
                )
            return PRResult(
                repo_name=repo_name,
                source_branch=source_branch,
                target_branch=target_branch,
                pr_id=str(pr_id) if pr_id is not None else None,
                web_url=web_url,
                has_conflict=has_conflict,
                merge_status=merge_status,
            )
        except ProcessError as exc:
            if not self._is_existing_pr_error(exc):
                raise
            existing = self._fetch_existing_pr(
                repo_name=repo_name,
                source_branch=source_branch,
                target_branch=target_branch,
                dry_run=dry_run,
                logger=logger,
                org=org,
                project=project,
            )
            if not existing:
                raise
            if existing.get("pull_request_id"):
                pr_id = str(existing.get("pull_request_id"))
            else:
                pr_id = None
            return PRResult(
                repo_name=repo_name,
                source_branch=source_branch,
                target_branch=target_branch,
                pr_id=pr_id,
                web_url=existing.get("web_url"),
                has_conflict=bool(existing.get("has_conflict")),
                merge_status=existing.get("merge_status"),
            )

    def get_pr_status(
        self,
        pr_id: str,
        *,
        repo_name: str | None = None,
        dry_run: bool = False,
        logger=None,
    ) -> PRResult:
        org: str | None = None
        project: str | None = None
        if repo_name:
            org, project = self._azure_defaults_for_repo(repo_name, dry_run=dry_run, logger=logger)
        cmd = self._build_pr_show_command(pr_id, org_url=org, project=project)
        result = run_command(cmd, dry_run=dry_run, logger=logger)
        payload = self._parse_pr_payload(result.stdout) or {}
        merge_status = payload.get("mergeStatus")
        has_conflict = self._is_conflict_status(merge_status)
        source_ref = payload.get("sourceRefName") or ""
        target_ref = payload.get("targetRefName") or ""
        source_branch = source_ref.replace("refs/heads/", "")
        target_branch = target_ref.replace("refs/heads/", "")
        web_url = self._extract_web_url(payload, repo_name or "")
        return PRResult(
            repo_name=repo_name or "",
            source_branch=source_branch,
            target_branch=target_branch,
            pr_id=str(payload.get("pullRequestId")) if payload.get("pullRequestId") else str(pr_id),
            web_url=web_url,
            has_conflict=has_conflict,
            merge_status=merge_status,
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
        repo_dir = repo_path(self._ws, repo_name)
        org, project = self._azure_defaults_for_repo(repo_name, dry_run=dry_run, logger=logger)
        cmd = self._build_pr_list_command(
            repo_name=repo_name,
            source_branch=source_branch,
            target_branch=target_branch,
            org=org,
            project=project,
        )
        result = run_command(cmd, cwd=repo_dir, dry_run=dry_run, logger=logger)
        payload = self._parse_pr_payload(result.stdout)
        if not isinstance(payload, list):
            return []
        results: list[PRResult] = []
        for pr in payload:
            pr_id = pr.get("pullRequestId")
            merge_status = pr.get("mergeStatus")
            has_conflict = self._is_conflict_status(merge_status)
            web_url = self._extract_web_url(pr, repo_name)
            results.append(
                PRResult(
                    repo_name=repo_name,
                    source_branch=source_branch,
                    target_branch=target_branch,
                    pr_id=str(pr_id) if pr_id is not None else None,
                    web_url=web_url,
                    has_conflict=has_conflict,
                    merge_status=merge_status,
                )
            )
        return results
