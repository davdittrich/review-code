"""Lane prompts against the wire formats report.py parses: keys, enums, read scope, tools."""
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "skills", "review-code", "scripts"))

import report  # noqa: E402


def parts(name):
    """(frontmatter lines, body lines) of agents/<name>.md."""
    with open(os.path.join(ROOT, "agents", name + ".md"), encoding="utf-8") as f:
        lines = f.read().splitlines()
    end = lines.index("---", 1)
    return lines[1:end], lines[end + 1:]


def first(body, prefix):
    return json.loads(next(line for line in body if line.startswith(prefix)))


class Finder(unittest.TestCase):
    def setUp(self):
        self.fm, self.body = parts("finder")
        self.text = "\n".join(self.body)

    def test_candidate_line_has_exactly_the_candidate_keys(self):
        self.assertEqual(set(first(self.body, '{"path"')),
                         set(report.CANDIDATE_REQUIRED + report.CANDIDATE_OPTIONAL))

    def test_inspected_line(self):
        o = first(self.body, '{"inspected"')
        self.assertEqual(list(o), ["inspected"])
        self.assertIsInstance(o["inspected"], list)

    def test_enum_words_and_blocking_sentence(self):
        for w in report.CATEGORIES + report.SEVERITIES:
            self.assertIn("`%s`" % w, self.text)
        sentence = next(s for s in re.split(r"(?<=\.)\s", self.text) if "`blocking`" in s and "only" in s)
        for w in report.BLOCKING:
            self.assertIn(w, sentence)

    def test_spec_input_only_ever_forbidden(self):
        for line in self.body:
            if "inputs/spec.md" in line:
                self.assertIn("never", line.lower())
        self.assertNotIn("docs/ccr.md", self.text)

    def test_do_not_flag_names_all_four_items(self):  # VERI-05; obeying the last two stays model-dependent
        low = self.text.lower()
        for item in ("linter", "lint-ignore", "unchanged lines", "input-dependent"):
            self.assertIn(item, low)

    def test_tools_read_only(self):
        self.assertIn("tools: Read, Grep, Glob", self.fm)


class Verifier(unittest.TestCase):
    def setUp(self):
        self.fm, self.body = parts("verifier")
        self.text = "\n".join(self.body)

    def test_verdict_line_has_exactly_the_verdict_and_cite_keys(self):
        o = first(self.body, '{"id"')
        self.assertEqual(set(o), set(report.VERDICT_KEYS))
        self.assertTrue(o["evidence"])
        for cite in o["evidence"]:
            self.assertEqual(set(cite), set(report.CITE_KEYS))

    def test_verdict_words(self):
        for w in report.VERDICTS:
            self.assertIn("`%s`" % w, self.text)

    def test_read_scope(self):
        self.assertIn("handoff file", self.text)
        self.assertIn("tree/", self.text)
        for line in self.body:
            if any(w in line for w in ("candidates.json", "verdicts.json", "inputs/", "other handoff")):
                self.assertIn("never", line.lower())
        self.assertNotIn("docs/ccr.md", self.text)

    def test_do_not_flag_restated(self):  # the verifier cannot read finder.md
        low = self.text.lower()
        for item in ("linter", "lint-ignore", "unchanged lines", "input-dependent"):
            self.assertIn(item, low)

    def test_cites_relative_to_tree(self):
        for cite in first(self.body, '{"id"')["evidence"]:
            self.assertFalse(os.path.isabs(cite["path"]) or cite["path"].startswith("tree/"))

    def test_tools_read_only(self):
        self.assertIn("tools: Read, Grep, Glob", self.fm)


if __name__ == "__main__":
    unittest.main()
