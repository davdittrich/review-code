"""The pinned tree, the checkout difference and the review basis (gh#48), through the run-directory contract."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import paths  # noqa: F401

import gitfix  # noqa: E402
import ledger  # noqa: E402
import target  # noqa: E402
import test_fix as tfix  # noqa: E402
import test_post as tpost  # noqa: E402  (module imports: their test classes are not collected twice)
import test_target as tt  # noqa: E402

cli = gitfix.cli


def load(run, name="target.json"):
    with open(os.path.join(run, name), encoding="utf-8") as fh:
        return json.load(fh)


def difference(run):
    """{path: tag} from the difference file target.json names."""
    tj = load(run)
    with open(tj["difference"], encoding="utf-8") as fh:
        d = json.load(fh)
    return {e["path"]: e["tag"] for e in d["paths"]}


def raw(repo, *args):
    return subprocess.run(["git"] + list(args), cwd=repo, env=gitfix.env(), stdout=subprocess.PIPE, check=True).stdout


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def snapshot(repo):
    """HEAD, every ref, the index file and the working tree, as bytes."""
    gd = raw(repo, "rev-parse", "--absolute-git-dir").decode().strip()
    index = os.path.join(repo, raw(repo, "rev-parse", "--git-path", "index").decode().strip())
    out = {"HEAD": read(os.path.join(gd, "HEAD")), "refs": raw(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
           "index": read(index) if os.path.exists(index) else None}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            p = os.path.join(root, f)
            out["wt:" + os.path.relpath(p, repo)] = os.readlink(p) if os.path.islink(p) else read(p)
    return out


def write(repo, rel, data):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode())


class PRDifference(tt.PRWorld):
    """The checkout sits on main; the PR head is not checked out."""

    def test_synced_checkout_records_exactly_the_differing_paths(self):
        rc, out = tt.call(["7"], self.repo, self.tmp, gh=self.gh())
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        tj = load(out["run"])
        self.assertEqual(difference(out["run"]), {"src/cfg.py": "modified", "src/" + tt.CANARY_NAME + ".py": "absent-in-checkout",
                                                  "src/other.py": "untracked"})  # main moved on past the PR's base
        self.assertEqual(tj["pinned_tree"], self.git("rev-parse", self.pr + "^{tree}"))
        self.assertEqual((tj["pinned_head"], tj["checkout_head"], tj["difference_count"]),
                         (self.pr, self.git("rev-parse", "HEAD"), 3))
        self.assertEqual(os.path.dirname(tj["difference"]), out["run"])  # the list lives in the run directory

    def test_local_work_joins_the_difference_and_ignored_files_do_not(self):
        write(self.repo, "local.txt", "mine\n")
        write(self.repo, "debug.log", "noise\n")
        write(self.repo, ".git/info/exclude", "*.log\n")
        write(self.repo, "src/" + tt.CANARY_NAME + ".py", "x = 1\n")  # untracked here, at the PR head: present, so not absent
        rc, out = tt.call(["7"], self.repo, self.tmp, gh=self.gh())
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        self.assertEqual(difference(out["run"]), {"src/cfg.py": "modified", "local.txt": "untracked",
                                                  "src/other.py": "untracked",  # tracked here, absent at the PR head
                                                  "src/" + tt.CANARY_NAME + ".py": "modified"})


class InPlace(tt.Base):
    """staged a.py, unstaged b.py, deleted c.py, untracked u.py, ignored x.log."""

    def setUp(self):
        super().setUp()
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, {"a.py": "a = 1\n", "b.py": "b = 1\n", "c.py": "c = 1\n", "d.py": "d = 1\n",
                                  ".gitignore": "*.log\n"}, "base")
        write(self.repo, "a.py", "a = 2\n")
        self.git("add", "a.py")
        write(self.repo, "b.py", "b = 2\n")
        os.remove(os.path.join(self.repo, "c.py"))
        write(self.repo, "u.py", "u = 1\n")
        write(self.repo, "x.log", "log\n")

    def pin(self, *args):
        rc, out = tt.call(list(args), self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        return out["run"], load(out["run"])

    def blob(self, tree, path):
        return raw(self.repo, "cat-file", "blob", "%s:%s" % (tree, path))

    def test_staged_pins_the_index_and_records_worktree_edits(self):
        run, tj = self.pin("--staged")
        self.assertEqual(difference(run), {"b.py": "modified", "c.py": "absent-in-checkout", "u.py": "untracked"})
        self.assertEqual((self.blob(tj["pinned_tree"], "a.py"), self.blob(tj["pinned_tree"], "b.py")), (b"a = 2\n", b"b = 1\n"))
        self.assertIsNone(tj["pinned_head"])
        self.assertEqual(tj["checkout_head"], self.git("rev-parse", "HEAD"))

    def test_every_other_in_place_kind_pins_the_whole_checkout(self):
        for args in (["--unstaged"], [], ["b.py"], ["--repo"]):
            with self.subTest(args=args):
                run, tj = self.pin(*args)
                self.assertEqual(difference(run), {})
                names = raw(self.repo, "ls-tree", "--name-only", tj["pinned_tree"]).decode().split("\n")
                self.assertEqual(sorted(n for n in names if n), [".gitignore", "a.py", "b.py", "d.py", "u.py"])
                self.assertEqual([self.blob(tj["pinned_tree"], n) for n in ("a.py", "b.py", "u.py")],
                                 [b"a = 2\n", b"b = 2\n", b"u = 1\n"])

    def test_every_reviewed_file_is_in_the_pinned_tree(self):
        for args in (["--staged"], ["--unstaged"], [], ["b.py"], ["--repo"]):
            with self.subTest(args=args):
                run, tj = self.pin(*args)
                names = set(raw(self.repo, "ls-tree", "-r", "--name-only", tj["pinned_tree"]).decode().split("\n"))
                self.assertLessEqual(set(tj["included"]) - {"c.py"}, names)  # c.py is reviewed as a deletion

    def test_pinning_in_place_leaves_the_checkout_byte_identical(self):
        t = os.stat(os.path.join(self.repo, "d.py")).st_mtime
        os.utime(os.path.join(self.repo, "d.py"), (t + 7, t + 7))  # stat-dirty, same bytes: a refresh would rewrite the index
        before = snapshot(self.repo)
        for args in (["--staged"], ["--unstaged"], [], ["b.py"], ["--repo"]):
            with self.subTest(args=args):
                self.pin(*args)
                self.assertEqual(snapshot(self.repo), before)


class Conflict(tt.Base):
    """A merge stopped on a conflict in m.py; s.py is staged on top."""

    def setUp(self):
        super().setUp()
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, {"m.py": "m = 0\n", "s.py": "s = 0\n"}, "base")
        self.git("checkout", "-q", "-b", "side")
        gitfix.commit(self.repo, {"m.py": "m = 1\n"}, "side")
        self.git("checkout", "-q", "main")
        gitfix.commit(self.repo, {"m.py": "m = 2\n"}, "main")
        subprocess.run(["git", "merge", "-q", "side"], cwd=self.repo, env=gitfix.env(), stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        write(self.repo, "s.py", "s = 1\n")
        self.git("add", "s.py")

    def test_an_unmerged_path_stays_out_of_a_staged_pin_and_in_the_difference(self):
        rc, out = tt.call(["--staged"], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        tj = load(out["run"])
        self.assertEqual(raw(self.repo, "cat-file", "blob", tj["pinned_tree"] + ":s.py"), b"s = 1\n")
        self.assertNotIn(b"m.py", raw(self.repo, "ls-tree", "--name-only", tj["pinned_tree"]))
        self.assertEqual(difference(out["run"]), {"m.py": "modified"})

    def test_a_worktree_pin_takes_the_conflicted_file_as_it_is_on_disk(self):
        rc, out = tt.call([], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        tree = load(out["run"])["pinned_tree"]
        self.assertIn(b"<<<<<<<", raw(self.repo, "cat-file", "blob", tree + ":m.py"))
        self.assertEqual(difference(out["run"]), {})


class RangeDifference(tt.Base):
    """The checkout on main reviews main..feat; feat leaves b.py and c.py alone."""

    def test_uncommitted_and_staged_edits_to_an_unchanged_file_are_modified(self):
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, {"a.py": "a = 0\n", "b.py": "b = 0\n", "c.py": "c = 0\n"}, "base")
        self.git("checkout", "-q", "-b", "feat")
        gitfix.commit(self.repo, {"a.py": "a = 1\n"}, "feat")
        self.git("checkout", "-q", "main")
        self.git("remote", "add", "origin", self.repo)  # gh#49: its default-branch tip is this main, so it is synced
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")  # the remote behind it
        write(self.repo, "b.py", "b = 1\n")
        write(self.repo, "c.py", "c = 1\n")
        self.git("add", "c.py")
        write(self.repo, "c.py", "c = 0\n")  # staged only: the working file is back to the pinned bytes
        rc, out = tt.call(["main..feat"], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        self.assertEqual(difference(out["run"]), {"a.py": "modified", "b.py": "modified"})


class Filters(tt.Base):
    """A clean filter that leaves a marker when it runs."""

    def setUp(self):
        super().setUp()
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        self.marker = os.path.join(self.tmp, "filter-ran")
        self.git("config", "filter.spy.clean", "touch '%s'; tr a-z A-Z" % self.marker)
        self.git("config", "filter.spy.smudge", "cat")
        gitfix.commit(self.repo, {".gitattributes": "*.dat filter=spy text\n", "lib.dat": "abc\n", "keep.py": "k = 1\n",
                                  "old.dat": "def\n", "big.dat": "g" * 40 + "\n"}, "base")  # stored: DEF, GGG..
        for n in ("old.dat", "big.dat"):  # older than the index, so git's racy check never rereads them
            os.utime(os.path.join(self.repo, n), (1_600_000_000, 1_600_000_000))
        self.git("update-index", "-q", "--refresh")
        write(self.repo, "lib.dat", b"edited bytes\r\n")
        write(self.repo, "new.dat", b"fresh\n")
        t = os.stat(os.path.join(self.repo, "keep.py")).st_mtime
        os.utime(os.path.join(self.repo, "keep.py"), (t + 7, t + 7))
        if os.path.exists(self.marker):
            os.remove(self.marker)

    def test_worktree_pin_stores_raw_bytes_and_runs_no_filter(self):
        rc, out = tt.call([], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        tree = load(out["run"])["pinned_tree"]
        self.assertEqual(raw(self.repo, "cat-file", "blob", tree + ":lib.dat"), b"edited bytes\r\n")
        self.assertEqual(raw(self.repo, "cat-file", "blob", tree + ":new.dat"), b"fresh\n")
        self.assertFalse(os.path.exists(self.marker), "a clean filter ran")

    def test_an_unedited_filtered_file_enters_the_pinned_tree_as_it_is_on_disk(self):
        for args in (["--unstaged"], [], ["keep.py"], ["--repo"]):
            with self.subTest(args=args), mock.patch.object(target, "MAX_FILE", 30):  # big.dat is over it
                rc, out = tt.call(args, self.repo, self.tmp)
                self.assertEqual((rc, out["status"]), (0, "ok"), out)
                tree = load(out["run"])["pinned_tree"]
                self.assertEqual(raw(self.repo, "cat-file", "blob", tree + ":old.dat"), b"def\n")  # not the stored DEF
                self.assertEqual(difference(out["run"]), {"big.dat": "modified"})  # too large to hash: it differs


class AmbiguousRef(tt.Base):
    """The ambiguous-ref decision reads git's message, which gettext translates (review-code-ed7m)."""

    def test_the_decision_does_not_depend_on_the_message_language(self):
        repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(repo, {"a": "a\n"}, "base")
        real = subprocess.run
        seen = []

        def spy(argv, *a, **kw):
            env = kw.get("env") or {}
            seen.append((argv, env))
            if "--verify" in argv:  # a translated git: English only when the call forces the C locale
                msg = b"fatal: ambiguous argument\n" if env.get("LC_ALL") == "C" else b"fatal: mehrdeutiges Argument\n"
                return subprocess.CompletedProcess(argv, 128, b"", msg)
            return real(argv, *a, **kw)
        with mock.patch.dict(os.environ, {"LC_ALL": "de_DE.UTF-8"}), mock.patch.dict(target._NO_FILTERS, clear=True), \
                mock.patch.object(target.subprocess, "run", spy):
            with self.assertRaises(target.Refuse) as cm:
                target._commit_id("x", repo)
        self.assertEqual(cm.exception.reason, "ambiguous-ref")
        cfg = [e for argv, e in seen if "config" in argv]  # the filter config read keeps the caller's locale
        self.assertTrue(cfg and all(e.get("LC_ALL") == "de_DE.UTF-8" for e in cfg), cfg)


