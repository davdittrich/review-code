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


class RequireUtf8(unittest.TestCase):  # 87o1.6
    def test_a_lone_surrogate_refuses_with_the_callers_reason_and_detail(self):
        with self.assertRaises(target.Refuse) as cm:
            target.require_utf8(["fine", "bad\udcff"], "non-utf8-name", "why")
        self.assertEqual((cm.exception.reason, cm.exception.detail), ("non-utf8-name", "why"))

    def test_valid_text_passes(self):
        target.require_utf8(["fine", "café", ""], "non-utf8-name", "why")

    def test_the_posting_guard_keeps_its_own_reason(self):
        import post
        with self.assertRaises(post.Refuse) as cm:
            post._checked(["bad\udcff"])
        self.assertEqual(cm.exception.reason, "unsafe-text")


class DifferencePath(unittest.TestCase):  # 87o1.5
    PIN = {"pinned_tree": "t", "pinned_head": "h", "difference_count": 2}

    def basis(self, **keys):
        return ledger._basis(dict(self.PIN, **keys))

    def test_a_run_with_only_difference_names_it(self):
        self.assertEqual(ledger.difference_path({"difference": "/r/d.json"}), "/r/d.json")
        self.assertIn("checkout_difference: /r/d.json (2 paths)", self.basis(difference="/r/d.json"))

    def test_difference_list_wins_when_both_are_present(self):
        both = {"difference": "/r/d.json", "difference_list": "/r/l.txt"}
        self.assertEqual(ledger.difference_path(both), "/r/l.txt")
        self.assertIn("checkout_difference: /r/l.txt (2 paths)", self.basis(**both))

    def test_a_run_pinned_before_the_difference_shows_nothing(self):
        self.assertIsNone(ledger.difference_path({}))
        self.assertEqual(ledger._basis({}), "")
