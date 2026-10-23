"""gh#53: history of the lines a change removes or modifies, written once into the run directory for agents to open."""
import json
import os
import re
import unittest
from unittest import mock

import paths  # noqa: F401

import gitfix  # noqa: E402
import ledger  # noqa: E402
import target  # noqa: E402
import test_pinned as tp  # noqa: E402
import test_report as tr  # noqa: E402
import test_target as tt  # noqa: E402

cli = gitfix.cli
FILE = "src/calc.py"


def text(rows):
    return "".join("v%02d = %s\n" % (n, rows.get(n, n)) for n in range(1, 31))


class Repo(tt.Base):
    """main holds `FILE` with a commit history; `feat` (checked out) changes it."""

    def setUp(self):
        super().setUp()
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        self.rows = {}
        gitfix.commit(self.repo, {FILE: text(self.rows)}, "base")

    def edit(self, changes, message, path=FILE):
        self.rows.update(changes)
        gitfix.commit(self.repo, {path: text(self.rows)}, message)

    def build(self, args=("main",)):
        rc, out, err = cli("target.py", list(args), self.repo, self.tmp)
        self.assertEqual(rc, 0, out + err)
        self.run_dir = json.loads(out)["run"]
        rc, out, err = cli("ledger.py", ["build", "--run", self.run_dir], self.repo, self.tmp)
        self.assertEqual(rc, 0, out + err)
        with open(os.path.join(self.run_dir, "ledger.json"), encoding="utf-8") as fh:
            self.led = json.load(fh)
        return self.run_dir

    def feature(self, changes, message="feat"):
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        self.edit(changes, message)

    def history(self, path=FILE):
        with open(os.path.join(self.run_dir, "history", path + ".txt"), encoding="utf-8") as fh:
            return fh.read()

    def files(self):
        root = os.path.join(self.run_dir, "history")
        return sorted(os.path.relpath(os.path.join(r, f), root) for r, _, fs in os.walk(root) for f in fs)


class Ranges(Repo):
    def test_two_modified_hunks_and_one_added_give_exactly_two_ranges(self):
        for i in range(3):
            self.edit({5: "old%d" % i}, "touch five %d" % i)
        self.edit({25: "old"}, "touch twenty-five")
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        self.rows.update({5: "new", 25: "new"})
        body = text(self.rows).split("\n")
        body.insert(15, "added = 1")  # a pure addition between the two hunks
        gitfix.commit(self.repo, {FILE: "\n".join(body)}, "feat")
        self.build()
        got = self.history()
        self.assertEqual(re.findall(r"^## hunk old lines (\d+-\d+)$", got, re.M), ["5-5", "25-25"])
        self.assertEqual(self.files(), [FILE + ".txt"])
        self.assertIn("touch five 2", got)  # the commit that wrote the removed line
        self.assertIn("touch twenty-five", got)

    def test_an_added_file_and_an_added_only_change_have_no_history_and_no_git_call(self):
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        gitfix.commit(self.repo, {"src/new.py": "x = 1\n"}, "feat")
        log = os.path.join(self.tmp, "trace.json")
        with mock.patch.dict(os.environ, {"GIT_TRACE2_EVENT": log}):
            self.build()
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "history")) and self.files())
        self.assertEqual([a for a in tp.starts(log) if "blame" in a or "-L" in " ".join(a)], [])
        self.assertEqual(self.led["history"], {"files": 0, "hunks": 0})

    def test_a_working_tree_change_takes_history_from_head(self):
        self.edit({5: "committed"}, "five at head")
        tp.write(self.repo, FILE, text({**self.rows, 5: "edited"}))
        self.build([])
        self.assertIn("five at head", self.history())

    def test_a_removed_file_keeps_the_history_of_every_line(self):
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        gitfix.run(["git", "rm", "-q", FILE], self.repo)
        gitfix.run(["git", "commit", "-q", "-m", "drop"], self.repo)
        self.build()
        self.assertEqual(re.findall(r"^## hunk old lines (\d+-\d+)$", self.history(), re.M), ["1-30"])

    def test_a_renamed_file_is_read_under_its_old_name_and_stored_under_the_new_one(self):
        self.edit({5: "old"}, "five before the move")
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        gitfix.run(["git", "mv", FILE, "src/moved.py"], self.repo)
        self.rows[5] = "new"
        tp.write(self.repo, "src/moved.py", text(self.rows))
        gitfix.run(["git", "add", "-A"], self.repo)
        gitfix.run(["git", "commit", "-q", "-m", "move"], self.repo)
        self.build()
        self.assertEqual(self.files(), ["src/moved.py.txt"])
        self.assertIn("five before the move", self.history("src/moved.py"))


