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


if __name__ == "__main__":
    unittest.main()
