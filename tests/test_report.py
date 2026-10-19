"""report.py on real target.py and ledger.py run dirs: finder line -> verdict -> schema v1 document."""
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "review-code", "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import gitfix  # noqa: E402
import report  # noqa: E402

BASE = {"src/calc.py": "def load():\n    return 1\n"}
FEAT = "def load():\n    return 1\n\ndef add(a, b):\n    return a - b\n"  # lines 3 to 5 are added
CAND = {"path": "src/calc.py", "line": 5, "severity": "required", "category": "bug", "title": "add subtracts",
        "trigger": "add(2, 3) returns -1", "quote": "return a - b", "fix": "FIXTEXT-q1"}
CITE = {"path": "src/calc.py", "line": 5, "quote": "return a - b"}


def cli(script, args, cwd, tmp, stdin=""):
    e = gitfix.env()
    e["TMPDIR"] = tmp
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + args, cwd=cwd, env=e,
                       input=stdin.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8")


def lines(*objs):
    return "\n".join(o if isinstance(o, str) else json.dumps(o) for o in objs) + "\n"


def fid(path, category, quote):
    return "F-" + hashlib.sha256("\0".join((path, category, " ".join(quote.split()))).encode()).hexdigest()[:10]


class Base(unittest.TestCase):
    pin_args = ["main"]
    base_files, feat_files = BASE, {"src/calc.py": FEAT}

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, self.base_files, "base")
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        gitfix.commit(self.repo, self.feat_files, "feat")
        self.run_dir = self.pin(self.pin_args)

    def pin(self, args):
        rc, out, _ = cli("target.py", args, self.repo, self.tmp)
        self.assertEqual(rc, 0, out)
        run = json.loads(out)["run"]
        rc, out, _ = cli("ledger.py", ["build", "--run", run], self.repo, self.tmp)
        self.assertEqual(rc, 0, out)
        return run

    def rep(self, sub, stdin="", extra=()):
        rc, out, err = cli("report.py", [sub, "--run", self.run_dir] + list(extra), self.repo, self.tmp, stdin)
        self.assertEqual(err, "")
        return rc, out

    def ingest(self, *objs):
        rc, out = self.rep("ingest", lines(*objs))
        self.assertEqual(rc, 0, out)
        return json.loads(out)

    def verdicts(self, *objs):
        rc, out = self.rep("verdicts", lines(*objs))
        self.assertEqual(rc, 0, out)
        return json.loads(out)

    def doc(self):
        rc, out = self.rep("render", extra=["--format", "json"])
        self.assertEqual(rc, 0, out)
        return json.loads(out)


class Tracer(Base):
    def test_finder_line_to_json_document(self):
        out = self.ingest("prose from the finder, ignored", CAND, {"inspected": ["src/calc.py"]})
        self.assertEqual((out["status"], len(out["ids"])), ("ok", 1))
        fid = out["ids"][0]
        self.assertRegex(fid, "^" + report.ID_RE + "$")
        self.assertEqual(out["dropped"], {"invalid": 0, "do_not_flag": 0})
        hand = os.path.join(self.run_dir, "handoff", fid + ".md")
        self.assertEqual(out["handoff"], os.path.dirname(hand))
        with open(hand, encoding="utf-8") as fh:
            body = fh.read()
        self.assertRegex(body, r"(?s)<<<UNTRUSTED-([0-9a-f]+)\n.*add subtracts.*\n\1>>>")
        self.assertRegex(body, r"(?s)<<<UNTRUSTED-([0-9a-f]+)\n.*add\(2, 3\) returns -1.*\n\1>>>")
        for need in (fid, "src/calc.py:5"):
            self.assertIn(need, body)
        for banned in ("FIXTEXT-q1", "return a - b", "required", "category", "severity"):
            self.assertNotIn(banned, body)
        v = self.verdicts({"id": fid, "verdict": "confirmed", "evidence": [CITE], "reason": "r"})
        self.assertEqual((v["status"], v["confirmed"]), ("ok", [fid]))
        d = self.doc()
        self.assertEqual(d["schema_version"], 1)
        (f,) = d["findings"]
        self.assertEqual((f["id"], f["evidence_class"], f["score"], f["verified"], f["origin"]),
                         (fid, "traced", 75, True, "introduced"))
        self.assertEqual(d["inspected"]["areas"], ["src/calc.py"])
        self.assertEqual(d["unverifiable"], [])


