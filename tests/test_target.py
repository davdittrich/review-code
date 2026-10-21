"""target.py: every SCOPE-01 kind, forge eligibility, ambiguity (SCOPE-02)."""
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

import paths  # noqa: F401

SCRIPTS = paths.SCRIPTS

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

    def test_pr_url_from_forge_is_escaped(self):
        # 6xl.85: the forge's url field is untrusted text; target.json must not carry a hidden payload in it
        url = "https://github.com/o/r/pull/7#" + "".join(chr(0xE0000 + ord(c)) for c in "PAY")  # matches PR_URL
        rc, out = call(["7"], self.repo, self.tmp, gh=self.gh(url=url))
        self.assertIn(out.get("reason"), (None, "gh-failed"), out)  # the url must match the PR URL pattern
        if out.get("status") == "ok":
            with open(os.path.join(out["run"], "target.json"), "rb") as fh:
                raw = fh.read()
            self.assertNotIn(b"\\udb40", raw)
            self.assertIn(b"<TAGS:3>", raw)

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


class TestCliTimeout(unittest.TestCase):
    def test_a_hung_forge_tool_refuses(self):
        hung = mock.Mock(side_effect=subprocess.TimeoutExpired(["gh"], target.CLI_TIMEOUT))
        with mock.patch.object(target.subprocess, "run", hung):
            with self.assertRaises(target.Refuse) as cm:
                target.gh_json(["pr", "view"], ".")
        self.assertEqual((cm.exception.reason, cm.exception.detail), ("gh-failed", "timeout"))
        self.assertEqual(hung.call_args.kwargs["timeout"], target.CLI_TIMEOUT)


class TestLoadTarget(unittest.TestCase):
    def test_one_refusal_for_a_missing_or_malformed_file(self):
        with tempfile.TemporaryDirectory() as run:
            for text in (None, "{", "[]", "{}", json.dumps({"kind": "pr", "included": "x", "repo": "r", "url": 1,
                                                          "head_sha": "h"})):
                if text is not None:
                    with open(os.path.join(run, "target.json"), "w") as fh:
                        fh.write(text)
                with self.assertRaises(target.Refuse) as cm:
                    target.load_target(run)
                self.assertEqual(cm.exception.reason, "no-target", text)
            good = {"kind": "pr", "included": [], "repo": "r", "url": None, "head_sha": "h"}
            with open(os.path.join(run, "target.json"), "w") as fh:
                json.dump(good, fh)
            self.assertEqual(target.load_target(run), good)


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

    def test_unexpected_error_keeps_one_line_json(self):
        # WR-03: an OSError on a write must still end in the one-line JSON contract
        with mock.patch.object(target, "pin", side_effect=PermissionError("denied")):
            rc, out = self.pin("main")
        self.assertEqual((rc, out), (1, {"status": "refuse", "reason": "internal-error", "detail": "PermissionError"}))

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


class TestAmbiguity(Local):
    def ask(self, *args):
        rc, out = self.pin(*args)
        self.assertEqual((rc, out["status"]), (2, "ambiguous"))
        self.assertEqual([c["index"] for c in out["candidates"]], list(range(1, len(out["candidates"]) + 1)))
        self.assertTrue(all(set(c) == {"index", "kind", "reading"} for c in out["candidates"]))
        return out["candidates"]

    def test_ambiguity_ref_and_dir(self):
        gitfix.commit(self.repo, {"main/m.txt": "m\n"}, "dir named like a branch")
        c = self.ask("main")
        self.assertEqual([x["kind"] for x in c], ["branch", "path"])
        rc, out = self.pin("main", "--choice", "2")
        self.assertEqual((rc, out["kind"]), (0, "path"))
        self.assertEqual(load(out["run"])["included"], ["main/m.txt"])
        rc, out = self.pin("--choice=1", "main")
        self.assertEqual((rc, out["kind"], out["base_sha"]), (0, "branch", self.root))
        for bad in ("3", "0"):
            rc, out = self.pin("main", "--choice", bad)
            self.assertEqual((rc, out["status"], out["reason"]), (1, "refuse", "bad-choice"))
        rc, out = self.pin("main", "--choice", "x")
        self.assertEqual(out["reason"], "bad-choice")

    def test_default_branch_name_is_quoted_in_reading(self):
        # IN-02: a ref name from the remote is printed on stdout, so it is shown quoted unless plainly safe
        odd = "m" + chr(0x202E) + "x"
        self.git("update-ref", "refs/remotes/origin/" + odd, self.root)
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/" + odd)
        self.git("checkout", "-q", "feat")
        cands = target.resolve([], self.repo)
        self.assertEqual(len(cands), 1)
        self.assertTrue(cands[0]["reading"].startswith("changes on HEAD since "), cands)
        self.assertNotIn(chr(0x202E), cands[0]["reading"])
        self.assertEqual(target._q("origin/main"), "origin/main")
        self.assertEqual(target._q("a b"), '"a b"')

    def test_ambiguity_pr_and_file(self):
        self.write("7", "seven\n")
        c = self.ask("7")
        self.assertEqual([x["kind"] for x in c], ["pr", "path"])
        rc, out = self.pin("7", "--choice", "2")
        self.assertEqual((rc, out["kind"], out["number"]), (0, "path", None))

    def test_ambiguity_descendant_ref(self):
        self.git("checkout", "-q", "main")
        c = self.ask("feat")
        self.assertEqual([x["reading"] for x in c], ["changes on HEAD since feat", "changes on feat since main"])
        rc, out = self.pin("feat", "--choice", "2")
        self.assertEqual((rc, out["kind"], out["base_sha"], out["head_sha"]), (0, "branch", self.root, self.c2))

    def test_ambiguity_diverged_pins(self):
        self.git("checkout", "-q", "main")
        main_tip = gitfix.commit(self.repo, {"z.txt": "z\n"}, "main moves")
        self.git("checkout", "-q", "feat")
        rc, out = self.pin("main")
        self.assertEqual((rc, out["kind"], out["base_sha"], out["merge_base"]), (0, "branch", main_tip, self.root))

    def test_ambiguity_unique(self):
        rc, out = self.pin("HEAD~1")
        self.assertEqual((rc, out["kind"], out["base_sha"]), (0, "branch", self.c1))

    def test_ambiguity_none(self):
        for args, reason in ((["no-such-thing"], "bad-ref"), (["--upload-pack=x"], "option-like-argument"),
                             (["main", "a.txt"], "mixed-target"), (["--staged", "a.txt"], "mixed-target"), (["a.txt", "--repo"], "mixed-target"),
                             (["0"], "bad-number")):
            rc, out = self.pin(*args)
            self.assertEqual((rc, out["status"], out["reason"]), (1, "refuse", reason), args)
        rc, out = self.pin("a.txt", "src")
        self.assertEqual((rc, out["kind"]), (0, "path"))
        self.assertEqual(load(out["run"])["included"], ["a.txt", "src/x.py", "src/y.py"])

    def test_mixed_target_detail_names_the_accepted_forms(self):  # review-code-to1
        for args in (["main", "a.txt"], ["--staged", "a.txt"]):
            rc, out = self.pin(*args)
            self.assertIn("--staged", out["detail"], args)
            self.assertIn("PR number", out["detail"], args)

    def test_ambiguity_no_arg(self):
        rc, out = self.pin()
        self.assertEqual((rc, out["kind"], out["base_sha"]), (0, "branch", self.root))  # clean, on feat
        self.write("a.txt", "dirty\n")
        rc, out = self.pin()
        self.assertEqual((rc, out["kind"]), (0, "worktree"))


