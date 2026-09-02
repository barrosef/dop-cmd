import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dop import cli
from dop.config.schema import RepoConfig, WorkspaceConfig


def _make_workspace(tmp: str, repos=("lifesupport-api", "optum-support-be")) -> WorkspaceConfig:
    repo_dict = {}
    for name in repos:
        repo_dict[name] = RepoConfig(
            name=name,
            dir=f"repos/{name}",
            base_branch="hml",
            pr_targets=["hml", "desenv"],
        )
    return WorkspaceConfig(
        name="optum-test",
        root=tmp,
        demands_dir="docs/RFC",
        jira_base_url="https://csptech.atlassian.net/browse",
        repos=repo_dict,
    )


def _args(**kw):
    defaults = {"command": "x", "dry_run": False, "workspace": None, "force": False}
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class TestDemandInit(unittest.TestCase):
    @patch("dop.cli.push_branch")
    @patch("dop.cli.create_branch")
    @patch("dop.cli.checkout_branch")
    @patch("dop.cli.list_local_branches", return_value=[])
    @patch("dop.cli.pull_branch_if_exists", return_value=True)
    def test_creates_state_and_alias(self, _pull, _list, _checkout, _create, _push):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            args = _args(
                command="demand-init",
                jira_key="OG-1550",
                repos="lifesupport-api,optum-support-be",
                linked="OG-1551,OG-3030",
                branch=None,
            )
            rc = cli.handle_demand_init(args, MagicMock(), ws, MagicMock())
            self.assertEqual(rc, 0)

            state_path = Path(tmp) / "docs/RFC/OG-1550/.state.json"
            self.assertTrue(state_path.exists())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["jiraKey"], "OG-1550")
            self.assertEqual(set(state["linkedJiraKeys"]), {"OG-1551", "OG-3030"})
            self.assertTrue(state["repos"]["lifesupport-api"]["impacted"])
            self.assertTrue(state["repos"]["optum-support-be"]["impacted"])
            self.assertEqual(state["repos"]["lifesupport-api"]["branch"], "OG-1550")

            # Aliases
            for k in ["OG-1551", "OG-3030"]:
                alias = Path(tmp) / f"docs/RFC/{k}/.alias"
                self.assertTrue(alias.exists())
                self.assertEqual(alias.read_text(encoding="utf-8").strip(), "OG-1550")

            _create.assert_called()  # branches criadas
            _push.assert_called()    # push inicial


class TestLinkHandler(unittest.TestCase):
    @patch("dop.cli.write_alias")
    def test_adds_only_new_links(self, mock_write):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            # cria estado prévio
            state_path = Path(tmp) / "docs/RFC/OG-1550/.state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({
                "jiraKey": "OG-1550",
                "jiraUrl": "https://csptech.atlassian.net/browse/OG-1550",
                "linkedJiraKeys": ["OG-1551"],
                "createdAt": "2026-01-01T00:00:00Z",
                "repos": {},
                "prs": [],
                "commands_log": [],
            }))
            args = _args(
                command="link",
                jira_key="OG-1550",
                linked_keys=["OG-1551", "OG-3030"],
            )
            rc = cli.handle_link(args, MagicMock(), ws, MagicMock())
            self.assertEqual(rc, 0)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(set(state["linkedJiraKeys"]), {"OG-1551", "OG-3030"})
            # write_alias só para o novo
            self.assertEqual(mock_write.call_count, 1)
            self.assertEqual(mock_write.call_args.args[1], "OG-3030")


class TestPrPublish(unittest.TestCase):
    @patch("dop.cli.checkout_branch")
    @patch("dop.cli.current_branch", return_value="OG-101")
    @patch("dop.cli.push_branch")
    @patch("dop.cli.commit_changes")
    @patch("dop.cli.has_uncommitted_changes", return_value=True)
    @patch("dop.cli.build_platform_provider")
    def test_commits_pushes_and_creates_prs(self, mock_platform_builder, _has, _commit, _push, _cur, _checkout):
        from dop.platform.base import PRResult
        platform = MagicMock()
        platform.create_pr.side_effect = [
            PRResult(repo_name="lifesupport-api", source_branch="OG-101", target_branch="hml",
                     pr_id="11", web_url="https://x/pr/11", has_conflict=False, merge_status="ok"),
            PRResult(repo_name="lifesupport-api", source_branch="OG-101", target_branch="desenv",
                     pr_id="12", web_url="https://x/pr/12", has_conflict=True, merge_status="conflict"),
        ]
        mock_platform_builder.return_value = platform

        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            # Estado prévio
            state_path = Path(tmp) / "docs/RFC/OG-101/.state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({
                "jiraKey": "OG-101",
                "jiraUrl": "https://csptech.atlassian.net/browse/OG-101",
                "linkedJiraKeys": [],
                "createdAt": "2026-01-01T00:00:00Z",
                "repos": {"lifesupport-api": {"impacted": True, "branch": "OG-101"}},
                "prs": [],
                "commands_log": [],
            }))

            args = _args(
                command="pr-publish",
                jira_key="OG-101",
                repo="lifesupport-api",
                title="OG-101: fix - lifesupport-api",
                body="Jira: https://x",
                source_branch=None,
                target=None,
                commit_message=None,
            )
            rc = cli.handle_pr_publish(args, MagicMock(), ws, MagicMock())
            self.assertEqual(rc, 0)

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(state["prs"]), 2)
            self.assertEqual(state["prs"][0]["pr_id"], "11")
            self.assertEqual(state["prs"][1]["target_branch"], "desenv")
            self.assertTrue(state["prs"][1]["has_conflict"])

            _commit.assert_called_once()
            _push.assert_called_once()
            self.assertEqual(platform.create_pr.call_count, 2)


class TestTeamsMessageHandler(unittest.TestCase):
    def test_writes_file_and_prints(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            state_path = Path(tmp) / "docs/RFC/OG-1550/.state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({
                "jiraKey": "OG-1550",
                "jiraUrl": "https://csptech.atlassian.net/browse/OG-1550",
                "linkedJiraKeys": ["OG-1551"],
                "createdAt": "2026-01-01T00:00:00Z",
                "repos": {},
                "prs": [{
                    "repo": "lifesupport-api", "source_branch": "OG-1550",
                    "target_branch": "hml", "pr_id": "1",
                    "web_url": "https://x/pr/1", "has_conflict": False,
                }],
                "commands_log": [],
            }))
            args = _args(command="teams-message", jira_key="OG-1550")
            rc = cli.handle_teams_message(args, MagicMock(), ws, MagicMock())
            self.assertEqual(rc, 0)
            msg_path = Path(tmp) / "docs/RFC/OG-1550/03-pr-team-message.md"
            self.assertTrue(msg_path.exists())
            content = msg_path.read_text(encoding="utf-8")
            self.assertIn("PRs - OG-1550 + OG-1551", content)
            self.assertIn("App: lifesupport-api", content)


if __name__ == "__main__":
    unittest.main()
