"""One definition each for constants and helpers that review once found copied across scripts (review-code-87o1)."""
import glob
import os
import unittest

import paths  # noqa: F401

import ledger  # noqa: E402
import target  # noqa: E402


def _scripts_text():
    for f in sorted(glob.glob(os.path.join(paths.SCRIPTS, "*.py"))):
        with open(f, encoding="utf-8") as fh:
            yield os.path.basename(f), fh.read()


class SingleDefinition(unittest.TestCase):
    def test_inplace_kinds_are_defined_once_in_target(self):  # 87o1.3
        self.assertFalse(hasattr(ledger, "INPLACE"))
        copies = [n for n, t in _scripts_text() if '"staged", "unstaged", "worktree", "path", "repo")' in t]
        self.assertEqual(copies, ["target.py"])

    def test_regular_file_modes_are_spelled_once_in_target(self):  # 87o1.2
        copies = [n for n, t in _scripts_text() for _ in range(t.count('("100644", "100755")'))]
        self.assertEqual(copies, ["target.py"])
        self.assertEqual(target.REGULAR, ("100644", "100755"))
