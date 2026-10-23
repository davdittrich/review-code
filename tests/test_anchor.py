"""review-code-0t6j: the lane-facing difference list is one path per line, so a lane can anchor it."""
import os
import re
import unittest

import paths  # noqa: F401

import gitfix  # noqa: E402
import test_pinned as tp  # noqa: E402
import test_target as tt  # noqa: E402


class Anchorable(tt.Base):
    """review-code-0t6j, review-code-98hy: the lane-facing difference list holds one path per line, nothing else, so a lane
    anchors `^path$`; a longer path (lib/src/a.py, src/a.py.orig) cannot match, and no JSON escaping hides a name."""
    NAMES = ["src/a.py", "lib/src/a.py", "src/a.py.orig", "café.py", "日本.py", 'q"x.py', "b\\s.py", "n\ny.py"]

    def setUp(self):
        super().setUp()
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "répo"))
        gitfix.commit(self.repo, {"a.py": "a = 1\n", **{n: "x = 1\n" for n in self.NAMES}}, "base")
        for n in self.NAMES:
            tp.write(self.repo, n, "x = 2\n")
        tp.write(self.repo, "a.py", "a = 2\n")
        tp.raw(self.repo, "add", "a.py")
        rc, out = tt.call(["--staged"], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        self.run_dir = out["run"]
        self.tj = tp.load(self.run_dir)

    def lines(self):
        with open(self.tj["difference_list"], encoding="utf-8") as fh:
            return fh.read().split("\n")

    def test_the_list_holds_each_visible_name_as_written_one_per_line(self):
        want = sorted(["café.py", "日本.py", 'q"x.py', "b\\s.py", "src/a.py", "lib/src/a.py", "src/a.py.orig", "n<U+000A>y.py"])
        self.assertEqual(self.lines(), want + [""])
        self.assertEqual(self.tj["difference_count"], len(want))

    def test_an_anchored_pattern_hits_the_one_path_and_no_longer_one(self):
        for name in ("src/a.py", 'q"x.py', "b\\s.py", "café.py"):
            hit = [ln for ln in self.lines() if re.search("^" + re.escape(name) + "$", ln)]
            self.assertEqual(hit, [name], name)
        self.assertGreater(len([ln for ln in self.lines() if re.search(re.escape("src/a.py"), ln)]), 1)  # unanchored: the bug

    def test_the_lane_input_points_at_the_list_not_the_json(self):
        diff = self.tj["difference_list"]
        self.assertNotEqual(diff, self.tj["difference"])
        rc, out, err = tp.cli("ledger.py", ["build", "--run", self.run_dir], self.repo, self.tmp)
        self.assertEqual(rc, 0, out + err)
        with open(os.path.join(self.run_dir, "inputs", "defects.md"), encoding="utf-8") as fh:
            self.assertIn("checkout_difference: %s (%d paths)" % (diff, self.tj["difference_count"]), fh.read())


if __name__ == "__main__":
    unittest.main()