class TestRunDirPruning(Local):
    """IN-01: run directories older than the retention period are removed when a new one is made."""

    def mk(self, name, age_days, link_to=None):
        base = os.path.join(self.repo, ".git", "review-code")
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, name)
        if link_to:
            os.symlink(link_to, path)
            return path
        os.makedirs(os.path.join(path, "inputs"))
        with open(os.path.join(path, "inputs", "spec.md"), "w") as fh:
            fh.write("old\n")
        t = time.time() - age_days * 86400
        os.utime(path, (t, t))
        return path

    def test_old_runs_are_pruned_and_the_rest_kept(self):
        old = self.mk("review-code.OLD123", target.RETENTION_DAYS + 1)
        fresh = self.mk("review-code.NEW123", target.RETENTION_DAYS - 1)
        other = self.mk("not-a-run", 400)  # not ours: never touched
        keep = os.path.join(self.tmp, "keep")
        os.makedirs(keep)
        link = self.mk("review-code.LINK12", 0, link_to=keep)  # a link is never followed or removed
        rc, out = self.pin("feat~1..feat")
        self.assertEqual((rc, out["status"]), (0, "ok"))
        self.assertFalse(os.path.exists(old))
        for p in (fresh, other, keep, link, out["run"]):
            self.assertTrue(os.path.exists(p), p)

    def test_an_old_captures_directory_is_pruned_and_a_fresh_one_kept(self):  # gh#8
        caps = os.path.join(self.repo, ".git", "review-code", "captures")
        for age, kept in ((target.RETENTION_DAYS + 1, False), (0, True)):
            os.makedirs(caps, exist_ok=True)
            with open(os.path.join(caps, "aid.stop.json"), "w") as fh:
                fh.write("{}")
            os.utime(caps, (time.time() - age * 86400,) * 2)
            self.assertEqual(self.pin("feat~1..feat")[0], 0)
            self.assertEqual(os.path.exists(caps), kept)

    def test_a_write_deep_in_a_run_keeps_it_past_the_cutoff(self):  # review-code-j2w
        for write in (target.write_private, lambda p, t: target.write_atomic(p, t.encode())):
            run = self.mk("review-code.TOUCH1", target.RETENTION_DAYS + 1)
            os.makedirs(os.path.join(run, "post"))
            write(os.path.join(run, "post", "result.json"), "{}")
            target._prune(os.path.dirname(run))
            self.assertTrue(os.path.exists(run))
            shutil.rmtree(run)

    def test_a_refused_target_prunes_nothing(self):
        old = self.mk("review-code.OLD123", target.RETENTION_DAYS + 1)
        rc, out = self.pin("--staged")  # nothing staged: refused before a run directory is made
        self.assertEqual(out["status"], "skip")
        self.assertTrue(os.path.exists(old))


