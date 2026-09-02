import argparse
import unittest
from unittest.mock import MagicMock, patch

from dop import cli
from dop.platform.base import PRResult


def _make_args(repo=None, dry_run=False, workspace=None):
    args = argparse.Namespace(
        command="integrate-desenv",
        repo=repo,
        dry_run=dry_run,
        workspace=workspace,
    )
    return args


def _make_workspace(repos=None):
    ws = MagicMock()
    ws.root = "/tmp/test"
    ws.repos = {}
    for name in (repos or ["repo-a", "repo-b"]):
        cfg = MagicMock()
        cfg.base_branch = "OG-GLOBAL"
        cfg.pr_targets = ["OG-GLOBAL"]
        cfg.dir = f"repos/{name}"
        ws.repos[name] = cfg
    ws.pr_doc_suffix_map = {}
    ws.platform = "azure_devops"
    return ws


class TestIntegrateDesenv(unittest.TestCase):
    @patch("dop.cli.build_platform_provider")
    @patch("dop.cli.log_diff")
    @patch("dop.cli.fetch_origin")
    def test_skip_when_no_new_commits(self, mock_fetch, mock_log_diff, mock_platform_builder):
        mock_log_diff.return_value = []
        platform = MagicMock()
        mock_platform_builder.return_value = platform

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_integrate_desenv(_make_args(), logger, ws, auth)

        self.assertEqual(result, 0)
        mock_fetch.assert_called_once()
        platform.create_pr.assert_not_called()

    @patch("dop.cli.build_platform_provider")
    @patch("dop.cli.log_diff")
    @patch("dop.cli.fetch_origin")
    def test_create_pr_when_diff_exists(self, mock_fetch, mock_log_diff, mock_platform_builder):
        mock_log_diff.return_value = ["abc1234 feat: something"]
        platform = MagicMock()
        platform.list_prs.return_value = []
        platform.create_pr.return_value = PRResult(
            repo_name="repo-a",
            source_branch="OG-GLOBAL",
            target_branch="desenv",
            pr_id="1234",
            web_url="https://example.test/pr/1234",
            has_conflict=False,
            merge_status="succeeded",
        )
        mock_platform_builder.return_value = platform

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_integrate_desenv(_make_args(), logger, ws, auth)

        self.assertEqual(result, 0)
        platform.create_pr.assert_called_once()
        call_kwargs = platform.create_pr.call_args
        self.assertEqual(call_kwargs.kwargs["source_branch"], "OG-GLOBAL")
        self.assertEqual(call_kwargs.kwargs["target_branch"], "desenv")

    @patch("dop.cli.build_platform_provider")
    @patch("dop.cli.log_diff")
    @patch("dop.cli.fetch_origin")
    def test_detect_conflict_on_created_pr(self, mock_fetch, mock_log_diff, mock_platform_builder):
        mock_log_diff.return_value = ["abc1234 feat: something"]
        platform = MagicMock()
        platform.list_prs.return_value = []
        platform.create_pr.return_value = PRResult(
            repo_name="repo-a",
            source_branch="OG-GLOBAL",
            target_branch="desenv",
            pr_id="1235",
            web_url="https://example.test/pr/1235",
            has_conflict=True,
            merge_status="conflicts",
        )
        mock_platform_builder.return_value = platform

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_integrate_desenv(_make_args(), logger, ws, auth)

        self.assertEqual(result, 0)
        logger.warn.assert_called()

    @patch("dop.cli.build_platform_provider")
    @patch("dop.cli.log_diff")
    @patch("dop.cli.fetch_origin")
    def test_skip_existing_active_pr(self, mock_fetch, mock_log_diff, mock_platform_builder):
        mock_log_diff.return_value = ["abc1234 feat: something"]
        existing_pr = PRResult(
            repo_name="repo-a",
            source_branch="OG-GLOBAL",
            target_branch="desenv",
            pr_id="999",
            web_url="https://example.test/pr/999",
            has_conflict=False,
            merge_status="succeeded",
        )
        platform = MagicMock()
        platform.list_prs.return_value = [existing_pr]
        mock_platform_builder.return_value = platform

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_integrate_desenv(_make_args(), logger, ws, auth)

        self.assertEqual(result, 0)
        platform.create_pr.assert_not_called()

    @patch("dop.cli.build_platform_provider")
    @patch("dop.cli.log_diff")
    @patch("dop.cli.fetch_origin")
    def test_dry_run(self, mock_fetch, mock_log_diff, mock_platform_builder):
        mock_log_diff.return_value = ["abc1234 feat: something"]
        platform = MagicMock()
        platform.list_prs.return_value = []
        platform.create_pr.return_value = PRResult(
            repo_name="repo-a",
            source_branch="OG-GLOBAL",
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

        result = cli.handle_integrate_desenv(_make_args(dry_run=True), logger, ws, auth)

        self.assertEqual(result, 0)
        mock_fetch.assert_called_once()
        self.assertTrue(mock_fetch.call_args.kwargs.get("dry_run"))


if __name__ == "__main__":
    unittest.main()
