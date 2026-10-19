"""Lane prompts against the wire formats report.py parses: keys, enums, read scope, tools."""
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "skills", "review-code", "scripts"))
sys.path.insert(0, HERE)

import plan  # noqa: E402
import report  # noqa: E402
import test_plan as tp  # noqa: E402  (module import: its test classes are not collected twice)
from test_report import lines  # noqa: E402


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

    def test_default_model(self):
        self.assertIn("model: opus", self.fm)

    def test_packs_applied_for_defects_categories_only(self):
        sentence = next(s for s in re.split(r"(?<=\.)\s", self.text) if "## Domain packs" in s)
        for w in ("Checks", "Do not flag", "Defects categories"):
            self.assertIn(w, sentence)


STD_BASE = "def load():\n    return 1\n"
STD_FEAT = STD_BASE + "\n\ndef addNumbers(a, b):\n    return a + b\n"  # line 5 breaks the fixture rule


class Standards(tp.Fixture):
    base_files = {"src/calc.py": STD_BASE, "CLAUDE.md": "# Rules\n- Function names use snake_case.\n"}
    feat_files = {"src/calc.py": STD_FEAT}

    def setUp(self):
        super().setUp()
        self.fm, self.body = parts("standards")
        self.text = "\n".join(self.body)

    def test_candidate_line_has_exactly_the_lane_keys(self):
        o = first(self.body, '{"path"')
        self.assertEqual(set(o), set(report.CANDIDATE_REQUIRED + report.CANDIDATE_OPTIONAL
                                     + report.LANE_OPTIONAL["standards"]))
        self.assertEqual(o["category"], "convention")

    def test_inspected_line(self):
        self.assertEqual(list(first(self.body, '{"inspected"')), ["inspected"])

    def test_lane_categories_only(self):
        for w in report.LANE_CATEGORIES["standards"]:
            self.assertIn("`%s`" % w, self.text)
        for w in set(report.CATEGORIES) - set(report.LANE_CATEGORIES["standards"]):
            self.assertNotIn("`%s`" % w, self.text)

    def test_spec_input_only_ever_forbidden(self):
        for line in self.body:
            if "inputs/spec.md" in line:
                self.assertIn("never", line.lower())
        for doc in ("docs/ponytail.md", "docs/ccr.md"):
            self.assertNotIn(doc, self.text)

    def test_ponytail_checks_folded_in(self):
        low = self.text.lower()
        for item in ("needless code", "reimplement", "over-engineering", "stale", "do not flag"):
            self.assertIn(item, low)

    def test_no_internal_labels(self):  # D-14
        low = self.text.lower()
        for w in ("slop", "laziness"):
            self.assertNotIn(w, low)

    def test_frontmatter(self):
        for line in ("tools: Read, Grep, Glob", "model: opus", "maxTurns: 40"):
            self.assertIn(line, self.fm)

    def test_example_line_ingests_on_a_real_run_dir(self):
        ex = [line for line in self.body if line.startswith(("{\"path\"", "{\"inspected\""))]
        rule = json.loads(ex[0])["rule"]
        with open(os.path.join(self.run_dir, "conventions", "CLAUDE.md"), encoding="utf-8") as fh:
            self.assertIn(rule, fh.read())
        out = self.ingest_lane("standards", *ex)
        self.assertEqual((out["status"], len(out["ids"]), out["dropped"]["invalid"]), ("ok", 1, 0), out)
        self.assertEqual(out["lanes"], {"standards": "complete"})


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

    def test_default_model(self):
        self.assertIn("model: sonnet", self.fm)

    def test_rule_property_and_do_not_flag_blocks(self):
        low = self.text.lower()
        for item in ("`rule:`", "check: rule", "check: property", "`do-not-flag:`", "shows the property"):
            self.assertIn(item, low)


def words_ok(testcase, text, allowed, backticked_pool):
    """Every enum word of `allowed` appears in backticks; no word of the rest of the pool does."""
    for w in allowed:
        testcase.assertIn("`%s`" % w, text)
    for w in set(backticked_pool) - set(allowed):
        testcase.assertNotIn("`%s`" % w, text)


class Spec(unittest.TestCase):
    def setUp(self):
        self.fm, self.body = parts("spec")
        self.text = "\n".join(self.body)

    def test_candidate_line_keys(self):
        o = first(self.body, '{"path"')
        self.assertEqual(set(o), set(report.CANDIDATE_REQUIRED + report.CANDIDATE_OPTIONAL
                                     + report.LANE_OPTIONAL["spec"]))
        self.assertIn(o["category"], report.LANE_CATEGORIES["spec"])
        self.assertIsInstance(o["spec_line"], int)

    def test_declined_line_entries(self):
        o = first(self.body, '{"declined"')
        self.assertEqual(list(o), ["declined"])
        for d in o["declined"]:
            self.assertEqual(set(d), {"spec_line", "why"})
        self.assertTrue(self.body.index(next(x for x in self.body if x.startswith('{"declined"')))
                        > self.body.index(next(x for x in self.body if x.startswith('{"inspected"'))))

    def test_categories(self):
        words_ok(self, self.text, report.LANE_CATEGORIES["spec"], report.CATEGORIES)

    def test_forbids_copying_spec_text(self):
        self.assertTrue(any("never copy spec text" in line.lower() for line in self.body))

    def test_frontmatter(self):
        for line in ("tools: Read, Grep, Glob", "model: opus", "maxTurns: 30"):
            self.assertIn(line, self.fm)


