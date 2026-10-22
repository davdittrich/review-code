"""fix.py on real gitfix repos: plan, baseline, begin, a simulated fixer edit, finish, report."""
import ast
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import paths  # noqa: F401

SCRIPTS = paths.SCRIPTS

import fix  # noqa: E402
import gitfix  # noqa: E402
from gitfix import cli, lines  # noqa: E402
import target  # noqa: E402

BASE = {"src/calc.py": "def load():\n    return 1\n"}
FEAT = "def load():\n    return 1\n\ndef add(a, b):\n    return a - b\n"  # add is lines 4 and 5
QUOTE = "return a - b"
CAND = {"path": "src/calc.py", "line": 5, "severity": "required", "category": "bug", "title": "add subtracts",
        "trigger": "add(2, 3) returns -1", "quote": QUOTE, "fix": "return a + b"}
CITE = {"path": "src/calc.py", "line": 5, "quote": QUOTE}


class Base(gitfix.FeatureRepo):
    base_files, feat_files = BASE, {"src/calc.py": FEAT}

    def tool(self, script, args, stdin=""):
        rc, out, err = cli(script, args, self.repo, self.tmp, stdin.encode("utf-8"))
        self.assertNotIn("Traceback", err)
        return rc, out

    def pin(self, arg="main", trust="own"):
        rc, out = self.tool("target.py", [] if arg is None else [arg])
        self.assertEqual(rc, 0, out)
        run = json.loads(out)["run"]
        self.assertEqual(self.tool("ledger.py", ["build", "--run", run])[0], 0)
        if trust:
            self.assertEqual(self.tool("ledger.py", ["trust", "set", trust, "--run", run])[0], 0)
        return run

    def ingest(self, run, *cands):
        seen = sorted({c["path"] for c in cands} | {"src/calc.py"})
        rc, out = self.tool("report.py", ["ingest", "--run", run], lines(*(list(cands) + [{"inspected": seen}])))
        self.assertEqual(rc, 0, out)
        return json.loads(out)["ids"]

    def confirm(self, run, *pairs):
        """pairs: (id, cite) -> a confirmed verdict each."""
        rc, out = self.tool("report.py", ["verdicts", "--run", run], lines(*[
            {"id": i, "verdict": "confirmed", "evidence": [c], "reason": "r"} for i, c in pairs]))
        self.assertEqual(rc, 0, out)

    def cmd(self):
        return [sys.executable, "-B", "-c", "import sys; sys.path.insert(0, 'src'); import calc"]

    def fixrun(self, args, stdin=""):
        rc, out = self.tool("fix.py", args, stdin)
        last = [x for x in out.split("\n") if x][-1]
        return rc, out, json.loads(last)

    def plan(self, run, words=None):
        return self.fixrun(["plan", "--run", run], "".join(w + "\n" for w in (self.cmd() if words is None else words)))

    def baseline(self, run, digest):
        return self.fixrun(["baseline", "--run", run, "--digest", digest])

    def begin(self, run):
        return self.fixrun(["begin", "--run", run])

    def finish(self, run, seal, status="applied"):
        return self.fixrun(["finish", "--run", run, "--seal", seal, "--status", status])

    def report(self, run):
        return self.fixrun(["report", "--run", run])

    def edit(self, rel, old, new):
        """The simulated fixer: one in-place edit of one file."""
        path = os.path.join(self.repo, rel)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(old, new, 1))

    def refs(self):
        return self.git("rev-parse", "HEAD"), self.git("for-each-ref"), self.git("stash", "list")

    def ready(self, cand=CAND):
        """A pinned run with one confirmed finding, plan written and baseline green: (run, fid)."""
        run = self.pin()
        (fid,) = self.ingest(run, cand)
        self.confirm(run, (fid, dict(CITE, quote=cand["quote"], line=cand["line"])))
        return run, fid

    def go(self, run, words=None):
        rc, out, j = self.plan(run, words)
        self.assertEqual((rc, j["status"]), (0, "ok"), out)
        rc, _, j2 = self.baseline(run, j["digest"])
        self.assertEqual((rc, j2["status"]), (0, "ok"), j2)
        return j["digest"]