# Lines 1 to 5 exist on main; lines 6 to 20 are added on the branch. Line 17 carries a lint-ignore marker.
BASE2 = {"src/calc.py": "def load():\n    return 1\n\ndef old(x):\n    return x / 0\n"}
FEAT2 = (BASE2["src/calc.py"] + "\ndef add(a, b):\n    return a - b\n\ndef mul(a, b):\n    return a * b\n"
         "\ndef neg(a):\n    return -a\n\ndef sq(x):\n    return x * x  # noqa\n\ndef inc(a):\n    return a + 1\n")
Q_OLD, Q_ADD, Q_MUL, Q_NEG, Q_SQ, Q_INC = ("return x / 0", "return a - b", "return a * b", "return -a", "return x * x",
                                           "return a + 1")


def cand(quote, line, **kw):
    c = dict(CAND, quote=quote, line=line)
    c.update(kw)
    return c


def verdict(path_quote_line, kind="confirmed", **kw):
    quote, line = path_quote_line
    return dict({"id": fid("src/calc.py", "bug", quote), "verdict": kind, "reason": "r",
                 "evidence": [{"path": "src/calc.py", "line": line, "quote": quote}]}, **kw)


class Derivation(Base):
    base_files, feat_files = BASE2, {"src/calc.py": FEAT2}

    def items(self, which="findings"):
        return {i["id"]: i for i in self.doc()[which]}

    def test_verdict_outcomes(self):
        self.ingest(cand(Q_ADD, 8), cand(Q_MUL, 11), cand(Q_NEG, 14), cand(Q_INC, 20), {"inspected": ["src/calc.py"]})
        a, m, n, o = (fid("src/calc.py", "bug", q) for q in (Q_ADD, Q_MUL, Q_NEG, Q_INC))
        v = self.verdicts(verdict((Q_ADD, 8), "refuted"), verdict((Q_MUL, 11), "unverifiable", reason="needs runtime"),
                          verdict((Q_NEG, 14), "confirmed", evidence=[{"path": "src/calc.py", "line": 14, "quote": "nope"}]))
        self.assertEqual((v["confirmed"], v["refuted"], v["unverifiable"]), ([], [a], sorted([m, n, o])))
        d = self.doc()
        self.assertEqual(d["findings"], [])
        self.assertEqual(d["dropped"], {"refuted": 1, "do_not_flag": 0, "invalid": 0})
        un = {i["id"]: i for i in d["unverifiable"]}
        self.assertNotIn(a, un)
        self.assertEqual((un[m]["reason"], un[m]["evidence_class"], un[m]["score"], un[m]["verified"]),
                         ("needs runtime", "inferred", 50, False))
        self.assertEqual((un[n]["reason"], un[n]["evidence_class"]), ("no-valid-cite", "speculative"))
        self.assertEqual((un[o]["reason"], un[o]["evidence_class"], un[o]["score"]), ("no-verdict", "speculative", 25))

    def test_first_verdict_wins_and_unknown_id_ignored(self):
        self.ingest(cand(Q_ADD, 8))
        self.verdicts("{broken", verdict((Q_ADD, 8), "refuted"), verdict((Q_ADD, 8)),
                      dict(verdict((Q_MUL, 11)), id="F-0000000000"))
        d = self.doc()
        self.assertEqual((d["findings"], d["dropped"]["refuted"]), ([], 1))

    def test_severity_cap_and_score(self):
        self.ingest(cand(Q_ADD, 8, severity="blocking"),
                    cand(Q_MUL, 11, severity="blocking", category="security"),
                    cand(Q_NEG, 14, severity="blocking", category="accessibility"),
                    cand(Q_INC, 20, severity="suggestion", category="security"))
        ids = {q: fid("src/calc.py", c, q) for q, c in ((Q_ADD, "bug"), (Q_MUL, "security"),
                                                         (Q_NEG, "accessibility"), (Q_INC, "security"))}
        self.verdicts(verdict((Q_ADD, 8)), dict(verdict((Q_MUL, 11)), id=ids[Q_MUL]),
                      dict(verdict((Q_NEG, 14), "unverifiable"), id=ids[Q_NEG]),
                      dict(verdict((Q_INC, 20)), id=ids[Q_INC]))
        f = self.items()
        self.assertEqual((f[ids[Q_ADD]]["severity"], f[ids[Q_ADD]]["evidence_class"]), ("required", "traced"))
        self.assertEqual(f[ids[Q_MUL]]["severity"], "blocking")
        self.assertEqual(f[ids[Q_INC]]["severity"], "suggestion")  # the finder's weaker rating stands
        self.assertEqual(self.items("unverifiable")[ids[Q_NEG]]["severity"], "required")  # accessibility, inferred

    def test_inferred_and_speculative_caps(self):
        self.ingest(cand(Q_ADD, 8, severity="blocking"), cand(Q_MUL, 11, severity="blocking"))
        self.verdicts(verdict((Q_ADD, 8), "unverifiable"))
        u = self.items("unverifiable")
        self.assertEqual(u[fid("src/calc.py", "bug", Q_ADD)]["severity"], "suggestion")
        self.assertEqual(u[fid("src/calc.py", "bug", Q_MUL)]["severity"], "noted")

    def test_repro_record_upgrades_class_only_when_reproduced(self):
        self.ingest(cand(Q_ADD, 8), cand(Q_MUL, 11), cand(Q_NEG, 14))
        a, m, n = (fid("src/calc.py", "bug", q) for q in (Q_ADD, Q_MUL, Q_NEG))
        self.verdicts(verdict((Q_ADD, 8)), verdict((Q_MUL, 11)), verdict((Q_NEG, 14), "unverifiable"))
        os.makedirs(os.path.join(self.run_dir, "repro"))
        for i, outcome in ((a, "reproduced"), (m, "not-reproduced"), (n, "reproduced")):
            with open(os.path.join(self.run_dir, "repro", i + ".json"), "w") as fh:
                json.dump({"outcome": outcome, "exit": 1}, fh)
        f = self.items()
        self.assertEqual((f[a]["evidence_class"], f[a]["score"], f[a]["verified"]), ("reproduced", 100, True))
        self.assertEqual(f[m]["evidence_class"], "traced")
        self.assertEqual(self.items("unverifiable")[n]["evidence_class"], "inferred")

    def test_do_not_flag_unchanged_lines(self):
        self.ingest(cand(Q_OLD, 5, severity="suggestion", category="security"),
                    cand("return 1", 2, severity="blocking", category="security"),
                    cand(Q_ADD, 8, severity="required"), cand(Q_MUL, 11, severity="required"))
        old, one, add, mul = (fid("src/calc.py", c, q) for q, c in ((Q_OLD, "security"), ("return 1", "security"),
                                                                    (Q_ADD, "bug"), (Q_MUL, "bug")))
        self.verdicts(dict(verdict((Q_OLD, 5)), id=old), dict(verdict(("return 1", 2)), id=one),
                      verdict((Q_ADD, 8)), verdict((Q_MUL, 11), "unverifiable"))
        d = self.doc()
        got = {i["id"]: i for i in d["findings"]}
        self.assertNotIn(old, got)  # pre-existing Suggestion: dropped
        self.assertEqual((got[one]["severity"], got[one]["origin"]), ("blocking", "pre-existing"))  # Blocking stays
        self.assertEqual(got[add]["origin"], "introduced")
        self.assertEqual(d["dropped"]["do_not_flag"], 1)
        self.assertEqual([i["id"] for i in d["unverifiable"]], [mul])  # introduced noted/suggestion is kept

    def test_pre_existing_inferred_and_speculative_are_dropped(self):
        self.ingest(cand(Q_OLD, 5, severity="required"), cand("return 1", 2, severity="required"))
        self.verdicts(verdict((Q_OLD, 5), "unverifiable"))
        d = self.doc()
        self.assertEqual((d["findings"], d["unverifiable"], d["dropped"]["do_not_flag"]), ([], [], 2))

    def test_origin_spans_the_cited_range(self):
        self.ingest(cand(Q_OLD, 5, end_line=7))
        self.verdicts(verdict((Q_OLD, 5)))
        self.assertEqual(self.doc()["findings"][0]["origin"], "introduced")  # lines 6 and 7 are added

    def test_lint_ignore_line_is_dropped_at_ingest(self):
        out = self.ingest(cand(Q_SQ, 17), cand(Q_ADD, 8))
        self.assertEqual((len(out["ids"]), out["dropped"]), (1, {"invalid": 0, "do_not_flag": 1}))
        self.assertEqual(self.doc()["dropped"]["do_not_flag"], 1)
        for marker in ("noqa", "type: ignore", "pylint: disable", "eslint-disable", "@ts-ignore", "@ts-expect-error",
                       "nolint", "NOSONAR", "rubocop:disable", "#[allow(dead_code)]", "@SuppressWarnings", "nosec"):
            self.assertRegex("x = 1  // " + marker.upper(), report.LINT_IGNORE, marker)

    def test_invalid_candidates_are_counted_and_prose_is_not(self):
        os.symlink("/etc/hostname", os.path.join(self.run_dir, "tree", "link.py"))
        good = cand(Q_ADD, 8)
        out = self.ingest("prose", good, dict(good), cand(Q_ADD, 8, path="../x"), cand(Q_ADD, 8, path="/etc/hostname"),
                          cand(Q_ADD, 8, path="nope.py"), cand("x", 1, path="link.py"), cand("not on the line", 8),
                          cand(Q_ADD, 99), cand(Q_ADD, True), cand(Q_ADD, 8, severity="fatal"),
                          cand(Q_ADD, 8, category="style"), "{broken", "[1]", "{}")
        self.assertEqual((len(out["ids"]), out["dropped"]["invalid"]), (1, 12))

    def test_candidate_self_rating_is_ignored(self):
        self.ingest(dict(cand(Q_ADD, 8), id="F-0000000000", score=100, evidence_class="reproduced",
                         origin="pre-existing", verified=False, confidence=100))
        self.verdicts(verdict((Q_ADD, 8)))
        (f,) = self.doc()["findings"]
        self.assertEqual((f["id"], f["score"], f["evidence_class"], f["origin"], f["verified"]),
                         (fid("src/calc.py", "bug", Q_ADD), 75, "traced", "introduced", True))

    def test_order_is_severity_path_line_id(self):
        self.ingest(cand(Q_MUL, 11, severity="suggestion"), cand(Q_ADD, 8, severity="required"),
                    cand(Q_NEG, 14, severity="blocking", category="security"))
        self.verdicts(verdict((Q_MUL, 11)), verdict((Q_ADD, 8)),
                      dict(verdict((Q_NEG, 14)), id=fid("src/calc.py", "security", Q_NEG)))
        self.assertEqual([(i["severity"], i["line"]) for i in self.doc()["findings"]],
                         [("blocking", 14), ("required", 8), ("suggestion", 11)])

    def test_ingest_resets_review_state(self):
        self.ingest(cand(Q_ADD, 8))
        self.verdicts(verdict((Q_ADD, 8)))
        self.ingest(cand(Q_ADD, 8))
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "verdicts.json")))
        self.assertEqual(self.doc()["findings"], [])


