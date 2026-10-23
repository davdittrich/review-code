"""gh#52: tree/ holds exactly the checkout difference; every other file is read from the user's repository."""
import json
import os
import unittest

import paths  # noqa: F401

import gitfix  # noqa: E402
import test_pinned as tp  # noqa: E402
import test_plan as tpl  # noqa: E402
import test_report as tr  # noqa: E402
import test_target as tt  # noqa: E402

CANARY = "src/" + tt.CANARY_NAME + ".py"


def files(tree):
    """{path relative to tree: text} of every file under tree."""
    return {os.path.relpath(os.path.join(r, f), tree): open(os.path.join(r, f), encoding="utf-8").read()
            for r, _, fs in os.walk(tree) for f in fs}


def mentions(run, word):
    """Run-directory files whose text names `word`, change.diff and the copies aside."""
    hits = []
    for r, dirs, fs in os.walk(run):
        dirs[:] = [d for d in dirs if d not in ("tree", "conventions")]
        for f in fs:
            if f != "change.diff":
                with open(os.path.join(r, f), encoding="utf-8", errors="replace") as fh:
                    if word in fh.read():
                        hits.append(os.path.relpath(os.path.join(r, f), run))
    return hits


class PRTree(tt.PRWorld):
    """A synced checkout on main reviewing PR 7, which is not checked out."""

    def build(self):
        rc, out = tt.call(["7"], self.repo, self.tmp, gh=self.gh())
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        run = out["run"]
        for argv in (["build", "--run", run], ["trust", "set", "own", "--run", run]):
            rc, got, err = gitfix.cli("ledger.py", argv, self.repo, self.tmp)
            self.assertEqual(rc, 0, got + err)
        return run

    def test_tree_holds_exactly_the_pinned_content_of_the_difference(self):
        tp.write(self.repo, "src/cfg.py", "def load():\n    return 99\n")  # local work on a difference path
        run = self.build()
        self.assertEqual(tp.difference(run), {"src/cfg.py": "modified", CANARY: "absent-in-checkout", "src/other.py": "untracked"})
        # the untracked path has no pinned content; the others hold the PR head's blobs, not the checkout's edit
        self.assertEqual(files(os.path.join(run, "tree")), {"src/cfg.py": "def load():\n    return 2\n", CANARY: "x = 1\n"})

    def test_nothing_in_the_run_names_the_helper_copy(self):
        run = self.build()
        rc, out, err = gitfix.cli("ledger.py", ["context", "--run", run], self.repo, self.tmp)
        self.assertEqual(rc, 0, out + err)
        rc, plan, err = gitfix.cli("plan.py", ["plan", "--run", run], self.repo, self.tmp)
        self.assertEqual(rc, 0, plan + err)
        self.assertNotIn("helper-cap", out + plan)
        self.assertEqual(mentions(run, "helper-cap"), [])
        self.assertNotIn("helpers", tp.load(run, "ledger.json"))


