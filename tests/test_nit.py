"""--nit: the nit severity, its two bases (a quoted rule, a counted norm), and how the report shows it."""
import json
import os
import unittest

import paths  # noqa: F401

from gitfix import lines  # noqa: E402
import nit  # noqa: E402
import report  # noqa: E402
from test_report import Base, write_plan  # noqa: E402

RULE = "Never subtract operands."
DOMINANT, DEVIANT = "= []", "= list()"


def _feat(dominant, deviant, spread=0):
    """spread > 0 puts that many dominant rows before each deviant row; adjacent candidates (within 2 lines) merge at ingest."""
    a = ["a%d %s" % (i, DOMINANT) for i in range(dominant)]
    b = ["b%d %s" % (i, DEVIANT) for i in range(deviant)]
    rows = a + b if not spread else [r for i, x in enumerate(b) for r in a[i * spread:(i + 1) * spread] + [x]] + a[deviant * spread:]
    return "def f():\n" + "\n".join("    " + r for r in rows) + "\n    return 1\n", rows


class NitBase(Base):
    DOM, DEV, SPREAD = 22, 3, 0
    base_files = {"src/calc.py": "def f():\n    return 1\n", "CLAUDE.md": RULE + "\n"}

    @classmethod
    def setUpClass(cls):
        cls.feat_files = {"src/calc.py": _feat(cls.DOM, cls.DEV, cls.SPREAD)[0]}
        cls.rows = _feat(cls.DOM, cls.DEV, cls.SPREAD)[1]

    def setUp(self):
        super().setUp()
        write_plan(self.run_dir, nit=True)

    def line_of(self, prefix):  # 1-based line in src/calc.py of the row starting with prefix
        return 2 + next(i for i, r in enumerate(self.rows) if r.startswith(prefix))

    def quote(self, prefix):
        return next(r for r in self.rows if r.startswith(prefix))

    def nit(self, prefix="b0", **kw):
        q = self.quote(prefix)
        return dict({"path": "src/calc.py", "line": self.line_of(prefix), "severity": "nit", "category": "norm",
                     "title": "list() where the repo writes []", "trigger": "the file writes [] on most lines",
                     "quote": q, "norm_for": DOMINANT, "norm_against": DEVIANT, "norm_glob": "*.py"}, **kw)

    def rule_nit(self, **kw):
        return dict(self.nit(), **dict({"category": "convention", "rule": RULE, "rule_source": "conventions/CLAUDE.md"}, **kw))

    def lane(self, *objs, rc=0):
        code, out = self.rep("ingest", lines(*objs), extra=["--lane", "standards"])
        self.assertEqual(code, rc, out)
        return json.loads(out)

    def confirm(self, out, cites):
        """Confirm the accepted ids in input order (out["ids"] is sorted) with these cites, one verdicts call per verify batch."""
        ids = list(dict.fromkeys(d["id"] for d in out["dispositions"] if d["id"] in out["ids"]))
        cite = dict(zip(ids, cites))
        for call in json.loads(self.rep("status")[1])["verify_calls"]:
            text = lines(*({"id": i, "verdict": "confirmed", "evidence": [cite[i]], "reason": "r"} for i in call["ids"]))
            self.assertEqual(self.rep("verdicts", text, extra=["--batch", str(call["batch"])])[0], 0)

    def cite(self, prefix="b0"):
        return {"path": "src/calc.py", "line": self.line_of(prefix), "quote": self.quote(prefix)}


class Severity(NitBase):
    def test_nit_ranks_between_suggestion_and_noted(self):
        self.assertEqual(report.SEVERITIES, ("blocking", "required", "suggestion", "nit", "noted"))

    def test_a_nit_is_refused_when_the_plan_did_not_ask_for_nits(self):
        write_plan(self.run_dir)
        out = self.lane(self.nit())
        self.assertEqual([r["check"] for r in out["rejected"]], ["nit-not-enabled"])

    def test_a_nit_must_be_a_rule_or_a_norm(self):
        out = self.lane(dict(self.nit(), category="lean"))
        self.assertEqual([r["check"] for r in out["rejected"]], ["nit-basis"])

    def test_a_norm_is_always_a_nit(self):
        out = self.lane(dict(self.nit(), severity="suggestion"))
        self.assertEqual([r["check"] for r in out["rejected"]], ["nit-basis"])

    def test_only_the_standards_lane_may_report_a_nit(self):
        code, out = self.rep("ingest", lines(self.nit()), extra=["--lane", "defects"])
        self.assertEqual([r["check"] for r in json.loads(out)["rejected"]], ["bad-type"])


