"""The integrity check (gh#51): what changed in the checkout, its index and tool knowledge while a review ran.
It reports and never blocks; driven through target.py, ledger.py and report.py on fixture repositories."""
import hashlib
import json
import os
import subprocess
import unittest
from unittest import mock

import paths  # noqa: F401

import gitfix  # noqa: E402
import report  # noqa: E402
import target  # noqa: E402
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

    CLEAN = ("integrity: no change to the checkout, index or tool knowledge during the run "
             "(not seen: submodule worktrees, nested repositories)")

    def test_a_clean_run_reports_no_change_in_either_tier(self):
        self.assertIn(self.CLEAN, self.render().split("\n"))
        self.assertEqual(self.render("json")["integrity"], {"working_tree": [], "index": False, "knowledge": [], "cause": CAUSE})

    def test_an_edit_inside_an_untracked_directory_is_seen(self):  # review-code-71de.2 (was the 8434 blind spot)
        tp.write(self.repo, "scratch/notes.txt", "one\n")  # normal mode collapsed the wholly untracked directory to one entry
        self.make_run()
        self.review(5)
        tp.write(self.repo, "scratch/notes.txt", "one, two, three, four\n")
        self.assertEqual(self.render("json")["integrity"]["working_tree"], ["scratch/notes.txt"])

    def test_an_edit_inside_a_nested_repository_is_a_blind_spot_the_line_names(self):  # -uall still lists it as one entry
        gitfix.make_repo(os.path.join(self.repo, "nested"))
        tp.write(self.repo, "nested/a.txt", "one\n")
        self.make_run()
        self.review(5)
        tp.write(self.repo, "nested/a.txt", "one, two, three, four\n")
        self.assertEqual(self.render("json")["integrity"]["working_tree"], [])
        self.assertIn(self.CLEAN, self.render().split("\n"))

    def test_an_edit_inside_a_submodule_worktree_is_a_blind_spot_the_line_names(self):  # review-code-exrf
        sub = gitfix.make_repo(os.path.join(self.tmp, "subsrc"))
        gitfix.commit(sub, {"lib.py": "x = 1\n"}, "sub base")
        gitfix.run(["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", sub, "vendor"], self.repo)
        gitfix.run(["git", "commit", "-q", "-m", "add submodule"], self.repo)
        self.make_run()
        self.review(5)
        tp.write(self.repo, "vendor/lib.py", "x = 2\n")  # modified content inside the submodule: status =dirty hides it
        tp.write(self.repo, "vendor/new.py", "y = 1\n")
        out = self.render()
        self.assertEqual(self.render("json")["integrity"]["working_tree"], [])
        self.assertIn(self.CLEAN, out.split("\n"))
        gitfix.run(["git", "commit", "-q", "-am", "move"], os.path.join(self.repo, "vendor"))
        self.assertEqual(self.render("json")["integrity"]["working_tree"], ["vendor"])  # the moved pointer is seen

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

    def index_bytes(self):
        with open(os.path.join(self.repo, ".git", "index"), "rb") as fh:
            return fh.read()

    def test_a_stat_only_index_refresh_is_no_index_change(self):  # review-code-87o1.1 (F-a47f8a2321)
        os.utime(os.path.join(self.repo, "src/calc.py"), (1, 1))
        snap, before = target.integrity(self.repo), self.index_bytes()
        subprocess.run(["git", "status"], cwd=self.repo, check=True, stdout=subprocess.DEVNULL)  # a prompt's plain status
        self.assertNotEqual(self.index_bytes(), before)  # it did rewrite the index: refreshed stat data, no entry changed
        self.assertFalse(target.integrity_changes(self.repo, snap)["index"])

    def test_a_stat_only_refresh_keeps_the_flags_and_is_no_index_change(self):  # review-code-87o1.10
        gitfix.commit(self.repo, {"src/b.py": "b = 1\n", "src/c.py": "c = 1\n"}, "more")
        gitfix.run(["git", "update-index", "--assume-unchanged", "src/b.py"], self.repo)
        gitfix.run(["git", "update-index", "--skip-worktree", "src/c.py"], self.repo)
        for p in ("src/calc.py", "src/b.py", "src/c.py"):
            os.utime(os.path.join(self.repo, p), (1, 1))
        snap, before = target.integrity(self.repo), self.index_bytes()
        subprocess.run(["git", "status"], cwd=self.repo, check=True, stdout=subprocess.DEVNULL)
        self.assertNotEqual(self.index_bytes(), before)  # refreshed, and the two flags are still set
        self.assertEqual(gitfix.run(["git", "ls-files", "-v", "src/b.py", "src/c.py"], self.repo).split(), ["h", "src/b.py", "S", "src/c.py"])
        self.assertFalse(target.integrity_changes(self.repo, snap)["index"])

    def test_a_flag_that_hides_an_edit_from_status_is_an_index_change(self):  # review-code-87o1.10
        for flag in ("--assume-unchanged", "--skip-worktree"):
            snap = target.integrity(self.repo)
            gitfix.run(["git", "update-index", flag, "src/calc.py"], self.repo)
            self.assertTrue(target.integrity_changes(self.repo, snap)["index"], flag)
            snap = target.integrity(self.repo)  # set before the snapshot, cleared during the run
            gitfix.run(["git", "update-index", flag.replace("--", "--no-", 1), "src/calc.py"], self.repo)
            self.assertTrue(target.integrity_changes(self.repo, snap)["index"], flag)

    def test_a_snapshot_from_another_version_skips_the_index_with_a_note(self):  # review-code-87o1.11
        old = dict(target.integrity(self.repo), version=1, index=hashlib.sha256(self.index_bytes()).hexdigest())
        with open(os.path.join(self.run_dir, "integrity.json"), "w", encoding="utf-8") as fh:
            json.dump(old, fh)  # the shape 4504ca6 wrote: version 1, a digest of the raw index file
        out = self.render().split("\n")
        self.assertNotIn("integrity warning: index " + CAUSE, out)
        self.assertNotIn(self.CLEAN, out)  # it would say the index did not change, which was not checked
        self.assertIn("integrity note: index not compared: snapshot from another version", out)
        got = self.render("json")["integrity"]
        self.assertEqual((got["index"], got["working_tree"], got["version_note"]),
                         (False, [], "index not compared: snapshot from another version"))

    def test_a_staged_change_is_an_index_warning(self):
        tp.write(self.repo, "src/calc.py", "edited before the run\n")
        self.make_run()
        self.review(5)
        gitfix.run(["git", "add", "src/calc.py"], self.repo)  # a new blob in the index; the file itself is unchanged
        self.assertIn("integrity warning: index " + CAUSE, self.render().split("\n"))
        self.assertTrue(self.render("json")["integrity"]["index"])  # its status record changes too, so working_tree lists it

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