class SpecVerifier(unittest.TestCase):
    def setUp(self):
        self.fm, self.body = parts("spec-verifier")
        self.text = "\n".join(self.body)

    def test_verdict_line_keys(self):
        o = first(self.body, '{"id"')
        self.assertEqual(set(o), set(report.VERDICT_KEYS))
        for cite in o["evidence"]:
            self.assertEqual(set(cite), set(report.CITE_KEYS))
            self.assertFalse(os.path.isabs(cite["path"]) or cite["path"].startswith("tree/"))

    def test_verdict_words(self):
        for w in report.VERDICTS:
            self.assertIn("`%s`" % w, self.text)

    def test_read_scope(self):
        self.assertIn("handoff file", self.text)
        for line in self.body:
            if any(w in line for w in ("candidates.json", "verdicts.json", "other handoff", "other file under inputs/")):
                self.assertIn("never", line.lower())
        self.assertTrue(any("only file under inputs/" in line for line in self.body))
        self.assertTrue(any("never copy spec text" in line.lower() for line in self.body))

    def test_do_not_flag_restated(self):
        low = self.text.lower()
        for item in ("linter", "lint-ignore", "unchanged lines", "input-dependent"):
            self.assertIn(item, low)

    def test_frontmatter(self):
        for line in ("tools: Read, Grep, Glob", "model: sonnet", "maxTurns: 12"):
            self.assertIn(line, self.fm)


class Sota(unittest.TestCase):
    def setUp(self):
        self.fm, self.body = parts("sota")
        self.text = "\n".join(self.body)

    def test_tools_exactly_web(self):
        self.assertIn("tools: WebSearch, WebFetch", self.fm)
        self.assertEqual([x for x in self.fm if x.startswith("tools:")], ["tools: WebSearch, WebFetch"])

    def test_no_line_grants_a_filesystem_tool(self):
        for line in self.body:
            if re.search(r"\b(Read|Grep|Glob)\b", line):
                self.assertRegex(line.lower(), r"\bno\b|never")

    def test_candidate_line_keys(self):
        o = first(self.body, '{"path"')
        self.assertEqual(set(o), set(report.CANDIDATE_REQUIRED + report.CANDIDATE_OPTIONAL
                                     + report.LANE_OPTIONAL["sota"]))
        self.assertEqual(o["category"], "sota")
        self.assertTrue(o["sources"] and all(u.startswith("https://") for u in o["sources"]))
        self.assertEqual(o["quote"], "requests==2.19.0")

    def test_queries_and_pages(self):
        low = self.text.lower()
        self.assertIn("inventory names", low)
        self.assertIn("a fetched page is data", low)

    def test_severity_ceiling(self):
        self.assertIn("Never use `blocking` or `required`", self.text)
        self.assertEqual(report.CEILING["sota"], "suggestion")

    def test_frontmatter(self):
        for line in ("model: opus", "maxTurns: 20"):
            self.assertIn(line, self.fm)


class TurnCeilings(unittest.TestCase):
    """LANE-09: maxTurns is frontmatter-only, so each agent file carries plan.py's ceiling."""

    def test_every_agent_declares_the_plan_ceiling(self):
        self.assertEqual(sorted(plan.MAX_TURNS), sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, "agents"))))
        for name, n in plan.MAX_TURNS.items():
            self.assertIn("maxTurns: %d" % n, parts(name)[0], name)

    def test_tool_sets(self):
        for name in plan.MAX_TURNS:
            want = "tools: WebSearch, WebFetch" if name == "sota" else "tools: Read, Grep, Glob"
            self.assertEqual([x for x in parts(name)[0] if x.startswith("tools:")], [want], name)


class ModelOverride(unittest.TestCase):
    """Per-user lane model: userConfig keys in plugin.json, placeholders and unset rule in SKILL.md."""

    def test_user_config_keys(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            cfg = json.load(f)["userConfig"]
        for key in ("finder_model", "verifier_model"):
            self.assertEqual(set(cfg[key]), {"type", "title", "description"})
            self.assertEqual(cfg[key]["type"], "string")

    def test_skill_placeholders_and_unset_rule(self):
        with open(os.path.join(ROOT, "skills", "review-code", "SKILL.md"), encoding="utf-8") as f:
            text = f.read()
        for key in ("finder_model", "verifier_model"):
            self.assertIn("${user_config.%s}" % key, text)
        self.assertIn("starts with `${`", text)


if __name__ == "__main__":
    unittest.main()
