"""End-to-end tracer: target.py then ledger.py on a real branch fixture."""
import ast
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "review-code", "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import gitfix  # noqa: E402

CANARY = "CANARY-COMMIT-7d2"
TARGET_KEYS = {"status", "reason", "detail", "kind", "base_sha", "head_sha", "merge_base", "number",
               "run", "included_count", "excluded_count", "untracked_count", "candidates"}
LEDGER_KEYS = {"status", "reason", "run", "label", "spec_status", "counts"}
RLO = chr(0x202E)


def cli(script, args, cwd, tmp):
    e = gitfix.env()
    e["TMPDIR"] = tmp
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + args, cwd=cwd, env=e,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8")


class Fixture(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, {"src/cfg.py": "def load():\n    return 1\n", "CLAUDE.md": "# rules\n"}, "base")
        self.base = gitfix.run(["git", "rev-parse", "HEAD"], self.repo).strip()
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)

    def git(self, *a):
        return gitfix.run(["git"] + list(a), self.repo).strip()

    def feature(self, files=None, message="feat: add parse_cfg\n\n" + CANARY):
        files = files or {"src/cfg.py": "def load():\n    return 1\n\ndef parse_cfg(s):\n    return s\n"}
        return gitfix.commit(self.repo, files, message)

    def pin(self, arg="main"):
        rc, out, _ = cli("target.py", [arg], self.repo, self.tmp)
        return rc, json.loads(out)

    def build(self, run, extra=()):
        rc, out, _ = cli("ledger.py", ["build", "--run", run] + list(extra), self.repo, self.tmp)
        return rc, out


