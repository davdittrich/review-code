"""repro.py base: the change's own test fails on the merge base and passes on head."""
import io
import json
import os
import sys
from contextlib import redirect_stdout
from unittest import mock

import paths  # noqa: F401

import gitfix  # noqa: E402
from gitfix import cli  # noqa: E402
import repro  # noqa: E402,F401
from test_repro import Base  # noqa: E402

TEST = "tests/test_calc.py"
SRC = "import sys\nsys.path.insert(0, 'src')\nimport calc\nsys.exit(0 if calc.V == 2 else 1)\n"


class BaseCheck(Base):
    feat_files = {"src/calc.py": "V = 2\n", TEST: SRC}

    def base(self, run, test=TEST, cmd=None):
        rc, out, err = cli("repro.py", ["base", "--run", run, "--test", test, "--"] + (cmd or [sys.executable, test]),
                           self.repo, self.tmp)
        self.assertNotIn("Traceback", err)
        return rc, json.loads(out)

    def outcome(self, run, test=TEST):
        return self.base(run, test)[1]["outcome"]

    def test_verified(self):
        run = self.start()
        before = self.state()
        rc, out = self.base(run)
        self.assertEqual((rc, out["status"], out["outcome"], out["head_exit"]), (0, "ok", "verified", 0))
        self.assertNotEqual(out["base_exit"], 0)
        with open(os.path.join(run, "repro", "base-tests_test_calc_py.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["outcome"], "verified")
        self.assert_clean(run)
        self.assertEqual(self.state(), before)

    def test_passes_on_base(self):
        run = self.start()
        self.assertEqual(self.base(run, cmd=[sys.executable, "-c", "pass"])[1]["outcome"], "passes-on-base")

    def test_fails_on_head(self):
        run = self.start()
        out = self.base(run, cmd=["false"])[1]
        self.assertEqual((out["outcome"], out["base_exit"]), ("fails-on-head", None))

    def test_timeout(self):
        run = self.start()
        out = io.StringIO()
        with mock.patch.object(repro, "TIMEOUT", 1), gitfix.isolated(self.tmp), redirect_stdout(out):
            repro.main(["base", "--run", run, "--test", TEST, "--", "sleep", "30"])
        self.assertEqual(json.loads(out.getvalue())["outcome"], "timeout")
        self.assert_clean(run)

    def test_untrusted_refused(self):
        for trust, reason in (("external", "trust-external"), (None, "trust-unset")):
            run = self.start(trust=trust)
            self.assertEqual(self.base(run), (1, {"status": "refuse", "reason": reason}))
            self.assertFalse(os.path.exists(os.path.join(run, "repro")))

    def test_bad_input(self):
        run = self.start()
        self.assertEqual(self.base(run, "../x.py")[1]["reason"], "bad-test-path")
        self.assertEqual(self.base(run, "nope.py")[1]["reason"], "no-test-file")
        rc, out, _ = cli("repro.py", ["base", "--run", run, "--id", "F-0123456789", "--test", TEST, "--", "true"],
                         self.repo, self.tmp)
        self.assertEqual((rc, json.loads(out)["reason"]), (1, "usage"))
        self.assert_clean(run)

    def test_run_mode_unchanged(self):
        run = self.start()
        rc, out = self.repro(run, "t.py", [sys.executable, "t.py"])
        self.assertEqual((rc, out["outcome"]), (0, "reproduced"))