class RuleBasis(NitBase):
    def test_a_nit_that_quotes_a_rule_is_reported_with_its_basis_and_never_changes_the_verdict(self):
        out = self.lane(self.rule_nit())
        self.assertEqual(out["dropped"]["invalid"], 0)
        self.confirm(out, [self.cite()])
        d = self.doc()
        (f,) = d["findings"]
        self.assertEqual((f["severity"], f["basis"], f["rule"]), ("nit", "rule", RULE))
        self.assertEqual(d["verdict"], "No blocking findings")

    def test_a_rule_that_is_not_in_the_conventions_is_refused(self):
        out = self.lane(self.rule_nit(rule="Always subtract operands."))
        self.assertEqual([r["check"] for r in out["rejected"]], ["rule-not-in-conventions"])


class NormBasis(NitBase):
    def test_a_counted_norm_is_reported_with_the_scripts_own_counts(self):
        out = self.lane(self.nit())
        self.assertEqual(out["dropped"]["invalid"], 0)
        self.confirm(out, [self.cite()])
        (f,) = self.doc()["findings"]
        self.assertEqual((f["severity"], f["basis"]), ("nit", "norm"))
        self.assertEqual(f["norm"], {"for": 22, "against": 3, "total": 25})

    def test_a_norm_needs_a_missing_field_named(self):
        out = self.lane({k: v for k, v in self.nit().items() if k != "norm_glob"})
        self.assertEqual([r["check"] for r in out["rejected"]], ["norm-malformed"])

    def test_the_quote_must_hold_the_deviating_idiom(self):
        out = self.lane(self.nit("a0"))
        self.assertEqual([r["check"] for r in out["rejected"]], ["norm-quote-mismatch"])

    def test_the_other_idiom_must_be_the_majority(self):
        out = self.lane(dict(self.nit(), norm_for=DEVIANT, norm_against=DOMINANT, quote=self.quote("a0"),
                             line=self.line_of("a0")))
        self.assertEqual([r["check"] for r in out["rejected"]], ["norm-not-majority"])

    def test_an_empty_idiom_is_refused(self):
        out = self.lane(dict(self.nit(), norm_for=""))
        self.assertEqual([r["check"] for r in out["rejected"]], ["norm-malformed"])


class SmallSample(NitBase):
    DOM, DEV = 12, 3  # 15 observations: below 20

    def test_under_twenty_observations_is_no_finding(self):
        out = self.lane(self.nit())
        self.assertEqual([r["check"] for r in out["rejected"]], ["norm-sample-small"])


class NormGuards(NitBase):
    def test_an_idiom_inside_the_other_idiom_is_no_norm(self):
        out = self.lane(self.nit(norm_for="= list", norm_against=DEVIANT), self.nit("b1", norm_for=DEVIANT, norm_against="= list"))
        self.assertEqual([r["check"] for r in out["rejected"]], ["norm-malformed", "norm-malformed"])

    def test_a_one_or_two_character_idiom_is_no_norm(self):
        out = self.lane(self.nit(norm_for="a", norm_against="b"))
        self.assertEqual([r["check"] for r in out["rejected"]], ["norm-malformed"])

    def test_a_symlink_in_the_tree_is_never_counted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            outside = os.path.join(d, "outside.py")
            with open(outside, "w") as fh:
                fh.write("".join("x%d = []\n" % i for i in range(30)))
            tree = os.path.join(d, "tree")
            os.mkdir(tree)
            os.symlink(outside, os.path.join(tree, "l.py"))
            got, why = nit.norm({"norm_for": "= []", "norm_against": "= list()", "norm_glob": "*.py"}, tree, "y = list()")
        self.assertEqual((got, why), (None, "norm-sample-small"))


