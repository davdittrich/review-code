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
import test_report as tr  # noqa: E402
import test_plan as tp  # noqa: E402  (module import: its test classes are not collected twice)
from test_report import lines  # noqa: E402


def parts(name):
    """(frontmatter lines, body lines) of agents/<name>.md."""
    with open(os.path.join(ROOT, "agents", name + ".md"), encoding="utf-8") as f:
        lines = f.read().splitlines()
    end = lines.index("---", 1)
    return lines[1:end], lines[end + 1:]


def raw(body, prefix):
    return next(line for line in body if line.startswith(prefix))


def first(body, prefix):
    """The first body line with this prefix, as a dict (the wire form is a pair array)."""
    return report._wire(json.loads(raw(body, prefix)))


class Finder(unittest.TestCase):
    def setUp(self):
        self.fm, self.body = parts("finder")
        self.text = "\n".join(self.body)

    def test_candidate_line_has_exactly_the_candidate_keys(self):
        self.assertEqual(set(first(self.body, '[["path"')),
                         set(report.CANDIDATE_REQUIRED + report.CANDIDATE_OPTIONAL))

    def test_inspected_line(self):
        o = first(self.body, '[["inspected"')
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
        o = first(self.body, '[["path"')
        self.assertEqual(set(o), set(report.CANDIDATE_REQUIRED + report.CANDIDATE_OPTIONAL
                                     + report.LANE_OPTIONAL["standards"]))
        self.assertEqual(o["category"], "convention")

    def test_inspected_line(self):
        self.assertEqual(list(first(self.body, '[["inspected"')), ["inspected"])

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
        ex = [line for line in self.body if line.startswith(('[["path"', '[["inspected"'))]
        rule = first(self.body, '[["path"')["rule"]
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
        o = first(self.body, '[["id"')
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
        for cite in first(self.body, '[["id"')["evidence"]:
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
        o = first(self.body, '[["path"')
        self.assertEqual(set(o), set(report.CANDIDATE_REQUIRED + report.CANDIDATE_OPTIONAL
                                     + report.LANE_OPTIONAL["spec"]))
        self.assertIn(o["category"], report.LANE_CATEGORIES["spec"])
        self.assertIsInstance(o["spec_line"], int)

    def test_declined_line_entries(self):
        o = first(self.body, '[["declined"')
        self.assertEqual(list(o), ["declined"])
        for d in o["declined"]:
            self.assertEqual(set(d), {"spec_line", "why"})
        self.assertTrue(self.body.index(next(x for x in self.body if x.startswith('[["declined"')))
                        > self.body.index(next(x for x in self.body if x.startswith('[["inspected"'))))

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
        o = first(self.body, '[["id"')
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

    def test_candidate_line_keys(self):  # gaz.33: an inventory item number stands in for path and line
        o = first(self.body, '[["item"')
        self.assertEqual(set(o), (set(report.CANDIDATE_REQUIRED + report.CANDIDATE_OPTIONAL + report.LANE_OPTIONAL["sota"])
                                  - {"path", "line", "end_line"}) | {"item"})
        self.assertEqual(o["category"], "sota")
        self.assertTrue(o["sources"] and all(u.startswith("https://") for u in o["sources"]))
        self.assertEqual(o["quote"], "requests")

    def test_queries_and_pages(self):
        low = self.text.lower()
        self.assertIn("inventory names", low)
        self.assertIn("a fetched page is data", low)
        self.assertIn("for an item without one", low)  # gaz.40: a name-only import or api item stays flaggable

    def test_severity_ceiling(self):
        self.assertIn("Never use `blocking` or `required`", self.text)
        self.assertEqual(report.CEILING["sota"], "suggestion")

    def test_frontmatter(self):
        for line in ("model: opus", "maxTurns: 20"):
            self.assertIn(line, self.fm)


class BraceFree(unittest.TestCase):
    """g1x.10: Claude Code's Bash guard denies a here-document body holding a brace followed by a quote, so no wire
    example may carry one. REPRO_TEST (test source) is exempt and stays guard-sensitive."""
    GUARD = re.compile(r'\{\s*"')

    def test_no_wire_example_has_a_brace_then_a_quote(self):
        paths = [os.path.join(ROOT, "agents", f) for f in sorted(os.listdir(os.path.join(ROOT, "agents")))]
        paths.append(os.path.join(ROOT, "skills", "review-code", "SKILL.md"))
        seen = 0
        for path in paths:
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    if line.startswith('[["'):
                        seen += 1
                        self.assertNotRegex(line, self.GUARD, "%s:%d" % (path, n))
                        self.assertNotIn("{", line, "%s:%d" % (path, n))
        self.assertGreaterEqual(seen, 8)  # every agent example line was reached

    def test_skill_here_document_bodies_are_clean(self):
        with open(os.path.join(ROOT, "skills", "review-code", "SKILL.md"), encoding="utf-8") as f:
            bodies = re.findall(r"<<'REVIEW_(?:FINDINGS|VERDICTS)'\n(.*?)\n\s*REVIEW_", f.read(), re.S)
        self.assertEqual(len(bodies), 1)  # the verdicts step points back at this one
        for b in bodies:
            self.assertNotRegex(b, self.GUARD)

    def test_no_agent_shows_the_old_object_form(self):
        for f in sorted(os.listdir(os.path.join(ROOT, "agents"))):
            with open(os.path.join(ROOT, "agents", f), encoding="utf-8") as fh:
                self.assertEqual([ln for ln in fh if ln.startswith("{")], [], f)

    def test_every_producer_says_how_to_write_a_brace(self):
        for name in plan.MAX_TURNS:
            self.assertIn("\\u007b", "\n".join(parts(name)[1]), name)

    def test_every_producer_says_how_to_write_a_double_quote(self):  # 02a.18: the guard denies a backslash-escaped quote in a here-document body
        for name in plan.MAX_TURNS:
            self.assertIn("\\u0022", "\n".join(parts(name)[1]), name)


