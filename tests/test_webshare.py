import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import webshare


class Paths(unittest.TestCase):
    def test_safe_relative_blocks_escape(self):
        root = Path(tempfile.mkdtemp())
        self.assertIsNone(webshare.safe_relative("../secret", root))
        self.assertEqual(webshare.safe_relative("", root), "")
        self.assertEqual(webshare.safe_relative("a/b", root), "a/b")

    def test_safe_target_rejects_names(self):
        root = Path(tempfile.mkdtemp())
        root.mkdir(exist_ok=True)
        self.assertEqual(webshare.safe_target(root, "../x"), root.resolve() / "x")
        self.assertIsNone(webshare.safe_target(root, "."))
        target = webshare.safe_target(root, "file.txt")
        self.assertEqual(target, root.resolve() / "file.txt")

    def test_unique_path_does_not_overwrite(self):
        root = Path(tempfile.mkdtemp())
        root.mkdir(exist_ok=True)
        first = root / "data.txt"
        first.touch()
        second = webshare.unique_path(root, "data.txt")
        self.assertFalse(second.exists())
        self.assertEqual(second.name, "data_1.txt")


if __name__ == "__main__":
    unittest.main()
