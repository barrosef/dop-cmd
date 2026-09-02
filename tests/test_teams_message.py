import unittest

from dop.cli import format_teams_message


class TestTeamsMessage(unittest.TestCase):
    def test_single_jira_no_conflict(self):
        msg = format_teams_message(
            "OG-101", "https://csptech.atlassian.net/browse/OG-101", [],
            [{
                "repo": "lifesupport-api",
                "source_branch": "OG-101-feature",
                "target_branch": "hml",
                "web_url": "https://example.test/pr/1",
                "has_conflict": False,
            }],
        )
        self.assertIn("PRs - OG-101 - https://csptech.atlassian.net/browse/OG-101", msg)
        self.assertIn("Conflito: Não", msg)
        self.assertIn("App: lifesupport-api", msg)

    def test_with_conflict(self):
        msg = format_teams_message(
            "OG-101", "https://x/OG-101", [],
            [{
                "repo": "optum-support-be",
                "source_branch": "OG-101",
                "target_branch": "desenv",
                "web_url": "https://example.test/pr/2",
                "has_conflict": True,
            }],
        )
        self.assertIn("Conflito: Sim", msg)

    def test_multi_jira_header(self):
        msg = format_teams_message(
            "OG-1550", "https://x/OG-1550", ["OG-1551", "OG-3030"],
            [{
                "repo": "lifesupport-api",
                "source_branch": "OG-1550",
                "target_branch": "hml",
                "web_url": "https://example.test/pr/9",
                "has_conflict": False,
            }],
        )
        self.assertIn("PRs - OG-1550 + OG-1551 + OG-3030 - https://x/OG-1550", msg)


if __name__ == "__main__":
    unittest.main()