class TestWordsFromStdin(Local):
    """WR-08: target words reach the script as data on stdin, so no shell layer ever parses them."""

    HOSTILE = "dir/a;b $(touch PWNED) `touch PWNED` & 'q' \"d\".txt"

    def stdin_pin(self, text, *flags):
        with mock.patch.object(sys, "stdin", io.StringIO(text)):
            rc, out = call(list(flags) + ["--stdin"], self.repo, self.tmp)
        return rc, out

    def test_stdin_words_equal_argv_words(self):
        rc, out = self.stdin_pin("src/x.py\n")
        self.assertEqual((rc, out["kind"]), (0, "path"))
        self.assertEqual(load(out["run"])["included"], ["src/x.py"])
        rc, out = self.stdin_pin("src/x.py\nsrc/y.py\n\n")  # blank lines are not words
        self.assertEqual(load(out["run"])["included"], ["src/x.py", "src/y.py"])

    def test_stdin_with_choice_and_flags(self):
        gitfix.commit(self.repo, {"main/m.txt": "m\n"}, "dir named like a branch")
        rc, out = self.stdin_pin("main\n")
        self.assertEqual((rc, out["status"]), (2, "ambiguous"))
        rc, out = self.stdin_pin("main\n", "--choice", "2")
        self.assertEqual((rc, out["kind"]), (0, "path"))
        rc, out = self.stdin_pin("", "--staged")
        self.assertEqual(out["reason"], "empty-diff")

    def test_shell_metacharacters_arrive_as_one_word(self):
        # the exact shape SKILL.md prescribes: a quoted here-document, nothing expanded
        gitfix.commit(self.repo, {self.HOSTILE: "h\n"}, "odd name")
        script = "python3 %s --stdin <<'REVIEW_WORDS'\n%s\nREVIEW_WORDS\n" % (
            os.path.join(SCRIPTS, "target.py"), self.HOSTILE)
        e = gitfix.env()
        e["TMPDIR"] = self.tmp
        p = subprocess.run(["sh", "-c", script], cwd=self.repo, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out = json.loads(p.stdout.decode())
        self.assertEqual((p.returncode, out["status"], out["kind"]), (0, "ok", "path"), p.stdout + p.stderr)
        self.assertEqual(load(out["run"])["included"], [self.HOSTILE])
        self.assertEqual([n for n in os.listdir(self.repo) if "PWNED" in n], [])


class TestMR(PRWorld):
    URL = "https://gitlab.com/g/s/r.git"
    MR = "https://gitlab.com/g/s/r/-/merge_requests/5"

    def setUp(self):
        super().setUp()
        self.git("remote", "set-url", "origin", self.URL)
        self.git("config", "--unset", "url." + self.bare + ".insteadOf")
        self.git("config", "url." + self.bare + ".insteadOf", self.URL)
        gitfix.run(["git", "update-ref", "refs/merge-requests/5/head", self.pr], self.bare)

    def mr(self, **kw):
        d = {"iid": 5, "state": "opened", "draft": False, "target_branch": "main", "web_url": self.MR,
             "diff_refs": {"base_sha": self.main, "head_sha": self.pr, "start_sha": self.main}}
        d.update(kw)
        return mock.Mock(return_value=d)

    def test_mr_pin_open(self):
        refs = self.git("for-each-ref")
        for arg in ("!5", self.MR):
            glab = self.mr()
            rc, out = call([arg], self.repo, self.tmp, glab=glab)
            self.assertEqual((rc, out["status"], out["kind"], out["number"]), (0, "ok", "mr", 5), arg)
            self.assertEqual(glab.call_args[0][0][:2], ["mr", "view"])
        t = load(out["run"])
        self.assertEqual((t["head_sha"], t["base_sha"], t["url"]), (self.pr, self.main, self.MR))
        self.assertEqual(t["notes"], ["merge request reading is untested against a live glab"])
        self.assertEqual(self.git("for-each-ref"), refs)

    def test_mr_eligibility(self):
        before = self.run_dirs()
        for kw, reason in (({"state": "closed"}, "closed"), ({"state": "merged"}, "merged"),
                           ({"state": "locked"}, "locked"), ({"draft": True}, "draft")):
            rc, out = call(["!5"], self.repo, self.tmp, glab=self.mr(**kw))
            self.assertEqual((rc, out["status"], out["reason"]), (1, "skip", reason), kw)
        self.assertEqual(self.run_dirs(), before)
        self.assertFalse(self.has_pr_objects())
        rc, out = call(["!5"], self.repo, self.tmp, glab=self.mr(state="weird"))
        self.assertEqual((rc, out["reason"]), (1, "glab-failed"))
        rc, out = call(["!5"], self.repo, self.tmp,
                       glab=self.mr(diff_refs={"base_sha": "a" * 39, "head_sha": self.pr}))
        self.assertEqual(out["reason"], "glab-failed")
        rc, out = call(["https://gitlab.com/other/x/-/merge_requests/5"], self.repo, self.tmp)
        self.assertEqual(out["reason"], "cross-repo")

    def test_mr_glab_missing(self):
        with mock.patch.dict(os.environ, {"PATH": ""}):
            with self.assertRaises(target.Refuse) as cm:
                target.glab_json(["mr", "view", "5"], self.repo)
        self.assertEqual(cm.exception.reason, "glab-missing")


if __name__ == "__main__":
    unittest.main()


class TestFilter(Local):
    """CTX-07: noise files leave the review with a stated reason; the change cannot hide new code."""

    def change(self, files, message="change"):
        self.git("checkout", "-q", "-b", "chg", "main")
        return gitfix.commit(self.repo, files, message)

    def excluded(self, out):
        return {e["path"]: (e["reason"], e["detail"]) for e in load(out["run"])["excluded"]}

    def test_filter_lockfile(self):
        self.change({"package-lock.json": "{}\n", "src/app.js": "x = 1\n"})
        rc, out = self.pin("main")
        self.assertEqual((rc, out["included_count"], out["excluded_count"]), (0, 1, 1))
        t = load(out["run"])
        self.assertEqual(t["included"], ["src/app.js"])
        self.assertEqual(t["excluded"], [{"path": "package-lock.json", "reason": "lockfile", "detail": "basename"}])
        text = self.diff(out)
        self.assertIn("src/app.js", text)
        self.assertNotIn("package-lock.json", text)

    def test_filter_only_excluded(self):
        self.change({"yarn.lock": "y\n", "Cargo.lock": "c\n"})
        before = self.run_dirs()
        rc, out = self.pin("main")
        self.assertEqual((rc, out["status"], out["reason"]), (1, "skip", "only-excluded"))
        self.assertEqual(self.run_dirs(), before)

    def test_filter_diff_chunks(self):
        self.change({"Cargo.lock": "c\n", "src/a.js": "a\n", "src/b.js": "b\n", "src/c.js": "c\n"})
        with mock.patch.object(target, "_ARGV_BUDGET", 8):  # one path per git call
            rc, out = self.pin("main")
        self.assertEqual(rc, 0)
        text = self.diff(out)
        for name in ("src/a.js", "src/b.js", "src/c.js"):
            self.assertEqual(text.count("diff --git a/%s " % name), 1)
        self.assertNotIn("Cargo.lock", text)

    def base(self, files):
        """Commit on main, then branch the change from it."""
        self.git("checkout", "-q", "main")
        gitfix.commit(self.repo, files, "base")

    def notes(self, out):
        return load(out["run"])["notes"]

    def test_filter_minified(self):
        self.base({"a.min.js": "a\n", "b.min.css": "b\n", "c.map": "{}\n", "long.js": "x" * 1001 + "\n"})
        self.change({"a.min.js": "a2\n", "b.min.css": "b2\n", "c.map": "{1}\n", "long.js": "y" * 1001 + "\n",
                     "edge.js": "x" * 1000 + "\n"})
        rc, out = self.pin("main")
        self.assertEqual(self.excluded(out), {
            "a.min.js": ("minified", "name pattern"), "b.min.css": ("minified", "name pattern"),
            "c.map": ("minified", "name pattern"), "long.js": ("minified", "longest line 1001")})
        self.assertEqual(load(out["run"])["included"], ["edge.js"])

    def test_non_utf8_file_name_round_trips(self):
        # IN-05: one decoding policy for names, so the listed name matches what git holds
        name = os.fsdecode(b"bad\xff.txt")
        try:
            self.change({name: "x\n"})
        except OSError:
            self.skipTest("file system refuses non-UTF-8 names")
        rc, out = self.pin("main")
        # 6xl.60: the undecodable byte is a hidden name: listed escaped and flagged; ledger.py maps it back
        t = load(out["run"])
        self.assertEqual((rc, t["included"]), (0, ["bad<BYTES:1>.txt"]))
        self.assertIn({"file": "bad<BYTES:1>.txt", "name": True, "codepoints": ["BYTES:1"]}, t["hidden_chars"])

    def test_forge_key_ignores_web_port(self):
        self.assertEqual(target._forge_key("ghe.example:8443", "O/R"),
                         target._repo_key("git@ghe.example:O/R.git"))
        self.assertEqual(target._forge_key("ghe.example:8443", "O/R"),
                         target._repo_key("ssh://git@ghe.example:22/O/R"))

    def test_run_files_are_private(self):
        self.change({"src/a.py": "a = 1\n"})
        rc, out = self.pin("main")
        for name in ("change.diff", "target.json"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(out["run"], name)).st_mode), 0o600, name)

    def test_diff_prefixes_pinned_against_user_config(self):
        # WR-05: ledger.py strips two characters from ---/+++ paths, so a/ and b/ must not depend on config
        self.git("config", "diff.noprefix", "true")
        self.change({"src/a.py": "a = 1\n"})
        rc, out = self.pin("main")
        self.assertIn("+++ b/src/a.py", self.diff(out))
        self.write("new.txt", "u\n")
        self.git("checkout", "-q", "main")
        rc, out = self.pin("--unstaged")
        self.assertIn("+++ b/new.txt", self.diff(out))

    def test_filter_added_noise_names_are_kept(self):
        # CR-02: the change cannot hide a file it adds behind a name or a long line
        self.change({"evil.min.js": "evil()\n", "src/b.py": "x = '" + "a" * 1200 + "'\nrun()\n"})
        rc, out = self.pin("main")
        self.assertEqual((rc, out["status"]), (0, "ok"))
        self.assertEqual(load(out["run"])["included"], ["evil.min.js", "src/b.py"])
        self.assertEqual(self.excluded(out), {})
        self.assertIn("evil.min.js: kept", " ".join(self.notes(out)))

    def test_filter_added_long_js_is_kept(self):
        self.change({"app.js": "x = '" + "a" * 1200 + "'\n"})
        rc, out = self.pin("main")
        self.assertEqual((rc, out["status"]), (0, "ok"))
        self.assertEqual(load(out["run"])["included"], ["app.js"])

    def test_filter_attr_from_base_only(self):
        self.change({".gitattributes": "evil.js linguist-generated\nevil.js -diff\n", "evil.js": "evil()\n"})
        rc, out = self.pin("main")
        self.assertEqual(self.excluded(out), {})
        self.assertEqual(load(out["run"])["included"], [".gitattributes", "evil.js"])
        text = self.diff(out)  # the change's own -diff must not turn its file into "Binary files differ"
        self.assertIn("+evil()", text)
        self.assertNotIn("Binary files", text)

    def test_filter_header_marker(self):
        marker = "// Code generated by protoc. DO NOT EDIT.\n"
        filler = "// x\n"
        self.base({"api.go": filler * 9 + marker + "package a\n", "plain.go": filler * 10 + marker + "package p\n",
                   "own.go": "package o\n"})
        self.change({"api.go": filler * 9 + marker + "package a2\n", "plain.go": filler * 10 + marker + "package p2\n",
                     "own.go": marker + "package o2\n", "fresh.go": marker + "package f\n"})
        rc, out = self.pin("main")
        self.assertEqual(self.excluded(out), {"api.go": ("generated", "header line 10")})
        self.assertEqual(load(out["run"])["included"], ["fresh.go", "own.go", "plain.go"])
        self.assertIn("fresh.go: kept; it matches the header rule but the change adds it", self.notes(out))

    def test_filter_path_rules(self):
        self.base({"dist/app.js": "a\n", "node_modules/x/i.js": "i\n", "api.pb.go": "p\n",
                   "db/migrations/0001.sql": "-- auto-generated\nselect 1;\n"})
        self.change({"dist/app.js": "b\n", "node_modules/x/i.js": "j\n", "api.pb.go": "q\n", "dist/new.js": "n\n",
                     "db/migrations/0001.sql": "-- auto-generated\nselect 2;\n"})
        rc, out = self.pin("main")
        self.assertEqual(self.excluded(out), {
            "dist/app.js": ("generated", "path rule dist/"), "node_modules/x/i.js": ("vendored", "path rule node_modules/"),
            "api.pb.go": ("generated", "path rule *.pb.go")})
        self.assertEqual(load(out["run"])["included"], ["db/migrations/0001.sql", "dist/new.js"])
        self.assertIn("dist/new.js: kept; it matches the path rule but the change adds it", self.notes(out))

    def test_filter_binary(self):
        self.change({"logo.png": b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR", "src/app.js": "x\n"})
        rc, out = self.pin("main")
        self.assertEqual(self.excluded(out), {"logo.png": ("binary", "NUL byte")})
        self.assertEqual(load(out["run"])["included"], ["src/app.js"])
        self.write("a.txt", "edited\n")  # an untracked binary has no numstat row
        with open(os.path.join(self.repo, "u.bin"), "wb") as fh:
            fh.write(b"\0\1\2")
        rc, out = self.pin("--unstaged")
        self.assertEqual(self.excluded(out), {"u.bin": ("binary", "NUL byte")})
        self.assertEqual(load(out["run"])["included"], ["a.txt"])

    def test_filter_explicit_path(self):
        gitfix.commit(self.repo, {"package-lock.json": "{}\n", "src/ok.js": "ok\n"}, "lock")
        rc, out = self.pin("package-lock.json")
        self.assertEqual((rc, out["excluded_count"], load(out["run"])["included"]), (0, 0, ["package-lock.json"]))
        rc, out = self.pin(".")
        self.assertEqual(self.excluded(out), {"package-lock.json": ("lockfile", "basename")})
        self.assertNotIn("package-lock.json", load(out["run"])["included"])

class TestOddNames(Local):
    def test_tab_and_newline_names_listed_in_every_diff_kind(self):
        # 6xl.72: a TAB (or LF, quote) in a changed name must not drop it from the numstat -z listing
        names = ["t\tb.py", "l\nf.py", 'q"t.py']
        self.git("checkout", "-q", "main")
        gitfix.commit(self.repo, {n: "x = 1\n" for n in names}, "odd")
        self.git("checkout", "-q", "-b", "chg")
        gitfix.commit(self.repo, dict({n: "x = 2\n" for n in names}, **{"package-lock.json": "{}\n"}), "edit")
        rc, out = self.pin("main")
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(n for n in load(out["run"])["included"] if n in names), sorted(names))
        for n in names:
            self.write(n, "x = 3\n")
        for words in (["--unstaged"], ["--staged"]):
            if words == ["--staged"]:
                self.git("add", "--", *names)
            rc, out = self.pin(*words)
            self.assertEqual(rc, 0, (words, out))
            self.assertEqual(sorted(load(out["run"])["included"]), sorted(names), words)


class TestQuotedProseNames(Local):
    NAMES = ["b\\s.md", 'q"t.md', "e\x1bc.md", "l\nf.md", "t\tb.md", "\u00fc.md", "sp ace.md"]

    @staticmethod
    def doc(edit=None):
        """A comment opened on line 1 that runs past line 20: an edit inside it shows its text without the opener."""
        body = "".join("QUOTECANARY %d\n" % i for i in range(30))
        return "<!--\n" + (body.replace("CANARY %d\n" % edit, "CANARY %d x\n" % edit) if edit else body) + "-->\nend\n"

    def test_quoted_prose_names_get_whole_file_context(self):
        # 6xl.70: git C-quotes these names in diff headers; the comment opened above the hunk must still go
        self.git("checkout", "-q", "main")
        gitfix.commit(self.repo, {n: self.doc() for n in self.NAMES}, "docs")
        self.git("checkout", "-q", "-b", "chg")
        gitfix.commit(self.repo, {n: self.doc(15) for n in self.NAMES}, "edit")
        rc, out = self.pin("main")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("QUOTECANARY", self.diff(out))
        for n in self.NAMES:
            self.write(n, self.doc(16))
        rc, out = self.pin("--unstaged")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("QUOTECANARY", self.diff(out))
        self.assertEqual(len(load(out["run"])["included"]), len(self.NAMES))


class TestTooLarge(Local):
    def excluded(self, out):
        return {e["path"]: (e["reason"], e["detail"]) for e in load(out["run"])["excluded"]}

    def change(self, files):
        self.git("checkout", "-q", "-b", "chg", "main")
        return gitfix.commit(self.repo, files, "change")

    def test_only_oversized_file_reports_its_size(self):
        # 6xl.89: a run whose one file is over the cap says so, with the size (no name on stdout)
        self.change({"big.py": "v = 1\n" * (target.MAX_FILE // 6 + 1)})
        rc, out = self.pin("main")
        self.assertEqual((rc, out["reason"]), (1, "only-excluded"))
        self.assertIn("too-large", out["detail"])
        self.assertIn("%d bytes" % (6 * (target.MAX_FILE // 6 + 1)), out["detail"])
        self.assertNotIn("big.py", out["detail"])

class TestDotTarget(Local):
    def test_dot_is_the_current_directory(self):
        # 6xl.78: '.' from a subdirectory means that directory, like every other path word
        rc, out = call(["."], os.path.join(self.repo, "src"), self.tmp)
        self.assertEqual((rc, out["kind"]), (0, "path"), out)
        self.assertTrue(all(n.startswith("src/") for n in load(out["run"])["included"]), load(out["run"])["included"])
        rc, out = call(["."], self.repo, self.tmp)
        self.assertEqual((rc, out["kind"]), (0, "repo"), out)


class TestBlobsByOid(Local):
    """6xl.82/.83: blobs are read by object id, so LF/CR-suffixed names get their own size, NUL and reasons."""

    def excluded(self, out):
        return {e["path"]: (e["reason"], e["detail"]) for e in load(out["run"])["excluded"]}

    def test_lf_and_cr_names_get_cap_and_nul_rule(self):
        line = "v = '" + "a" * 90 + "'\n"
        big = line * (target.MAX_FILE * 2 // len(line))
        self.git("checkout", "-q", "-b", "chg", "main")
        gitfix.commit(self.repo, {"big\n.py": big, "nul\r": b"x\x00y\n", "ok.py": "o = 1\n"}, "odd")
        for words in (["main"], ["HEAD^!"]):
            rc, out = self.pin(*words)
            self.assertEqual(rc, 0, out)
            ex = self.excluded(out)
            self.assertEqual(ex.get("big\n.py", ("",))[0], "too-large", (words, ex))
            self.assertEqual(ex.get("nul\r", ("",))[0], "binary", (words, ex))

    def test_cr_sibling_judged_by_its_own_content(self):
        self.git("checkout", "-q", "main")
        gitfix.commit(self.repo, {"logo.png": b"\x89PNG\r\n\x1a\n\x00\x00", "gen.py": "# @generated\ng = 1\n"}, "assets")
        self.git("checkout", "-q", "-b", "chg", "main")
        gitfix.commit(self.repo, {"logo.png\r": "HIDDEN_BEHIND_SIBLING = 1\n", "gen.py\r": "real = 1\n",
                                  "logo.png ": "s = 1\n", "gen.py\n": "n = 1\n"}, "sneak")
        for words in (["main"], ["HEAD^!"]):
            rc, out = self.pin(*words)
            self.assertEqual(rc, 0, out)
            self.assertEqual(self.excluded(out), {}, words)
            self.assertEqual(sorted(load(out["run"])["included"]), sorted(["gen.py\n", "gen.py\r", "logo.png ", "logo.png\r"]),
                             words)
        self.git("reset", "-q", "--soft", "main")
        self.git("reset", "-q")
        self.git("add", "--", "logo.png\r", "gen.py\r", "logo.png ", "gen.py\n")
        rc, out = self.pin("--staged")
        self.assertEqual((rc, self.excluded(out)), (0, {}), out)


class TestNonUtf8ProseName(Local):
    def test_non_utf8_prose_name_gets_whole_file_context(self):
        # 6xl.79: the diff header carries the raw byte; it must match the name the listing decoded
        name = os.fsdecode(b"caf\xe9.md")
        doc = lambda e=None: "<!--\n" + "".join("U8CANARY %d%s\n" % (i, " x" if i == e else "") for i in range(30)) + "-->\n"
        self.git("checkout", "-q", "main")
        try:
            gitfix.commit(self.repo, {name: doc()}, "doc")
        except OSError:
            self.skipTest("file system refuses non-UTF-8 names")
        self.git("checkout", "-q", "-b", "chg")
        gitfix.commit(self.repo, {name: doc(15)}, "edit")
        for words in (["main"], ["HEAD^!"]):
            rc, out = self.pin(*words)
            self.assertEqual(rc, 0, out)
            self.assertNotIn("U8CANARY", self.diff(out), words)
        with open(os.path.join(os.fsencode(self.repo), b"caf\xe9.md"), "w") as fh:
            fh.write(doc(17))
        rc, out = self.pin("--unstaged")
        self.assertNotIn("U8CANARY", self.diff(out))


class TestSidesPerKind(Local):
    """6xl.88: --unstaged compares the index with the working copy, so its base side is the index, not HEAD."""

    def excluded(self, out):
        return {e["path"]: e["reason"] for e in load(out["run"])["excluded"]}

    def test_unstaged_base_is_the_index(self):
        self.git("checkout", "-q", "main")
        big = "x = 1\n" * (target.MAX_FILE // 6 + 10)
        gitfix.commit(self.repo, {"logo.png": b"\x89PNG\x00\x00", "big.txt": big, "plain.md": "p\n"}, "head side")
        self.write("logo.png", "TEXT_IN_INDEX = 1\n")
        self.write("big.txt", "small\n")
        with open(os.path.join(self.repo, "plain.md"), "wb") as fh:
            fh.write(b"p\x00\n")
        self.write("keep.py", "k = 1\n")
        self.git("add", "logo.png", "big.txt", "plain.md", "keep.py")
        self.write("logo.png", "TEXT_IN_INDEX = 2\n")
        self.write("big.txt", "small 2\n")
        self.write("plain.md", "p2\n")
        rc, out = self.pin("--unstaged")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.excluded(out), {"plain.md": "binary"})  # the index side holds the NUL
        self.assertEqual(sorted(load(out["run"])["included"]), ["big.txt", "logo.png"])
        rc, out = self.pin("--staged")  # HEAD -> index: logo.png and big.txt changed to text, plain.md gained a NUL
        self.assertEqual(self.excluded(out), {"logo.png": "binary", "big.txt": "too-large", "plain.md": "binary"})
        rc, out = self.pin()  # HEAD -> working copy
        self.assertEqual((rc, out["kind"]), (0, "worktree"), out)
        self.assertEqual(self.excluded(out), {"logo.png": "binary", "big.txt": "too-large"})


class TestSpecialFiles(Local):
    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")
    def test_fifo_is_excluded_with_a_reason_and_never_opened(self):
        # 6xl.90: a tracked file the user replaced with a FIFO is listed as special, never read (no hang)
        self.git("checkout", "-q", "main")
        gitfix.commit(self.repo, {"pipe.txt": "p\n", "ok.py": "o = 1\n"}, "pipe")
        os.remove(os.path.join(self.repo, "pipe.txt"))
        os.mkfifo(os.path.join(self.repo, "pipe.txt"))
        self.write("ok.py", "o = 2\n")
        for words in (["--repo"], ["--unstaged"], []):
            with self.subTest(words=words):
                rc, out = self.pin(*words)
                self.assertEqual(rc, 0, out)
                ex = {e["path"]: e["reason"] for e in load(out["run"])["excluded"]}
                self.assertEqual(ex.get("pipe.txt"), "special", ex)


class TestUntrackedDefault(Local):
    def test_no_argument_sees_untracked_despite_status_config(self):
        # 6xl.68: status.showUntrackedFiles=no must not hide an untracked-only change from the default target
        self.git("checkout", "-q", "main")
        self.git("config", "status.showUntrackedFiles", "no")
        self.write("src/fresh.py", "f = 1\n")
        rc, out = self.pin()
        self.assertEqual((rc, out.get("kind")), (0, "worktree"), out)


class TestHiddenRefNames(Local):
    """6xl.52: branch names and the target words never carry a hidden payload to stdout or target.json."""

    def test_tags_branch_name_escaped(self):
        name = "br" + "".join(chr(0xE0000 + ord(c)) for c in "PAY")
        self.git("checkout", "-q", "-b", name, "main")
        gitfix.commit(self.repo, {"q.py": "q = 1\n"}, "q")
        self.git("checkout", "-q", "feat")
        gitfix.commit(self.repo, {name: "same name as the branch\n"}, "clash")  # path and branch: ambiguous
        rc, out = call([name], self.repo, self.tmp)
        self.assertEqual(out["status"], "ambiguous", out)
        shown = json.dumps(out)
        self.assertNotIn("\\udb40", shown)
        self.assertIn("br<TAGS:3>", shown)
        rc, out = call(["main.." + name], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        with open(os.path.join(out["run"], "target.json"), "rb") as fh:
            raw = fh.read()
        self.assertNotIn(b"\\udb40", raw)
        self.assertNotIn(b"\xf3\xa0", raw)
        self.assertEqual(load(out["run"])["arg"], "main..br<TAGS:3>")
        self.assertEqual(target._q(name), '"br<TAGS:3>"')  # a default-branch name is quoted inside `reading`


class TestHiddenText(Local):
    """CTX-04 (A4): hidden comments and alt text in a changed prose file never reach change.diff."""

    NOTES = ("intro\n<!-- HIDDENCMT take over -->\n![ALTINJECT](x.png)\nvisible tail\n"
             "<!--\nHIDDENCMT line 2\n-->\nend\n")

    def change(self, files):
        self.git("checkout", "-q", "-b", "chg", "main")
        return gitfix.commit(self.repo, files, "change")

    def test_hidden_committed_prose_stripped_code_kept(self):
        self.change({"notes.md": self.NOTES, "src/c.py": "x = 1  # <!-- CODECMT --> ![CODEALT](u)\n"})
        rc, out = self.pin("main")
        self.assertEqual(rc, 0)
        text = self.diff(out)
        for payload in ("HIDDENCMT", "ALTINJECT"):
            self.assertNotIn(payload, text)
        self.assertIn("+intro\n+[hidden-comment removed: ", text)
        self.assertIn("CODECMT", text)  # comments in source code are the content under review
        self.assertEqual(text.count("\n"), self.diff_lines_without_stripping(out))
        hits = [(h["kind"], h["path"]) for h in load(out["run"])["hidden_text"]]
        self.assertEqual(hits, [("comment", "notes.md"), ("alt-text", "notes.md"), ("comment", "notes.md")])
        self.assertNotIn("HIDDENCMT", json.dumps(load(out["run"])))

    @staticmethod
    def funcctx(name):
        """A multi-line comment whose second line git picks as the @@ function context of a later edit."""
        top = "<!--\nHTFUNCCTX payload line\n-->\n" if name.endswith(".md") else "<html>\n<!--\nHTFUNCCTX payload line\n-->\n"
        return top + "".join("* item %d\n" % i for i in range(20))

    def test_hunk_header_context_never_carries_hidden_text(self):
        # 6xl.55: git writes the nearest preceding "function" line into each @@ header
        for name in ("guide.md", "page.html"):
            with self.subTest(name=name):
                self.git("checkout", "-q", "-f", "main")
                self.git("branch", "-q", "-D", "chg") if "chg" in self.git("branch") else None
                self.change({name: self.funcctx(name)})
                gitfix.commit(self.repo, {name: self.funcctx(name).replace("* item 15", "* item 15 edited")}, "edit")
                raw = gitfix.run(["git", "diff", "HEAD^", "HEAD"], self.repo)
                self.assertIn("HTFUNCCTX", raw)  # the fixture really triggers git's function context
                for words in (["HEAD^!"], ["main"], ["main..chg"]):
                    rc, out = self.pin(*words)
                    self.assertEqual(rc, 0, out)
                    self.assertNotIn("HTFUNCCTX", self.diff(out), words)
                    self.assertNotIn("HTFUNCCTX", json.dumps(load(out["run"])), words)
                self.write(name, self.funcctx(name).replace("* item 16", "* item 16 dirty"))
                rc, out = self.pin()
                self.assertEqual((rc, out["kind"]), (0, "worktree"), out)
                self.assertNotIn("HTFUNCCTX", self.diff(out))
                self.git("checkout", "-q", "--", name)

    def test_prose_renamed_to_code_strips_removed_comment(self):
        # 6xl.63: the removed lines of README.md are judged by the old path after a rename to notes.py
        body = "".join("line %d\n" % i for i in range(10))
        self.git("checkout", "-q", "main")
        gitfix.commit(self.repo, {"README.md": "<!-- RENCANARY -->\n" + body}, "readme")
        self.git("checkout", "-q", "-b", "chg", "main")
        self.git("mv", "README.md", "notes.py")
        gitfix.commit(self.repo, {"notes.py": body + "y = 1\n"}, "rename")
        rc, out = self.pin("main")
        self.assertEqual(rc, 0, out)
        text = self.diff(out)
        self.assertIn("rename from README.md", text)
        self.assertNotIn("RENCANARY", text)

    def test_untracked_attributes_cannot_hide_untracked_text(self):
        # 6xl.65: an untracked `.gitattributes` with `* -diff` must not turn untracked files into "Binary files differ"
        self.write(".gitattributes", "* -diff\n")
        self.write("src/new.py", "UNTRACKEDBODY = 1\n")
        rc, out = self.pin()
        self.assertEqual((rc, out["kind"]), (0, "worktree"), out)
        self.assertIn("+UNTRACKEDBODY = 1", self.diff(out))

    def test_rename_kept_when_other_files_are_excluded(self):
        # 6xl.67: an exclusion narrows the diff to kept paths; the old side of a kept rename must stay in it
        body = "".join("line %d\n" % i for i in range(20))
        self.git("checkout", "-q", "main")
        gitfix.commit(self.repo, {"docs/a.py": body, "package-lock.json": "{}\n"}, "pre")
        self.git("checkout", "-q", "-b", "chg", "main")
        self.git("mv", "docs/a.py", "docs/b.py")
        gitfix.commit(self.repo, {"docs/b.py": body.replace("line 9", "line 9 edited"), "package-lock.json": "{1}\n"},
                      "rename")
        rc, out = self.pin("main")
        self.assertEqual(rc, 0, out)
        t = load(out["run"])
        self.assertIn("package-lock.json", [e["path"] for e in t["excluded"]])
        text = self.diff(out)
        self.assertIn("rename from docs/a.py", text)
        self.assertNotIn("package-lock", text)

    def test_hunk_header_context_kept_for_code(self):
        self.change({"src/m.py": "def handler():\n" + "".join("    x%d = %d\n" % (i, i) for i in range(20))})
        gitfix.commit(self.repo, {"src/m.py": "def handler():\n" + "".join("    x%d = %d\n" % (i, i if i != 15 else 99)
                                                                         for i in range(20))}, "edit")
        rc, out = self.pin("HEAD^!")
        self.assertEqual(rc, 0, out)
        self.assertRegex(self.diff(out), r"@@ -\d+,\d+ \+\d+,\d+ @@ def handler\(\):")

    def diff_lines_without_stripping(self, out):
        t = load(out["run"])
        raw = gitfix.run(["git", "diff"] + target._DIFF_FLAGS + [t["merge_base"], t["head_sha"], "--"] + t["included"],
                         self.repo)
        return raw.count("\n")

    def test_hidden_comment_opened_above_the_hunk(self):
        body = "".join("line %d\n" % i for i in range(1, 11))
        self.git("checkout", "-q", "main")
        self.git("checkout", "-q", "-b", "base2")
        gitfix.commit(self.repo, {"doc.md": "<!--\n" + body + "-->\nafter\n"}, "doc")
        self.git("checkout", "-q", "-b", "chg2")
        gitfix.commit(self.repo, {"doc.md": "<!--\n" + body.replace("line 8\n", "line 8\nHIDDENCMT\n") + "-->\nafter\n"}, "inject")
        rc, out = self.pin("base2")
        self.assertEqual(rc, 0)
        self.assertNotIn("HIDDENCMT", self.diff(out))
        self.assertEqual([h["kind"] for h in load(out["run"])["hidden_text"]], ["comment"])  # opener is above the hunk

    def test_hidden_untracked_worktree_file(self):
        self.write("fresh.md", self.NOTES)
        rc, out = self.pin()
        self.assertEqual(rc, 0)
        self.assertNotIn("HIDDENCMT", self.diff(out))
        self.assertNotIn("ALTINJECT", self.diff(out))
        self.assertEqual([h["kind"] for h in load(out["run"])["hidden_text"]], ["comment", "alt-text", "comment"])

    def test_hidden_unstaged_edit_of_tracked_prose(self):
        self.git("checkout", "-q", "main")
        self.write("docs/d.md", "d\n<!-- HIDDENCMT -->\n")
        rc, out = self.pin()
        self.assertEqual(rc, 0)
        self.assertNotIn("HIDDENCMT", self.diff(out))
        self.assertEqual([h["kind"] for h in load(out["run"])["hidden_text"]], ["comment"])