class FilterNames(tt.Base):
    """A driver whose name holds a non-UTF-8 byte, a dot or an uppercase letter is blanked too (review-code-215w): in a
    UTF-8 locale git's config regex `.` never matched the 0xff byte, so that driver ran."""

    def test_no_driver_runs_whatever_its_name(self):
        for name in (b"d\xffx", b"Up.Dot"):
            for kind, tail in (("clean", "cat"), ("smudge", "cat"), ("process", "exit 1")):
                with self.subTest(name=name, kind=kind), mock.patch.dict(os.environ, {"LC_ALL": "C.UTF-8"}):
                    tmp = tempfile.mkdtemp(dir=self.tmp)
                    repo = gitfix.make_repo(os.path.join(tmp, "repo"))
                    gitfix.commit(repo, {".gitattributes": b"*.dat filter=" + name + b"\n", "a.dat": "a\n", "b.dat": "b\n"},
                                  "base")
                    gitfix.commit(repo, {"a.dat": "a 2\n"}, "two")
                    write(repo, "a.dat", b"staged\n")
                    gitfix.run(["git", "add", "a.dat"], repo)  # before the driver exists: --staged has a change
                    marker = os.path.join(tmp, "ran")
                    subprocess.run([b"git", b"config", b"filter." + name + b"." + kind.encode(),
                                    ("touch '%s'; %s" % (marker, tail)).encode()], cwd=repo, env=gitfix.env(), check=True)
                    write(repo, "a.dat", b"edited\n")
                    t = os.stat(os.path.join(repo, "b.dat")).st_mtime
                    os.utime(os.path.join(repo, "b.dat"), (t + 7, t + 7))  # stat-dirty: git rereads it through the filter
                    for args in ([], ["--unstaged"], ["--staged"], ["--repo"], ["a.dat"], ["HEAD~1"]):
                        rc, out = tt.call(args, repo, tmp)
                        self.assertEqual((rc, out["status"]), (0, "ok"), (args, out))
                        self.assertFalse(os.path.exists(marker), "a %s driver ran on %s" % (kind, args))
                    os.remove(os.path.join(repo, "b.dat"))
                    with gitfix.isolated(tmp):
                        target._run(["checkout", "--", "b.dat"], repo)  # the smudge path: git writes a working file
                    self.assertFalse(os.path.exists(marker), "a %s driver ran on checkout" % kind)


