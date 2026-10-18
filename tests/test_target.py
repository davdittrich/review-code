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


class Local(Base):
    def setUp(self):
        super().setUp()
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        self.root = gitfix.commit(self.repo, {"a.txt": "1\n", "src/x.py": "x = 1\n", "docs/d.md": "d\n",
                                              ".gitignore": "*.log\n"}, "root")
        self.git("checkout", "-q", "-b", "feat")
        self.c1 = gitfix.commit(self.repo, {"src/x.py": "x = 2\n"}, "c1")
        self.c2 = gitfix.commit(self.repo, {"src/y.py": "y = 1\n", "B.py": "b\n", "a.py": "a\n"}, "c2")

    def pin(self, *args):
        return call(list(args), self.repo, self.tmp)

    def write(self, rel, text="n\n"):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)

    def diff(self, out):
        with open(os.path.join(out["run"], "change.diff"), encoding="utf-8") as fh:
            return fh.read()


class TestKinds(Local):
    def test_kinds_commit(self):
        rc, out = self.pin(self.c2 + "^!")
        self.assertEqual((rc, out["kind"], out["base_sha"], out["head_sha"]), (0, "commit", self.c1, self.c2))
        t = load(out["run"])
        self.assertEqual((t["commits"], t["merge_base"], t["included"]), ([self.c2], self.c1, ["B.py", "a.py", "src/y.py"]))
        rc, out = self.pin(self.root + "^!")  # root commit: base is the empty tree
        empty = subprocess.run(["git", "hash-object", "-t", "tree", "--stdin"], cwd=self.repo, input=b"",
                               env=gitfix.env(), stdout=subprocess.PIPE).stdout.decode().strip()
        self.assertEqual((rc, out["base_sha"], out["head_sha"]), (0, empty, self.root))
        self.assertIn("a.txt", load(out["run"])["included"])

    def _range(self, spec):
        self.git("checkout", "-q", "main")
        main_tip = gitfix.commit(self.repo, {"z.txt": "z\n"}, "main moves")
        self.git("checkout", "-q", "feat")
        rc, out = self.pin(spec % ("main", "feat"))
        self.assertEqual((rc, out["kind"], out["base_sha"], out["head_sha"], out["merge_base"]),
                         (0, "range", main_tip, self.c2, self.root))
        self.assertNotIn("z.txt", load(out["run"])["included"])

    def test_kinds_range_two_dot(self):
        self._range("%s..%s")

    def test_kinds_range_three_dot(self):
        self._range("%s...%s")

    def test_kinds_staged(self):
        self.write("a.txt", "staged\n")
        self.git("add", "a.txt")
        self.write("src/x.py", "unstaged\n")
        rc, out = self.pin("--staged")
        self.assertEqual((rc, out["kind"], out["head_sha"], out["base_sha"]), (0, "staged", self.c2, self.c2))
        t = load(out["run"])
        self.assertTrue(t["dirty"])
        self.assertEqual((t["included"], t["commits"]), (["a.txt"], []))
        self.assertEqual(t["diff_cmd"][-3:], ["--cached", self.c2, "--"])

    def test_kinds_unstaged(self):
        self.write("a.txt", "edited\n")
        self.write("n.txt")
        self.write("ignored.log")
        rc, out = self.pin("--unstaged")
        self.assertEqual((rc, out["kind"]), (0, "unstaged"))
        t = load(out["run"])
        self.assertTrue(t["dirty"])
        self.assertEqual((t["included"], t["untracked"]), (["a.txt", "n.txt"], ["n.txt"]))
        self.assertNotIn("--cached", t["diff_cmd"])
        self.assertNotIn(self.c2, t["diff_cmd"])
        text = self.diff(out)
        self.assertIn("new file mode", text)
        self.assertIn("+++ b/n.txt", text)

    def test_kinds_worktree(self):
        self.write("a.txt", "staged\n")
        self.git("add", "a.txt")
        self.write("src/x.py", "unstaged\n")
        self.write("n.txt")
        rc, out = self.pin()  # dirty tree, no argument
        self.assertEqual((rc, out["kind"], out["head_sha"]), (0, "worktree", self.c2))
        t = load(out["run"])
        self.assertTrue(t["dirty"])
        self.assertEqual(t["included"], ["a.txt", "n.txt", "src/x.py"])
        self.assertEqual(t["diff_cmd"][-2:], [self.c2, "--"])
        self.assertIn("+++ b/n.txt", self.diff(out))

    def test_kinds_path(self):
        self.write("src/new.py")
        rc, out = self.pin("a.txt")
        self.assertEqual((rc, out["kind"]), (0, "path"))
        t = load(out["run"])
        self.assertEqual((t["included"], t["diff_cmd"], t["head_sha"]), (["a.txt"], None, self.c2))
        rc, out = self.pin("src")
        self.assertEqual(load(out["run"])["included"], ["src/new.py", "src/x.py", "src/y.py"])
        self.write_outside = os.path.join(self.tmp, "outside.txt")
        with open(self.write_outside, "w") as fh:
            fh.write("o\n")
        rc, out = self.pin("../outside.txt")
        self.assertEqual((rc, out["status"], out["reason"]), (1, "refuse", "outside-repo"))

    def test_kinds_repo(self):
        everything = [".gitignore", "B.py", "a.py", "a.txt", "docs/d.md", "src/x.py", "src/y.py"]
        for arg in (".", "--repo"):
            rc, out = self.pin(arg)
            self.assertEqual((rc, out["kind"]), (0, "repo"))
            self.assertEqual(load(out["run"])["included"], everything)

    def test_adjacency_head_and_empty_range(self):
        before = self.run_dirs()
        for arg in ("HEAD", "feat..feat", "feat...feat"):
            rc, out = self.pin(arg)
            self.assertEqual((rc, out["status"], out["reason"]), (1, "skip", "empty-diff"), arg)
        self.assertEqual(self.run_dirs(), before)

    def test_empty_inputs(self):
        self.git("checkout", "-q", "main")
        rc, out = self.pin()
        self.assertEqual((rc, out["status"], out["reason"]), (1, "refuse", "no-target"))
        rc, out = self.pin("--staged")
        self.assertEqual((rc, out["status"], out["reason"]), (1, "skip", "empty-diff"))
        os.makedirs(os.path.join(self.repo, "emptydir"))
        rc, out = self.pin("emptydir")
        self.assertEqual((rc, out["status"], out["reason"]), (1, "skip", "no-files"))
        self.assertEqual(self.run_dirs(), [])

    def test_ordering_stable(self):
        rc1, o1 = self.pin("main")
        rc2, o2 = self.pin("main")
        a, b = load(o1["run"]), load(o2["run"])
        self.assertNotEqual(a["run"], b["run"])
        for d in (a, b):
            d.pop("run"), d.pop("created")
        self.assertEqual(a, b)
        self.assertEqual(a["included"], sorted(a["included"]))
        self.assertEqual(a["included"][:2], ["B.py", "a.py"])  # code point order: upper case first
        self.assertEqual(a["commits"], [self.c1, self.c2])

    def test_precision_full_ids(self):
        rc, out = self.pin(self.c1[:7])
        self.assertEqual((rc, out["base_sha"], out["head_sha"]), (0, self.c1, self.c2))
        t = load(out["run"])
        self.assertEqual({len(t[k]) for k in ("base_sha", "head_sha", "merge_base")}, {40})
        repo = os.path.join(self.tmp, "sha256")
        gitfix.run(["git", "init", "-q", "-b", "main", "--object-format=sha256", repo], self.tmp)
        gitfix.commit(repo, {"f.txt": "1\n"}, "base")
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], repo)
        gitfix.commit(repo, {"f.txt": "2\n"}, "change")
        rc, out = call(["main"], repo, self.tmp)
        self.assertEqual(rc, 0, out)
        t = load(out["run"])
        self.assertEqual({len(t[k]) for k in ("base_sha", "head_sha", "merge_base")} | {len(c) for c in t["commits"]},
                         {64})

    def test_concurrency_distinct_runs(self):
        rc, o1 = self.pin("main")
        rc, o2 = self.pin("main")
        self.assertNotEqual(o1["run"], o2["run"])
        before = load(o1["run"])
        gitfix.commit(self.repo, {"late.txt": "l\n"}, "late")
        self.assertEqual(load(o1["run"]), before)
        rc, o3 = self.pin("main")
        self.assertNotEqual(o3["head_sha"], o1["head_sha"])
        self.assertEqual(load(o1["run"])["head_sha"], self.c2)


if __name__ == "__main__":
    unittest.main()