class Tracer(Base):
    def test_one_fix_end_to_end(self):
        run, fid = self.ready()
        before = self.refs()
        rc, out, j = self.plan(run)
        self.assertEqual((rc, j["status"], j["apply"]), (0, "ok", 1), out)
        self.assertRegex(j["digest"], "^[0-9a-f]{64}$")
        head = out.split("\n")[0]
        self.assertTrue(head.startswith("review-code fix plan: 1 to apply, 0 skipped; tests: "), head)
        for need in (fid, "src/calc.py:5", "sonnet/medium", " ".join(self.cmd()[:2])):
            self.assertIn(need, out)
        fixdir = os.path.join(run, "fix")
        self.assertEqual(stat.S_IMODE(os.stat(fixdir).st_mode), 0o700)
        pj = os.path.join(fixdir, "plan.json")
        self.assertEqual(stat.S_IMODE(os.stat(pj).st_mode), 0o600)
        with open(pj, "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), j["digest"])
        self.assertFalse(os.path.exists(os.path.join(fixdir, "state.json")))
        rc, _, b = self.baseline(run, j["digest"])
        self.assertEqual((rc, b["status"]), (0, "ok"), b)
        rc, _, bg = self.begin(run)
        self.assertEqual((rc, bg["status"], bg["id"], bg["path"], bg["line"], bg["model"], bg["effort"]),
                         (0, "ok", fid, "src/calc.py", 5, "sonnet", "medium"), bg)
        self.assertRegex(bg["seal"], "^[0-9a-f]{64}$")
        self.assertEqual(os.path.dirname(bg["brief"]), fixdir)
        self.edit("src/calc.py", QUOTE, "return a + b")
        rc, _, f = self.finish(run, bg["seal"])
        self.assertEqual((rc, f["status"], f["outcome"]), (0, "ok", "fixed"), f)
        self.assertEqual(self.begin(run)[2]["status"], "done")
        rc, text, r = self.report(run)
        self.assertEqual((rc, r["status"], r["fixed"]), (0, "ok", 1), text)
        self.assertIn("%s src/calc.py:5 fixed" % fid, text)
        with open(os.path.join(self.repo, "src/calc.py"), encoding="utf-8") as fh:
            self.assertIn("return a + b", fh.read())
        self.assertEqual(self.refs(), before)
        self.assertEqual(self.git("status", "--porcelain"), "M src/calc.py")

    def test_failing_tests_revert_byte_exact(self):
        run, fid = self.ready()
        self.go(run)
        path = os.path.join(self.repo, "src/calc.py")
        with open(path, "rb") as fh:
            old = fh.read()
        mode = os.stat(path).st_mode
        before = self.refs()
        seal = self.begin(run)[2]["seal"]
        self.edit("src/calc.py", QUOTE, "return a +")
        rc, _, f = self.finish(run, seal)
        self.assertEqual((rc, f["status"], f["outcome"]), (0, "ok", "reverted-tests"), f)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), old)
        self.assertEqual(os.stat(path).st_mode, mode)
        rc, _, b = self.begin(run)
        self.assertEqual((rc, b), (0, {"status": "done"}))
        self.assertEqual(self.refs(), before)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_baseline_red_edits_nothing(self):
        run, _ = self.ready()
        rc, _, j = self.plan(run, [sys.executable, "-B", "-c", "raise SystemExit(3)"])
        rc, _, b = self.baseline(run, j["digest"])
        self.assertEqual((rc, b["status"], b["reason"], b["exit"]), (1, "halted", "baseline-red", 3), b)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.begin(run)[2]["reason"], "halted")

    def test_brief_fences_untrusted_text(self):
        run, fid = self.ready()
        self.go(run)
        bg = self.begin(run)[2]
        with open(bg["brief"], encoding="utf-8") as fh:
            brief = fh.read()
        self.assertTrue(brief.startswith("# Fix finding %s\n" % fid), brief)
        self.assertIn(json.dumps(os.path.join(self.repo, "src/calc.py")), brief)
        nonces = set(re.findall(r"<<<UNTRUSTED-([0-9a-f]+)\n", brief))
        self.assertEqual(len(nonces), 1)
        (n,) = nonces
        for label, text in (("title", "add subtracts"), ("trigger", "add(2, 3) returns -1"), ("quote", QUOTE),
                            ("fix", "return a + b")):
            self.assertRegex(brief, r"(?m)^%s:\n<<<UNTRUSTED-%s\n%s\n%s>>>$" % (label, n, re.escape(text), n))
        pre = os.path.join(run, "fix", fid + ".pre")
        with open(pre, "rb") as fh, open(os.path.join(self.repo, "src/calc.py"), "rb") as cur:
            self.assertEqual(fh.read(), cur.read())
        self.assertEqual(stat.S_IMODE(os.stat(pre).st_mode), 0o600)

    def test_no_history_writing_git_call(self):
        with open(os.path.join(SCRIPTS, "fix.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), feature_version=(3, 9))
        bad = {"commit", "push", "add", "stash", "reset", "checkout", "switch", "merge", "rebase", "tag", "fetch",
               "pull", "am"}
        self.assertEqual([n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value in bad], [])


def append(path, text):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


def two(path, line, quote, fix_text, **kw):
    return dict(CAND, path=path, line=line, quote=quote, fix=fix_text, **kw)


class PlanGates(Base):
    def refused(self, run, reason, words=None, fixdir=None):
        rc, out, j = self.plan(run, words)
        self.assertEqual((rc, j["status"], j["reason"]), (1, "refuse", reason), out)
        if reason != "already-run":
            self.assertFalse(os.path.exists(os.path.join(run, "fix", "state.json")))
        if fixdir is False:
            self.assertFalse(os.path.exists(os.path.join(run, "fix")))
        return j

    def test_uncommitted_targets_refused(self):
        path = os.path.join(self.repo, "src/calc.py")
        append(path, "# staged\n")
        self.git("add", "src/calc.py")
        append(path, "# unstaged\n")
        for arg in ("--staged", "--unstaged", None):
            with self.subTest(arg=arg):
                self.refused(self.pin(arg), "uncommitted-target", fixdir=False)

    def test_dirty_path_pin_refused(self):
        append(os.path.join(self.repo, "src/calc.py"), "# edit\n")
        run = self.pin("src/calc.py")
        with open(os.path.join(run, "target.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["kind"], "path")
        self.refused(run, "dirty-pin", fixdir=False)

    def test_trust_and_command_gates(self):
        self.refused(self.pin(trust=None), "trust-unset", fixdir=False)
        self.refused(self.pin(trust="external"), "trust-external", fixdir=False)
        run = self.pin()
        self.refused(run, "no-command", words=[], fixdir=False)
        self.refused(run, "bad-command", words=[sys.executable, "a\0b"], fixdir=False)

    def test_head_must_be_checked_out(self):
        run = self.pin()
        self.git("checkout", "-q", "main")
        self.refused(run, "head-not-checked-out", fixdir=False)

    def test_head_held_by_another_worktree(self):
        self.git("checkout", "-q", "main")
        wt = os.path.join(self.tmp, "wt")
        self.git("worktree", "add", "-q", wt, "feat")
        run, stale = self.pin("main...feat"), self.pin("main...feat")
        (fid,) = self.ingest(run, CAND)
        self.confirm(run, (fid, CITE))
        rc, out, pl = self.plan(run)
        self.assertIn("checkout: " + wt, out)
        self.assertEqual(self.baseline(run, pl["digest"])[0], 0)
        rc, _, bg = self.begin(run)
        self.assertEqual((rc, bg["status"]), (0, "ok"), bg)
        with open(bg["brief"], encoding="utf-8") as fh:
            self.assertIn("file: " + json.dumps(os.path.join(wt, "src/calc.py")), fh.read())
        self.assertEqual(self.finish(run, bg["seal"], "declined")[0], 0)
        self.assertIn("checkout: " + wt + " (run git diff there)", self.report(run)[1])
        self.git("-C", wt, "checkout", "-q", "--detach", "main")  # the recorded root lost the head
        self.refused(stale, "head-not-checked-out", fixdir=False)

    def test_checkout_path_shown_only_when_it_differs_from_repo(self):
        run, _ = self.ready()
        rc, out, j = self.plan(run)
        self.assertEqual((rc, j["status"]), (0, "ok"), out)
        self.assertNotIn("checkout: ", out)
        self.assertEqual(self.baseline(run, j["digest"])[0], 0)
        rc, out, j = self.report(run)
        self.assertNotIn("checkout: ", out)
        self.assertIn("git diff before committing", out)
        self.git("checkout", "-q", "main")
        wt = os.path.join(self.tmp, "wt")
        self.git("worktree", "add", "-q", wt, "feat")
        run = self.pin("main...feat")
        (fid,) = self.ingest(run, CAND)
        self.confirm(run, (fid, CITE))
        rc, out, j = self.plan(run)  # preview names the worktree
        self.assertIn("checkout: " + wt, out)
        self.assertEqual(self.baseline(run, j["digest"])[0], 0)
        rc, out, j = self.report(run)
        self.assertEqual((rc, j["status"]), (0, "ok"), out)
        self.assertIn("checkout: " + wt, out)
        self.assertIn("git diff in " + wt, out)

    def test_tree_must_be_clean(self):
        run = self.pin()
        append(os.path.join(self.repo, "src/calc.py"), "# edit\n")
        self.refused(run, "dirty-tree", fixdir=False)
        self.git("checkout", "--", "src/calc.py")
        append(os.path.join(self.repo, "junk.txt"), "x")
        self.refused(run, "dirty-tree", fixdir=False)

    def test_second_run_refused(self):
        run, _ = self.ready()
        self.go(run)
        self.refused(run, "already-run")
        rc, _, j = self.baseline(run, "0" * 64)
        self.assertEqual((rc, j["reason"]), (1, "already-run"))

    def test_nothing_to_fix_counts_reasons(self):
        run = self.pin()
        self.ingest(run, CAND)  # never confirmed: unverifiable
        self.assertEqual(self.refused(run, "nothing-to-fix")["detail"], "skipped: none")

    def test_same_plan_twice_same_digest(self):
        run, _ = self.ready()
        first = self.plan(run)[2]["digest"]
        self.assertEqual(self.plan(run)[2]["digest"], first)


class Skips(Base):
    feat_files = {
        "src/calc.py": FEAT,
        "src/dup.py": "def f(x):\n    return x * 2\n\ndef g(x):\n    return x * 2\n",
        "src/nofix.py": "def h(x):\n    return x - 1\n",
        "setup.py": "def setup(a, b):\n    return a - b\n",
    }

    def test_each_skip_is_named_in_the_preview(self):
        run = self.pin()
        cands = [CAND, two("src/dup.py", 2, "return x * 2", "return x * 3"),
                 two("src/nofix.py", 2, "return x - 1", ""), two("setup.py", 2, "return a - b", "return a + b")]
        self.assertEqual(len(self.ingest(run, *cands)), 4)
        self.confirm(run, *[(report_id(c), dict(path=c["path"], line=c["line"], quote=c["quote"])) for c in cands])
        rc, out, j = self.plan(run)
        self.assertEqual((rc, j["apply"], j["skipped"]), (0, 3, 1), out)
        block = out.split("== skipped ==\n")[1].split("\n")
        want = {report_id(cands[1]): "stale"}
        got = {ln.split()[0]: ln.split()[2] for ln in block[:1]}
        self.assertEqual(got, want)
        with open(os.path.join(run, "fix", "plan.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual({s["id"]: s["why"] for s in doc["skipped"]}, want)
        self.assertEqual([e["path"] for e in doc["entries"]], ["setup.py", "src/calc.py", "src/nofix.py"])


class DerivedFixes(Base):
    """review-code#33: a verified finding with no fix text is planned, marked, and handed to the fixer to derive."""
    feat_files = {"src/calc.py": FEAT}

    def test_finding_without_fix_text_is_planned_marked_and_briefed(self):
        run, fid = self.ready(dict(CAND, fix=""))
        rc, out, j = self.plan(run)
        self.assertEqual((rc, j["apply"], j["skipped"]), (0, 1, 0), out)
        self.assertIn("derived by fixer", out)
        self.assertNotIn("no-fix", out)
        self.assertEqual(self.baseline(run, j["digest"])[2]["status"], "ok")
        bg = self.begin(run)[2]
        self.assertEqual((bg["status"], bg["id"]), ("ok", fid), bg)
        with open(bg["brief"], encoding="utf-8") as fh:
            brief = fh.read()
        self.assertIn("fix: none. No fix text exists", brief)
        for label, text in (("title", "add subtracts"), ("trigger", "add(2, 3) returns -1"), ("quote", QUOTE)):
            self.assertRegex(brief, r"(?m)^%s:\n<<<UNTRUSTED-[0-9a-f]+\n%s\n" % (label, re.escape(text)))
        self.assertNotRegex(brief, r"(?m)^fix:\n")
        self.edit("src/calc.py", QUOTE, "return a + b")  # the derived edit is judged like any other
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "fixed")

    def test_derived_fix_that_breaks_the_tests_is_reverted(self):
        run, fid = self.ready(dict(CAND, fix=""))
        self.go(run)
        bg = self.begin(run)[2]
        self.edit("src/calc.py", QUOTE, "return a +")
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "reverted-tests")
        self.assertEqual(self.git("status", "--porcelain"), "")


class SecondFileNamed(Base):
    feat_files = {"src/calc.py": FEAT, "src/dup.py": "def f(x):\n    return x * 2\n", "src/utils.py": "def helper():\n    pass\n"}

    def skipped(self, lane, cand):
        run = self.pin()
        rc, out = self.tool("report.py", ["ingest", "--run", run, "--lane", lane],
                            lines(cand, {"inspected": sorted({cand["path"], "src/calc.py"})}))
        self.assertEqual(rc, 0, out)
        (fid,) = json.loads(out)["ids"]
        self.confirm(run, (fid, dict(path=cand["path"], line=cand["line"], quote=cand["quote"])))
        rc, _, j = self.plan(run)
        return rc, j

    def test_fix_naming_a_second_tracked_file_is_kept_for_the_fixer_to_decline_or_apply(self):
        # review-code-3ea.6: a reuse fix names the helper it calls; only finish enforces one edited file
        for cat, text in (("lean", "call helper in src/utils.py"), ("smell", "call helper in src/utils.py."),
                          ("altitude", "see src/utils.py:1"), ("lean", "call helper in ./src/utils.py")):
            with self.subTest(cat=cat, text=text):
                cand = two("src/dup.py", 2, "return x * 2", text, category=cat)
                rc, j = self.skipped("standards", cand)
                self.assertEqual((rc, j["apply"], j["skipped"]), (0, 1, 0), j)
                self.tearDown()
                self.setUp()

    def test_comment_finding_naming_a_second_file_is_kept(self):
        cand = two("src/dup.py", 2, "return x * 2", "see src/utils.py", category="comment")
        rc, j = self.skipped("standards", cand)
        self.assertEqual((rc, j["apply"], j["skipped"]), (0, 1, 0), j)

    def test_same_text_on_a_defects_finding_is_kept(self):
        cand = two("src/dup.py", 2, "return x * 2", "call helper in src/utils.py")
        rc, j = self.skipped("defects", cand)
        self.assertEqual((rc, j["apply"], j["skipped"]), (0, 1, 0), j)

    def test_standards_fix_naming_only_its_own_or_an_untracked_path_is_kept(self):
        for text in ("inline into src/dup.py", "call helper in src/missing.py"):
            with self.subTest(text=text):
                rc, j = self.skipped("standards", two("src/dup.py", 2, "return x * 2", text, category="lean"))
                self.assertEqual((rc, j["apply"], j["skipped"]), (0, 1, 0), j)
                self.tearDown()
                self.setUp()


class NoNarrowing(Base):
    feat_files = {"src/calc.py": FEAT, "src/dup.py": "def f(x):\n    return x * 2\n"}

    def test_plan_keeps_every_verified_finding_and_refuses_a_narrowing_list(self):
        run = self.pin()
        cands = [CAND, two("src/dup.py", 2, "return x * 2", "return x * 3")]
        self.ingest(run, *cands)
        self.confirm(run, *[(report_id(c), dict(path=c["path"], line=c["line"], quote=c["quote"])) for c in cands])
        words = "".join(w + "\n" for w in self.cmd())
        rc, out, j = self.fixrun(["plan", "--run", run, "--only", report_id(cands[0])], words)
        self.assertEqual((rc, j["reason"]), (1, "usage"), out)
        self.assertFalse(os.path.exists(os.path.join(run, "fix")))
        rc, out, j = self.plan(run)
        self.assertEqual((rc, j["apply"], j["skipped"]), (0, 2, 0), out)


RUN = "# runner\nrun 3 - 1\n"
SKIPPED = ("playwright.config.ts", "jest.setup.js", "webpack.config.babel.js", "setupTests.ts",
           "e2e/login.cy.ts", "src/Foo.test.mjs", "src/Foo.spec.cjs", "lib/foo_spec.rb", "src/FooTests.cs",
           "pytest.ini", "tox.ini")
SHARED = ("package.json", "pyproject.toml", "setup.cfg", "Makefile")  # judge the tests only when the test command names them
KEPT = ("ci/run-tests.sh", "src/spec/parser.py", "src/testing_utils.py", "src/latest.py", "src/contest.py", "src/Contests.cs",
        "lib/specs.rb", "src/webpack.configs.js")


class TestPathSkips(Base):
    feat_files = dict({"src/calc.py": FEAT, "tests/test_calc.py": "def test_x():\n    assert 1 - 1\n",
                       "conftest.py": "def f():\n    return 2 - 1\n", "jest.config.js": "module.exports = 3 - 1;\n"},
                      **{p: RUN for p in SKIPPED + KEPT + SHARED + tuple("sub/" + q for q in SHARED)})

    def planned(self, cands, words=None):
        run = self.pin()
        self.assertEqual(len(self.ingest(run, *cands)), len(cands))
        self.confirm(run, *[(report_id(c), dict(path=c["path"], line=c["line"], quote=c["quote"])) for c in cands])
        rc, out, j = self.plan(run, words)
        self.assertEqual(rc, 0, out)
        with open(os.path.join(run, "fix", "plan.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        return {s["path"]: s["why"] for s in doc["skipped"]}, [e["path"] for e in doc["entries"]]

    def test_test_files_are_skipped(self):
        cands = [CAND, two("tests/test_calc.py", 2, "assert 1 - 1", "assert 1 + 1"),
                 two("conftest.py", 2, "return 2 - 1", "return 2 + 1"),
                 two("jest.config.js", 1, "module.exports = 3 - 1;", "module.exports = 3 + 1;")]
        skipped, applied = self.planned(cands)
        self.assertEqual(skipped, {c["path"]: "test-path" for c in cands[1:]})
        self.assertEqual(applied, ["src/calc.py"])

    def test_runner_setup_and_test_name_shapes_are_skipped(self):
        cands = [CAND] + [two(p, 2, "run 3 - 1", "run 3 + 1") for p in SKIPPED]
        skipped, applied = self.planned(cands)
        self.assertEqual(skipped, {p: "test-path" for p in SKIPPED})
        self.assertEqual(applied, ["src/calc.py"])

    def test_shared_config_judges_only_when_the_test_command_names_it(self):
        cands = [two(p, 2, "run 3 - 1", "run 3 + 1") for p in ("package.json", "sub/package.json", "sub/Makefile")]
        skipped, applied = self.planned(cands)  # not named: all fixable, flagged protected
        self.assertEqual((skipped, sorted(applied)), ({}, sorted(c["path"] for c in cands)))
        self.tearDown()
        self.setUp()
        skipped, applied = self.planned(cands, ["./package.json"])
        self.assertEqual(skipped, {"package.json": "test-path"})
        self.assertEqual(sorted(applied), ["sub/Makefile", "sub/package.json"])

    def test_named_shared_config_stays_protected_with_fix_tests(self):
        run = self.pin()
        c = two("package.json", 2, "run 3 - 1", "run 3 + 1")
        self.ingest(run, c)
        self.confirm(run, (report_id(c), dict(path=c["path"], line=c["line"], quote=c["quote"])))
        rc, out, j = self.fixrun(["plan", "--run", run, "--fix-tests"], "package.json\n")
        self.assertEqual((rc, j["reason"], j["detail"]), (1, "nothing-to-fix", "skipped: test-path 1"), out)  # review-code#30

    def test_source_files_with_test_like_names_stay_fixable(self):
        cands = [two(p, 2, "run 3 - 1", "run 3 + 1") for p in KEPT]
        skipped, applied = self.planned(cands)
        self.assertEqual((skipped, sorted(applied)), ({}, sorted(KEPT)))

    def test_the_test_command_script_is_skipped(self):
        run_sh = two("ci/run-tests.sh", 2, "run 3 - 1", "run 3 + 1")
        for words in (["./ci/run-tests.sh"], ["sh", "ci/run-tests.sh"]):
            with self.subTest(words=words):
                skipped, applied = self.planned(
                    [CAND, run_sh, two("src/latest.py", 2, "run 3 - 1", "run 3 + 1")], words)
                self.assertEqual(skipped, {"ci/run-tests.sh": "test-path"})
                self.assertEqual(sorted(applied), ["src/calc.py", "src/latest.py"])
                self.tearDown()
                self.setUp()


class FixTestsOptIn(Base):
    """review-code#26: `plan --fix-tests` lets test-file findings into the plan, flagged and counted; the tests gate is unchanged."""
    feat_files = TestPathSkips.feat_files
    TEST_CAND = two("tests/test_calc.py", 2, "assert 1 - 1", "assert 1 + 1")
    WORDS = [sys.executable, "-B", "-c", "assert '-' in open('tests/test_calc.py').read()"]

    def opted(self, extra=("--fix-tests",)):
        run = self.pin()
        cands = [CAND, self.TEST_CAND]
        self.ingest(run, *cands)
        self.confirm(run, *[(report_id(c), dict(path=c["path"], line=c["line"], quote=c["quote"])) for c in cands])
        rc, out, j = self.fixrun(["plan", "--run", run, *extra], "".join(w + "\n" for w in self.WORDS))
        return run, rc, out, j

    def test_default_still_skips_the_test_file(self):
        run, rc, out, j = self.opted(extra=())
        self.assertEqual((rc, j["apply"], j["skipped"], j.get("test_files", 0)), (0, 1, 1, 0), out)

    def test_flag_plans_the_test_file_labels_it_and_counts_it(self):
        run, rc, out, j = self.opted()
        self.assertEqual((rc, j["apply"], j["skipped"], j["test_files"]), (0, 2, 0, 1), out)
        self.assertIn("; 1 test file;", out.split("\n")[0])
        rows = [ln for ln in out.split("\n") if ln[:1].isdigit()]
        self.assertEqual({ln.split()[2].split(":")[0]: ln.endswith("test file") for ln in rows},
                         {"src/calc.py": False, "tests/test_calc.py": True})
        self.assertNotIn("test-path", out)

    def test_brief_warns_and_a_red_post_fix_run_still_reverts(self):
        run, rc, out, j = self.opted()
        self.assertEqual(self.baseline(run, j["digest"])[2]["status"], "ok")
        for _ in range(2):
            bg = self.begin(run)[2]
            with open(bg["brief"], encoding="utf-8") as fh:
                self.assertEqual("test file:" in fh.read(), bg["path"] == "tests/test_calc.py")
            if bg["path"] == "tests/test_calc.py":
                self.edit(bg["path"], "assert 1 - 1", "assert 1 + 1")  # drops the '-' the command asserts: red
                self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "reverted-tests")
                self.assertIn("assert 1 - 1", slurp(os.path.join(self.repo, "tests/test_calc.py")).decode())
            else:
                self.edit(bg["path"], QUOTE, "return a + b")
                self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "fixed")


def report_id(c):
    return "F-" + hashlib.sha256("\0".join((c["path"], c["category"], " ".join(c["quote"].split()))).encode()).hexdigest()[:10]


class BeginGates(Base):
    def test_begin_needs_a_baseline(self):
        run, _ = self.ready()
        self.plan(run)
        self.assertEqual(self.begin(run)[2]["reason"], "no-baseline")

    def test_second_begin_while_in_flight(self):
        run, _ = self.ready()
        self.go(run)
        self.assertEqual(self.begin(run)[2]["status"], "ok")
        rc, _, j = self.begin(run)
        self.assertEqual((rc, j), (1, {"status": "refuse", "reason": "in-flight"}))

    def test_moved_state_refused(self):
        for reason, mutate in (
                ("head-moved", lambda run: self.git("commit", "-q", "--allow-empty", "-m", "later")),
                ("refs-moved", lambda run: self.git("branch", "other")),
                ("tree-changed", lambda run: append(os.path.join(self.repo, "src/calc.py"), "# x\n")),
                ("digest-mismatch", lambda run: append(os.path.join(run, "fix", "plan.json"), " "))):
            with self.subTest(reason=reason):
                run, _ = self.ready()
                self.go(run)
                mutate(run)
                rc, _, j = self.begin(run)
                self.assertEqual((rc, j), (1, {"status": "refuse", "reason": reason}))
                self.git("checkout", "-q", "--", "src/calc.py")
                self.git("branch", "-q", "-D", "other") if reason == "refs-moved" else None
                if reason == "head-moved":
                    self.git("reset", "-q", "--hard", "HEAD~1")

    def test_test_run_cache_is_not_a_change(self):
        run, _ = self.ready()
        words = [sys.executable, "-c", "import sys; sys.path.insert(0, 'src'); import calc"]  # leaves src/__pycache__
        j = self.plan(run, words)[2]
        self.assertEqual(self.baseline(run, j["digest"])[2]["status"], "ok")
        self.assertIn("src/__pycache__/", self.git("status", "--porcelain", "--untracked-files=normal"))
        self.assertEqual(self.begin(run)[2]["status"], "ok")


class Shift(Base):
    feat_files = {"src/calc.py": FEAT + "\ndef mul(a, b):\n    return a / b\n"}
    second = two("src/calc.py", 8, "return a / b", "return a * b")

    def start(self):
        run = self.pin()
        self.ingest(run, CAND, self.second)
        self.confirm(run, *[(report_id(c), dict(path=c["path"], line=c["line"], quote=c["quote"]))
                            for c in (CAND, self.second)])
        self.go(run)
        return run

    def test_later_finding_follows_the_inserted_lines(self):
        run = self.start()
        bg = self.begin(run)[2]
        self.assertEqual((bg["id"], bg["line"]), (report_id(CAND), 5))
        self.edit("src/calc.py", QUOTE, "total = a\n    return total + b")
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "fixed")
        bg = self.begin(run)[2]
        self.assertEqual((bg["id"], bg["line"]), (report_id(self.second), 9))
        with open(bg["brief"], encoding="utf-8") as fh:
            self.assertIn("lines: 9-9", fh.read())

    def test_vanished_quote_is_stale_not_edited(self):
        run = self.start()
        bg = self.begin(run)[2]
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.edit("src/calc.py", "return a / b", "return a // b")
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "fixed")
        self.assertEqual(self.begin(run)[2]["status"], "done")
        with open(os.path.join(run, "fix", "state.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["done"][report_id(self.second)]["outcome"], "stale")


class Paths(Base):
    feat_files = {"src/calc.py": FEAT, "src/dup.py": "x = 1\n"}

    def test_protected(self):
        for p in ("package-lock.json", "sub/Cargo.lock", ".github/workflows/ci.yml", ".env", "pyproject.toml",
                  "pkg/package.json", "conf/app.yaml", "requirements-dev.txt", "go.mod", "NAMESPACE", "Jenkinsfile"):
            self.assertTrue(fix._protected(p), p)
        for p in ("src/calc.py", "README.md", "tests/test_calc.py"):
            self.assertFalse(fix._protected(p), p)

    def test_live(self):
        outside = os.path.join(self.tmp, "outside.py")
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        os.symlink("src", os.path.join(self.repo, "link"))
        os.symlink("src/calc.py", os.path.join(self.repo, "alias.py"))
        self.git("add", "link", "alias.py")
        self.git("commit", "-q", "-m", "links")
        with open(os.path.join(self.repo, "loose.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        self.assertEqual(fix._live(self.repo, "src/calc.py"), os.path.join(self.repo, "src", "calc.py"))
        for p in (outside, "../outside.py", "src/../../outside.py", "link/calc.py", "alias.py", "loose.py", "src", "",
                  "src/missing.py", "src/calc.py\0", None):
            self.assertIsNone(fix._live(self.repo, p), p)

    def test_relocate(self):
        text = "a = 1\nif x:\n    return a -\n        b\nreturn 2\n"
        self.assertEqual(fix._relocate(text, "return a - b"), 3)
        self.assertEqual(fix._relocate(text, "return   2"), 5)
        self.assertIsNone(fix._relocate(text, "missing"))
        self.assertIsNone(fix._relocate(text + text, "return 2"))
        self.assertIsNone(fix._relocate(text, "   "))


class Units(unittest.TestCase):
    def test_top_level_defs_have_two_blank_lines(self):
        def slips(src):  # top-level def/class not preceded by exactly two blank lines (comments and decorators stay attached)
            rows, bad = src.split("\n"), []
            for node in ast.parse(src).body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    i = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 2
                    while i >= 0 and rows[i].startswith("#"):
                        i -= 1
                    n = 0
                    while i >= 0 and rows[i] == "":
                        n, i = n + 1, i - 1
                    if i >= 0 and n != 2:
                        bad.append((node.name, n))
            return bad

        self.assertEqual(slips("x = 1\ndef a():\n    pass\n"), [("a", 0)])
        self.assertEqual(slips("x = 1\n\ndef a():\n    pass\n"), [("a", 1)])
        self.assertEqual(slips("x = 1\n\n\n\ndef a():\n    pass\n"), [("a", 3)])
        self.assertEqual(slips("x = 1\n\n\n# c\n@d\ndef a():\n    pass\ns = \"\"\"\n\ndef b():\n\"\"\"\n"), [])
        with open(os.path.join(SCRIPTS, "fix.py"), encoding="utf-8") as fh:
            self.assertEqual(slips(fh.read()), [])

    def test_tier(self):
        simple = {"category": "bug", "severity": "required", "line": 5, "end_line": 5, "fix": "return a + b"}
        self.assertEqual(fix._tier(simple), ("sonnet", "medium"))
        self.assertEqual(fix._tier(dict(simple, category="security")), ("opus", "high"))
        self.assertEqual(fix._tier(dict(simple, severity="blocking")), ("opus", "high"))
        self.assertEqual(fix._tier(dict(simple, end_line=9)), ("sonnet", "medium"))
        self.assertEqual(fix._tier(dict(simple, end_line=10)), ("opus", "high"))
        self.assertEqual(fix._tier(dict(simple, fix="x" * 300)), ("sonnet", "medium"))
        self.assertEqual(fix._tier(dict(simple, fix="x" * 301)), ("opus", "high"))

    def test_seal_reads_files_only(self):
        with tempfile.TemporaryDirectory() as d:
            fid = "F-0123456789"
            os.mkdir(os.path.join(d, "fix"))
            cfg, attr = os.path.join(d, "config"), os.path.join(d, "attributes")
            files = {os.path.join(d, "fix", "plan.json"): b"{}", os.path.join(d, "fix", fid + ".pre"): b"pre",
                     os.path.join(d, "target.json"): b"{}", os.path.join(d, "ledger.json"): b"{}",
                     cfg: b"[core]\n", attr: b"* text\n"}
            state = os.path.join(d, "fix", "state.json")
            files[state] = json.dumps({"git_files": [cfg, attr], "x": 1}).encode()
            for p, data in files.items():
                with open(p, "wb") as fh:
                    fh.write(data)
            with mock.patch.object(target, "git_bytes", side_effect=AssertionError("git call")) as g, \
                    mock.patch("subprocess.Popen", side_effect=AssertionError("process")), \
                    mock.patch("subprocess.run", side_effect=AssertionError("process")):
                base = fix._seal(d, fid)
                self.assertRegex(base, "^[0-9a-f]{64}$")
                self.assertEqual(fix._seal(d, fid), base)
                for p, data in files.items():
                    with open(p, "wb") as fh:
                        fh.write(data + (b"\n" if p != state else b" "))
                    self.assertNotEqual(fix._seal(d, fid), base, p)
                    with open(p, "wb") as fh:
                        fh.write(data)
                os.remove(os.path.join(d, "fix", fid + ".pre"))
                self.assertNotEqual(fix._seal(d, fid), base)
            self.assertEqual(g.call_count, 0)

    def test_timeout_fits_the_bash_call(self):
        docs = os.path.dirname(SCRIPTS)
        with open(os.path.join(docs, "references", "fix.md"), encoding="utf-8") as fh:
            ref = " ".join(fh.read().split())
        skill = ref  # the Bash timeout instruction moved from SKILL.md step 12 into fix.md Flow
        ms, said = re.search(r"`timeout` of (\d+), because their test run may take up to (\d+) seconds", skill).groups()
        past = re.search(r"ran past (\d+) seconds \(`TIMEOUT`\)", ref).group(1)
        self.assertEqual((int(said), int(past)), (fix.TIMEOUT, fix.TIMEOUT))  # the docs state the constant
        self.assertLessEqual(fix.TIMEOUT + 60, int(ms) // 1000)  # 60 s for the group kill, restore and sweep

    def test_first_signal_ignores_the_rest(self):
        names = [n for n in ("SIGTERM", "SIGHUP") if getattr(signal, n, None)]
        old = {n: signal.getsignal(getattr(signal, n)) for n in names}
        for n, h in old.items():
            self.addCleanup(signal.signal, getattr(signal, n), h)
        for n in names:
            signal.signal(getattr(signal, n), fix._term)
        with self.assertRaises(SystemExit) as cm:
            fix._term(signal.SIGTERM, None)
        self.assertEqual(cm.exception.code, 143)
        for n in names:  # a second signal during the restore must not unwind it
            self.assertEqual(signal.getsignal(getattr(signal, n)), signal.SIG_IGN, n)

    def test_py39_grammar(self):
        with open(os.path.join(SCRIPTS, "fix.py"), encoding="utf-8") as fh:
            ast.parse(fh.read(), feature_version=(3, 9))


OTHER = "def other():\n    return 'o'\n"
SECOND = "def mul(a, b):\n    return a / b\n"
LOCK = '{"lock": 1}\n'


def slurp(path):
    with open(path, "rb") as fh:
        return fh.read()


class Scope(Base):
    feat_files = {"src/calc.py": FEAT, "src/other.py": OTHER, "src/second.py": SECOND, "package-lock.json": LOCK}

    def p(self, rel):
        return os.path.join(self.repo, rel)

    def started(self, cand=CAND, *more, words=None, armed=False):
        """(run, fid, bg): baseline green, first fix handed out, nothing edited yet. armed: the go file exists
        already, so the baseline run takes the probe branch too."""
        run = self.pin()
        cands = (cand,) + more
        ids = self.ingest(run, *cands)
        self.confirm(run, *[(i, dict(CITE, path=c["path"], quote=c["quote"], line=c["line"]))
                            for i, c in zip(ids, cands)])
        if armed:
            self.arm()
        self.go(run, words)
        bg = self.begin(run)[2]
        self.assertEqual(bg["status"], "ok", bg)
        return run, bg["id"], bg

    def arm(self):
        with open(os.path.join(self.tmp, "go"), "w", encoding="utf-8"):
            pass

    def probe(self, body):
        """A test command that passes until the go file exists, then runs body (one line of python source)."""
        # one line: the plan takes one command word per line
        code = "import os, sys, time; os.path.exists(%r) and exec(%r)" % (os.path.join(self.tmp, "go"), body)
        return [sys.executable, "-B", "-c", code]

    def state(self, run):
        with open(os.path.join(run, "fix", "state.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def quarantined(self, run, fid, rel):
        return os.path.join(run, "fix", "quarantine", fid, rel)

    def violation(self, run, bg, status="applied"):
        rc, _, f = self.finish(run, bg["seal"], status)
        self.assertEqual((rc, f["status"], f["outcome"]), (1, "halted", "reverted-violation"), f)
        self.assertEqual(self.state(run)["halted"], "reverted-violation")
        self.assertEqual(self.begin(run)[2], {"status": "refuse", "reason": "halted"})
        return f


class FinishScope(Scope):
    def test_second_tracked_file(self):
        run, fid, bg = self.started()
        before = slurp(self.p("src/other.py"))
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.edit("src/other.py", "'o'", "'p'")
        f = self.violation(run, bg)
        self.assertIn("src/other.py", f["detail"])
        self.assertEqual(slurp(self.p("src/other.py")), before)
        self.assertEqual(slurp(self.p("src/calc.py")), FEAT.encode())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_new_untracked_file(self):
        run, fid, bg = self.started()
        self.edit("src/calc.py", QUOTE, "return a + b")
        with open(self.p("conftest.py"), "wb") as fh:
            fh.write(b"import evil\n")
        self.violation(run, bg)
        self.assertFalse(os.path.exists(self.p("conftest.py")))
        q = self.quarantined(run, fid, "conftest.py")
        self.assertEqual(slurp(q), b"import evil\n")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(q)).st_mode), 0o700)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_non_utf8_stray_is_quarantined(self):
        run, fid, bg = self.started()
        self.edit("src/calc.py", QUOTE, "return a + b")
        name = b"stray\xff.py"
        path = os.path.join(os.fsencode(self.repo), name)
        try:
            with open(path, "wb") as fh:
                fh.write(b"x\n")
        except OSError:
            self.skipTest("the filesystem refuses a name that is not valid UTF-8")
        self.violation(run, bg)
        self.assertFalse(os.path.lexists(path))
        q = os.path.join(os.fsencode(self.quarantined(run, fid, "")), name)
        self.assertEqual(slurp(q), b"x\n")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_untracked_in_a_new_directory(self):
        run, fid, bg = self.started()
        os.makedirs(self.p("pkg/deep"))
        with open(self.p("pkg/deep/x.py"), "wb") as fh:
            fh.write(b"x\n")
        self.violation(run, bg)
        self.assertEqual(slurp(self.quarantined(run, fid, "pkg/deep/x.py")), b"x\n")

    def test_staged_change(self):
        run, fid, bg = self.started()
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.git("add", "src/calc.py")
        self.violation(run, bg)
        self.assertEqual(self.git("diff", "--cached"), "")
        self.assertEqual(slurp(self.p("src/calc.py")), FEAT.encode())

    def test_staged_new_file(self):
        run, fid, bg = self.started()
        with open(self.p("added.py"), "wb") as fh:
            fh.write(b"y\n")
        self.git("add", "added.py")
        self.violation(run, bg)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(slurp(self.quarantined(run, fid, "added.py")), b"y\n")

    def test_deleted_tracked_file_and_protected_file(self):
        run, fid, bg = self.started()
        self.edit("src/calc.py", QUOTE, "return a + b")
        os.remove(self.p("src/other.py"))
        with open(self.p("package-lock.json"), "w", encoding="utf-8") as fh:
            fh.write('{"lock": 2}\n')
        f = self.violation(run, bg)
        for rel in ("src/other.py", "package-lock.json"):
            self.assertIn(rel, f["detail"])
        self.assertEqual(slurp(self.p("src/other.py")), OTHER.encode())
        self.assertEqual(slurp(self.p("package-lock.json")), LOCK.encode())
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(slurp(self.quarantined(run, fid, "package-lock.json")), b'{"lock": 2}\n')

    def test_deleted_entry_file(self):
        run, fid, bg = self.started()
        os.remove(self.p("src/calc.py"))
        self.violation(run, bg)
        self.assertEqual(slurp(self.p("src/calc.py")), FEAT.encode())

    def test_accepted_file_changed_again(self):
        second = two("src/second.py", 2, "return a / b", "return a * b")
        run, fid, bg = self.started(CAND, second)
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "fixed")
        kept = slurp(self.p("src/calc.py"))
        bg2 = self.begin(run)[2]
        self.assertEqual(bg2["path"], "src/second.py")
        self.edit("src/second.py", "return a / b", "return a * b")
        self.edit("src/calc.py", "return a + b", "return b + a")
        f = self.violation(run, bg2)
        self.assertIn("src/calc.py", f["detail"])
        self.assertEqual(slurp(self.p("src/calc.py")), kept)
        self.assertEqual(slurp(self.p("src/second.py")), SECOND.encode())
        self.assertEqual(slurp(self.quarantined(run, bg2["id"], "src/calc.py")), kept.replace(b"a + b", b"b + a"))

    def test_user_edit_survives(self):
        run, fid, bg = self.started()
        self.edit("src/calc.py", QUOTE, "return a + b")
        edited = slurp(self.p("src/calc.py"))
        user = b"def other():\n    return 'mine'\n"
        with open(self.p("src/other.py"), "wb") as fh:
            fh.write(user)
        self.violation(run, bg)
        self.assertEqual(slurp(self.quarantined(run, fid, "src/other.py")), user)
        self.assertEqual(slurp(self.quarantined(run, fid, "src/calc.py")), edited)
        self.assertEqual(slurp(self.p("src/other.py")), OTHER.encode())

    def test_second_copy_of_one_path_gets_a_suffix(self):
        run, fid, bg = self.started()
        with open(self.p("junk.txt"), "wb") as fh:
            fh.write(b"1")
        self.assertEqual(fix._quarantine(run, fid, self.repo, ["junk.txt"], False), ["junk.txt"])
        with open(self.p("junk.txt"), "wb") as fh:
            fh.write(b"2")
        fix._quarantine(run, fid, self.repo, ["junk.txt"], False)
        self.assertEqual(slurp(self.quarantined(run, fid, "junk.txt")), b"1")
        self.assertEqual(slurp(self.quarantined(run, fid, "junk.txt.1")), b"2")

    def test_declined_and_no_change(self):
        second = two("src/second.py", 2, "return a / b", "return a * b")
        run, fid, bg = self.started(CAND, second)
        rc, _, f = self.finish(run, bg["seal"], "declined")
        self.assertEqual((rc, f), (0, {"status": "ok", "outcome": "declined"}))
        bg2 = self.begin(run)[2]
        self.assertEqual(bg2["status"], "ok")
        rc, _, f = self.finish(run, bg2["seal"], "applied")
        self.assertEqual((rc, f), (0, {"status": "ok", "outcome": "no-change"}))
        self.assertEqual(self.begin(run)[2]["status"], "done")
        self.assertEqual({k: v["outcome"] for k, v in self.state(run)["done"].items()},
                         {fid: "declined", bg2["id"]: "no-change"})

    def test_decline_code_and_line_are_recorded_and_reported(self):
        run, fid, bg = self.started()
        rc, _, f = self.fixrun(["finish", "--run", run, "--seal", bg["seal"], "--status", "declined",
                                "--code", "quote-not-unique", "--line", "2718"])
        self.assertEqual((rc, f), (0, {"status": "ok", "outcome": "declined"}))
        self.assertEqual(self.state(run)["done"][fid],
                         {"outcome": "declined", "exit": None, "code": "quote-not-unique", "line": 2718})
        self.assertIn("declined (quote-not-unique, line 2718)", self.report(run)[1])

    def test_decline_without_code_still_works(self):
        run, fid, bg = self.started()
        self.assertEqual(self.finish(run, bg["seal"], "declined")[0], 0)
        self.assertEqual(self.state(run)["done"][fid], {"outcome": "declined", "exit": None})

    def test_decline_code_misuse_is_usage_and_changes_nothing(self):
        run, fid, bg = self.started()
        base = ["finish", "--run", run, "--seal", bg["seal"], "--status"]
        for args in (["applied", "--code", "needs-context"], ["applied", "--line", "3"]):
            rc, out, j = self.fixrun(base + args)
            self.assertEqual((rc, j["reason"]), (1, "usage"), out)
        self.assertIsNotNone(self.state(run)["in_flight"])

    def test_decline_flags_in_any_order_and_line_alone(self):
        run, fid, bg = self.started()
        rc, _, f = self.fixrun(["finish", "--run", run, "--seal", bg["seal"], "--status", "declined",
                                "--line", "12", "--code", "needs-context"])
        self.assertEqual((rc, f["outcome"]), (0, "declined"))
        self.assertEqual(self.state(run)["done"][fid],
                         {"outcome": "declined", "exit": None, "code": "needs-context", "line": 12})
        second = two("src/second.py", 2, "return a / b", "return a * b")
        run, fid, bg = self.started(CAND, second)
        self.assertEqual(self.fixrun(["finish", "--run", run, "--seal", bg["seal"], "--status", "declined",
                                      "--line", "7"])[0], 0)
        self.assertEqual(self.state(run)["done"][fid], {"outcome": "declined", "exit": None, "line": 7})

    def test_applied_with_any_decline_flag_is_usage(self):
        run, fid, bg = self.started()
        for bad in ("0", "abc"):
            rc, out, j = self.fixrun(["finish", "--run", run, "--seal", bg["seal"], "--status", "applied",
                                      "--line", bad])
            self.assertEqual((rc, j["reason"]), (1, "usage"), out)
        self.assertIsNotNone(self.state(run)["in_flight"])

    def test_off_list_code_and_bad_line_are_dropped_not_refused(self):
        run, fid, bg = self.started()
        rc, _, f = self.fixrun(["finish", "--run", run, "--seal", bg["seal"], "--status", "declined",
                                "--code", "stale", "--line", "0"])
        self.assertEqual((rc, f["outcome"]), (0, "declined"))
        self.assertEqual(self.state(run)["done"][fid], {"outcome": "declined", "exit": None})

    def test_declined_with_an_edit_is_a_violation(self):
        run, fid, bg = self.started()
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.violation(run, bg, "declined")
        self.assertEqual(slurp(self.p("src/calc.py")), FEAT.encode())

    def test_too_large(self):
        for n, outcome in ((58, "fixed"), (59, "reverted-too-large")):  # n comment lines, +1 line, -1 line
            with self.subTest(added=n):
                run, fid, bg = self.started()
                self.edit("src/calc.py", QUOTE, "\n".join("# c%d" % i for i in range(n)) + "\n    return a + b")
                rc, _, f = self.finish(run, bg["seal"])
                self.assertEqual(f["outcome"], outcome, f)
                if outcome != "fixed":
                    self.assertEqual((rc, f["status"]), (0, "ok"))
                    self.assertEqual(slurp(self.p("src/calc.py")), FEAT.encode())
                    self.assertTrue(os.path.exists(self.quarantined(run, fid, "src/calc.py")))
                else:
                    self.git("checkout", "--", "src/calc.py")
                    self.git("clean", "-fdq")

    def test_head_or_refs_moved(self):
        for reason, mutate in (("head-moved", lambda: self.git("commit", "-q", "--allow-empty", "-m", "later")),
                               ("refs-moved", lambda: self.git("branch", "other"))):
            with self.subTest(reason=reason):
                run, fid, bg = self.started()
                self.edit("src/calc.py", QUOTE, "return a + b")
                mutate()
                f = self.violation(run, bg)
                self.assertIn(reason, f["detail"])
                self.assertEqual(slurp(self.p("src/calc.py")), FEAT.encode())
                if reason == "head-moved":
                    self.git("reset", "-q", "--hard", "HEAD~1")
                else:
                    self.git("branch", "-D", "other")

    def test_repeated_finish_is_not_tampering(self):
        run, fid, bg = self.started()
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "fixed")
        before = self.state(run)
        rc, _, j = self.finish(run, bg["seal"])
        self.assertEqual((rc, j), (1, {"status": "refuse", "reason": "not-in-flight"}))
        self.assertEqual(self.state(run), before)
        self.git("checkout", "--", "src/calc.py")
        run, fid, bg = self.started()  # a halt keeps its own reason
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.git("branch", "other")
        self.violation(run, bg)
        before = self.state(run)
        rc, _, j = self.finish(run, bg["seal"])
        self.assertEqual((rc, j), (1, {"status": "refuse", "reason": "not-in-flight"}))
        self.assertEqual(self.state(run), before)

    def test_save_is_atomic(self):
        run, fid, bg = self.started()
        path = os.path.join(run, "fix", "state.json")
        before = slurp(path)
        st = self.state(run)
        st["halted"] = "x"
        with mock.patch.object(fix.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                fix._save(run, st)
        self.assertEqual(slurp(path), before)  # an interrupted write leaves the old file whole
        self.assertEqual([n for n in os.listdir(os.path.join(run, "fix")) if n.startswith(".tmp-")], [])
        fix._save(run, st)
        self.assertEqual(self.state(run)["halted"], "x")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_not_in_flight_and_usage(self):
        run, _ = self.ready()
        self.go(run)
        rc, _, j = self.finish(run, "0" * 64)
        self.assertEqual((rc, j), (1, {"status": "refuse", "reason": "not-in-flight"}))
        bg = self.begin(run)[2]
        for args in (["--seal", "xyz", "--status", "applied"], ["--seal", bg["seal"], "--status", "maybe"]):
            rc, out, j = self.fixrun(["finish", "--run", run] + args)
            self.assertEqual((rc, j["reason"]), (1, "usage"), out)
        self.assertIsNotNone(self.state(run)["in_flight"])


class Tamper(Scope):
    def tampered(self, run, bg, fid):
        calls = []
        orig = target._run
        with mock.patch.object(target, "_run", side_effect=lambda *a, **k: calls.append(a) or orig(*a, **k)), \
                gitfix.isolated(self.tmp):
            f = fix.finish(run, bg["seal"], "applied")
        self.assertEqual(calls, [])
        self.assertEqual((f["status"], f["outcome"]), ("halted", "tampered"), f)
        st = self.state(run)
        self.assertEqual((st["done"][fid]["outcome"], st["halted"], st["in_flight"]), ("tampered", "tampered", None))
        self.assertEqual(self.begin(run)[2], {"status": "refuse", "reason": "halted"})

    def test_each_sealed_file(self):
        targets = {"state.json": lambda run, fid: os.path.join(run, "fix", "state.json"),
                   "pre": lambda run, fid: os.path.join(run, "fix", fid + ".pre"),
                   "plan.json": lambda run, fid: os.path.join(run, "fix", "plan.json"),
                   "target.json": lambda run, fid: os.path.join(run, "target.json"),
                   "ledger.json": lambda run, fid: os.path.join(run, "ledger.json"),
                   "git config": lambda run, fid: self.state(run)["git_files"][0],
                   "git exclude": lambda run, fid: os.path.join(self.repo, ".git", "info", "exclude")}
        for name, where in targets.items():
            with self.subTest(file=name):
                run, fid, bg = self.started()
                self.edit("src/calc.py", QUOTE, "return a + b")
                edited = slurp(self.p("src/calc.py"))
                path = where(run, fid)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "ab") as fh:
                    fh.write(b" ")
                self.tampered(run, bg, fid)
                self.assertEqual(slurp(self.p("src/calc.py")), edited)
                self.git("checkout", "--", "src/calc.py")

    def test_target_repointed_to_a_copy(self):
        run, fid, bg = self.started()
        copy = os.path.join(self.tmp, "copy")
        shutil.copytree(self.repo, copy, symlinks=True)
        with open(os.path.join(run, "target.json"), encoding="utf-8") as fh:
            tj = json.load(fh)
        tj["repo"] = copy
        with open(os.path.join(run, "target.json"), "w", encoding="utf-8") as fh:
            json.dump(tj, fh)
        self.edit("src/calc.py", QUOTE, "return a + b")
        with open(self.p("poetry.lock"), "w", encoding="utf-8") as fh:
            fh.write("pinned\n")
        edited = slurp(self.p("src/calc.py"))
        rc, _, f = self.finish(run, bg["seal"])
        self.assertEqual((rc, f["status"], f["outcome"]), (1, "halted", "tampered"), f)
        st = self.state(run)
        self.assertEqual((st["halted"], st["in_flight"]), ("tampered", None))
        self.assertEqual(self.begin(run)[2], {"status": "refuse", "reason": "halted"})
        self.assertEqual(slurp(self.p("src/calc.py")), edited)
        self.assertEqual(slurp(self.p("poetry.lock")), b"pinned\n")
        rc, text, _ = self.report(run)
        self.assertEqual(rc, 1, text)
        self.assertIn("TAMPERED", text)

    def test_unreadable_target_is_tampered(self):
        run, fid, bg = self.started()
        self.edit("src/calc.py", QUOTE, "return a + b")
        with open(os.path.join(run, "target.json"), "w", encoding="utf-8") as fh:
            fh.write("{")
        calls = []
        orig = target._run
        with mock.patch.object(target, "_run", side_effect=lambda *a, **k: calls.append(a) or orig(*a, **k)), \
                gitfix.isolated(self.tmp):
            f = fix.finish(run, bg["seal"], "applied")
            self.assertEqual((f["status"], f["outcome"]), ("halted", "tampered"), f)
            r = fix.report_(run)
        self.assertEqual(calls, [])
        self.assertEqual(self.state(run)["halted"], "tampered")
        self.assertEqual(r["status"], "ok")
        self.assertIn("TAMPERED", r["text"])
        self.assertEqual(self.begin(run)[2], {"status": "refuse", "reason": "halted"})

    def test_unparseable_state_is_tampered(self):
        for name, content in (("not json", "{"), ("not an object", "[]"), ("missing keys", "{}")):
            with self.subTest(state=name):
                run, fid, bg = self.started()
                self.edit("src/calc.py", QUOTE, "return a + b")
                edited = slurp(self.p("src/calc.py"))
                with open(os.path.join(run, "fix", "state.json"), "w", encoding="utf-8") as fh:
                    fh.write(content)
                for args in (["finish", "--run", run, "--seal", bg["seal"], "--status", "applied"],
                             ["begin", "--run", run], ["report", "--run", run]):
                    rc, _, j = self.fixrun(args)
                    self.assertEqual((rc, j["status"], j["reason"]), (1, "refuse", "tampered"), (args[0], j))
                    self.assertIn(".git/config", j["detail"])
                calls = []
                orig = target._run
                with mock.patch.object(target, "_run", side_effect=lambda *a, **k: calls.append(a) or orig(*a, **k)), \
                        gitfix.isolated(self.tmp):
                    for call in (lambda: fix.finish(run, bg["seal"], "applied"), lambda: fix.report_(run)):
                        with self.assertRaises(target.Refuse) as cm:
                            call()
                        self.assertEqual(cm.exception.reason, "tampered")
                self.assertEqual(calls, [])
                self.assertEqual(slurp(self.p("src/calc.py")), edited)
                self.git("checkout", "--", "src/calc.py")

    def test_wrong_typed_state_is_tampered(self):
        def with_(**kw):
            return lambda st: json.dumps(dict(st, **kw))
        cases = {"done entry not an object": with_(done={"F-1": "x"}),
                 "in_flight without id": with_(in_flight={"x": 1}),
                 "in_flight path not a string": with_(in_flight={"id": "F-1", "path": 1, "mode": 420}),
                 "in_flight mode missing": with_(in_flight={"id": "F-1", "path": "a.py"}),
                 "accepted not an object": with_(accepted=[]),
                 "untracked not a list": with_(untracked="x"),
                 "deeply nested": lambda st: "[" * 200000}
        for name, make in cases.items():
            with self.subTest(state=name):
                run, fid, bg = self.started()
                self.edit("src/calc.py", QUOTE, "return a + b")
                edited = slurp(self.p("src/calc.py"))
                path = os.path.join(run, "fix", "state.json")
                with open(path, encoding="utf-8") as fh:
                    good = json.load(fh)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(make(good))
                try:
                    for args in (["finish", "--run", run, "--seal", bg["seal"], "--status", "applied"],
                                 ["begin", "--run", run], ["report", "--run", run]):
                        rc, _, j = self.fixrun(args)
                        self.assertEqual((rc, j["status"], j.get("reason")), (1, "refuse", "tampered"), (args[0], j))
                    self.assertEqual(slurp(self.p("src/calc.py")), edited)
                finally:
                    self.git("checkout", "--", "src/calc.py")

    def test_in_flight_cleared_is_tampered(self):
        run, fid, bg = self.started()
        self.edit("src/calc.py", QUOTE, "return a + b")
        edited = slurp(self.p("src/calc.py"))
        st = self.state(run)
        st["in_flight"] = None
        with open(os.path.join(run, "fix", "state.json"), "w", encoding="utf-8") as fh:
            json.dump(st, fh)

        def recorded():
            calls = []
            orig = target._run
            with mock.patch.object(target, "_run", side_effect=lambda *a, **k: calls.append(a) or orig(*a, **k)), \
                    gitfix.isolated(self.tmp):
                r = fix.report_(run)
            self.assertEqual((r["status"], calls), ("ok", []))
            return r["text"]

        self.assertIn("no git command ran", recorded())
        rc, _, f = self.finish(run, bg["seal"])
        self.assertEqual((rc, f["status"], f["outcome"]), (1, "halted", "tampered"), f)
        self.assertEqual(self.state(run)["halted"], "tampered")
        self.assertEqual(self.begin(run)[2], {"status": "refuse", "reason": "halted"})
        self.assertEqual(slurp(self.p("src/calc.py")), edited)
        self.assertIn("TAMPERED", recorded())

    def test_accepted_snapshot_rewritten(self):
        second = two("src/second.py", 2, "return a / b", "return a * b")
        run, fid, bg = self.started(CAND, second)
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "fixed")
        bg2 = self.begin(run)[2]
        with open(os.path.join(run, "fix", fid + ".post"), "ab") as fh:
            fh.write(b"#")
        self.tampered(run, bg2, bg2["id"])


SECOND_CAND = two("src/second.py", 2, "return a / b", "return a * b")


class TestRuns(Scope):
    def test_timeout(self):
        run, fid, bg = self.started(words=self.probe("time.sleep(30)"))
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.arm()
        t0 = time.monotonic()
        with mock.patch.object(fix, "TIMEOUT", 1), gitfix.isolated(self.tmp):
            f = fix.finish(run, bg["seal"], "applied")
        self.assertLess(time.monotonic() - t0, 15)
        self.assertEqual((f["status"], f["outcome"]), ("ok", "reverted-timeout"), f)
        self.assertEqual(slurp(self.p("src/calc.py")), FEAT.encode())
        self.assertIsNone(self.state(run)["halted"])

    def test_failing_tail(self):
        body = 'sys.stdout.write("MARK-" + chr(27) + "[31m\\n"); sys.exit(1)'
        run, fid, bg = self.started(words=self.probe(body))
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.arm()
        rc, _, f = self.finish(run, bg["seal"])
        self.assertEqual((rc, f["outcome"], f["exit"]), (0, "reverted-tests", 1), f)
        self.assertIn("MARK-", f["tail"])
        self.assertNotIn("\x1b", f["tail"])
        self.assertLessEqual(len(f["tail"]), 2000 * 8)

    def test_interrupt_restores(self):
        run, fid, bg = self.started()
        self.edit("src/calc.py", QUOTE, "return a + b")
        with mock.patch.object(fix.repro, "_execute", side_effect=KeyboardInterrupt), gitfix.isolated(self.tmp):
            with self.assertRaises(KeyboardInterrupt):
                fix.finish(run, bg["seal"], "applied")
        self.assertEqual(slurp(self.p("src/calc.py")), FEAT.encode())
        st = self.state(run)
        self.assertEqual((st["done"][fid]["outcome"], st["halted"], st["in_flight"]),
                         ("reverted-interrupt", "reverted-interrupt", None))
        self.assertEqual(self.begin(run)[2], {"status": "refuse", "reason": "halted"})

    def signalled(self, sig):
        started = os.path.join(self.tmp, "started")
        run, fid, bg = self.started(words=self.probe("open(%r, 'w').write(str(os.getpid())); os.rename(%r, %r); "
                                                     "time.sleep(60)" % (started + ".tmp", started + ".tmp", started)))
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.arm()
        e = gitfix.env()
        e["TMPDIR"] = self.tmp
        proc = subprocess.Popen([sys.executable, os.path.join(SCRIPTS, "fix.py"), "finish", "--run", run, "--seal",
                                 bg["seal"], "--status", "applied"], cwd=self.repo, env=e, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
        self.addCleanup(proc.stderr.close)  # cleanups run last-in first-out: kill, then close the pipes
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.kill)
        deadline = time.monotonic() + 20
        while not os.path.exists(started):
            self.assertLess(time.monotonic(), deadline, "the test command never started")
            time.sleep(0.05)
        with open(started, encoding="utf-8") as fh:
            pgid = int(fh.read())

        def reap():  # the probe leads its own session, so its pid is its group id
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.addCleanup(reap)
        proc.send_signal(sig)
        out, err = proc.communicate(timeout=20)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn(b"Traceback", err)
        self.assertEqual(slurp(self.p("src/calc.py")), FEAT.encode())
        self.assertEqual(self.state(run)["done"][fid]["outcome"], "reverted-interrupt")
        return out.decode("utf-8")

    def test_sigterm_restores(self):
        self.signalled(signal.SIGTERM)

    def test_sigint_restores_and_prints_one_json_line(self):
        out = self.signalled(signal.SIGINT)
        last = [ln for ln in out.splitlines() if ln.strip()][-1]
        self.assertEqual(json.loads(last), {"reason": "interrupted", "status": "halted"})
        with open(os.path.join(os.path.dirname(SCRIPTS), "references", "fix.md"), encoding="utf-8") as fh:
            self.assertIn("`interrupted`", fh.read())

    def test_interrupted_row_says_it_prints_status_halted(self):  # 02a.19: the table above it is the refusals
        with open(os.path.join(os.path.dirname(SCRIPTS), "references", "fix.md"), encoding="utf-8") as fh:
            row = [ln for ln in fh if ln.startswith("| `interrupted` |")][0]
        self.assertIn("status `halted`", row)

    def test_sigint_handler_is_one_shot(self):  # 02a.20: a second Ctrl-C must not cut the restore short
        before = signal.getsignal(signal.SIGINT)
        try:
            with self.assertRaises(KeyboardInterrupt):
                fix._term(signal.SIGINT, None)
            self.assertEqual(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
        finally:
            signal.signal(signal.SIGINT, before)
            for name in ("SIGTERM", "SIGHUP"):
                signal.signal(getattr(signal, name), signal.SIG_DFL)

    def test_sighup_restores(self):
        self.signalled(signal.SIGHUP)

    def test_side_effect_restored(self):
        body = 'open("src/other.py", "w").write("x")'
        run, fid, bg = self.started(words=self.probe(body))
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.arm()
        rc, _, f = self.finish(run, bg["seal"])
        self.assertEqual((rc, f["outcome"]), (0, "fixed"), f)
        self.assertEqual(slurp(self.p("src/other.py")), OTHER.encode())
        self.assertEqual(self.state(run)["side_effects"], ["src/other.py"])
        self.assertEqual(slurp(self.quarantined(run, fid, "src/other.py")), b"x")

    def test_side_effect_restored_at_baseline(self):
        run, fid, bg = self.started(words=self.probe('open("src/other.py", "w").write("x")'), armed=True)
        self.assertEqual(slurp(self.p("src/other.py")), OTHER.encode())
        self.assertEqual(self.state(run)["side_effects"], ["src/other.py"])
        self.assertEqual(slurp(self.quarantined(run, "baseline", "src/other.py")), b"x")

    def test_tests_rewrite_fixed_file(self):
        run, fid, bg = self.started(words=self.probe('open("src/calc.py", "a").write("# t\\n")'))
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.arm()
        f = self.violation(run, bg)
        self.assertIn("src/calc.py", f["detail"])
        self.assertEqual(slurp(self.p("src/calc.py")), FEAT.encode())

    def test_tests_move_refs(self):
        run, fid, bg = self.started(words=self.probe('os.system("git branch zzz")'))
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.arm()
        f = self.violation(run, bg)
        self.assertIn("refs-moved", f["detail"])
        self.assertEqual(slurp(self.p("src/calc.py")), FEAT.encode())

    def test_baseline_refs_moved_halts(self):
        run = self.pin()
        (fid,) = self.ingest(run, CAND)
        self.confirm(run, (fid, CITE))
        self.arm()
        j = self.plan(run, self.probe('os.system("git branch zzz")'))[2]
        rc, _, b = self.baseline(run, j["digest"])
        self.assertEqual((rc, b["status"], b["reason"]), (1, "halted", "refs-moved"), b)
        self.assertEqual(self.begin(run)[2]["reason"], "halted")

    def test_killed_baseline_stays_halted(self):
        run = self.pin()
        (fid,) = self.ingest(run, CAND)
        self.confirm(run, (fid, CITE))
        self.arm()
        started = os.path.join(self.tmp, "started")
        j = self.plan(run, self.probe("open(%r, 'w').write(str(os.getpid())); os.rename(%r, %r); time.sleep(60)" %
                                      (started + ".tmp", started + ".tmp", started)))[2]
        e = gitfix.env()
        e["TMPDIR"] = self.tmp
        proc = subprocess.Popen([sys.executable, os.path.join(SCRIPTS, "fix.py"), "baseline", "--run", run,
                                 "--digest", j["digest"]], cwd=self.repo, env=e, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self.addCleanup(proc.stderr.close)  # cleanups run last-in first-out: kill, then close the pipes
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.kill)
        deadline = time.monotonic() + 20
        while not os.path.exists(started):
            self.assertLess(time.monotonic(), deadline, "the test command never started")
            time.sleep(0.05)
        with open(started, encoding="utf-8") as fh:
            pgid = int(fh.read())

        def reap():  # the probe leads its own session, so its pid is its group id
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.addCleanup(reap)
        proc.kill()
        proc.wait(timeout=20)
        self.assertEqual(self.state(run)["halted"], "baseline-interrupted")
        self.assertEqual(self.begin(run)[2], {"status": "refuse", "reason": "halted"})

    def test_baseline_head_moved_halts(self):
        run = self.pin()
        (fid,) = self.ingest(run, CAND)
        self.confirm(run, (fid, CITE))
        self.arm()
        j = self.plan(run, self.probe('os.system("git reset -q --soft HEAD~1")'))[2]
        rc, _, b = self.baseline(run, j["digest"])
        self.assertEqual((rc, b["status"], b["reason"]), (1, "halted", "head-moved"), b)
        self.assertEqual(self.begin(run)[2]["reason"], "halted")

    def test_test_cache_left(self):
        run, fid, bg = self.started(CAND, SECOND_CAND, words=self.probe('open("out.log", "w").write("x")'))
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.arm()
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "fixed")
        self.assertTrue(os.path.exists(self.p("out.log")))
        self.assertIn("out.log", self.state(run)["untracked"])
        self.assertEqual(self.begin(run)[2]["status"], "ok")

    def test_env_secret(self):
        run, _ = self.ready()
        code = "import os, sys; sys.exit(1 if 'REVIEW_CODE_SECRET' in os.environ else 0)"
        j = self.plan(run, [sys.executable, "-B", "-c", code])[2]
        rc, out, _ = cli("fix.py", ["baseline", "--run", run, "--digest", j["digest"]], self.repo, self.tmp,
                         extra={"REVIEW_CODE_SECRET": "x"})
        self.assertEqual(rc, 0, out)


class ProtectedPaths(Scope):
    """review-code#34: protected paths join the plan flagged; untracked, symlinked and .git paths stay refused."""
    feat_files = dict(Scope.feat_files, **{"requirements.txt": "pkg==1\n"})  # lockfiles are excluded from a review's diff
    LOCK_CAND = two("requirements.txt", 1, "pkg==1", "pkg==2")
    WORDS = [sys.executable, "-B", "-c", "assert '==' in open('requirements.txt').read()"]

    def test_lockfile_finding_is_planned_flagged_confirmed_and_tests_gated(self):
        run, fid, bg = self.started(self.LOCK_CAND, words=self.WORDS)
        with open(os.path.join(run, "fix", "plan.json"), encoding="utf-8") as fh:
            (entry,) = json.load(fh)["entries"]
        self.assertEqual((entry["id"], entry["path"], entry["protected"]), (fid, "requirements.txt", True))
        self.edit("requirements.txt", "pkg==1", "pkg")  # the tests gate reverts it
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "reverted-tests")
        self.assertEqual(slurp(self.p("requirements.txt")), b"pkg==1\n")

    def test_preview_marks_each_protected_row_and_counts_them_for_the_question(self):
        run = self.pin()
        cands = [CAND, self.LOCK_CAND]
        self.ingest(run, *cands)
        self.confirm(run, *[(report_id(c), dict(path=c["path"], line=c["line"], quote=c["quote"])) for c in cands])
        rc, out, j = self.plan(run, self.WORDS)
        self.assertEqual((rc, j["apply"], j["protected"], j["skipped"]), (0, 2, 1, 0), out)
        self.assertIn("; 1 protected path;", out.split("\n")[0])
        rows = [ln for ln in out.split("\n") if ln[:1].isdigit()]
        self.assertEqual({ln.split()[2].split(":")[0]: ln.endswith("protected path") for ln in rows},
                         {"src/calc.py": False, "requirements.txt": True})
        self.assertNotIn("protected-path", out)
        rc, _, b = self.baseline(run, j["digest"])
        self.assertEqual((rc, b["status"]), (0, "ok"), b)
        edits = {"src/calc.py": (QUOTE, "return a + b"), "requirements.txt": ("pkg==1", "pkg==2")}
        for _ in range(2):
            bg = self.begin(run)[2]
            with open(bg["brief"], encoding="utf-8") as fh:
                self.assertEqual("protected path:" in fh.read(), bg["path"] == "requirements.txt")
            self.edit(bg["path"], *edits[bg["path"]])
            self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "fixed")
        self.assertEqual(self.begin(run)[2], {"status": "done"})

    def test_untracked_symlink_and_git_internal_paths_are_still_refused(self):
        found = [dict(id="F-000000000%d" % i, path=p, line=1, end_line=1, severity="required", category="bug",
                      origin="defects", title="t", trigger="g", quote="x", fix="y")
                 for i, p in enumerate((".git/config", "src/untracked.py", "link.py"))]
        os.symlink("src/calc.py", self.p("link.py"))
        self.git("add", "link.py")
        self.git("commit", "-q", "-m", "link")
        run = self.pin()
        with gitfix.isolated(self.tmp), mock.patch.object(fix.report, "build", return_value={"findings": found}):
            with self.assertRaises(target.Refuse) as cm:
                fix.plan(run, self.cmd())
        self.assertEqual((cm.exception.reason, cm.exception.detail), ("nothing-to-fix", "skipped: bad-path 3"))


THIRD = "def div(a, b):\n    return a // b\n"
THIRD_CAND = two("src/third.py", 2, "return a // b", "return a / b")


class ContinueAfterRevert(Scope):
    """review-code#32: a restored tests/size/timeout failure does not strand the entries after it."""
    feat_files = dict(Scope.feat_files, **{"src/third.py": THIRD})
    WORDS = [sys.executable, "-B", "-c", "import sys; sys.path.insert(0, 'src'); import calc, second, third"]

    def test_failed_middle_entry_is_reported_and_the_rest_are_fixed(self):
        run, fid, bg = self.started(CAND, SECOND_CAND, THIRD_CAND, words=self.WORDS)
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "fixed")
        bg2 = self.begin(run)[2]
        self.edit("src/second.py", "return a / b", "return a *")
        rc, _, f = self.finish(run, bg2["seal"])
        self.assertEqual((rc, f["status"], f["outcome"]), (0, "ok", "reverted-tests"), f)
        self.assertEqual(slurp(self.p("src/second.py")), SECOND.encode())  # restored: nothing stacks on the failed edit
        self.assertIsNone(self.state(run)["halted"])
        bg3 = self.begin(run)[2]
        self.assertEqual((bg3["status"], bg3["id"]), ("ok", report_id(THIRD_CAND)), bg3)
        self.edit("src/third.py", "return a // b", "return a / b")
        self.assertEqual(self.finish(run, bg3["seal"])[2]["outcome"], "fixed")
        self.assertEqual(self.begin(run)[2], {"status": "done"})
        rc, text, r = self.report(run)
        self.assertEqual((rc, r["fixed"], r["reverted"], r["halted"]), (1, 2, 1, 0), text)
        for fid_, rel, outcome in ((fid, "src/calc.py", "fixed"), (bg2["id"], "src/second.py", "reverted-tests"),
                                   (bg3["id"], "src/third.py", "fixed")):
            self.assertIn("%s %s:2" % (fid_, rel) if rel != "src/calc.py" else "%s %s:5" % (fid_, rel), text)
            self.assertRegex(text, r"%s \S+ %s" % (fid_, outcome))

    def test_next_entry_is_gated_on_the_restored_tree_matching_the_last_green_one(self):
        run, fid, bg = self.started(CAND, SECOND_CAND, words=self.WORDS)
        self.edit("src/calc.py", QUOTE, "return a +")
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "reverted-tests")
        self.assertEqual(slurp(self.p("src/calc.py")), FEAT.encode())  # byte-exact: the tree is the one the baseline ran green on
        self.edit("src/calc.py", QUOTE, "return a +")  # a red edit left in place after the restore
        self.assertEqual(self.begin(run)[2], {"status": "refuse", "reason": "tree-changed"})  # no brief on a tree that differs

    def test_size_failure_also_continues(self):
        run, fid, bg = self.started(CAND, SECOND_CAND, words=self.WORDS)
        self.edit("src/calc.py", QUOTE, "\n".join("# c%d" % i for i in range(70)) + "\n    return a + b")
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "reverted-too-large")
        self.assertEqual(self.begin(run)[2]["status"], "ok")

    def test_violation_interrupt_and_tamper_still_halt(self):
        run, fid, bg = self.started(CAND, SECOND_CAND, words=self.WORDS)
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.edit("src/other.py", "return 'o'", "return 'p'")
        rc, _, f = self.finish(run, bg["seal"])
        self.assertEqual((rc, f["status"], f["outcome"]), (1, "halted", "reverted-violation"), f)
        self.assertEqual(self.begin(run)[2], {"status": "refuse", "reason": "halted"})
        self.assertEqual(slurp(self.p("src/calc.py")), FEAT.encode())