class Tie(NitBase):
    DOM, DEV = 10, 10  # 20 observations, no majority

    def test_a_tie_is_no_majority(self):
        out = self.lane(self.nit())
        self.assertEqual([r["check"] for r in out["rejected"]], ["norm-not-majority"])


class SameQuote(NitBase):
    def test_a_stronger_finding_on_a_nits_quote_is_not_dropped_as_a_duplicate(self):
        out = self.lane(self.rule_nit(), self.rule_nit(severity="required"))
        self.assertEqual([d["disposition"] for d in out["dispositions"]], ["accepted", "accepted"])
        self.confirm(out, [self.cite()])  # the two share one cluster; the more severe survives it
        (f,) = self.doc()["findings"]
        self.assertEqual(f["severity"], "required")


class LeanNitOnly(NitBase):
    def setUp(self):
        super().setUp()
        write_plan(self.run_dir, nit=True, tier="lean")

    def test_other_severities_are_refused_under_nit_lean(self):
        out = self.lane(self.rule_nit(severity="suggestion"))
        self.assertEqual([r["check"] for r in out["rejected"]], ["nit-only"])

    def test_a_nit_is_accepted_under_nit_lean(self):
        self.assertEqual(self.lane(self.rule_nit())["dropped"]["invalid"], 0)


class UnchangedFiles:
    """src/calc.py holds the whole norm in the base commit; the branch only appends a comment, so b0 is an unchanged line."""
    base_files = dict(NitBase.base_files, **{"src/calc.py": _feat(22, 3)[0]})

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.feat_files = {"src/calc.py": _feat(22, 3)[0] + "# end\n"}


class UnchangedLineOnDiff(UnchangedFiles, NitBase):
    def test_a_nit_on_an_unchanged_line_is_dropped_as_pre_existing(self):
        out = self.lane(self.nit())
        d = self.doc()  # the pre-existing drop lands at render; the verifier is never asked
        self.assertEqual((out["ids"], d["findings"], d["dropped"]["do_not_flag"]), ([], [], 1))


class UnchangedLineOnRepo(UnchangedFiles, NitBase):
    pin_args = ["--repo"]

    def test_a_nit_on_an_unchanged_line_is_kept_as_existing(self):
        out = self.lane(self.nit())
        self.confirm(out, [self.cite()])
        d = self.doc()
        (f,) = d["findings"]
        self.assertEqual((f["severity"], f["origin"], d["dropped"]["do_not_flag"]), ("nit", "existing", 0))


class Unverifiable(NitBase):
    def test_a_nit_without_a_valid_cite_is_unverifiable_and_never_blocks(self):
        out = self.lane(self.rule_nit())
        self.confirm(out, [dict(self.cite(), quote="not on that line")])
        d = self.doc()
        self.assertEqual(d["findings"], [])
        (u,) = d["unverifiable"]
        self.assertEqual((u["id"], u["verified"]), (out["ids"][0], False))
        self.assertEqual(d["verdict"], "No blocking findings")


class Counts(NitBase):
    SPREAD = 3

    def test_the_nit_count_is_in_the_json_and_the_text_report(self):
        self.confirm(self.lane(self.nit("b0"), self.nit("b1")), [self.cite("b0"), self.cite("b1")])
        self.assertEqual(self.doc()["nit"], 2)
        self.assertIn("nits: 2", self.rep("render")[1])

    def test_the_count_is_absent_without_the_flag(self):
        write_plan(self.run_dir)
        self.confirm(self.lane(self.rule_nit(severity="suggestion")), [self.cite()])
        self.assertNotIn("nit", self.doc())
        self.assertNotIn("nits:", self.rep("render")[1])


class Nineteen(NitBase):
    DOM, DEV = 16, 3  # 19 observations

    def test_one_below_the_minimum_is_no_finding(self):
        self.assertEqual([r["check"] for r in self.lane(self.nit())["rejected"]], ["norm-sample-small"])


class Twenty(NitBase):
    DOM, DEV = 17, 3  # 20 observations

    def test_exactly_the_minimum_stands(self):
        out = self.lane(self.nit())
        self.assertEqual((out["rejected"], out["dropped"]["invalid"]), ([], 0))
        self.confirm(out, [self.cite()])
        self.assertEqual(self.doc()["findings"][0]["norm"], {"for": 17, "against": 3, "total": 20})


