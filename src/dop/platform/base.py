from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PRResult:
    repo_name: str
    source_branch: str
    target_branch: str
    pr_id: str | None
    web_url: str | None
    has_conflict: bool
    merge_status: str | None


class PlatformProvider(ABC):
    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def get_pr_status(
        self,
        pr_id: str,
        *,
        repo_name: str | None = None,
        dry_run: bool = False,
        logger=None,
    ) -> PRResult:
        raise NotImplementedError

    @abstractmethod
    def list_prs(
        self,
        *,
        repo_name: str,
        source_branch: str,
        target_branch: str,
        dry_run: bool = False,
        logger=None,
    ) -> list[PRResult]:
        raise NotImplementedError