class ReportCoverage(Scope):
    """review-code#35: every verified finding appears once in the report, fixed or with a reason; the counts sum to it."""
    feat_files = dict(Scope.feat_files, **{"src/third.py": THIRD, "src/dup.py": "def f(x):\n    return x * 2\n\ndef g(x):\n    return x * 2\n"})
    DUP = two("src/dup.py", 2, "return x * 2", "return x * 3")
    CANDS = [CAND, SECOND_CAND, THIRD_CAND, DUP]
    EDITS = {"src/calc.py": (QUOTE, "return a + b"), "src/second.py": ("return a / b", "return a * b"),
             "src/third.py": ("return a // b", "return a / b")}
    WORDS = [sys.executable, "-B", "-c", "import sys; sys.path.insert(0, 'src'); import calc, second, third"]

    def check(self, run, want):
        rc, text, r = self.report(run)
        ids = [report_id(c) for c in self.CANDS]
        for i in ids:
            self.assertEqual(len(re.findall(r"(?m)^%s " % i, text.split("\n== ")[0])), 1, (i, text))
        self.assertEqual(r["verified"], 4)
        line = re.search(r"(?m)^verified findings: (\d+) = (.*)$", text)
        self.assertEqual(sum(int(x) for x in re.findall(r"(\d+) [a-z-]+", line.group(2))), int(line.group(1)), line.group(0))
        self.assertEqual(dict((k, int(n)) for n, k in re.findall(r"(\d+) ([a-z-]+)", line.group(2))), want, line.group(0))
        return rc, text, r

    def test_halted_run_names_the_entries_it_never_reached(self):
        run, fid, bg = self.started(*self.CANDS, words=self.WORDS)
        self.edit(bg["path"], *self.EDITS[bg["path"]])
        self.edit("src/other.py", "return 'o'", "return 'p'")
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "reverted-violation")
        rc, text, r = self.check(run, {"reverted-violation": 1, "not-reached": 2, "stale": 1})
        self.assertEqual((rc, r["not_reached"]), (1, 2))
        self.assertRegex(text, r"(?m)^%s src/dup.py stale$" % report_id(self.DUP))

    def test_failed_declined_and_fixed_entries_each_show_once(self):
        run, fid, bg = self.started(*self.CANDS, words=self.WORDS)
        outcomes = []
        for status in ("applied", "applied", "declined"):
            if outcomes:
                bg = self.begin(run)[2]
            if status == "applied" and len(outcomes) == 1:
                self.edit(bg["path"], self.EDITS[bg["path"]][0], "return a +")  # a failing edit
            elif status == "applied":
                self.edit(bg["path"], *self.EDITS[bg["path"]])
            outcomes.append(self.finish(run, bg["seal"], status)[2]["outcome"])
        self.assertEqual(outcomes, ["fixed", "reverted-tests", "declined"])
        self.assertEqual(self.begin(run)[2], {"status": "done"})
        self.check(run, {"fixed": 1, "reverted-tests": 1, "declined": 1, "stale": 1})


