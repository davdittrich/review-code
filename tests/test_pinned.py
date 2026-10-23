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
        gitfix.commit(self.repo, {".gitattributes": "*.dat filter=spy text\n", "lib.dat": "abc\n", "keep.py": "k = 1\n"}, "base")
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
    def count(self, n):
        repo = gitfix.make_repo(os.path.join(self.tmp, "r%d" % n))
        gitfix.commit(repo, {"m%02d.py" % i: "v = 0\n" for i in range(n)}, "base")
        for i in range(n):
            write(repo, "m%02d.py" % i, "v = 1\n")
        log = os.path.join(self.tmp, "trace%d.json" % n)
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
            self.assertEqual(sum(1 for a in argv if "diff" in a and "--name-status" in a), 1)


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


if __name__ == "__main__":
    unittest.main()
