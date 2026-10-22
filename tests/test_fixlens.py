"""finder.md carries the fix-style diff checks, each tied to a concrete trigger."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FixLens(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "agents", "finder.md"), encoding="utf-8") as f:
            self.t = f.read()
        self.low = self.t.lower()

    def test_group_present(self):
        self.assertIn("Fix-style diffs", self.t)

    def test_narrowed_guard(self):
        for w in ("narrow", "caller", "old behaviour", "flags, caches"):
            self.assertIn(w, self.low)

    def test_regression_patterns(self):
        for w in ("incomplete enumeration", "partial fix", "silent fallthrough", "stale test"):
            self.assertIn(w, self.low)

    def test_paper_over(self):
        for w in ("special-cases the failing input", "swallows an error", "presentation"):
            self.assertIn(w, self.low)

    def test_large_deletion(self):
        for w in ("50 lines", "could do before", "surviving path"):
            self.assertIn(w, self.low)

    def test_trigger_sentence_and_no_nit(self):
        self.assertIn("nothing in this group is reportable without a concrete trigger", self.low)
        self.assertNotIn("`nit`", self.t)


if __name__ == "__main__":
    unittest.main()
