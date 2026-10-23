"""The integrity check (gh#51): what changed in the checkout, its index and tool knowledge while a review ran.
It reports and never blocks; driven through target.py, ledger.py and report.py on fixture repositories."""
import json
import os
import subprocess
import unittest
from unittest import mock

import paths  # noqa: F401

import gitfix  # noqa: E402
import test_pinned as tp  # noqa: E402
import test_post as tpost  # noqa: E402
import test_target as tt  # noqa: E402

cli = gitfix.cli
CAUSE = "changed during the run; hooks, index daemons or other sessions may be the cause"


class Integrity(tpost.Base):
    """A reviewed branch run; edits land between the snapshot (target.py) and the comparison (render)."""

    def setUp(self):
        super().setUp()
        tp.write(self.repo, ".wolf/memory.md", "read a.py\n")  # untracked knowledge, there before the run
        self.make_run()
        self.review(5)

    def render(self, fmt=None):
        rc, out, err = cli("report.py", ["render", "--run", self.run_dir] + (["--format", fmt] if fmt else []), self.repo, self.tmp)
        self.assertEqual((rc, err), (0, ""), out)
        return json.loads(out) if fmt else out

    def test_a_clean_run_reports_no_change_in_either_tier(self):
        self.assertIn("integrity: no change to the checkout, index or tool knowledge during the run", self.render())
        self.assertEqual(self.render("json")["integrity"], {"working_tree": [], "index": False, "knowledge": [], "cause": CAUSE})

    def test_a_tracked_edit_is_a_warning_with_its_path_and_cause(self):
        tp.write(self.repo, "src/calc.py", "edited\n")
        self.assertIn("integrity warning: src/calc.py (working tree) " + CAUSE, self.render().split("\n"))
        self.assertEqual(self.render("json")["integrity"]["working_tree"], ["src/calc.py"])

    def test_a_second_edit_to_a_file_already_dirty_is_seen(self):
        tp.write(self.repo, "src/calc.py", "dirty before the run\n")
        self.make_run()
        self.review(5)  # snapshot with the file already modified
        tp.write(self.repo, "src/calc.py", "dirty before the run, edited again during it\n")
        self.assertEqual(self.render("json")["integrity"]["working_tree"], ["src/calc.py"])

    def test_a_changed_index_is_a_warning(self):
        os.utime(os.path.join(self.repo, "src/calc.py"), ns=(1, 1))
        gitfix.run(["git", "update-index", "-q", "--refresh"], self.repo)  # rewrites the index's stat data; status output stays the same
        out = self.render()
        self.assertIn("integrity warning: index " + CAUSE, out.split("\n"))
        self.assertEqual(self.render("json")["integrity"]["working_tree"], [])

    def test_a_knowledge_write_is_informational_not_a_warning(self):
        tp.write(self.repo, ".wolf/memory.md", "read a.py\nread b.py\n")
        tp.write(self.repo, ".serena/new.json", "{}\n")
        out = self.render()
        self.assertIn("integrity note: .wolf/memory.md " + CAUSE, out.split("\n"))
        self.assertNotIn("integrity warning", out)
        got = self.render("json")["integrity"]
        self.assertEqual((got["working_tree"], got["index"], got["knowledge"]), ([], False, [".serena/new.json", ".wolf/memory.md"]))

    def test_a_tracked_knowledge_file_is_informational_too(self):
        gitfix.commit(self.repo, {".codegraph/meta.txt": "v1\n"}, "track it")
        self.make_run()
        self.review(5)
        tp.write(self.repo, ".codegraph/meta.txt", "v2\n")
        got = self.render("json")["integrity"]
        self.assertEqual((got["working_tree"], got["knowledge"]), ([], [".codegraph/meta.txt"]))

    def test_a_detected_change_alters_neither_exit_status_nor_findings(self):
        before = self.render("json")
        tp.write(self.repo, "src/calc.py", "edited\n")
        tp.write(self.repo, ".wolf/memory.md", "more\n")
        after = self.render("json")  # render asserts exit status 0
        self.assertTrue(after["integrity"]["working_tree"])
        self.assertEqual({k: v for k, v in before.items() if k != "integrity"}, {k: v for k, v in after.items() if k != "integrity"})

    def test_the_ledger_records_where_the_snapshot_is(self):
        led = tp.load(self.run_dir, "ledger.json")["target"]
        self.assertEqual(led["integrity"], os.path.join(self.run_dir, "integrity.json"))
        self.assertEqual(tp.load(self.run_dir)["integrity"], led["integrity"])


class RenderProcessCount(tpost.Base):
    def count(self, n):
        self.make_run()
        self.review(5)
        for i in range(n):
            tp.write(self.repo, "new/f%02d.txt" % i, "x\n")
        log = os.path.join(self.tmp, "render%d.json" % n)
        rc, out, err = cli("report.py", ["render", "--run", self.run_dir], self.repo, self.tmp, extra={"GIT_TRACE2_EVENT": log})
        self.assertEqual(rc, 0, out + err)
        self.assertIn("integrity warning: new/ (working tree) " + CAUSE, out.split("\n"))
        return tp.starts(log)

    def test_the_comparison_runs_one_status_and_no_per_file_process(self):
        two, twenty = self.count(2), self.count(20)
        self.assertEqual(len(two), len(twenty))
        for argv in (two, twenty):
            self.assertEqual(sum(1 for a in argv if "status" in a), 1)
            self.assertEqual(sum(1 for a in argv if "--porcelain=v2" in a and "-z" in a), 1)


class LargeSnapshot(tp.LargeRepo):
    """10k tracked files: the snapshot records stat data and digests, never file content, from one status call."""

    test_a_three_file_change_records_three_paths_and_reads_no_other_blob = None  # runs in test_pinned

    def test_the_snapshot_reads_no_content_and_runs_one_status(self):
        tp.write(self.repo, ".wolf/locked.md", "secret\n")
        os.chmod(os.path.join(self.repo, ".wolf/locked.md"), 0)  # stat works, a read would fail
        self.addCleanup(os.chmod, os.path.join(self.repo, ".wolf/locked.md"), 0o600)
        log = os.path.join(self.tmp, "trace.json")
        with mock.patch.dict(os.environ, {"GIT_TRACE2_EVENT": log}):
            rc, out = tt.call(["main..feat"], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        self.assertEqual(sum(1 for a in tp.starts(log) if "--porcelain=v2" in a), 1)
        path = os.path.join(out["run"], "integrity.json")
        snap = tp.load(out["run"], "integrity.json")
        self.assertEqual(sorted(snap), ["dirty", "index", "knowledge", "status", "version"])
        self.assertEqual(set(snap["dirty"]), {".wolf/"})  # only the drift: no entry for any clean tracked file
        self.assertEqual(snap["knowledge"], {".wolf/locked.md": [7, os.lstat(os.path.join(self.repo, ".wolf/locked.md")).st_mtime_ns]})
        self.assertLess(os.path.getsize(path), 1024)
        self.assertNotIn("secret", open(path, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