class NoFilterInAnyScript(tfix.Base):
    """Clean-only, smudge-only and process-only spy drivers, configured after the commits, on stat-dirty files: no
    script runs one (rooc), the in-place apply, the head checkout and the restore from HEAD included."""
    base_files = dict(tfix.BASE, **{".gitattributes": "*.c filter=c\n*.s filter=s\n*.p filter=p\n",
                                    "x.c": "c\n", "x.s": "s\n", "x.p": "p\n"})
    feat_files = {"src/calc.py": tfix.FEAT, "x.c": "c 2\n", "x.s": "s 2\n", "x.p": "p 2\n"}

    def setUp(self):
        super().setUp()
        self.marks = {k: os.path.join(self.tmp, "ran-" + k) for k in ("clean", "smudge", "process", "late")}
        for drv, key, tail in (("c", "clean", "cat"), ("s", "smudge", "cat"), ("p", "process", "exit 1")):
            self.git("config", "filter.%s.%s" % (drv, key), "touch '%s'; %s" % (self.marks[key], tail))
        for n in ("x.c", "x.s", "x.p"):
            t = os.stat(os.path.join(self.repo, n)).st_mtime
            os.utime(os.path.join(self.repo, n), (t + 7, t + 7))

    def check(self, step):
        self.assertEqual([k for k, m in sorted(self.marks.items()) if os.path.exists(m)], [], step)

    def test_no_script_runs_a_filter(self):
        run, fid = self.ready()  # target.py on main..feat, ledger build and trust, report ingest and verdicts
        self.check("target, ledger, report")
        late = ("import subprocess; subprocess.run(['git', 'config', 'filter.late.clean', %r]); "
                "open('.git/info/attributes', 'a').write('x.c filter=late\\n')" % ("touch '%s'; cat" % self.marks["late"]))
        words = [sys.executable, "-B", "-c", "import sys; sys.path.insert(0, 'src'); import calc; open('x.s', 'w').write('t\\n'); "
                 + late]
        self.go(run, words)  # the tests change x.s, which fix.py restores from HEAD, and define a filter fix.py must not run
        self.check("fix plan and baseline")
        seal = self.begin(run)[2]["seal"]
        self.edit("src/calc.py", tfix.QUOTE, "return a + b")
        self.assertEqual(self.finish(run, seal)[2]["outcome"], "fixed")
        self.report(run)
        self.assertEqual(cli("report.py", ["render", "--run", run], self.repo, self.tmp)[0], 0)
        self.check("fix begin, finish and report, report render")
        src = "import sys\nprint('REPRO-F-0123456789')\nsys.exit(1)\n"
        rc, out, err = cli("repro.py", ["run", "--run", run, "--id", "F-0123456789", "--test", "t.py", "--",
                                        sys.executable, "t.py"], self.repo, self.tmp, src.encode())
        self.assertEqual(json.loads(out)["outcome"], "reproduced", out + err)
        self.check("repro on the head")
        write(self.repo, "x.c", "c 3\n")
        run = self.pin("--unstaged")
        rc, out, err = cli("repro.py", ["run", "--run", run, "--id", "F-0123456789", "--test", "t.py", "--",
                                        sys.executable, "t.py"], self.repo, self.tmp, src.encode())
        self.assertEqual(json.loads(out)["outcome"], "reproduced", out + err)
        self.check("target in place and repro with its apply")