class NormGlob(NitBase):
    DOM, DEV = 12, 3  # 15 lines in src/calc.py; other/x.py adds 10 more of the dominant idiom

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.feat_files = dict(cls.feat_files, **{"other/x.py": "".join("c%d = []\n" % i for i in range(10))})

    def check(self, glob):
        return self.lane(self.nit(norm_glob=glob))

    def test_a_bare_glob_counts_every_matching_file(self):
        out = self.check("*.py")
        self.assertEqual(out["rejected"], [])
        self.confirm(out, [self.cite()])
        self.assertEqual(self.doc()["findings"][0]["norm"], {"for": 22, "against": 3, "total": 25})

    def test_a_path_glob_counts_only_the_files_it_names(self):
        self.assertEqual([r["check"] for r in self.check("src/*.py")["rejected"]], ["norm-sample-small"])

    def test_the_other_file_alone_is_too_small(self):
        self.assertEqual([r["check"] for r in self.check("other/*.py")["rejected"]], ["norm-sample-small"])

    def test_a_glob_matching_nothing_is_no_norm(self):
        self.assertEqual([r["check"] for r in self.check("*.rs")["rejected"]], ["norm-sample-small"])


class ManyRules(NitBase):
    RULES = ["Rule number %d applies." % k for k in range(17)]
    DOM, DEV, SPREAD = 60, 17, 3
    base_files = dict(NitBase.base_files, **{"CLAUDE.md": "\n".join(RULES) + "\n"})

    def nits(self):
        return [self.rule_nit(rule=r, **self.at("b%d " % k)) for k, r in enumerate(self.RULES)]

    def at(self, prefix):
        return {"quote": self.quote(prefix), "line": self.line_of(prefix)}

    def ingest_all(self):
        out = self.lane(*self.nits())
        self.assertEqual(len(out["ids"]), 17, out)
        self.confirm(out, [self.cite("b%d " % k) for k in range(17)])

    def test_the_json_keeps_every_nit_while_the_text_caps_the_rules(self):
        self.ingest_all()
        d = self.doc()
        self.assertEqual(sorted(f["rule"] for f in d["findings"]), sorted(self.RULES))
        text = report._text(d)
        self.assertEqual(sum(1 for ln in text.splitlines() if ln.startswith("- rule: ")), nit.NIT_RULE_CAP)
        self.assertIn("(+2 more rules not shown)", text)


class Render(NitBase):
    def doc_with(self, n_rules, per_rule=1):
        out = self.lane(self.rule_nit())
        self.confirm(out, [self.cite()])
        d = self.doc()
        base = d["findings"][0]
        d["findings"] = [dict(base, id="F-%010d" % (k * 10 + j), rule="Rule number %d applies." % k, line=base["line"] + j)
                         for k in range(n_rules) for j in range(per_rule)]
        return d

    def test_the_text_report_groups_nits_by_rule_and_caps_the_rules(self):
        text = report._text(self.doc_with(17, per_rule=2))
        self.assertIn("Nits", text)
        self.assertEqual(sum(1 for ln in text.splitlines() if ln.startswith("- rule: ")), nit.NIT_RULE_CAP)
        self.assertIn("(+2 more rules not shown)", text)
        self.assertIn("2 findings", text)

    def test_no_nit_section_without_nits(self):
        d = self.doc_with(1)
        d["findings"] = []
        self.assertNotIn("Nits", report._text(d))

    def sarif_result(self):
        rc, text = self.rep("render", extra=["--format", "sarif"])
        self.assertEqual(rc, 0, text)
        (res,) = json.loads(text)["runs"][0]["results"]
        self.assertEqual(res["level"], "note")
        return res

    def test_sarif_carries_a_rule_nits_basis_rule_and_rule_id(self):
        self.confirm(self.lane(self.rule_nit()), [self.cite()])
        res = self.sarif_result()
        self.assertEqual(res["ruleId"], "review-code/convention")
        self.assertEqual((res["properties"]["basis"], res["properties"]["rule"], res["properties"]["severity"]),
                         ("rule", RULE, "nit"))

    def test_sarif_carries_a_norm_nits_basis_and_rule_id(self):
        self.confirm(self.lane(self.nit()), [self.cite()])
        res = self.sarif_result()
        self.assertEqual(res["ruleId"], "review-code/norm")
        self.assertEqual(res["properties"]["basis"], "norm")


