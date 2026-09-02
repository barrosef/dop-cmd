import argparse
import unittest
from unittest.mock import MagicMock, patch

from dop import cli


def _make_args(repo=None, dry_run=False, workspace=None):
    return argparse.Namespace(
        command="prepare-merge-conflicts",
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
    return ws


class TestPrepareMergeConflicts(unittest.TestCase):
    @patch("dop.cli.merge_remote_branch")
    @patch("dop.cli.checkout_new_branch_from_remote")
    @patch("dop.cli.fetch_origin")
    @patch("dop.cli.repo_path")
    def test_checkout_from_desenv(self, mock_repo_path, mock_fetch, mock_checkout, mock_merge):
        mock_repo_path.return_value = "/tmp/test/repos/repo-a"
        mock_merge.return_value = (True, [])

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_prepare_merge_conflicts(_make_args(repo="repo-a"), logger, ws, auth)

        self.assertEqual(result, 0)
        mock_checkout.assert_called_once_with(
            "repo-a",
            "merge-conflicts-desenv-from-OG-GLOBAL",
            "desenv",
            workspace=ws,
            dry_run=False,
            logger=logger,
        )

    @patch("dop.cli.merge_remote_branch")
    @patch("dop.cli.checkout_new_branch_from_remote")
    @patch("dop.cli.fetch_origin")
    @patch("dop.cli.repo_path")
    def test_merge_no_conflicts(self, mock_repo_path, mock_fetch, mock_checkout, mock_merge):
        mock_repo_path.return_value = "/tmp/test/repos/repo-a"
        mock_merge.return_value = (True, [])

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_prepare_merge_conflicts(_make_args(repo="repo-a"), logger, ws, auth)

        self.assertEqual(result, 0)
        logger.info.assert_any_call("Merge sem conflitos em repo-a. Execute finish-merge-conflicts.")

    @patch("dop.cli.merge_remote_branch")
    @patch("dop.cli.checkout_new_branch_from_remote")
    @patch("dop.cli.fetch_origin")
    @patch("dop.cli.repo_path")
    def test_merge_with_conflicts(self, mock_repo_path, mock_fetch, mock_checkout, mock_merge):
        mock_repo_path.return_value = "/tmp/test/repos/repo-a"
        mock_merge.return_value = (False, ["src/main.java", "src/config.xml"])

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_prepare_merge_conflicts(_make_args(repo="repo-a"), logger, ws, auth)

        self.assertEqual(result, 0)
        logger.warn.assert_any_call("Conflitos em repo-a:")
        logger.warn.assert_any_call("  src/main.java")
        logger.warn.assert_any_call("  src/config.xml")

    @patch("dop.cli.merge_remote_branch")
    @patch("dop.cli.checkout_new_branch_from_remote")
    @patch("dop.cli.fetch_origin")
    @patch("dop.cli.repo_path")
    def test_dry_run(self, mock_repo_path, mock_fetch, mock_checkout, mock_merge):
        mock_repo_path.return_value = "/tmp/test/repos/repo-a"
        mock_merge.return_value = (True, [])

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_prepare_merge_conflicts(_make_args(repo="repo-a", dry_run=True), logger, ws, auth)

        self.assertEqual(result, 0)
        self.assertTrue(mock_fetch.call_args.kwargs.get("dry_run"))
        self.assertTrue(mock_checkout.call_args.kwargs.get("dry_run"))
        self.assertTrue(mock_merge.call_args.kwargs.get("dry_run"))


if __name__ == "__main__":
    unittest.main()