class Report(Scope):
    def test_two_entries_and_the_untrusted_tail(self):
        words = [sys.executable, "-B", "-c", "import sys; sys.path.insert(0, 'src'); import calc, second"]
        run, fid, bg = self.started(CAND, SECOND_CAND, words=words)
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "fixed")
        bg2 = self.begin(run)[2]
        self.edit("src/second.py", "return a / b", "return a *")
        self.assertEqual(self.finish(run, bg2["seal"])[2]["outcome"], "reverted-tests")
        rc, text, r = self.report(run)
        self.assertEqual((rc, r["fixed"], r["reverted"], r["halted"]), (1, 1, 1, 0), text)
        self.assertIn("%s src/calc.py:5 fixed" % fid, text)
        self.assertIn("%s src/second.py:2 reverted-tests" % bg2["id"], text)
        self.assertNotIn("halted:", text)
        self.assertRegex(text, r"== changed files ==\n[^\n]*src/calc\.py")
        self.assertRegex(text, r"== test output \(untrusted\) ==\n[^\n]*\n<<<UNTRUSTED-([0-9a-f]+)\n(?s:.*SyntaxError.*)\1>>>")
        self.assertIn("HEAD and refs: unchanged", text)
        self.assertIn("nothing was committed, staged or pushed", text)
        self.assertIn("not checked", text)  # the new-import limit
        self.assertIn("== quarantined ==", text)
        self.assertIn("src/second.py", text.split("== quarantined ==")[1])

    def test_halt_reason_is_named(self):
        run, _ = self.ready()
        j = self.plan(run, [sys.executable, "-B", "-c", "import sys; sys.exit(1)"])[2]
        self.assertEqual(self.baseline(run, j["digest"])[2]["reason"], "baseline-red")
        rc, text, r = self.report(run)
        self.assertEqual((rc, r["halted"]), (0, 1), text)
        self.assertIn("halted: baseline-red", text)

    def test_tampered_runs_no_git(self):
        run, fid, bg = self.started()
        self.edit("src/calc.py", QUOTE, "return a + b")
        with open(os.path.join(run, "fix", "plan.json"), "ab") as fh:
            fh.write(b" ")
        with gitfix.isolated(self.tmp):
            self.assertEqual(fix.finish(run, bg["seal"], "applied")["outcome"], "tampered")
            calls = []
            orig = target._run
            with mock.patch.object(target, "_run", side_effect=lambda *a, **k: calls.append(a) or orig(*a, **k)):
                r = fix.report_(run)
        self.assertEqual(calls, [])
        self.assertIn("tampered", r["text"])
        self.assertIn(".git/config", r["text"])

    def test_in_flight_names_the_snapshot(self):
        run, fid, bg = self.started()
        self.edit("src/calc.py", QUOTE, "return a + b")
        rc, text, r = self.report(run)
        sec = text.split("== in flight ==")[1]
        self.assertIn("src/calc.py", sec)
        self.assertIn(os.path.join(run, "fix", fid + ".pre"), sec)

    def test_in_flight_report_runs_no_git(self):
        run, fid, bg = self.started()
        self.edit("src/calc.py", QUOTE, "return a + b")
        calls = []
        orig = target._run
        with mock.patch.object(target, "_run", side_effect=lambda *a, **k: calls.append(a) or orig(*a, **k)), \
                gitfix.isolated(self.tmp):
            r = fix.report_(run)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(calls, [])
        sec = r["text"].split("== in flight ==")[1]
        self.assertIn("src/calc.py", sec)
        self.assertIn(os.path.join(run, "fix", fid + ".pre"), sec)
        self.assertIn("no git command ran", sec)
        self.assertNotIn("HEAD and refs:", r["text"])

    def test_unexpected_changes_and_leftovers(self):
        run, fid, bg = self.started(CAND, SECOND_CAND, words=self.probe(
            'open("out.log", "w").write("x"); open("src/other.py", "w").write("x")'))
        self.edit("src/calc.py", QUOTE, "return a + b")
        self.arm()
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "fixed")
        with open(self.p("stray.txt"), "w", encoding="utf-8") as fh:
            fh.write("s")
        text = self.report(run)[1]
        self.assertIn("src/other.py", text.split("== restored after tests ==")[1].split("==")[0])
        self.assertIn("out.log", text.split("== untracked files left by tests ==")[1].split("==")[0])
        self.assertIn("stray.txt", text.split("== unexpected changes ==")[1].split("==")[0])
        self.assertNotIn("out.log", text.split("== unexpected changes ==")[1].split("==")[0])


