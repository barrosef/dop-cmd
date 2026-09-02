import os
import tempfile
import unittest
from pathlib import Path

from dop.config.loader import load_config


class TestConfigLoader(unittest.TestCase):
    def test_load_config_with_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text(
                """
[workspaces.demo]
root = "/tmp/demo"
platform = "github"
auth_method = "token"

[workspaces.demo.credentials]
token_env = "GITHUB_TOKEN"

[workspaces.demo.platform_config]
org = "example-org"

[workspaces.demo.repos.app]
dir = "repos/app"
base_branch = "main"
pr_targets = ["main"]
primary = true
""".lstrip(),
                encoding="utf-8",
            )
            os.environ["DOP_CONFIG"] = str(config_path)
            try:
                cfg = load_config()
                self.assertIn("demo", cfg)
                ws = cfg["demo"]
                self.assertEqual(ws.root, "/tmp/demo")
                self.assertEqual(ws.platform, "github")
                self.assertEqual(ws.credentials.token_env, "GITHUB_TOKEN")
                self.assertIn("app", ws.repos)
                self.assertEqual(ws.repos["app"].base_branch, "main")
            finally:
                os.environ.pop("DOP_CONFIG", None)


if __name__ == "__main__":
    unittest.main()
