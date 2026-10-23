"""review-code-d9os/c0c0/51ru: a path or ref name read from git or the forge keeps its exact bytes (surrogateescape)."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import paths  # noqa: F401

import fix  # noqa: E402
import gitfix  # noqa: E402
import ledger  # noqa: E402
import plan  # noqa: E402
import post  # noqa: E402
import target  # noqa: E402

NAME = os.fsdecode(b"d\xffx.py")  # byte 0xff on disk, lone surrogate in a str


class ChangedPaths(unittest.TestCase):
    def test_changed_lists_a_non_utf8_path_exactly(self):  # d9os, post._changed
        with tempfile.TemporaryDirectory() as tmp:
            repo = gitfix.make_repo(os.path.join(tmp, "r"))
            a = gitfix.commit(repo, {"keep.py": "k\n", NAME: "1\n"}, "base")
            b = gitfix.commit(repo, {NAME: "2\n"}, "change")
            with mock.patch.object(post.target, "load_target", return_value={"repo": repo}):
                self.assertEqual(post._changed("run", a, b), {NAME})


class ForgeDiff(unittest.TestCase):
    RAW = (b"diff --git a/d\xffx.py b/d\xffx.py\n--- a/d\xffx.py\n+++ b/d\xffx.py\n@@ -1 +1 @@\n-old \xfe\n+new \xfe\n")

    def test_the_pr_diff_names_its_file_exactly_and_decodes_content_lossily(self):  # c0c0, post.GitHub.diff
        gh = post.GitHub({"repo": "o/r", "number": 7, "url": "https://github.com/o/r/pull/7", "run": "."})
        with mock.patch.object(post, "_cli", return_value=(0, self.RAW, b"")):
            text = gh.diff()
        self.assertIn("+++ b/" + NAME + "\n", text)
        self.assertIn("+new \ufffd\n", text)
        self.assertEqual(post._anchors(text)[(NAME, 1)][0], "+")


class GitCallers(unittest.TestCase):
    """review-code-51ru: every target.git() caller that carries a path or ref name reads it exactly."""

    def repo(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = os.path.realpath(td.name)
        r = gitfix.make_repo(os.path.join(self.tmp, "r"))
        gitfix.commit(r, {"a": "1\n"}, "base")
        return r

    def test_the_ref_digest_tells_two_non_utf8_branch_names_apart(self):  # fix._refs
        r = self.repo()
        gitfix.run(["git", "branch", os.fsdecode(b"x\xfe")], r)
        before = fix._refs(r)
        gitfix.run(["git", "branch", "-m", os.fsdecode(b"x\xfe"), os.fsdecode(b"x\xff")], r)
        self.assertNotEqual(fix._refs(r), before)  # both names are U+FFFD under errors=replace

    def test_churn_counts_a_non_utf8_path(self):  # plan._churn
        r = self.repo()
        gitfix.commit(r, {NAME: "1\n2\n"}, "add")
        with mock.patch.object(plan, "CHURN_DAYS", 100000):
            self.assertEqual(plan._churn(r, {NAME}), {NAME: 2})

    def test_checkout_root_finds_a_worktree_whose_path_is_non_utf8(self):  # target._checkout_root
        r = self.repo()
        head = gitfix.run(["git", "rev-parse", "HEAD"], r).strip()
        w = os.path.join(self.tmp, os.fsdecode(b"w\xffd"))
        gitfix.run(["git", "worktree", "add", "-q", "--detach", w, head], r)
        gitfix.run(["git", "checkout", "-q", "--detach", gitfix.commit(r, {"b": "2\n"}, "moved")], r)
        self.assertEqual(target._checkout_root(r, head), os.path.realpath(w))


class SeparateGitDir(gitfix.FeatureRepo):
    """A repository whose git directory name holds byte 0xff: `rev-parse --git-path` prints it absolute."""
    base_files, feat_files = {"a": "1\n"}, {"b": "2\n"}

    def test_git_files_exist(self):  # fix._git_files; the run-directory flow cannot reach fix.baseline here (ledger write)
        gd = os.path.join(self.tmp, os.fsdecode(b"gd\xff"))
        gitfix.run(["git", "init", "-q", "--separate-git-dir", gd, self.repo], self.repo)
        files = fix._git_files(self.repo)
        self.assertEqual(len(files), 3)
        self.assertTrue(all(f.startswith(gd) for f in files), files)
        self.assertTrue(os.path.exists(files[0]), files)


class NonUtf8Refusal(unittest.TestCase):
    """review-code-n5xl: a run path that cannot be written as UTF-8 is refused with one reason, before any write."""

    def test_ledger_build_refuses_a_run_directory_inside_a_non_utf8_git_dir(self):  # ledger.build
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)
            gd, repo = os.path.join(tmp, os.fsdecode(b"gd\xff")), os.path.join(tmp, "work")
            os.makedirs(repo)
            gitfix.run(["git", "init", "-q", "-b", "main", "--separate-git-dir", gd, repo], tmp)
            gitfix.commit(repo, {"a.py": "a = 0\n"}, "base")
            with open(os.path.join(repo, "a.py"), "w", encoding="utf-8") as fh:
                fh.write("a = 1\n")
            buf = io.StringIO()
            with gitfix.isolated(tmp), redirect_stdout(buf):
                self.assertEqual(target.main([], repo), 0)
                run = json.loads(buf.getvalue())["run"]
                buf.truncate(0), buf.seek(0)
                self.assertEqual(ledger.main(["build", "--run", run]), 1)
            self.assertEqual(json.loads(buf.getvalue()), {"reason": "non-utf8-name", "status": "refuse"})


if __name__ == "__main__":
    unittest.main()
