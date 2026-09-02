import unittest

from dop import cli


class TestCLIParsing(unittest.TestCase):
    def test_demand_init_basic(self):
        args = cli.build_parser().parse_args(
            ["demand-init", "OG-101", "--repos", "lifesupport-api,optum-support-be"]
        )
        self.assertEqual(args.command, "demand-init")
        self.assertEqual(args.jira_key, "OG-101")
        self.assertEqual(args.repos, "lifesupport-api,optum-support-be")
        self.assertIsNone(args.linked)
        self.assertIsNone(args.branch)
        self.assertEqual(args.func, cli.handle_demand_init)

    def test_demand_init_with_linked_and_branch(self):
        args = cli.build_parser().parse_args([
            "demand-init", "OG-1550",
            "--repos", "lifesupport-api",
            "--linked", "OG-1551,OG-3030",
            "--branch", "OG-1550-merged",
        ])
        self.assertEqual(args.linked, "OG-1551,OG-3030")
        self.assertEqual(args.branch, "OG-1550-merged")

    def test_link_parser(self):
        args = cli.build_parser().parse_args(["link", "OG-1550", "OG-1551", "OG-3030"])
        self.assertEqual(args.command, "link")
        self.assertEqual(args.jira_key, "OG-1550")
        self.assertEqual(args.linked_keys, ["OG-1551", "OG-3030"])

    def test_update_repos(self):
        args = cli.build_parser().parse_args(["update-repos"])
        self.assertEqual(args.command, "update-repos")
        self.assertIsNone(args.repo)

    def test_git_push_with_force(self):
        args = cli.build_parser().parse_args(["git-push", "OG-101", "--repo", "lifesupport-api", "--force"])
        self.assertTrue(args.force)
        self.assertEqual(args.repo, "lifesupport-api")

    def test_pr_publish_requires_repo_and_title(self):
        parser = cli.build_parser()
        from dop.core.errors import ValidationError
        with self.assertRaises(ValidationError):
            parser.parse_args(["pr-publish", "OG-101"])

    def test_pr_publish_full(self):
        args = cli.build_parser().parse_args([
            "pr-publish", "OG-101",
            "--repo", "lifesupport-api",
            "--title", "OG-101: fix - lifesupport-api",
            "--body", "Jira: https://x",
            "--source-branch", "OG-101-feature",
        ])
        self.assertEqual(args.repo, "lifesupport-api")
        self.assertEqual(args.title, "OG-101: fix - lifesupport-api")
        self.assertEqual(args.body, "Jira: https://x")
        self.assertEqual(args.source_branch, "OG-101-feature")

    def test_teams_message(self):
        args = cli.build_parser().parse_args(["teams-message", "OG-101"])
        self.assertEqual(args.command, "teams-message")
        self.assertEqual(args.jira_key, "OG-101")

    def test_solve_conflict(self):
        args = cli.build_parser().parse_args(["solve-conflict", "OG-101", "--repo", "lifesupport-api"])
        self.assertEqual(args.command, "solve-conflict")
        self.assertEqual(args.repo, "lifesupport-api")

    def test_integrate_desenv(self):
        args = cli.build_parser().parse_args(["integrate-desenv"])
        self.assertEqual(args.command, "integrate-desenv")

    def test_show(self):
        args = cli.build_parser().parse_args(["show", "OG-101"])
        self.assertEqual(args.command, "show")

    def test_aaa_parser(self):
        args = cli.build_parser().parse_args(["aaa", "lifesupport-api", "-k", "FooTest"])
        self.assertEqual(args.command, "aaa")
        self.assertEqual(args.targets, ["lifesupport-api"])
        self.assertEqual(args.k, "FooTest")
        self.assertFalse(args.fresh_report)

    def test_aaa_all(self):
        args = cli.build_parser().parse_args(["aaa", "all", "--fresh-report"])
        self.assertEqual(args.targets, ["all"])
        self.assertTrue(args.fresh_report)

    def test_it_parser(self):
        args = cli.build_parser().parse_args(["it", "optum-support-be"])
        self.assertEqual(args.command, "it")
        self.assertEqual(args.targets, ["optum-support-be"])
        self.assertIsNone(args.k)
        self.assertFalse(args.fresh_report)

    def test_version_flag(self):
        import contextlib
        import io
        from dop import __version__
        for flag in ("--version", "-v"):
            buf = io.StringIO()
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(buf):
                cli.build_parser().parse_args([flag])
            self.assertEqual(cm.exception.code, 0)
            self.assertIn(__version__, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
