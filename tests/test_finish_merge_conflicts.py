import argparse
import unittest
from unittest.mock import MagicMock, patch

from dop import cli
from dop.core.errors import ValidationError
from dop.platform.base import PRResult


def _make_args(repo=None, dry_run=False, workspace=None):
    return argparse.Namespace(
        command="finish-merge-conflicts",
        repo=repo,
        dry_run=dry_run,
        workspace=workspace,
    )


def _make_workspace(repos=None):
    ws = MagicMock()
    ws.root = "/tmp/test"
    ws.repos = {}
    for name in (repos or ["repo-a"]):
        cfg = MagicMock()
        cfg.base_branch = "OG-GLOBAL"
        cfg.pr_targets = ["OG-GLOBAL"]
        cfg.dir = f"repos/{name}"
        ws.repos[name] = cfg
    ws.pr_doc_suffix_map = {}
    ws.platform = "azure_devops"
    return ws


class TestFinishMergeConflicts(unittest.TestCase):
    @patch("dop.cli.build_platform_provider")
    @patch("dop.cli.push_branch")
    @patch("dop.cli.log_diff")
    @patch("dop.cli.has_pending_merge")
    @patch("dop.cli.current_branch")
    @patch("dop.cli.repo_path")
    def test_push_and_create_pr(
        self, mock_repo_path, mock_current, mock_pending, mock_log_diff, mock_push, mock_platform_builder
    ):
        mock_repo_path.return_value = "/tmp/test/repos/repo-a"
        mock_current.return_value = "merge-conflicts-desenv-from-OG-GLOBAL"
        mock_pending.return_value = False
        mock_log_diff.return_value = ["abc1234 merge commit"]
        platform = MagicMock()
        platform.list_prs.return_value = []
        platform.create_pr.return_value = PRResult(
            repo_name="repo-a",
            source_branch="merge-conflicts-desenv-from-OG-GLOBAL",
            target_branch="desenv",
            pr_id="500",
            web_url="https://example.test/pr/500",
            has_conflict=False,
            merge_status="succeeded",
        )
        mock_platform_builder.return_value = platform

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_finish_merge_conflicts(_make_args(repo="repo-a"), logger, ws, auth)

        self.assertEqual(result, 0)
        mock_push.assert_called_once()
        platform.create_pr.assert_called_once()

    @patch("dop.cli.has_pending_merge")
    @patch("dop.cli.current_branch")
    @patch("dop.cli.repo_path")
    def test_error_on_pending_merge(self, mock_repo_path, mock_current, mock_pending):
        mock_repo_path.return_value = "/tmp/test/repos/repo-a"
        mock_current.return_value = "merge-conflicts-desenv-from-OG-GLOBAL"
        mock_pending.return_value = True

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        with self.assertRaises(ValidationError) as ctx:
            cli.handle_finish_merge_conflicts(_make_args(repo="repo-a"), logger, ws, auth)
        self.assertIn("Merge incompleto", str(ctx.exception))

    @patch("dop.cli.log_diff")
    @patch("dop.cli.has_pending_merge")
    @patch("dop.cli.current_branch")
    @patch("dop.cli.repo_path")
    def test_error_on_no_commits(self, mock_repo_path, mock_current, mock_pending, mock_log_diff):
        mock_repo_path.return_value = "/tmp/test/repos/repo-a"
        mock_current.return_value = "merge-conflicts-desenv-from-OG-GLOBAL"
        mock_pending.return_value = False
        mock_log_diff.return_value = []

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        with self.assertRaises(ValidationError) as ctx:
            cli.handle_finish_merge_conflicts(_make_args(repo="repo-a"), logger, ws, auth)
        self.assertIn("Nenhum commit", str(ctx.exception))

    @patch("dop.cli.build_platform_provider")
    @patch("dop.cli.push_branch")
    @patch("dop.cli.log_diff")
    @patch("dop.cli.has_pending_merge")
    @patch("dop.cli.current_branch")
    @patch("dop.cli.repo_path")
    def test_skip_existing_pr(
        self, mock_repo_path, mock_current, mock_pending, mock_log_diff, mock_push, mock_platform_builder
    ):
        mock_repo_path.return_value = "/tmp/test/repos/repo-a"
        mock_current.return_value = "merge-conflicts-desenv-from-OG-GLOBAL"
        mock_pending.return_value = False
        mock_log_diff.return_value = ["abc1234 merge commit"]
        existing_pr = PRResult(
            repo_name="repo-a",
            source_branch="merge-conflicts-desenv-from-OG-GLOBAL",
            target_branch="desenv",
            pr_id="500",
            web_url="https://example.test/pr/500",
            has_conflict=False,
            merge_status="succeeded",
        )
        platform = MagicMock()
        platform.list_prs.return_value = [existing_pr]
        mock_platform_builder.return_value = platform

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_finish_merge_conflicts(_make_args(repo="repo-a"), logger, ws, auth)

        self.assertEqual(result, 0)
        platform.create_pr.assert_not_called()

    @patch("dop.cli.build_platform_provider")
    @patch("dop.cli.push_branch")
    @patch("dop.cli.log_diff")
    @patch("dop.cli.has_pending_merge")
    @patch("dop.cli.current_branch")
    @patch("dop.cli.repo_path")
    def test_dry_run(
        self, mock_repo_path, mock_current, mock_pending, mock_log_diff, mock_push, mock_platform_builder
    ):
        mock_repo_path.return_value = "/tmp/test/repos/repo-a"
        mock_current.return_value = "merge-conflicts-desenv-from-OG-GLOBAL"
        mock_pending.return_value = False
        mock_log_diff.return_value = ["abc1234 merge commit"]
        platform = MagicMock()
        platform.list_prs.return_value = []
        platform.create_pr.return_value = PRResult(
            repo_name="repo-a",
            source_branch="merge-conflicts-desenv-from-OG-GLOBAL",
            target_branch="desenv",
            pr_id=None,
            web_url=None,
            has_conflict=False,
            merge_status=None,
        )
        mock_platform_builder.return_value = platform

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_finish_merge_conflicts(_make_args(repo="repo-a", dry_run=True), logger, ws, auth)

        self.assertEqual(result, 0)
        self.assertTrue(mock_push.call_args.kwargs.get("dry_run"))


if __name__ == "__main__":
    unittest.main()