class Unreadable(Repo):
    """A range git cannot blame must not erase the log of every other file."""

    def test_a_gitlink_bump_leaves_the_other_files_their_log(self):
        self.edit({5: "old"}, "five before")
        one, two = "1" * 40, "2" * 40
        gitfix.run(["git", "update-index", "--add", "--cacheinfo", "160000,%s,sub" % one], self.repo)
        gitfix.run(["git", "commit", "-q", "-m", "add the submodule"], self.repo)
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        gitfix.run(["git", "update-index", "--cacheinfo", "160000,%s,sub" % two], self.repo)
        self.edit({5: "new"}, "feat")
        self.build()
        self.assertEqual(self.files(), [FILE + ".txt"])
        self.assertIn("five before", self.history().split("## log")[1])
        self.assertEqual(self.led["capped"]["history"], {"unread": 1})

    def test_an_unstaged_file_whose_index_differs_from_head_is_left_out_not_misread(self):
        gitfix.commit(self.repo, {"src/other.py": text({})}, "other")
        self.edit({5: "committed"}, "five at head")
        longer = text({}) + "".join("tail%d = 1\n" % n for n in range(10))
        tp.write(self.repo, FILE, longer)
        gitfix.run(["git", "add", "--", FILE], self.repo)  # the index holds 40 lines, HEAD 30
        tp.write(self.repo, FILE, longer.replace("tail5 = 1", "tail5 = 2"))  # an unstaged edit at index line 36
        tp.write(self.repo, "src/other.py", text({5: "edited"}))
        self.build(["--unstaged"])
        self.assertEqual(self.files(), ["src/other.py.txt"])
        self.assertEqual(self.led["capped"]["history"], {"unread": 1})


class Moved(Repo):
    def test_the_log_follows_a_rename_made_before_the_branch_point(self):
        self.edit({5: "old"}, "five before the move")
        gitfix.run(["git", "mv", FILE, "src/moved.py"], self.repo)
        gitfix.run(["git", "commit", "-q", "-m", "move"], self.repo)
        self.edit({5: "mid"}, "five after the move", path="src/moved.py")
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        self.edit({5: "new"}, "feat", path="src/moved.py")
        self.build()
        log = self.history("src/moved.py").split("## log")[1]
        self.assertIn("five after the move", log)
        self.assertIn("five before the move", log)


