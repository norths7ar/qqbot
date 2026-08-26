import unittest
from pathlib import Path

from qqbot.runtime.paths import PROJECT_ROOT


class ProjectRootTests(unittest.TestCase):
    def test_project_root_is_repository_root(self) -> None:
        expected_root = Path(__file__).resolve().parents[1]

        self.assertEqual(PROJECT_ROOT, expected_root)
        self.assertTrue((PROJECT_ROOT / "bot.py").is_file())
        self.assertTrue((PROJECT_ROOT / "pyproject.toml").is_file())
