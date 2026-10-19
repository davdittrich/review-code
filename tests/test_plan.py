"""plan.py and the multi-lane report.py on a real fixture: target.py -> ledger.py -> plan.py -> report.py."""
import ast
import json
import os
import re
import stat
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_report as tr  # noqa: E402  (module import: its test classes are not collected twice)
from test_report import CAND, CITE, cand, cli, fid, verdict  # noqa: E402

SCRIPTS = tr.SCRIPTS
RULE = "Use snake_case names"
CONV = {"rule": RULE, "rule_source": "conventions/CLAUDE.md"}


def std(category="convention", **kw):
    o = {"path": "src/calc.py", "line": 5, "severity": "required", "category": category, "title": "name style",
         "trigger": "the function name breaks the rule", "quote": "return a - b"}
    if category == "convention":
        o.update(CONV)
    o.update(kw)
    return o


class Fixture(tr.Base):
    base_files = {"src/calc.py": tr.BASE["src/calc.py"], "CLAUDE.md": "# Rules\n- %s\n" % RULE}
    feat_files = {"src/calc.py": tr.FEAT}

    def plan(self, *args):
        rc, out, err = cli("plan.py", list(args), self.repo, self.tmp)
        self.assertEqual(err, "")
        return rc, json.loads(out)

    def plan_json(self):
        with open(os.path.join(self.run_dir, "plan.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def ingest_lane(self, lane, *objs):
        rc, out = self.rep("ingest", tr.lines(*objs), extra=["--lane", lane])
        self.assertEqual(rc, 0, out)
        return json.loads(out)

    def handoff(self, i):
        with open(os.path.join(self.run_dir, "handoff", i + ".md"), encoding="utf-8") as fh:
            return fh.read()


class Tracer(Fixture):
    KEYS = {"version", "tier", "mode", "calls", "verifier", "spec_verifier", "caps", "max_turns", "packs", "shards",
            "skipped", "lost_guarantees", "sota_inventory", "preview", "confirm"}

    def test_standard_tier_without_spec(self):
        rc, out = self.plan("plan", "--run", self.run_dir, "--tier", "standard")
        self.assertEqual((rc, out["status"], out["tier"], out["calls"]), (0, "ok", "standard", 2))
        self.assertEqual(out["skipped"], [{"what": "spec", "why": "no spec"}])
        p = self.plan_json()
        self.assertEqual(set(p), self.KEYS)
        self.assertEqual([(c["lane"], c["axis"], c["agent"], c["model"], c["effort"], c["shard"]) for c in p["calls"]],
                         [("defects", "defects", "review-code:finder", "opus", "medium", None),
                          ("standards", "standards", "review-code:standards", "opus", "medium", None)])
        self.assertEqual(p["calls"][0]["input"], os.path.join(self.run_dir, "inputs", "defects.md"))
        self.assertEqual(p["calls"][1]["prompt"], "Standards lane. Input: " + p["calls"][1]["input"])
        self.assertEqual((p["verifier"]["model"], p["verifier"]["effort"]), ("sonnet", "high"))
        self.assertEqual(p["caps"]["max_verifier_calls"], 25)
        self.assertEqual((p["mode"], p["confirm"], p["packs"], p["shards"]), ("parallel", False, [], []))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.run_dir, "plan.json")).st_mode), 0o600)

    def test_default_tier_is_standard(self):
        rc, out = self.plan("plan", "--run", self.run_dir)
        self.assertEqual((rc, out["tier"]), (0, "standard"))

    def test_deep_tier_with_spec_skips_sota(self):
        os.makedirs(os.path.join(self.repo, "docs"), exist_ok=True)
        with open(os.path.join(self.repo, "docs", "spec.md"), "w") as fh:
            fh.write("# Spec\nadd must add.\n")
        rc, out, _ = cli("ledger.py", ["build", "--run", self.run_dir, "--spec", "docs/spec.md"], self.repo, self.tmp)
        self.assertEqual(json.loads(out)["spec_status"], "found")
        rc, out = self.plan("plan", "--run", self.run_dir, "--tier", "deep")
        self.assertEqual((rc, out["calls"]), (0, 3))
        self.assertEqual(out["skipped"], [{"what": "sota", "why": "no dependency or import names in the change"}])
        self.assertEqual([c["lane"] for c in self.plan_json()["calls"]], ["defects", "standards", "spec"])

    def test_usage_refusals(self):
        for args in ([], ["plan"], ["plan", "--run", self.run_dir, "--tier", "huge"],
                     ["plan", "--run", self.run_dir, "extra"], ["plan", "--run", self.run_dir, "--tier"],
                     ["bogus", "--run", self.run_dir]):
            rc, out = self.plan(*args)
            self.assertEqual((rc, out["status"], out["reason"]), (1, "refuse", "usage"), args)

    def test_missing_ledger_refuses(self):
        os.remove(os.path.join(self.run_dir, "ledger.json"))
        rc, out = self.plan("plan", "--run", self.run_dir)
        self.assertEqual((rc, out["reason"]), (1, "no-ledger"))

    def test_two_lanes_keep_axes_apart(self):
        d = self.ingest_lane("defects", CAND, {"inspected": ["src/calc.py"]})
        s = self.ingest_lane("standards", std(), {"inspected": ["src/calc.py"]})
        self.assertEqual((len(d["ids"]), len(s["ids"])), (1, 2))  # additive: the defects candidate survives
        self.verdicts(*[{"id": i, "verdict": "confirmed", "evidence": [CITE], "reason": "r"} for i in s["ids"]])
        fs = self.doc()["findings"]
        self.assertEqual([(f["axis"], f["source_lane"]) for f in fs], [("defects", "defects"), ("standards", "standards")])

    def test_ids_defects_unchanged_standards_salted(self):
        d = self.ingest_lane("defects", CAND)["ids"][0]
        self.assertEqual(d, fid("src/calc.py", "bug", "return a - b"))
        (s,) = [i for i in self.ingest_lane("standards", std("lean"))["ids"] if i != d]
        self.assertNotEqual(s, fid("src/calc.py", "lean", "return a - b"))
        self.assertRegex(s, "^" + tr.report.ID_RE + "$")

    def test_standards_validation(self):
        out = self.ingest_lane(
            "standards", std("bug"), std(rule="Never use tabs"), std(rule_source="conventions/NOPE.md"),
            std(rule_source="CLAUDE.md"), std(rule_source="conventions/../ledger.json"), std(rule=""),
            std(rule="x" * 501), std(rule_source="/etc/hostname"), std(), {"inspected": []})
        self.assertEqual((len(out["ids"]), out["dropped"]["invalid"]), (1, 8))

    def test_foreign_keys_cannot_retag_a_lane(self):
        out = self.ingest_lane("standards", dict(std("lean"), source_lane="defects", axis="defects"))
        self.verdicts({"id": out["ids"][0], "verdict": "confirmed", "evidence": [CITE], "reason": "r"})
        (f,) = self.doc()["findings"]
        self.assertEqual((f["axis"], f["source_lane"]), ("standards", "standards"))

    def test_convention_handoff_fences_the_rule(self):
        (i,) = self.ingest_lane("standards", std())["ids"]
        body = self.handoff(i)
        self.assertRegex(body, r"(?s)check: rule\n.*<<<UNTRUSTED-([0-9a-f]+)\n" + RULE + r"\n\1>>>")
        for banned in ("severity", "category", "fix", "return a - b", "required"):
            self.assertNotIn(banned, body)

    def test_lean_handoff_and_traced_suggestion(self):
        (i,) = self.ingest_lane("standards", std("lean", severity="blocking"))["ids"]
        body = self.handoff(i)
        self.assertIn("check: property\n", body)
        for banned in ("severity", "category", "fix", "return a - b", "required", "rule:"):
            self.assertNotIn(banned, body)
        self.verdicts({"id": i, "verdict": "confirmed", "evidence": [CITE], "reason": "r"})
        (f,) = self.doc()["findings"]
        self.assertEqual((f["evidence_class"], f["severity"]), ("traced", "suggestion"))

    def test_ceiling_clamps_per_category(self):
        out = self.ingest_lane("standards", std("comment"), std("smell", title="smell"))
        self.verdicts(*[{"id": i, "verdict": "confirmed", "evidence": [CITE], "reason": "r"} for i in out["ids"]])
        got = {f["category"]: f["severity"] for f in self.doc()["findings"]}
        self.assertEqual(got, {"comment": "required", "smell": "suggestion"})

    def test_text_shows_axes_in_fixed_order_and_spec_skipped(self):
        self.plan("plan", "--run", self.run_dir)
        self.ingest_lane("standards", std())
        self.ingest_lane("defects", CAND)
        rc, out = self.rep("render")
        heads = [m.group(0) for m in re.finditer(r"(?m)^(Defects|Standards|Spec)$", out)]
        self.assertEqual(heads, ["Defects", "Standards", "Spec"])
        self.assertIn("Spec: skipped (no spec)", out)

    def test_unknown_lane_refuses(self):
        rc, out = self.rep("ingest", "", extra=["--lane", "bogus"])
        self.assertEqual((rc, json.loads(out)["reason"]), (1, "usage"))

    def test_plan_lanes_ingest_under_their_own_axis(self):
        self.plan("plan", "--run", self.run_dir, "--tier", "quick")
        cats = {"defects": "bug", "standards": "lean"}
        for c in self.plan_json()["calls"]:
            self.ingest_lane(c["lane"], cand("return a - b", 5, category=cats[c["lane"]]) if c["lane"] == "defects"
                             else std(cats[c["lane"]]))
        out = json.loads(self.rep("render", extra=["--format", "json"])[1])
        self.assertEqual({f["axis"] for f in out["unverifiable"]}, {"defects", "standards"})

    def test_python_39_grammar(self):
        with open(os.path.join(SCRIPTS, "plan.py"), encoding="utf-8") as fh:
            ast.parse(fh.read(), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