class Tracer(Fixture):
    def test_tracer_branch_run(self):
        self.feature()
        refs = self.git("for-each-ref")
        rc, t = self.pin()
        self.assertEqual((rc, t["status"], t["kind"]), (0, "ok", "branch"))
        self.assertEqual(t["base_sha"], self.git("rev-parse", "main"))
        self.assertEqual(t["head_sha"], self.git("rev-parse", "HEAD"))
        self.assertEqual(t["merge_base"], self.git("merge-base", "main", "HEAD"))
        run = t["run"]
        self.assertTrue(os.path.basename(run).startswith("review-code."))
        # A3 failed: the run dir lives under the git dir, not under $TMPDIR
        gp = os.path.realpath(os.path.join(self.repo, self.git("rev-parse", "--git-path", "review-code")))
        self.assertEqual(os.path.dirname(os.path.realpath(run)), gp)
        self.assertEqual(stat.S_IMODE(os.stat(run).st_mode), 0o700)
        rc, _ = self.build(run)
        self.assertEqual(rc, 0)
        for name in ("target.json", "change.diff", "ledger.json", "inputs/spec.md", "inputs/defects.md",
                     "inputs/standards.md"):
            self.assertTrue(os.path.isfile(os.path.join(run, name)), name)
        with open(os.path.join(run, "conventions", "CLAUDE.md"), "rb") as fh:
            self.assertEqual(fh.read(), self.git("show", "main:CLAUDE.md").encode() + b"\n")
        hits = []
        for d, _, fs in os.walk(run):
            for f in fs:
                p = os.path.join(d, f)
                with open(p, "rb") as fh:
                    if CANARY.encode() in fh.read():
                        hits.append(os.path.relpath(p, run))
        self.assertEqual(hits, ["inputs/spec.md"])
        with open(os.path.join(run, "inputs", "spec.md"), encoding="utf-8") as fh:
            spec = fh.read()
        self.assertRegex(spec, r"(?s)<<<UNTRUSTED-([0-9a-f]+)\n.*" + CANARY + r".*\n\1>>>")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("for-each-ref"), refs)
        with open(os.path.join(run, "ledger.json"), encoding="utf-8") as fh:
            led = json.load(fh)
        self.assertEqual((led["label"], led["trust"], led["trust_asked"]), ("full", "unset", False))
        self.assertEqual(led["conventions"][0]["file"], "CLAUDE.md")
        self.assertFalse(led["conventions"][0]["differs_on_head"])

    def test_tracer_no_target_execution(self):
        self.feature()
        marker = os.path.join(self.tmp, "ran")
        evil = os.path.join(self.tmp, "evil.sh")
        with open(evil, "w") as fh:
            fh.write("#!/bin/sh\ntouch '%s'\ncat \"$@\" 2>/dev/null\n" % marker)
        os.chmod(evil, 0o755)
        hooks = os.path.join(self.tmp, "hooks")
        os.makedirs(hooks)
        for h in ("post-checkout", "post-merge", "pre-commit", "reference-transaction"):
            with open(os.path.join(hooks, h), "w") as fh:
                fh.write("#!/bin/sh\ntouch '%s'\n" % marker)
            os.chmod(os.path.join(hooks, h), 0o755)
        for k, v in (("diff.external", evil), ("core.fsmonitor", evil), ("core.hooksPath", hooks),
                     ("diff.evil.textconv", evil)):
            self.git("config", k, v)
        with open(os.path.join(self.repo, ".gitattributes"), "w") as fh:
            fh.write("*.py diff=evil\n")
        e = gitfix.env()
        e.update(TMPDIR=self.tmp, GIT_EXTERNAL_DIFF=evil)
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "target.py"), "main"], cwd=self.repo, env=e,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        run = json.loads(p.stdout)["run"]
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "ledger.py"), "build", "--run", run],
                           cwd=self.repo, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(os.path.exists(marker), "an external program ran")
        with open(os.path.join(run, "change.diff"), encoding="utf-8") as fh:
            self.assertIn("parse_cfg", fh.read())

    def test_tracer_hidden_char(self):
        name = "src/CANARY-NAME-5c1.py"
        self.feature({name: "x = 1\n# a" + RLO + "b\n"})
        with gitfix.isolated(self.tmp):
            buf = io.StringIO()
            import target
            with redirect_stdout(buf):
                rc = target.main(["main"], self.repo)
        self.assertEqual(rc, 0)
        t = json.loads(buf.getvalue())
        rc, lout = self.build(t["run"])
        self.assertEqual(rc, 0)
        for out in (buf.getvalue(), lout):
            self.assertNotIn("CANARY-NAME-5c1", out)
        with open(os.path.join(t["run"], "change.diff"), encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        self.assertFalse([c for ln in lines for c in ln if ord(c) == 0x202E])
        with open(os.path.join(t["run"], "ledger.json"), encoding="utf-8") as fh:
            hc = [h for h in json.load(fh)["hidden_chars"] if h["file"] == "change.diff"]  # tree/ files add their own
        self.assertEqual(len(hc), 1)
        self.assertEqual((hc[0]["file"], hc[0]["codepoints"]), ("change.diff", ["U+202E"]))
        self.assertIn("<U+202E>", lines[hc[0]["line"] - 1])

    def test_tracer_stdout_minimal(self):
        self.feature()
        rc, t = self.pin()
        self.assertLessEqual(set(t), TARGET_KEYS)
        rc, out = self.build(t["run"])
        self.assertLessEqual(set(json.loads(out)), LEDGER_KEYS)
        self.git("checkout", "-q", "main")  # clean tree on the default branch: nothing to infer
        cases = [(["-x"], "option-like-argument"), (["no-such-ref"], "bad-ref"), ([], "no-target")]
        for args, reason in cases:
            rc, out, _ = cli("target.py", args, self.repo, self.tmp)
            o = json.loads(out)
            self.assertEqual((rc, o["status"], o["reason"]), (1, "refuse", reason))
            self.assertLessEqual(set(o), TARGET_KEYS)
        before = os.listdir(os.path.dirname(t["run"]))
        rc, out, _ = cli("target.py", ["HEAD"], self.repo, self.tmp)
        o = json.loads(out)
        self.assertEqual((rc, o["status"], o["reason"]), (1, "skip", "empty-diff"))
        self.assertEqual(os.listdir(os.path.dirname(t["run"])), before)

    def test_tracer_py39_grammar(self):
        for f in ("target.py", "ledger.py", "sanitize.py"):
            with open(os.path.join(SCRIPTS, f), encoding="utf-8") as fh:
                ast.parse(fh.read(), filename=f, feature_version=(3, 9))
        self.assertTrue(re.match(r"3\.", sys.version))


if __name__ == "__main__":
    unittest.main()