class Context(tr.Base):
    """A file the change calls stays in the repository: the ledger points at it, tree/ never copies it."""
    base_files = dict(tr.BASE, **{"src/util.py": "def norm(x):\n    return x or 0\n", "tests/test_calc.py": "import calc\n"})
    feat_files = {"src/calc.py": tr.FEAT + "\nfrom util import norm\n\ndef safe(a):\n    return norm(a)\n"}

    def test_tree_holds_no_context_or_helper_file(self):
        with open(os.path.join(self.run_dir, "ledger.json"), encoding="utf-8") as fh:
            led = json.load(fh)
        self.assertNotIn("helpers", led)
        self.assertNotIn("helpers", led["capped"])
        self.assertNotIn("helper-cap", [k["reason"] for k in led["tree"]["skipped"]])
        self.assertEqual(files(os.path.join(self.run_dir, "tree")), {})  # the reviewed head is checked out: no difference

    def test_a_verdict_citing_the_called_helper_is_confirmed(self):
        with open(os.path.join(self.run_dir, "plan.json"), "w") as fh:
            json.dump({"caps": {"max_verifier_calls": 5}}, fh)
        (i,) = self.ingest(tr.cand("return norm(a)", 9, title="safe(None) returns 0", trigger="safe(None)"))["ids"]
        helper = {"path": "src/util.py", "line": 2, "quote": "return x or 0"}
        rc, out = self.rep("verdicts", tr.lines({"id": i, "verdict": "confirmed", "evidence": [helper], "reason": "r"}),
                           extra=["--batch", "1"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out)["confirmed"], [i])
        with open(os.path.join(self.run_dir, "verdicts.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)[i]["cites"], [helper])


class Greppable(tt.Base):
    """The evidence rule greps difference.json for a path, so a non-ASCII name is stored as UTF-8, not as a \\u escape."""

    def test_a_non_ascii_difference_path_is_stored_as_written(self):
        repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(repo, {"a.py": "a = 1\n", "café.py": "x = 1\n"}, "base")
        tp.write(repo, "a.py", "a = 2\n")
        tp.raw(repo, "add", "a.py")
        tp.write(repo, "café.py", "x = 2\n")
        rc, out = tt.call(["--staged"], repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        self.assertEqual(tp.difference(out["run"]), {"café.py": "modified"})
        with open(tp.load(out["run"])["difference"], "rb") as fh:
            self.assertIn('"café.py"'.encode("utf-8"), fh.read())


class ShardDifference(tpl.ShardBase):
    """A path target whose difference is not empty: a tracked file the pin could not hash (over MAX_FILE on disk) keeps
    its committed blob in the pinned tree. Every shard's tree holds the whole difference."""
    base_files = {"big/a/x.py": tpl.BIG, "big/b/y.py": tpl.BIG, "small1/huge.py": "h = 1\n", "small2/n.py": tpl.SMALL}
    feat_files = {"big/a/x.py": tpl.BIG + "z = 2\n"}

    def setUp(self):
        gitfix.FeatureRepo.setUp(self)
        tp.write(self.repo, "small1/huge.py", b"#" * 4_000_001)
        self.run_dir = self.pin(self.pin_args)

    def test_every_shard_tree_holds_the_whole_difference_and_cites_hold_across_shards(self):
        self.assertEqual(tp.difference(self.run_dir), {"small1/huge.py": "modified"})
        p = self.run_plan()
        self.assertGreater(len(p["shards"]), 1)
        for s in [None] + [x["id"] for x in p["shards"]]:
            base = self.run_dir if s is None else os.path.join(self.run_dir, "shards", str(s))
            self.assertEqual(files(os.path.join(base, "tree")), {"small1/huge.py": "h = 1\n"}, s)
        one, two = p["shards"][0], p["shards"][1]
        other = next(f for f in one["files"] if f not in two["files"])
        cites = [dict(tr.CAND, path=other, line=1, quote="xxxxxxxx"), dict(tr.CAND, path="small1/huge.py", line=1, quote="h = 1")]
        rc, out = self.rep("ingest", tr.lines(*cites), extra=["--lane", "defects", "--shard", str(two["id"])])
        self.assertEqual(rc, 0, out)
        self.assertEqual(tr.json.loads(out)["dropped"]["invalid"], 0)


class NonAsciiRun(tt.Base):
    """review-code-98hy: the handoff's `checkout_difference:` line names a run-directory file; under a non-ASCII repository
    path it holds the path as written (a \\u escape names no file a lane could open)."""

    def test_the_handoff_line_holds_a_non_ascii_run_path_as_written(self):
        import report
        repo = gitfix.make_repo(os.path.join(self.tmp, "répo"))
        gitfix.commit(repo, {"a.py": "a = 1\n"}, "base")
        tp.write(repo, "a.py", "a = 2\n")
        tp.raw(repo, "add", "a.py")
        rc, out = tt.call(["--staged"], repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        diff = tp.load(out["run"])["difference"]
        c = dict(tr.CAND, path="a.py", line=1, id="d1", title="t", trigger="g", axis="defects", end_line=1)
        body = report._handoff(c, out["run"], "nonce", (), {}, diff)
        self.assertIn("\ncheckout_difference: " + json.dumps(diff, ensure_ascii=False) + "\n", body)
        self.assertIn("répo", body)

    def test_every_handoff_path_line_holds_a_non_ascii_path_as_written(self):
        """review-code-re8i: tree:, history:, spec: and location: are read by a lane; an escape names no file."""
        import report
        repo = gitfix.make_repo(os.path.join(self.tmp, "répo"))
        gitfix.commit(repo, {"é.py": "a = 1\n"}, "base")
        tp.write(repo, "é.py", "a = 2\n")
        tp.raw(repo, "add", "é.py")
        rc, out = tt.call(["--staged"], repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        run = out["run"]
        os.makedirs(os.path.join(run, "history"), exist_ok=True)
        with open(os.path.join(run, "history", "é.py.txt"), "w", encoding="utf-8") as fh:
            fh.write("h\n")
        c = dict(tr.CAND, path="é.py", line=1, id="d1", title="t", trigger="g", axis="spec", spec_line=3, end_line=1)
        body = report._handoff(c, run, "nonce", (), {}, None)
        for want in (os.path.join(run, "tree"), os.path.join(run, "history", "é.py.txt"),
                     os.path.join(run, "inputs", "spec.md")):
            self.assertIn(json.dumps(want, ensure_ascii=False), body)
        self.assertIn("\nlocation: " + json.dumps("é.py:1", ensure_ascii=False) + "\n", body)
        self.assertNotIn("\\u", body)


if __name__ == "__main__":
    unittest.main()