class DirtyFlag(tpost.Base):
    """review-code-t177: pin() takes `dirty` from the integrity snapshot's status when that shows a tracked edit; the
    separate tracked-files status runs only when it cannot (a clean snapshot hides submodule worktree edits)."""

    def pin(self):
        log = os.path.join(self.tmp, "dirty%d.json" % len(os.listdir(self.tmp)))
        rc, out, err = cli("target.py", ["main"], self.repo, self.tmp, extra={"GIT_TRACE2_EVENT": log})
        self.assertEqual(rc, 0, out + err)
        v1 = sum(1 for a in tp.starts(log) if "status" in a and "--porcelain" in a)
        return tp.load(json.loads(out)["run"])["dirty"], v1

    def test_a_clean_checkout_is_not_dirty_and_needs_the_tracked_status(self):
        self.assertEqual(self.pin(), (False, 1))

    def test_a_tracked_edit_is_dirty_from_the_snapshot_alone(self):
        tp.write(self.repo, "src/calc.py", "edited\n")
        self.assertEqual(self.pin(), (True, 0))

    def test_an_untracked_file_is_not_dirty(self):
        tp.write(self.repo, "new.txt", "x\n")
        self.assertEqual(self.pin(), (False, 1))

    def test_an_edit_only_inside_a_submodule_worktree_is_still_dirty(self):
        sub = gitfix.make_repo(os.path.join(self.tmp, "subsrc"))
        gitfix.commit(sub, {"lib.py": "x = 1\n"}, "sub base")
        gitfix.run(["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", sub, "vendor"], self.repo)
        gitfix.run(["git", "commit", "-q", "-m", "add submodule"], self.repo)
        tp.write(self.repo, "vendor/lib.py", "x = 2\n")
        self.assertEqual(self.pin(), (True, 1))  # the snapshot (=dirty) hides it, so the tracked status must still run


