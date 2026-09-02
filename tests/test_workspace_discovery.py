import os
import tempfile
import unittest
from pathlib import Path

from dop.config.discovery import find_workspace_by_cwd, get_workspace
from dop.core.errors import ValidationError


class TestWorkspaceDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ws1 = self.root / "ws1"
        self.ws2 = self.root / "ws2"
        self.ws1.mkdir()
        self.ws2.mkdir()
        self.config_path = self.root / "config.toml"
        self.config_path.write_text(
            f"""
[workspaces.one]
root = "{self.ws1}"

[workspaces.two]
root = "{self.ws2}"
""".lstrip(),
            encoding="utf-8",
        )
        os.environ["DOP_CONFIG"] = str(self.config_path)

    def tearDown(self):
        os.environ.pop("DOP_CONFIG", None)
        self.tmp.cleanup()

    def test_find_workspace_by_cwd(self):
        cwd = self.ws1 / "nested"
        cwd.mkdir()
        ws = find_workspace_by_cwd(cwd)
        self.assertEqual(ws.name, "one")

    def test_get_workspace_by_name(self):
        ws = get_workspace("two", cwd=self.ws1)
        self.assertEqual(ws.name, "two")

    def test_find_workspace_none(self):
        other = self.root / "other"
        other.mkdir()
        with self.assertRaises(ValidationError):
            find_workspace_by_cwd(other)


if __name__ == "__main__":
    unittest.main()
