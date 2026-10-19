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


class Procedure(unittest.TestCase):
    """03-04: lanes, verdicts, reproduction and render, and a pre-approval that leaves repro.py to the harness."""

    REPRO_CALL = "python3 /x/skills/review-code/scripts/repro.py run --run r --id F-0123456789 --test t.py -- python3 t.py"

    def setUp(self):
        with open(SKILL, encoding="utf-8") as fh:
            self.text = fh.read()

    def allowed(self):
        fm = self.text.split("\n---", 1)[0]
        m = re.search(r"^allowed-tools:[ \t]*\n((?:[ \t]+- .*\n)+)", fm + "\n", re.M)
        self.assertIsNotNone(m, "allowed-tools must be a YAML list")
        return [ln.strip()[2:].strip() for ln in m.group(1).splitlines()]

    def test_allowed_tools_names_three_scripts_not_repro(self):
        items = self.allowed()
        self.assertEqual(len(items), 3, items)
        for name in ("target.py", "ledger.py", "report.py"):
            self.assertTrue(any(name in i for i in items), name)
        for i in items:
            self.assertNotIn("repro", i)
            self.assertNotIn("scripts/*", i)
            pat = re.fullmatch(r"Bash\((.*)\)", i)
            self.assertIsNotNone(pat, i)
            rx = ".*".join(re.escape(p) for p in pat.group(1).replace("${CLAUDE_SKILL_DIR}", "/x/skills/review-code")
                           .split("*"))
            self.assertIsNone(re.fullmatch(rx, self.REPRO_CALL), i)

    def test_steps_in_order(self):
        steps = ["review-code:finder", "report.py ingest", "review-code:verifier", "report.py verdicts",
                 "repro.py run", "report.py render"]
        at = [self.text.find(s) for s in steps]
        self.assertNotIn(-1, at, dict(zip(steps, at)))
        self.assertEqual(at, sorted(at), dict(zip(steps, at)))

    def test_delimiters_and_flags(self):
        for s in ("REVIEW_FINDINGS", "REVIEW_VERDICTS", "REPRO_TEST", "REPRO-<id>", "--json", "--sarif"):
            self.assertIn(s, self.text)


if __name__ == "__main__":
    unittest.main()
