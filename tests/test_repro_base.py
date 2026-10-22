"""repro.py run --base: the finding's claim 'this test passes on the merge base' is observed, then gated through report.py."""
import io
import json
import os
import sys
from contextlib import redirect_stdout
from unittest import mock

import paths  # noqa: F401

import gitfix  # noqa: E402
from gitfix import cli, fid, lines  # noqa: E402
import repro  # noqa: E402,F401
from test_repro import Base, FID  # noqa: E402

TEST = "tests/test_calc.py"
Q = "sys.exit(0 if getattr(calc, 'V', 0) >= 0 else 1)"  # passes on base and head: the claim holds
SRC = "import sys\nsys.path.insert(0, 'src')\nimport calc\n" + Q + "\n"
QF = "sys.exit(0 if calc.V == 2 else 1)"  # fails on base: the test does test the fix
SRCF = "import sys\nsys.path.insert(0, 'src')\nimport calc\n" + QF + "\n"


class RunBase(Base):
    def base(self, run, test=TEST, cmd=None, fid_=FID, base=True):
        args = ["run", "--run", run, "--id", fid_, "--test", test] + (["--base"] if base else []) + ["--"]
        rc, out, err = cli("repro.py", args + (cmd or [sys.executable, test]), self.repo, self.tmp)
        self.assertNotIn("Traceback", err)
        return rc, json.loads(out)


class BaseCheck(RunBase):
    feat_files = {"src/calc.py": "V = 2\n", TEST: SRC}

    def test_reproduced_when_passes_on_base(self):
        run = self.start()
        before = self.state()
        rc, out = self.base(run)
        self.assertEqual((rc, out["status"], out["outcome"], out["exit"]), (0, "ok", "reproduced", 0))
        self.assertEqual(self.record(run), {"outcome": "reproduced", "exit": 0})
        self.assert_clean(run)
        self.assertEqual(self.state(), before)

    def test_sentinel_is_not_needed(self):
        run = self.start()
        self.assertEqual(self.base(run, cmd=[sys.executable, "-c", "import sys;sys.exit(0)", TEST])[1]["outcome"], "reproduced")

    def test_timeout(self):
        run = self.start()
        out = io.StringIO()
        with mock.patch.object(repro, "TIMEOUT", 1), gitfix.isolated(self.tmp), redirect_stdout(out):
            repro.main(["run", "--run", run, "--id", FID, "--test", TEST, "--base", "--", "sh", "-c", "sleep 30 #" + TEST])
        self.assertEqual(json.loads(out.getvalue())["outcome"], "timeout")
        self.assert_clean(run)

    def test_untrusted_refused(self):
        for trust, reason in (("external", "trust-external"), (None, "trust-unset")):
            run = self.start(trust=trust)
            self.assertEqual(self.base(run), (1, {"status": "refuse", "reason": reason}))
            self.assertFalse(os.path.exists(os.path.join(run, "repro")))
            self.assert_clean(run)

    def test_refusals(self):
        run = self.start()
        self.assertEqual(self.base(run, "../x.py")[1]["reason"], "bad-test-path")
        self.assertEqual(self.base(run, "tests/nope.py", cmd=["true", "tests/nope.py"])[1]["reason"], "no-test-at-head")
        self.assertEqual(self.base(run, cmd=["true"])[1]["reason"], "cmd-misses-test")
        self.assert_clean(run)
        self.assertFalse(os.path.exists(os.path.join(run, "repro")))

    def test_no_merge_base(self):
        run = self.start()
        path = os.path.join(run, "target.json")
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
        rec["merge_base"] = None
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        self.assertEqual(self.base(run)[1]["reason"], "no-merge-base")
        self.assert_clean(run)

    def test_old_subcommand_gone_and_run_unchanged(self):
        run = self.start()
        rc, out, _ = cli("repro.py", ["base", "--run", run, "--test", TEST, "--", "true"], self.repo, self.tmp)
        self.assertEqual((rc, json.loads(out)["reason"]), (1, "usage"))
        rc, out = self.repro(run, "t.py", [sys.executable, "t.py"])
        self.assertEqual((rc, out["outcome"]), (0, "reproduced"))


class FailsOnBase(RunBase):
    feat_files = {"src/calc.py": "V = 2\n", TEST: SRCF}  # the test needs the fix

    def test_not_reproduced_when_fails_on_base(self):
        run = self.start()
        out = self.base(run)[1]
        self.assertEqual((out["outcome"], out["exit"] != 0), ("not-reproduced", True))
        self.assertEqual(self.record(run)["outcome"], "not-reproduced")
        self.assert_clean(run)


class GateBase(Base):
    """A fix commit with a test: the finding stays unverifiable unless the base run reproduces it."""
    base_files = {"src/calc.py": "V = 1\n"}
    feat_files = {"src/calc.py": "V = 2\n", TEST: SRC}
    Qt = Q

    def gate(self, category="test"):
        run = self.start()
        cand = {"path": TEST, "line": 4, "severity": "suggestion", "category": category, "title": "test passes on base",
                "trigger": "passes on the merge base without the fix", "quote": self.Qt, "fix": "assert the fixed value"}
        ev = [{"path": TEST, "line": 4, "quote": self.Qt}]
        i = fid(TEST, category, self.Qt)
        for sub, objs in (("ingest", [cand]), ("verdicts", [{"id": i, "verdict": "unverifiable", "reason": "r", "evidence": ev}])):
            rc, out, _ = cli("report.py", [sub, "--run", run], self.repo, self.tmp, lines(*objs))
            self.assertEqual(rc, 0, out)
        self.base_run(run, i)
        rc, out, err = cli("report.py", ["render", "--run", run, "--format", "json"], self.repo, self.tmp)
        self.assertEqual(rc, 0, out)
        d = json.loads(out)
        return i, {x["id"]: x for x in d["findings"]}, {x["id"]: x for x in d["unverifiable"]}

    def base_run(self, run, i):
        rc, out, _ = cli("repro.py", ["run", "--run", run, "--id", i, "--test", TEST, "--base", "--", sys.executable, TEST],
                         self.repo, self.tmp)
        self.assertEqual(rc, 0, out)


class Gated(GateBase):
    def test_reproduced_test_finding_is_verified(self):
        i, f, u = self.gate()
        self.assertEqual((f[i]["evidence_class"], f[i]["verified"], f[i]["category"]), ("reproduced", True, "test"))
        self.assertNotIn(i, u)

    def test_other_category_stays_unverifiable(self):
        i, f, u = self.gate(category="bug")
        self.assertEqual((i in f, u[i]["evidence_class"]), (False, "inferred"))


class GatedFails(GateBase):
    feat_files, Qt = {"src/calc.py": "V = 2\n", TEST: SRCF}, QF

    def test_not_reproduced_stays_unverifiable(self):
        i, f, u = self.gate()
        self.assertEqual((i in f, u[i]["evidence_class"]), (False, "inferred"))


class FinderBullet(Base):
    def test_bullet_asks_for_a_diff_fix_not_pr_text(self):
        with open(os.path.join(os.path.dirname(paths.SCRIPTS), "..", "..", "agents", "finder.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("passes on the merge base without the fix", text)
        self.assertIn("When the diff shows a fix", text)
        self.assertNotIn("PR says", text)
