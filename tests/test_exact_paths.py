"""review-code-d9os/c0c0/51ru: a path or ref name read from git or the forge keeps its exact bytes (surrogateescape)."""
import os
import tempfile
import unittest
from unittest import mock

import paths  # noqa: F401

import gitfix  # noqa: E402
import post  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
