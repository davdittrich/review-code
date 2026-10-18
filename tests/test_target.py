"""target.py: every SCOPE-01 kind, forge eligibility, ambiguity (SCOPE-02)."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "review-code", "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import gitfix  # noqa: E402
import target  # noqa: E402

CANARY_NAME = "CANARY-PRNAME-3e8"


def call(args, cwd, tmp, gh=None, glab=None):
    """Run target.main in-process under the isolated git env; return (rc, parsed stdout)."""
    buf = io.StringIO()
    with gitfix.isolated(tmp), redirect_stdout(buf):
        with mock.patch.object(target, "gh_json", gh or mock.Mock(side_effect=AssertionError("gh called"))), \
                mock.patch.object(target, "glab_json",
                                  glab or mock.Mock(side_effect=AssertionError("glab called"))):
            rc = target.main(list(args), cwd)
    return rc, json.loads(buf.getvalue())


def load(run):
    with open(os.path.join(run, "target.json"), encoding="utf-8") as fh:
        return json.load(fh)


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)

    def git(self, *a, cwd=None):
        return gitfix.run(["git"] + list(a), cwd or self.repo).strip()

    def run_dirs(self, repo=None):
        base = os.path.join(repo or self.repo, ".git", "review-code")
        return sorted(os.listdir(base)) if os.path.isdir(base) else []


class PRWorld(Base):
    """A bare 'GitHub' with refs/pull/7/head, and a clone whose raw remote URL is a github.com URL."""

    URL = "https://github.com/o/r.git"

    def setUp(self):
        super().setUp()
        self.bare = os.path.join(self.tmp, "bare.git")
        gitfix.run(["git", "init", "-q", "--bare", "-b", "main", self.bare], self.tmp)
        src = gitfix.make_repo(os.path.join(self.tmp, "src"))
        gitfix.commit(src, {"src/cfg.py": "def load():\n    return 1\n"}, "base")
        self.main = gitfix.run(["git", "rev-parse", "HEAD"], src).strip()
        gitfix.run(["git", "push", "-q", self.bare, "main"], src)
        gitfix.run(["git", "checkout", "-q", "-b", "pr"], src)
        gitfix.commit(src, {"src/cfg.py": "def load():\n    return 2\n", "src/" + CANARY_NAME + ".py": "x = 1\n"},
                      "feat: pr")
        self.pr = gitfix.run(["git", "rev-parse", "HEAD"], src).strip()
        gitfix.run(["git", "push", "-q", self.bare, "pr:refs/pull/7/head"], src)
        self.repo = os.path.join(self.tmp, "clone")
        gitfix.run(["git", "clone", "-q", "--no-local", self.bare, self.repo], self.tmp)
        self.git("remote", "set-url", "origin", self.URL)
        self.git("config", "url." + self.bare + ".insteadOf", self.URL)
        # main advances on the remote after the clone: an opportunistic ref update would move origin/main
        gitfix.run(["git", "checkout", "-q", "main"], src)
        gitfix.commit(src, {"src/other.py": "y = 1\n"}, "main moves on")
        gitfix.run(["git", "push", "-q", self.bare, "main"], src)
        self.assertNotEqual(subprocess.call(["git", "cat-file", "-e", self.pr + "^{commit}"], cwd=self.repo,
                                            stderr=subprocess.DEVNULL), 0)

    def payload(self, **kw):
        d = {"state": "OPEN", "isDraft": False, "baseRefOid": self.main, "headRefOid": self.pr,
             "baseRefName": "main", "isCrossRepository": True, "url": "https://github.com/o/r/pull/7",
             "number": 7}
        d.update(kw)
        return d

    def gh(self, **kw):
        return mock.Mock(return_value=self.payload(**kw))

    def has_pr_objects(self):
        return subprocess.call(["git", "cat-file", "-e", self.pr + "^{commit}"], cwd=self.repo,
                               stderr=subprocess.DEVNULL) == 0


class TestPR(PRWorld):
    def test_pr_pin_open(self):
        refs = self.git("for-each-ref")
        status = self.git("status", "--porcelain")
        fh_path = os.path.join(self.repo, ".git", "FETCH_HEAD")
        existed = os.path.exists(fh_path)
        rc, out = call(["7"], self.repo, self.tmp, gh=self.gh())
        self.assertEqual((rc, out["status"], out["kind"], out["number"]), (0, "ok", "pr", 7))
        self.assertEqual((out["head_sha"], out["base_sha"], out["merge_base"]), (self.pr, self.main, self.main))
        self.assertEqual(self.git("for-each-ref"), refs)
        self.assertEqual(self.git("status", "--porcelain"), status)
        self.assertEqual(os.path.exists(fh_path), existed)
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD"), "main")
        t = load(out["run"])
        self.assertEqual((t["kind"], t["number"], t["head_sha"]), ("pr", 7, self.pr))
        self.assertEqual(t["url"], "https://github.com/o/r/pull/7")
        self.assertEqual(t["commits"], [self.pr])

    def test_pr_stdout_has_no_head_names(self):
        buf = io.StringIO()
        with gitfix.isolated(self.tmp), redirect_stdout(buf), \
                mock.patch.object(target, "gh_json", self.gh()):
            self.assertEqual(target.main(["#7"], self.repo), 0)
        out = json.loads(buf.getvalue())
        self.assertNotIn(CANARY_NAME, buf.getvalue())
        self.assertIn("src/" + CANARY_NAME + ".py", load(out["run"])["included"])
        e = gitfix.env()
        e["TMPDIR"] = self.tmp
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "ledger.py"), "build", "--run", out["run"]],
                           cwd=self.repo, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn(CANARY_NAME.encode(), p.stdout)

    def test_pr_url_argument(self):
        gh = self.gh()
        rc, out = call(["https://github.com/o/r/pull/7/files"], self.repo, self.tmp, gh=gh)
        self.assertEqual((rc, out["status"], out["number"]), (0, "ok", 7))
        self.assertEqual(gh.call_args[0][0][:3], ["pr", "view", "https://github.com/o/r/pull/7"])

    def _skip(self, reason, **kw):
        before = self.run_dirs()
        rc, out = call(["7"], self.repo, self.tmp, gh=self.gh(**kw))
        self.assertEqual((rc, out["status"], out["reason"]), (1, "skip", reason))
        self.assertEqual(self.run_dirs(), before)
        self.assertFalse(self.has_pr_objects(), "a fetch ran for an ineligible PR")

    def test_eligibility_closed(self):
        self._skip("closed", state="CLOSED")

    def test_eligibility_merged(self):
        self._skip("merged", state="MERGED")

    def test_eligibility_draft(self):
        self._skip("draft", isDraft=True)

    def test_eligibility_cross_repo(self):
        for arg, reason in (("https://github.com/other/repo/pull/7", "cross-repo"),):
            rc, out = call([arg], self.repo, self.tmp)  # gh must not be called
            self.assertEqual((rc, out["status"], out["reason"]), (1, "refuse", reason))
        rc, out = call(["7"], self.repo, self.tmp, gh=self.gh(url="https://github.com/other/repo/pull/7"))
        self.assertEqual((rc, out["reason"]), (1, "no-remote-for-pr-repo"))

    def test_eligibility_head_moved(self):
        rc, out = call(["7"], self.repo, self.tmp, gh=self.gh(headRefOid="1" * 40))
        self.assertEqual((rc, out["status"], out["reason"]), (1, "refuse", "head-moved"))

    def test_eligibility_fetch_failed(self):
        self.git("config", "url." + os.path.join(self.tmp, "nope.git") + ".insteadOf", self.URL)
        self.git("config", "--unset", "url." + self.bare + ".insteadOf")
        rc, out = call(["7"], self.repo, self.tmp, gh=self.gh())
        self.assertEqual((rc, out["reason"]), (1, "fetch-failed"))

    def test_boundary_pr_number(self):
        rc, out = call(["0"], self.repo, self.tmp)
        self.assertEqual((rc, out["status"], out["reason"]), (1, "refuse", "bad-number"))
        gh = mock.Mock(side_effect=target.Refuse("gh-failed", "x"))
        rc, out = call(["1"], self.repo, self.tmp, gh=gh)
        self.assertEqual((out["reason"], gh.call_args[0][0][2]), ("gh-failed", "1"))

    def test_boundary_sha_length(self):
        for bad in ("a" * 39, "a" * 41, "a" * 63, "a" * 65, "A" * 40, "g" * 40, ""):
            rc, out = call(["7"], self.repo, self.tmp, gh=self.gh(headRefOid=bad))
            self.assertEqual((rc, out["reason"]), (1, "gh-failed"), bad)
            rc, out = call(["7"], self.repo, self.tmp, gh=self.gh(baseRefOid=bad))
            self.assertEqual((rc, out["reason"]), (1, "gh-failed"), bad)
        for good in ("b" * 40, "b" * 64):  # valid length and alphabet: passes validation, object absent
            rc, out = call(["7"], self.repo, self.tmp, gh=self.gh(headRefOid=good))
            self.assertEqual((rc, out["reason"]), (1, "head-moved"), good)

    def test_boundary_base_branch_name(self):
        for bad in ("-x", "a..b", "@{-1}", "a b"):
            rc, out = call(["7"], self.repo, self.tmp, gh=self.gh(baseRefName=bad))
            self.assertEqual((rc, out["reason"]), (1, "gh-failed"), bad)


if __name__ == "__main__":
    unittest.main()