if __name__ == "__main__":
    unittest.main()


class Unit(unittest.TestCase):
    """scripts/nit.py through its own interface, no run directory."""

    def tree(self, dominant, deviant, name="a.py"):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d)
        with open(os.path.join(d, name), "w") as fh:
            fh.write("".join("a%d %s\n" % (i, DOMINANT) for i in range(dominant)) + "".join("b%d %s\n" % (i, DEVIANT) for i in range(deviant)))
            fh.write("both %s %s\n" % (DOMINANT, DEVIANT))  # a line holding both idioms counts for neither
        return d

    def norm(self, tree, **kw):
        return nit.norm(dict({"norm_for": DOMINANT, "norm_against": DEVIANT, "norm_glob": "*.py"}, **kw), tree, "y " + DEVIANT)

    def test_norm_counts_lines_holding_exactly_one_idiom(self):
        got, why = self.norm(self.tree(22, 3))
        self.assertEqual((why, got["norm"]), (None, {"for": 22, "against": 3, "total": 25}))

    def test_norm_refusals_and_the_glob(self):
        self.assertEqual(self.norm(self.tree(12, 3))[1], "norm-sample-small")
        self.assertEqual(self.norm(self.tree(10, 10))[1], "norm-not-majority")
        self.assertEqual(self.norm(self.tree(22, 3), norm_glob="*.js")[1], "norm-sample-small")
        self.assertEqual(self.norm(self.tree(22, 3), norm_glob="")[1], "norm-malformed")

    def test_every_check_and_norm_reason_has_a_retry_hint(self):
        for why in ("nit-not-enabled", "nit-only", "nit-basis", "norm-malformed", "norm-quote-mismatch", "norm-sample-small", "norm-not-majority"):
            self.assertIn(why, report.REASONS)

    def test_check_order_and_basis(self):
        nitc, normc, sug = [{"severity": s, "category": c} for s, c in (("nit", "convention"), ("nit", "norm"), ("suggestion", "convention"))]
        self.assertEqual([nit.check(c, "standards", True) for c in (nitc, normc, sug)], [None, None, None])
        self.assertEqual(nit.check(nitc, "defects", True), "bad-type")
        self.assertEqual(nit.check(nitc, "standards", False), "nit-not-enabled")
        self.assertEqual(nit.check({"severity": "suggestion", "category": "norm"}, "standards", True), "nit-basis")
        self.assertEqual(nit.check(sug, "standards", "only"), "nit-only")
        self.assertEqual(nit.check(sug, "defects", False), None)
        self.assertEqual((nit.basis(nitc), nit.basis(normc), nit.basis(sug)), ("rule", "norm", None))

    def test_basis_text_words_both_bases_once(self):
        norm = {"basis": "norm", "norm_for": "= []", "norm_against": "= list()", "norm": {"for": 22, "against": 3, "total": 25}}
        self.assertEqual(nit.basis_text(norm), 'norm "= []" over "= list()" (22 of 25 lines)')
        self.assertEqual(nit.basis_text({"basis": "rule", "rule": RULE}), RULE)
        self.assertEqual(nit.basis_text({"severity": "required"}), "")

    def test_rows_group_by_basis_and_cap_the_groups(self):
        mk = lambda k, j: {"basis": "rule", "rule": "Rule %d." % k, "path": "p.py", "line": j + 1, "title": "t", "id": "F-%d%d" % (k, j)}  # noqa: E731
        out = nit.rows([mk(k, j) for k in range(17) for j in range(4)], str)
        heads = [r for r in out if r.startswith("- ")]
        self.assertEqual((len(heads), heads[0], len(out)), (nit.NIT_RULE_CAP, "- rule: Rule 0. (4 findings)", nit.NIT_RULE_CAP * 4 + 1))
        self.assertEqual(out[-1], "(+2 more rules not shown)")
        self.assertEqual(nit.rows([mk(0, 0)], str)[0], "- rule: Rule 0. (1 finding)")