class CappedDirtyFlag(tpost.Base):
    """review-code-87o1.9: above TRACKED_CAP the snapshot has no tracked row, only tracked{count, digest}; `dirty`
    still comes from the snapshot alone (in-process, so the patched cap reaches pin)."""

    def setUp(self):
        super().setUp()
        gitfix.commit(self.repo, {"bulk/f%d.txt" % i: "x\n" for i in range(5)}, "bulk")
        for i in range(5):  # tracked edits only: no knowledge row that would make dirty true by itself
            tp.write(self.repo, "bulk/f%d.txt" % i, "x, modified\n")

    def pin(self, cap):
        log = os.path.join(self.tmp, "capped%d.json" % cap)
        with mock.patch.object(target, "TRACKED_CAP", cap), mock.patch.dict(os.environ, {"GIT_TRACE2_EVENT": log}):
            rc, out = tt.call(["main"], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        snap = tp.load(out["run"], "integrity.json")
        plain = sum(1 for a in tp.starts(log) if "status" in a and "--porcelain" in a)
        return "tracked" in snap, tt.load(out["run"])["dirty"], plain

    def test_a_capped_tracked_set_is_dirty_from_the_snapshot_alone(self):
        self.assertEqual(self.pin(3), (True, True, 0))

    def test_below_the_cap_the_rows_still_make_it_dirty(self):
        self.assertEqual(self.pin(100), (False, True, 0))


class RenderProcessCount(tpost.Base):
    def count(self, n):
        self.make_run()
        self.review(5)
        for i in range(n):
            tp.write(self.repo, "new/f%02d.txt" % i, "x\n")
        log = os.path.join(self.tmp, "render%d.json" % n)
        rc, out, err = cli("report.py", ["render", "--run", self.run_dir], self.repo, self.tmp, extra={"GIT_TRACE2_EVENT": log})
        self.assertEqual(rc, 0, out + err)
        self.assertIn("integrity warning: new/f00.txt (working tree) " + CAUSE, out.split("\n"))
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
        self.assertEqual(set(snap["dirty"]), {".wolf/locked.md"})  # only the drift: no entry for any clean tracked file
        self.assertEqual(snap["knowledge"], {".wolf/locked.md": [7, os.lstat(os.path.join(self.repo, ".wolf/locked.md")).st_mtime_ns]})
        self.assertLess(os.path.getsize(path), 1024)
        self.assertNotIn("secret", open(path, encoding="utf-8").read())


class CappedUntracked(tpost.Base):
    """review-code-71de.15: above UNTRACKED_CAP untracked files the snapshot keeps a count and a digest, not a row each."""

    def setUp(self):
        super().setUp()
        for i in range(5):
            tp.write(self.repo, "bulk/f%d.txt" % i, "x\n")
        self.make_run()
        self.review(5)
        cap = mock.patch.object(target, "UNTRACKED_CAP", 3, create=True)
        cap.start()
        self.addCleanup(cap.stop)

    def snap_here(self):
        snap = json.loads(json.dumps(target.integrity(self.repo)))
        with open(os.path.join(self.run_dir, "integrity.json"), "w", encoding="utf-8") as fh:
            json.dump(snap, fh)
        return snap

    def test_the_snapshot_is_bounded_and_keeps_a_count_and_a_digest(self):
        snap = self.snap_here()
        self.assertEqual(snap["dirty"], {})
        self.assertEqual(snap["untracked"]["count"], 5)
        self.assertEqual(len(snap["untracked"]["digest"]), 64)

    def test_an_edit_above_the_cap_is_flagged_though_not_itemised(self):
        snap = self.snap_here()
        self.assertEqual(target.integrity_changes(self.repo, snap)["untracked"], {"count": 5, "changed": False})
        tp.write(self.repo, "bulk/f2.txt", "edited, longer\n")
        got = target.integrity_changes(self.repo, snap)
        self.assertEqual((got["working_tree"], got["untracked"]), ([], {"count": 5, "changed": True}))
        self.assertIn("integrity warning: untracked files changed (not fully seen: 5 untracked files, not itemised) " + CAUSE,
                      report.render(self.run_dir, "text").split("\n"))

    def test_an_unchanged_capped_tree_says_what_it_did_not_see(self):
        self.snap_here()
        self.assertIn("integrity: no change to the checkout, index or tool knowledge during the run "
                      "(not seen: submodule worktrees, nested repositories; not fully seen: 5 untracked files)",
                      report.render(self.run_dir, "text").split("\n"))

    def test_crossing_the_cap_is_one_flag_not_a_row_per_file(self):
        with mock.patch.object(target, "UNTRACKED_CAP", 100):
            snap = self.snap_here()  # itemised: five rows
        self.assertEqual(len(snap["dirty"]), 5)
        got = target.integrity_changes(self.repo, snap)  # the cap is 3 again: now has no rows
        self.assertEqual((got["working_tree"], got["untracked"]), ([], {"count": 5, "changed": True}))

    def test_untracked_tool_knowledge_stays_informational_above_the_cap(self):
        tp.write(self.repo, ".wolf/memory.md", "one\n")
        snap = self.snap_here()
        self.assertEqual(snap["untracked"]["count"], 5)  # not the knowledge file
        tp.write(self.repo, ".wolf/memory.md", "one, two, three\n")
        got = target.integrity_changes(self.repo, snap)
        self.assertEqual((got["working_tree"], got["knowledge"], got["untracked"]["changed"]), ([], [".wolf/memory.md"], False))
        self.assertNotIn("integrity warning", report.render(self.run_dir, "text"))

    def test_a_tracked_edit_does_not_hide_that_untracked_files_were_not_fully_seen(self):
        self.snap_here()
        tp.write(self.repo, "src/calc.py", "edited\n")
        out = report.render(self.run_dir, "text").split("\n")
        self.assertIn("integrity warning: src/calc.py (working tree) " + CAUSE, out)
        self.assertIn("integrity note: not fully seen: 5 untracked files", out)

    def test_the_comparison_leaves_the_snapshot_it_was_given_intact(self):
        with mock.patch.object(target, "UNTRACKED_CAP", 100):
            snap = self.snap_here()  # five rows, which the capped comparison drops from its own copy
        before = json.dumps(snap, sort_keys=True)
        target.integrity_changes(self.repo, snap)
        self.assertEqual(json.dumps(snap, sort_keys=True), before)


class CappedTracked(tpost.Base):
    """review-code-71de.17: above TRACKED_CAP changed tracked files the snapshot keeps a count and a digest, not a row each."""
    snap_here = CappedUntracked.snap_here

    def setUp(self):
        super().setUp()
        gitfix.commit(self.repo, dict({"bulk/f%d.txt" % i: "x\n" for i in range(5)}, **{".codegraph/meta.txt": "v1\n"}), "bulk")
        for i in range(5):
            tp.write(self.repo, "bulk/f%d.txt" % i, "x, modified before the run\n")
        tp.write(self.repo, ".codegraph/meta.txt", "v2\n")  # tracked knowledge, modified: informational, never counted
        self.make_run()
        self.review(5)
        cap = mock.patch.object(target, "TRACKED_CAP", 3, create=True)
        cap.start()
        self.addCleanup(cap.stop)

    def test_the_snapshot_is_bounded_and_keeps_a_count_and_a_digest(self):
        snap = self.snap_here()
        self.assertEqual(set(snap["dirty"]), {".codegraph/meta.txt"})
        self.assertEqual((snap["tracked"]["count"], len(snap["tracked"]["digest"])), (5, 64))
        self.assertNotIn("untracked", snap)

    def test_an_edit_above_the_cap_is_flagged_though_not_itemised(self):
        snap = self.snap_here()
        self.assertEqual(target.integrity_changes(self.repo, snap)["tracked"], {"count": 5, "changed": False})
        tp.write(self.repo, "bulk/f2.txt", "x, modified before the run, edited again\n")
        got = target.integrity_changes(self.repo, snap)
        self.assertEqual((got["working_tree"], got["tracked"]), ([], {"count": 5, "changed": True}))
        self.assertIn("integrity warning: tracked files changed (not fully seen: 5 tracked files, not itemised) " + CAUSE,
                      report.render(self.run_dir, "text").split("\n"))

    def test_an_unchanged_capped_tree_says_what_it_did_not_see(self):
        self.snap_here()
        self.assertIn("integrity: no change to the checkout, index or tool knowledge during the run "
                      "(not seen: submodule worktrees, nested repositories; not fully seen: 5 tracked files)",
                      report.render(self.run_dir, "text").split("\n"))

    def test_crossing_the_cap_is_one_flag_not_a_row_per_file(self):
        with mock.patch.object(target, "TRACKED_CAP", 100):
            snap = self.snap_here()  # itemised: five rows and the knowledge file
        self.assertEqual(len(snap["dirty"]), 6)
        got = target.integrity_changes(self.repo, snap)  # the cap is 3 again: now has no tracked rows
        self.assertEqual((got["working_tree"], got["tracked"]), ([], {"count": 5, "changed": True}))

    def test_tracked_tool_knowledge_stays_informational_above_the_cap(self):
        snap = self.snap_here()
        tp.write(self.repo, ".codegraph/meta.txt", "v3, longer\n")
        got = target.integrity_changes(self.repo, snap)
        self.assertEqual((got["working_tree"], got["knowledge"], got["tracked"]["changed"]), ([], [".codegraph/meta.txt"], False))
        self.assertNotIn("integrity warning", report.render(self.run_dir, "text"))

    def test_an_untracked_file_does_not_hide_that_tracked_files_were_not_fully_seen(self):
        self.snap_here()
        tp.write(self.repo, "new.txt", "x\n")
        out = report.render(self.run_dir, "text").split("\n")
        self.assertIn("integrity warning: new.txt (working tree) " + CAUSE, out)
        self.assertIn("integrity note: not fully seen: 5 tracked files", out)

    def test_the_comparison_leaves_the_snapshot_it_was_given_intact(self):
        with mock.patch.object(target, "TRACKED_CAP", 100):
            snap = self.snap_here()  # six rows, which the capped comparison drops from its own copy
        before = json.dumps(snap, sort_keys=True)
        target.integrity_changes(self.repo, snap)
        self.assertEqual(json.dumps(snap, sort_keys=True), before)


if __name__ == "__main__":
    unittest.main()