class Renders(Base):
    base_files, feat_files = BASE2, {"src/calc.py": FEAT2}

    def seed(self, title="add subtracts"):
        self.ingest(cand(Q_ADD, 8, title=title, severity="blocking", category="security"), cand(Q_MUL, 11),
                    cand(Q_NEG, 14), cand(Q_INC, 20, category="accessibility"), {"inspected": ["src/calc.py"]})
        self.verdicts(dict(verdict((Q_ADD, 8)), id=fid("src/calc.py", "security", Q_ADD)),
                      verdict((Q_MUL, 11), "unverifiable", reason="needs runtime"),
                      dict(verdict((Q_INC, 20)), id=fid("src/calc.py", "accessibility", Q_INC)))

    def test_json_is_identical_for_shuffled_input(self):
        cs = [cand(Q_ADD, 8), cand(Q_MUL, 11, severity="suggestion"), cand(Q_NEG, 14), cand(Q_OLD, 5, severity="blocking",
              category="security")]
        vs = [verdict((Q_ADD, 8)), verdict((Q_MUL, 11), "unverifiable"), verdict((Q_NEG, 14), "refuted"),
              dict(verdict((Q_OLD, 5)), id=fid("src/calc.py", "security", Q_OLD))]
        outs = []
        for order in (1, -1):
            self.ingest(*cs[::order], {"inspected": ["src/calc.py"]})
            self.verdicts(*vs[::order])
            outs.append(self.rep("render", extra=["--format", "json"])[1])
        self.assertEqual(outs[0], outs[1])
        self.assertTrue(outs[0].isascii() and outs[0].endswith("}\n"))

    def test_sarif(self):
        self.seed()
        d = self.doc()
        rc, out = self.rep("render", extra=["--format", "sarif"])
        sarif = json.loads(out)
        self.assertEqual((sarif["version"], sarif["$schema"]), ("2.1.0", "https://json.schemastore.org/sarif-2.1.0.json"))
        (run,) = sarif["runs"]
        self.assertEqual(run["tool"]["driver"]["name"], "review-code")
        rules = [r["id"] for r in run["tool"]["driver"]["rules"]]
        self.assertEqual(len(run["results"]), len(d["findings"]))
        self.assertEqual(len(d["findings"]), 2)
        self.assertEqual(rules, sorted({"review-code/" + f["category"] for f in d["findings"]}))
        for r, f in zip(run["results"], d["findings"]):
            self.assertIn(r["ruleId"], rules)
            self.assertEqual(r["message"]["text"], f["title"])
            (loc,) = r["locations"]
            self.assertEqual(loc["physicalLocation"]["artifactLocation"]["uri"], f["path"])
            self.assertEqual(loc["physicalLocation"]["region"], {"startLine": f["line"], "endLine": f["end_line"]})
            self.assertEqual(r["partialFingerprints"], {"reviewCodeId/v1": f["id"]})
            self.assertEqual(r["properties"], {"evidenceClass": f["evidence_class"], "severity": f["severity"],
                                               "origin": f["origin"], "score": f["score"]})
        self.assertEqual([r["level"] for r in run["results"]], ["error", "warning"])

    def test_text_sections_and_escape(self):
        self.seed(title="boom \x1b[31mRED\r\x1b]0;pwn\x07")
        rc, out = self.rep("render")
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\r", out)
        self.assertIn("<U+001B>[31mRED", out)
        heads = [h for h in ("Summary", "Blocking", "Required", "Suggestions", "Unverifiable", "Areas inspected", "Verdict")
                 if re.search(r"(?m)^" + h + r"\b", out)]
        self.assertEqual(heads, ["Summary", "Blocking", "Required", "Suggestions", "Unverifiable", "Areas inspected", "Verdict"])
        for want in ("Needs changes", "advisory only", "needs runtime", "src/calc.py:8", "refuted 0", "do-not-flag 0",
                     "invalid 0", "trust: external"):
            self.assertIn(want, out)
        self.assertRegex(out, r"(?m)^Suggestions\nnone$")

    def test_render_before_ingest_refuses(self):
        for fmt in ("text", "json", "sarif"):
            rc, out = self.rep("render", extra=["--format", fmt])
            self.assertEqual((rc, json.loads(out)["status"]), (1, "refuse"))

    def test_usage_refuses_with_one_json_line(self):
        for args in ([], ["bogus", "--run", self.run_dir], ["render", "--run"], ["render", "--run", self.run_dir, "--format", "xml"],
                     ["ingest", "--run", self.run_dir, "x"], ["ingest", "--run", self.tmp]):
            rc, out, err = cli("report.py", args, self.repo, self.tmp)
            self.assertEqual((rc, err, json.loads(out)["status"]), (1, "", "refuse"), args)

    def test_python_39_grammar(self):
        with open(os.path.join(SCRIPTS, "report.py"), encoding="utf-8") as fh:
            ast.parse(fh.read(), feature_version=(3, 9))


class RepoTarget(Base):
    pin_args = ["--repo"]

    def test_whole_file_finding_is_existing_and_says_so(self):
        self.ingest(cand("return 1", 2, severity="suggestion"), {"inspected": ["src/calc.py"]})
        self.verdicts(verdict(("return 1", 2)))
        d = self.doc()
        (f,) = d["findings"]
        self.assertEqual((f["origin"], f["severity"], d["target"]["not_a_change"], d["dropped"]["do_not_flag"]),
                         ("existing", "suggestion", True, 0))
        rc, out = self.rep("render")
        self.assertIn("Target is not a change: whole-file findings", out)


if __name__ == "__main__":
    unittest.main()