class LineBreaks(tt.Base):
    def test_a_repository_path_with_lf_pins_raw_bytes(self):
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "re\npo"))  # the run directory sits under it too
        os.symlink("a.py", os.path.join(self.repo, "ln"))
        gitfix.commit(self.repo, {"a.py": "a = 1\n", "b.py": "b = 1\n"}, "base")
        self.git("add", "ln")
        self.git("commit", "-q", "-m", "link")
        write(self.repo, "a.py", "a = 2\n")
        write(self.repo, "n\nl\r", "n = 1\n")
        write(self.repo, 'q"b\\s', "q = 1\n")
        os.remove(os.path.join(self.repo, "ln"))
        os.symlink("b.py", os.path.join(self.repo, "ln"))
        rc, out = tt.call([], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        tree = load(out["run"])["pinned_tree"]
        self.assertEqual([raw(self.repo, "cat-file", "blob", tree + ":" + n) for n in ("a.py", "n\nl\r", 'q"b\\s', "ln")],
                         [b"a = 2\n", b"n = 1\n", b"q = 1\n", b"b.py"])


class FileMode(tt.Base):
    def test_without_core_filemode_the_index_mode_wins_and_new_files_are_not_executable(self):
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        write(self.repo, "x.sh", "x\n")
        os.chmod(os.path.join(self.repo, "x.sh"), 0o755)
        gitfix.commit(self.repo, {"p.txt": "p\n"}, "base")
        self.git("add", "x.sh")
        self.git("commit", "-q", "-m", "x")
        self.git("config", "core.fileMode", "false")
        for n, m in (("x.sh", 0o644), ("p.txt", 0o755)):
            write(self.repo, n, "edited\n")
            os.chmod(os.path.join(self.repo, n), m)
        write(self.repo, "u.sh", "u\n")
        os.chmod(os.path.join(self.repo, "u.sh"), 0o755)
        rc, out = tt.call([], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        tree = load(out["run"])["pinned_tree"]
        modes = {n: raw(self.repo, "ls-tree", tree, n).split(b" ")[0] for n in ("x.sh", "p.txt", "u.sh")}
        self.assertEqual(modes, {"x.sh": b"100755", "p.txt": b"100644", "u.sh": b"100644"})
        self.assertEqual(raw(self.repo, "cat-file", "blob", tree + ":x.sh"), b"edited\n")


class RacyIndex(tt.Base):
    def test_an_edit_racily_clean_against_the_index_enters_the_pinned_tree(self):
        """Same size, mtime and inode as its index entry, which is as new as the index file: only git's racy check,
        keyed on the index file's mtime, finds the edit, so the index copy must keep that mtime (jd4i)."""
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        self.git("config", "core.trustctime", "false")  # rewriting the file moves ctime, which would flag it anyway
        a, t = os.path.join(self.repo, "a.py"), 1_600_000_000_123_456_789
        write(self.repo, "a.py", "a = 1\n")
        os.utime(a, ns=(t, t))
        gitfix.commit(self.repo, {"b.py": "b = 1\n"}, "base")
        self.git("add", "a.py")
        self.git("commit", "-q", "-m", "a")
        write(self.repo, "a.py", "a = 2\n")  # in place: the inode stays
        os.utime(a, ns=(t, t))
        os.utime(os.path.join(self.repo, ".git", "index"), ns=(t, t))  # last: no git call may rewrite the index after it
        rc, out = tt.call(["--unstaged"], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        self.assertEqual(raw(self.repo, "cat-file", "blob", load(out["run"])["pinned_tree"] + ":a.py"), b"a = 2\n")


class Symlinks(tt.Base):
    def test_an_edited_symlink_enters_the_tree_as_a_link(self):
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        os.symlink("a.py", os.path.join(self.repo, "ln"))
        gitfix.commit(self.repo, {"a.py": "a = 1\n", "b.py": "b = 1\n"}, "base")
        self.git("add", "ln")
        self.git("commit", "-q", "-m", "link")
        os.remove(os.path.join(self.repo, "ln"))
        os.symlink("b.py", os.path.join(self.repo, "ln"))
        rc, out = tt.call([], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        tree = load(out["run"])["pinned_tree"]
        self.assertEqual(raw(self.repo, "ls-tree", tree, "ln").split(b" ")[0], b"120000")
        self.assertEqual(raw(self.repo, "cat-file", "blob", tree + ":ln"), b"b.py")


def starts(path):
    """argv of every git process start recorded in a GIT_TRACE2_EVENT file."""
    with open(path, encoding="utf-8") as fh:
        return [e["argv"] for e in map(json.loads, fh) if e.get("event") == "start" and os.path.basename(e["argv"][0]) == "git"]


class ProcessCount(tt.Base):
    def count(self, n, filtered=0):
        """n edited files; with filtered, a configured driver and that many unedited files it filters."""
        repo = gitfix.make_repo(os.path.join(self.tmp, "r%d-%d" % (n, filtered)))
        if filtered:
            gitfix.run(["git", "config", "filter.up.clean", "tr a-z A-Z"], repo)
        files = dict({"m%02d.py" % i: "v = 0\n" for i in range(n)}, **{"f%02d.dat" % i: "f\n" for i in range(filtered)})
        gitfix.commit(repo, dict(files, **{".gitattributes": "*.dat filter=up\n"} if filtered else {}), "base")
        for i in range(n):
            write(repo, "m%02d.py" % i, "v = 1\n")
        log = os.path.join(self.tmp, "trace%d-%d.json" % (n, filtered))
        with mock.patch.dict(os.environ, {"GIT_TRACE2_EVENT": log}):
            rc, out = tt.call([], repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        return starts(log)

    def test_pin_and_difference_run_a_fixed_number_of_git_processes(self):
        three, thirty = self.count(3), self.count(30)
        self.assertEqual(len(three), len(thirty))
        for argv in (three, thirty):
            self.assertEqual(sum(1 for a in argv if "hash-object" in a and "--stdin-paths" in a and "--no-filters" in a), 1)
            self.assertEqual(sum(1 for a in argv if "update-index" in a and "--index-info" in a), 1)
            self.assertEqual(sum(1 for a in argv if "ls-files" in a and "--others" in a), 1)
            self.assertEqual(sum(1 for a in argv if "diff" in a and "--raw" in a), 1)
            self.assertEqual(sum(1 for a in argv if "config" in a and "core.fileMode" in a), 1)
            self.assertEqual([a for a in argv if "check-attr" in a], [])  # no driver configured: no attribute read

    def untracked(self, n):
        repo = gitfix.make_repo(os.path.join(self.tmp, "u%d" % n))
        gitfix.commit(repo, {"t.py": "t = 0\n"}, "base")
        for i in range(n):
            write(repo, "u%02d.py" % i, "u = %d\n" % i)
        log = os.path.join(self.tmp, "trace-u%d.json" % n)
        with mock.patch.dict(os.environ, {"GIT_TRACE2_EVENT": log}):
            rc, out = tt.call(["--unstaged"], repo, self.tmp)
        self.assertEqual((rc, out["status"], out["untracked_count"]), (0, "ok", n), out)
        with open(os.path.join(out["run"], "change.diff"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().count("new file mode"), n)
        return starts(log)

    def test_untracked_files_reach_the_change_diff_in_a_fixed_number_of_git_processes(self):
        three, thirty = self.untracked(3), self.untracked(30)
        self.assertEqual(len(three), len(thirty))
        self.assertEqual([a for a in three + thirty if "--no-index" in a], [])

    def test_a_path_target_lists_untracked_files_once(self):
        repo = gitfix.make_repo(os.path.join(self.tmp, "p"))
        gitfix.commit(repo, {"d/a.py": "a = 0\n", "da.py": "x = 0\n"}, "base")
        for n in ("d/new.py", "dz.py", "top.py"):
            write(repo, n, "n = 1\n")
        log = os.path.join(self.tmp, "trace-path.json")
        with mock.patch.dict(os.environ, {"GIT_TRACE2_EVENT": log}):
            rc, out = tt.call(["d"], repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        self.assertEqual(sum(1 for a in starts(log) if "ls-files" in a and "--others" in a), 1)
        tj = load(out["run"])
        self.assertEqual((tj["included"], tj["untracked"]), (["d/a.py", "d/new.py"], ["d/new.py"]))  # not dz.py
        names = set(raw(repo, "ls-tree", "-r", "--name-only", tj["pinned_tree"]).decode().split("\n"))
        self.assertLessEqual({"d/new.py", "dz.py", "top.py"}, names)  # the pin still takes the whole checkout
        rc, out = tt.call([".."], os.path.join(repo, "d"), self.tmp)  # a path target naming the top: its spec is "."
        self.assertEqual((out["kind"], load(out["run"])["untracked"]), ("path", ["d/new.py", "dz.py", "top.py"]))

    def test_unedited_filtered_files_cost_a_fixed_number_of_git_processes(self):
        three, thirty = self.count(3, 3), self.count(3, 30)
        self.assertEqual(len(three), len(thirty))
        for argv in (three, thirty):
            self.assertEqual(sum(1 for a in argv if "check-attr" in a and "--stdin" in a), 1)
            self.assertEqual(sum(1 for a in argv if "hash-object" in a and "--stdin-paths" in a), 1)


class UntrackedDiff(tt.Base):
    def test_untracked_sections_match_git_diff_no_index(self):
        """The one diff of pinned blobs prints each kept untracked file as `git diff --no-index /dev/null` does."""
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, {"t.py": "t = 0\n"}, "base")
        write(self.repo, "t.py", "t = 1\n")
        files = {"exe.sh": "#!/bin/sh\n", "empty.txt": "", "nonl.txt": "no newline", "crlf.txt": "a\r\nb\r\n",
                 "sp ace.py": "s = 1\n", 't\tq".py': "q = 1\n", "sub/dir/deep.py": "d = 1\n", "a.b": "x\n", "a/c.py": "y\n",
                 "lock/package-lock.json": "{}\n"}  # the lockfile is excluded: it must not appear
        for n, d in files.items():
            write(self.repo, n, d)
        os.chmod(os.path.join(self.repo, "exe.sh"), 0o755)
        os.symlink("t.py", os.path.join(self.repo, "link"))
        rc, out = tt.call([], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        with open(os.path.join(out["run"], "change.diff"), "rb") as fh:
            got = fh.read()
        want = b"".join(subprocess.run(["git", "diff", "--no-index", "--text"] + target._DIFF_FLAGS + ["--", "/dev/null", n],
                                       cwd=self.repo, env=gitfix.env(), stdout=subprocess.PIPE).stdout
                        for n in sorted(set(files) - {"lock/package-lock.json"} | {"link"}))
        self.assertEqual(got[got.index(b"diff --git", 1):], want)
        self.assertFalse(os.path.exists(os.path.join(out["run"], "untracked.index.tmp")))


class LargeRepo(tt.Base):
    """10k tracked files, a 3-file change, packed: the pin reads no blob outside the difference."""

    def setUp(self):
        super().setUp()
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "big"))
        out = []
        for ref, files in (("main", {"f/%05d.txt" % i: "v%d\n" % i for i in range(10000)}),
                           ("feat", {"f/%05d.txt" % i: "changed %d\n" % i for i in (1, 500, 9999)})):
            out.append("commit refs/heads/%s\ncommitter F <f@x> 0 +0000\ndata 1\nc\n" % ref)
            if ref == "feat":
                out.append("from refs/heads/main\n")
            out += ["M 100644 inline %s\ndata %d\n%s\n" % (p, len(d), d) for p, d in files.items()]
        subprocess.run(["git", "fast-import", "--quiet"], cwd=self.repo, env=gitfix.env(), input="".join(out).encode(), check=True)
        self.git("reset", "-q", "--hard", "main")
        self.git("repack", "-adq")
        self.git("prune-packed")
        self.git("remote", "add", "origin", self.repo)  # gh#49: its default-branch tip is this main, so it is synced
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")  # the remote behind it

    def blob_offsets(self):
        pack = [f for f in os.listdir(os.path.join(self.repo, ".git", "objects", "pack")) if f.endswith(".idx")][0]
        rows = raw(self.repo, "verify-pack", "-v", os.path.join(".git", "objects", "pack", pack)).decode().split("\n")
        return {int(r.split()[4]): r.split()[0] for r in rows if len(r.split()) >= 5 and r.split()[1] == "blob"}

    def test_a_three_file_change_records_three_paths_and_reads_no_other_blob(self):
        log = os.path.join(self.tmp, "pack-access")
        with mock.patch.dict(os.environ, {"GIT_TRACE_PACK_ACCESS": log}):
            rc, out = tt.call(["main..feat"], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        diff = difference(out["run"])
        self.assertEqual(diff, {"f/%05d.txt" % i: "modified" for i in (1, 500, 9999)})
        allowed = {raw(self.repo, "rev-parse", "%s:%s" % (rev, p)).decode().strip() for rev in ("main", "feat") for p in diff}
        offsets = self.blob_offsets()
        with open(log, encoding="utf-8") as fh:
            read = {offsets[int(ln.split()[-1])] for ln in fh if ln.split() and int(ln.split()[-1]) in offsets}
        self.assertTrue(read, "the trace recorded no blob access")
        self.assertLessEqual(read, allowed)


class LaneInputs(tt.Base):
    def test_lane_inputs_point_to_the_difference_file_and_list_none_of_its_paths(self):
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, {"a.py": "a = 1\n", "zz_other.py": "o = 1\n"}, "base")
        write(self.repo, "a.py", "a = 2\n")
        self.git("add", "a.py")
        write(self.repo, "zz_other.py", "o = 2\n")
        rc, out = tt.call(["--staged"], self.repo, self.tmp)
        self.assertEqual(rc, 0, out)
        run = out["run"]
        rc, out, err = cli("ledger.py", ["build", "--run", run], self.repo, self.tmp)
        self.assertEqual(rc, 0, out + err)
        path = load(run)["difference"]
        inputs = os.path.join(run, "inputs")
        for name in ("defects.md", "standards.md", "spec.md", "spec-find.md"):
            with self.subTest(name=name), open(os.path.join(inputs, name), encoding="utf-8") as fh:
                text = fh.read()
                self.assertIn("checkout_difference: %s (1 path)" % path, text)
                self.assertNotIn("zz_other.py", text)
        led = load(run, "ledger.json")["target"]
        self.assertEqual({k: led[k] for k in ("pinned_tree", "pinned_head", "checkout_head", "difference", "difference_count")},
                         {k: load(run)[k] for k in ("pinned_tree", "pinned_head", "checkout_head", "difference", "difference_count")})
        cli("ledger.py", ["trust", "set", "own", "--run", run], self.repo, self.tmp)
        rc, out, _ = cli("ledger.py", ["context", "--run", run], self.repo, self.tmp)
        ctx = json.loads(out)
        self.assertEqual(ctx["difference"], 1)
        self.assertIn("checkout difference: 1 path, listed in " + path, ctx["notes"])


class Basis(tpost.Base):
    """The plan step's tool list, then the report and the posted body."""

    def plan(self, *tools):
        rc, out, err = cli("plan.py", ["plan", "--run", self.run_dir] + [a for t in tools for a in ("--tool", t)],
                           self.repo, self.tmp)
        self.assertEqual((rc, err), (0, ""), out)
        return json.loads(out)

    def test_a_tool_not_passed_is_a_lost_capability_in_plan_report_and_post(self):
        self.review(5)  # ingest and verdicts first: with a plan, verdicts take batches
        out = self.plan("serena")
        self.assertEqual(out["lost_capabilities"], ["codegraph: callers and blast radius"])
        self.assertEqual(load(self.run_dir, "plan.json")["tools"], ["serena"])
        rc, text, _ = cli("report.py", ["render", "--run", self.run_dir], self.repo, self.tmp)
        head = load(self.run_dir)["head_sha"]
        self.assertIn("basis: pinned head %s, checkout difference 0 paths" % head, text)
        self.assertIn("lost capability: codegraph: callers and blast radius", text)
        doc = json.loads(cli("report.py", ["render", "--run", self.run_dir, "--format", "json"], self.repo, self.tmp)[1])
        self.assertEqual(doc["basis"], {"pinned_head": head, "pinned_tree": load(self.run_dir)["pinned_tree"], "difference": 0,
                                        "lost_capabilities": ["codegraph: callers and blast radius"]})
        rc, _, res = self.build()
        self.assertEqual(rc, 0, res)
        body = self.payload()[1]["body"]
        self.assertIn("**Pinned:** head %s, checkout difference 0 paths; lost capability: codegraph: callers and blast radius"
                      % head, body.split("\n"))

    def test_both_tools_passed_lose_nothing(self):
        self.assertEqual(self.plan("codegraph", "serena")["lost_capabilities"], [])

    def test_no_tool_passed_loses_both(self):
        self.assertEqual(self.plan()["lost_capabilities"], ["codegraph: callers and blast radius",
                                                             "serena: symbol and reference lookup"])

    def test_an_unknown_tool_is_a_usage_error(self):
        rc, out, _ = cli("plan.py", ["plan", "--run", self.run_dir, "--tool", "grep"], self.repo, self.tmp)
        self.assertEqual((rc, json.loads(out)["reason"]), (1, "usage"))


class PinnedListingArgMax(tt.Base):
    """review-code-wgk8: ledger._pinned_listing named every difference path on one ls-tree command line, so a difference past
    ARG_MAX of path bytes killed the build. It runs the shared chunking helper: one process per budget of path bytes."""

    def setUp(self):
        super().setUp()
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, {"a.py": "a = 1\n", "d/b.py": "b = 1\n"}, "base")
        self.tree = raw(self.repo, "rev-parse", "HEAD^{tree}").decode().strip()

    def listing(self, paths):
        log = os.path.join(self.tmp, "trace%d.json" % len(os.listdir(self.tmp)))
        with mock.patch.dict(os.environ, {"GIT_TRACE2_EVENT": log}):
            got = ledger._pinned_listing(self.repo, self.tree, paths)
        return got, [x for x in starts(log) if "ls-tree" in x]

    def test_a_small_difference_costs_one_ls_tree(self):
        got, procs = self.listing(["a.py", "d/b.py", "gone.py"])
        self.assertEqual(sorted(got), ["a.py", "d/b.py"])
        self.assertEqual(len(procs), 1)

    def test_a_difference_past_arg_max_is_listed_in_chunks_not_refused(self):
        many = ["d%04d/%s" % (k, "x" * 3990) for k in range(os.sysconf("SC_ARG_MAX") // 4000 + 2)]
        got, procs = self.listing(["a.py"] + many + ["d/b.py"])
        self.assertEqual(sorted(got), ["a.py", "d/b.py"])
        self.assertTrue(1 < len(procs) < 2 * len(many) * 4000 // ledger.ARGV_BYTES)

    def test_a_pruned_tree_refuses_pin_pruned(self):
        with self.assertRaises(target.Refuse) as cm:
            ledger._pinned_listing(self.repo, "0" * 40, ["a.py"])
        self.assertEqual(cm.exception.reason, "pin-pruned")


if __name__ == "__main__":
    unittest.main()