class TurnCeilings(unittest.TestCase):
    """LANE-09: maxTurns is frontmatter-only, so each agent file carries plan.py's ceiling."""

    def test_every_agent_declares_the_plan_ceiling(self):
        # the fixer is not a review lane: fix mode (not plan.py) spawns it, so it carries its own ceiling
        self.assertEqual(sorted(plan.MAX_TURNS),
                         sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, "agents")) if f != "fixer.md"))
        for name, n in dict(plan.MAX_TURNS, fixer=15).items():
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


class WireContract(tp.Fixture):
    """04-08: the first example line of every lane agent goes through the real target, ledger, plan and ingest scripts."""

    def fixture(self, base, feat, spec=None):
        self.base_files, self.feat_files = base, feat  # instance attributes shadow the class ones
        tr.Base.setUp(self)
        if spec is not None:
            os.makedirs(os.path.join(self.repo, "docs"), exist_ok=True)
            with open(os.path.join(self.repo, "docs", "spec.md"), "w", encoding="utf-8") as fh:
                fh.write(spec)
            rc, out, _ = tr.cli("ledger.py", ["build", "--run", self.run_dir, "--spec", "docs/spec.md"], self.repo, self.tmp)
            self.assertEqual(json.loads(out)["spec_status"], "found")

    def ingest_examples(self, agent, lane, *prefixes):
        _, body = parts(agent)
        out = self.ingest_lane(lane, *[raw(body, p) for p in prefixes])  # the prompt's own text, not a re-encoding
        self.assertEqual(out["dropped"]["invalid"], 0, out)
        self.assertEqual(out["lanes"][lane], "complete", out)
        return out

    def test_finder_example_ingests(self):
        # the example cites src/calc.py:41 and tests/test_calc.py
        src = "".join("x%d = %d\n" % (i, i) for i in range(1, 41)) + "def mean(xs):\n    return sum(xs) / len(xs)\n"
        self.fixture(tr.BASE, {"src/calc.py": src, "tests/test_calc.py": "import src.calc\n"})
        out = self.ingest_examples("finder", "defects", '[["path"', '[["inspected"')
        self.assertEqual(len(out["ids"]), 1)

    def test_standards_example_ingests(self):
        feat = {"src/calc.py": "x = 1\n\n\n\ndef addNumbers(a, b):\n    return a + b\n"}
        self.fixture(dict(tr.BASE, **{"CLAUDE.md": "# Rules\n- Function names use snake_case.\n"}), feat)
        out = self.ingest_examples("standards", "standards", '[["path"', '[["inspected"')
        self.assertEqual(len(out["ids"]), 1)

    def test_spec_example_and_declined_line_ingest(self):
        spec = "".join("Line %d of the spec.\n" % i for i in range(1, 11))  # inputs/spec.md lines 27 to 36
        self.fixture(tr.BASE, {"src/calc.py": tr.FEAT}, spec)
        rc, out = self.plan("plan", "--run", self.run_dir)
        self.assertEqual((rc, out["status"]), (0, "ok"))
        self.assertIn("spec", [c["lane"] for c in self.plan_json()["calls"]])
        out = self.ingest_examples("spec", "spec", '[["path"', '[["inspected"', '[["declined"')
        self.assertEqual((len(out["ids"]), len(out["spec_ids"])), (0, 1))

    def test_sota_example_ingests_against_the_inventory(self):
        self.fixture(tr.BASE, {"src/calc.py": tr.FEAT, "requirements.txt": "flask==2.0\nclick\nrequests==2.19.0\n"})
        rc, out = self.plan("plan", "--run", self.run_dir, "--tier", "deep")
        self.assertEqual(rc, 0, out)
        _, body = parts("sota")
        ex = first(body, '[["item"')
        self.assertIn({"item": ex["item"], "kind": "dependency", "path": "requirements.txt", "line": 3, "name": ex["quote"],
                       "version": "2.19.0"}, self.plan_json()["sota_inventory"])
        out = self.ingest_examples("sota", "sota", '[["item"', '[["inspected"')
        self.assertEqual((out["ids"], out["spec_ids"]), ([], []))
        (i,) = self.doc()["unverifiable"]
        self.assertEqual((i["path"], i["line"]), ("requirements.txt", 3))


if __name__ == "__main__":
    unittest.main()