class WeakenGate(Base):
    """review-code#30: a --fix-tests edit that drops an assertion or adds a skip marker is restored and the run goes on;
    only covered test files are plannable; fixtures, runner configs and the command's script stay protected."""
    TEST = "def test_x():\n    assert 1 - 1\n    assert 2 - 1\n"
    base_files = dict(BASE, **{"tests/test_old.py": "def test_o():\n    assert 1 - 1\n"})
    feat_files = {"src/calc.py": FEAT, "tests/test_calc.py": TEST, "conftest.py": "def f():\n    return 2 - 1\n",
                  "jest.config.js": "module.exports = 3 - 1;\n", "ci/run-tests.sh": RUN, "tests/run.sh": RUN, "src/second.py": "def g(a, b):\n    return a / b\n"}
    WORDS = [sys.executable, "-B", "-c", "assert '-' in open('tests/test_calc.py').read()"]
    TC = two("tests/test_calc.py", 2, "assert 1 - 1", "assert 1 + 1", severity="blocking")

    def planned(self, cands, words=None, flag=True):
        run = self.pin()
        self.ingest(run, *cands)
        self.confirm(run, *[(report_id(c), dict(path=c["path"], line=c["line"], quote=c["quote"])) for c in cands])
        rc, out, j = self.fixrun(["plan", "--run", run] + ["--fix-tests"] * flag, "".join(w + "\n" for w in (words or self.WORDS)))
        self.assertEqual(rc, 0, out)
        with open(os.path.join(run, "fix", "plan.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        return run, j, {s["path"]: s["why"] for s in doc["skipped"]}, [e["path"] for e in doc["entries"]]

    def started(self, *cands):
        run, j, _, _ = self.planned(list(cands))
        self.assertEqual(self.baseline(run, j["digest"])[2]["status"], "ok")
        bg = self.begin(run)[2]
        self.assertEqual(bg["status"], "ok", bg)
        return run, bg

    def test_harness_files_stay_protected_under_the_flag(self):
        cands = [two("conftest.py", 2, "return 2 - 1", "return 2 + 1"),
                 two("jest.config.js", 1, "module.exports = 3 - 1;", "module.exports = 3 + 1;"),
                 two("ci/run-tests.sh", 2, "run 3 - 1", "run 3 + 1")]
        _, _, skipped, applied = self.planned(cands + [self.TC], ["./ci/run-tests.sh"])
        self.assertEqual(skipped, {c["path"]: "test-path" for c in cands})
        self.assertEqual(applied, ["tests/test_calc.py"])  # in the target: covered without being named

    def test_a_command_script_inside_a_test_dir_stays_protected(self):
        run_sh = two("tests/run.sh", 2, "run 3 - 1", "run 3 + 1")
        self.assertEqual(self.planned([run_sh, self.TC], ["./tests/run.sh"])[2], {"tests/run.sh": "test-path"})
        self.assertEqual(self.planned([self.TC])[3], ["tests/test_calc.py"])  # a test file the command names stays editable

    def test_a_test_file_in_the_target_is_covered(self):
        _, j, skipped, applied = self.planned([self.TC])
        self.assertEqual((skipped, applied, j["test_files"]), ({}, ["tests/test_calc.py"], 1))

    def test_coverage_is_the_target_the_ledger_or_a_command_word(self):
        cov = fix._covered
        self.assertTrue(cov("tests/a.py", set(), {"tests/a.py"}))
        self.assertTrue(cov("tests/a.py", {"tests/a.py"}, set()))
        self.assertTrue(cov("tests/a.py", {"tests"}, set()))
        self.assertTrue(cov("tests/a.py", {"."}, set()))
        self.assertFalse(cov("tests/a.py", {"pytest"}, {"tests/b.py"}))
        self.assertFalse(cov("tests2/a.py", {"tests"}, set()))

    def test_the_flag_is_needed_for_a_test_file(self):
        run = self.pin()
        self.ingest(run, self.TC)
        self.confirm(run, (report_id(self.TC), dict(path=self.TC["path"], line=self.TC["line"], quote=self.TC["quote"])))
        j = self.plan(run, self.WORDS)[2]
        self.assertEqual((j["reason"], j["detail"]), ("nothing-to-fix", "skipped: test-path 1"))

    def test_a_swapped_expected_value_is_kept(self):
        run, bg = self.started(self.TC)
        self.edit("tests/test_calc.py", "assert 1 - 1", "assert 1 + 1")
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "fixed")

    def test_a_dropped_assertion_is_restored_and_the_run_continues(self):
        run, bg = self.started(self.TC, two("src/second.py", 2, "return a / b", "return a * b"))
        if bg["path"] != "tests/test_calc.py":  # entry order is the report's: decline the source fix to reach the test file
            self.assertEqual(self.finish(run, bg["seal"], "declined")[2]["outcome"], "declined")
            bg = self.begin(run)[2]
        self.assertEqual(bg["path"], "tests/test_calc.py")
        self.edit("tests/test_calc.py", "    assert 1 - 1\n", "")
        rc, _, f = self.finish(run, bg["seal"])
        self.assertEqual((rc, f["status"], f["outcome"]), (0, "ok", "reverted-weakened"), f)
        self.assertIn("assertion", f["detail"])
        self.assertEqual(slurp(os.path.join(self.repo, "tests/test_calc.py")), self.TEST.encode())
        self.assertNotEqual(self.begin(run)[2].get("reason"), "halted")  # recoverable: the run is not halted

    def test_an_added_skip_marker_is_restored(self):
        run, bg = self.started(self.TC)
        self.edit("tests/test_calc.py", "def test_x", "@pytest.mark.skip\ndef test_x")
        self.assertEqual(self.finish(run, bg["seal"])[2]["outcome"], "reverted-weakened")


if __name__ == "__main__":
    unittest.main()