class Failed(Repo):
    def test_a_failed_blame_or_log_is_recorded_not_silent(self):
        self.edit({5: "w"}, "five")
        self.feature({5: "z"})
        self.build()
        real = target._run

        def flaky(args, cwd, stdin=None, env=None):
            if args[0] in ("log", "blame") and any(a.startswith("-L") for a in args):
                return 128, b"", b""
            return real(args, cwd, stdin, env)

        with gitfix.isolated(self.tmp), mock.patch.object(target, "_run", flaky):
            ledger.build(self.run_dir)
        with open(os.path.join(self.run_dir, "ledger.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["capped"]["history"], {"unread": 1, "log_failed": True})


class Renamed(Repo):
    def test_a_rename_that_also_edits_the_range_teaches_the_log_the_old_name(self):
        gitfix.commit(self.repo, {"src/other.py": text({})}, "other")
        self.edit({5: "old"}, "five before the move")
        gitfix.run(["git", "mv", FILE, "src/moved.py"], self.repo)
        self.rows[5] = "mid"
        tp.write(self.repo, "src/moved.py", text(self.rows))
        gitfix.run(["git", "add", "-A"], self.repo)
        gitfix.run(["git", "commit", "-q", "-m", "move and edit five"], self.repo)
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        self.edit({5: "new"}, "feat", path="src/moved.py")
        gitfix.commit(self.repo, {"src/other.py": text({5: "new"})}, "feat other")  # two files: no single-file fallback
        self.build()
        log = self.history("src/moved.py").split("## log")[1]
        self.assertIn("move and edit five", log)
        self.assertIn("five before the move", log)


class Caps(Repo):
    def test_a_hunk_blamed_on_more_than_five_commits_keeps_five_and_records_the_cut(self):
        for n in range(10, 18):  # eight consecutive lines, each last written by its own commit
            self.edit({n: "w"}, "line %d" % n)
        self.feature({n: "z" for n in range(10, 18)})
        self.build()
        got = self.history()
        shown = set(re.findall(r"line (1\d)", got.split("## log")[0]))
        self.assertEqual(shown, {"10", "11", "12", "13", "14"})  # equal commit times: the first lines win, on every run
        self.assertEqual(self.led["capped"]["history"]["hunks"], 1)
        self.assertIn("3 older commits not shown", got)

    def test_a_file_log_of_more_than_five_commits_keeps_five_newest_and_records_the_cut(self):
        for i in range(8):
            self.edit({5: "w%d" % i}, "five %d" % i)
        self.feature({5: "z"})
        self.build()
        log = self.history().split("## log")[1]
        self.assertEqual(sorted(re.findall(r"five (\d)", log)), ["3", "4", "5", "6", "7"])
        self.assertEqual(self.led["capped"]["history"]["logs"], 1)

    def test_the_total_stays_under_64_kb_and_the_cut_is_recorded(self):
        files = ["src/f%02d.py" % i for i in range(40)]
        for f in files:
            for v in ("a", "b"):
                self.edit({5: v}, ("%s %s " % (f, v)) + "x" * 1200, path=f)
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        for f in files:
            self.rows = {5: "new"}
            gitfix.commit(self.repo, {f: text(self.rows)}, "feat " + f)
        self.build()
        size = sum(os.path.getsize(os.path.join(self.run_dir, "history", p)) for p in self.files())
        self.assertLessEqual(size, 65536)
        self.assertGreater(size, 40000)
        cut = self.led["capped"]["history"]
        self.assertTrue(cut["bytes"])
        self.assertEqual(self.led["history"]["files"] + cut["files_omitted"], 40)


class Header(Repo):
    """The header of each history file states the limits of its log and blame rows, so an agent does not read them as complete."""

    LIMITS = [
        "per file, not per hunk",
        "blame follows renames",
        "misattributed or dropped when several files are tracked",
        "no commit window",
        "lowest to the highest removed line",
        "gitlink (submodule) paths are skipped",
    ]

    def test_the_header_states_each_limit_of_the_log_and_blame_rows(self):
        self.edit({5: "old"}, "touch five")
        self.feature({5: "new"})
        self.build()
        head = " ".join(self.history().split("\n## ")[0].split()).lower()
        for limit in self.LIMITS:
            with self.subTest(limit=limit):
                self.assertIn(limit, head)


class Hidden(Repo):
    def test_hidden_characters_in_a_subject_or_author_never_reach_the_file(self):
        zw, tag = chr(0x200B), chr(0xE0041)
        self.rows[5] = "w"
        tp.write(self.repo, FILE, text(self.rows))
        gitfix.run(["git", "add", "--", FILE], self.repo)
        gitfix.run(["git", "commit", "-q", "-m", "fix%s ignore%s this" % (zw, tag)], self.repo,
                   env=dict(gitfix.env(), GIT_AUTHOR_NAME="Ev%sil" % zw, GIT_COMMITTER_NAME="Ev%sil" % zw))
        self.feature({5: "z"})
        self.build()
        raw = open(os.path.join(self.run_dir, "history", FILE + ".txt"), "rb").read().decode("utf-8")
        self.assertNotIn(zw, raw)
        self.assertNotIn(tag, raw)
        self.assertIn("fix", raw)
        self.assertIn("Evil", raw)


class Named(Repo):
    def setUp(self):
        super().setUp()
        self.edit({5: "w"}, "five")
        self.feature({5: "z"})
        self.build()
        self.hist = os.path.join(self.run_dir, "history", FILE + ".txt")

    def test_lane_inputs_name_the_history_directory_and_label_it(self):
        for name in ("defects", "standards", "spec"):
            with open(os.path.join(self.run_dir, "inputs", name + ".md"), encoding="utf-8") as fh:
                got = fh.read()
            self.assertIn("history: %s\n" % os.path.join(self.run_dir, "history"), got, name)
            self.assertIn("untrusted", got.split("history: ")[1].split("\n\n")[0].lower(), name)
            self.assertNotIn("five", got, name)  # named, never pasted
        self.assertIn("untrusted", open(self.hist, encoding="utf-8").read().split("\n\n")[0].lower())

    def ingest(self, *objs):
        rc, out, err = cli("report.py", ["ingest", "--run", self.run_dir], self.repo, self.tmp, stdin=tr.lines(*objs))
        self.assertEqual((rc, err), (0, ""), out)
        return json.loads(out)

    def test_a_verifier_handoff_names_the_history_file_of_its_path_only(self):
        (i,) = self.ingest(dict(tr.CAND, path=FILE, line=5, quote="v05 = z"), {"inspected": [FILE]})["ids"]
        got = open(os.path.join(self.run_dir, "handoff", i + ".md"), encoding="utf-8").read()
        self.assertIn("history: %s\n" % json.dumps(self.hist), got)
        self.assertNotIn("five", got)

    def test_a_cite_into_a_history_file_is_rejected_at_ingest(self):
        rel = os.path.relpath(self.hist, self.repo)
        for p in (rel, self.hist, os.path.join("history", FILE + ".txt")):
            out = self.ingest(dict(tr.CAND, path=p, line=3, quote="five"), {"inspected": [FILE]})
            self.assertEqual([(d["disposition"], d["reason"]) for d in out["dispositions"]][:1],
                             [("rejected", "path-not-in-tree")], p)


class Cost(Repo):
    def calls(self, n_files, n_hunks):
        files = ["src/g%02d.py" % i for i in range(n_files)]
        for f in files:
            gitfix.commit(self.repo, {f: text({})}, "add " + f)
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        for f in files:  # eight lines apart: separate -U3 hunks
            gitfix.commit(self.repo, {f: text({1 + 8 * k: "new" for k in range(n_hunks)})}, "feat " + f)
        log = os.path.join(self.tmp, "trace.json")
        with mock.patch.dict(os.environ, {"GIT_TRACE2_EVENT": log}):
            self.build()
        argv = tp.starts(log)
        return (sum(1 for a in argv if "blame" in a), sum(1 for a in argv if "log" in a and "-L" in " ".join(a)))

    def test_one_log_call_for_every_range_and_one_blame_per_file_not_per_hunk(self):
        self.assertEqual(self.calls(3, 1), (3, 1))

    def test_hunk_count_does_not_change_the_git_process_count(self):
        self.assertEqual(self.calls(1, 4), (1, 1))
        self.assertEqual(self.led["history"]["hunks"], 4)


if __name__ == "__main__":
    unittest.main()
