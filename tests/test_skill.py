"""SKILL.md procedure text that live runs showed is load-bearing."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "review-code", "SKILL.md")


class AskUserQuestionLoading(unittest.TestCase):
    """AskUserQuestion is a deferred tool in Claude Code: until ToolSearch loads it the model sees it as absent
    and skips the ask-once questions (live runs, review-code-myo)."""

    def setUp(self):
        with open(SKILL, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_names_toolsearch_load_before_unavailable(self):
        m = re.search(r"ToolSearch[^\n]*select:AskUserQuestion", self.text)
        self.assertIsNotNone(m, "SKILL.md must tell the model to load AskUserQuestion with ToolSearch")

    def test_unavailable_only_after_failed_load(self):
        self.assertRegex(self.text, r"(?s)load it[^.]*before[^.]*unavailable")


if __name__ == "__main__":
    unittest.main()
