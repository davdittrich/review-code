"""gh#50: ingest and verdicts judge every cite against the pinned tree, not against the run directory's tree/ copy."""
import json
import os
import unittest

import paths  # noqa: F401

import gitfix  # noqa: E402
import test_pinned as tp  # noqa: E402
import test_report as tr  # noqa: E402

FAR = {"pkg/far.py": "def far():\n    return 42\n"}
FAR_CITE = {"path": "pkg/far.py", "line": 2, "quote": "return 42"}


def reasons(out):
    return [(d["disposition"], d["reason"]) for d in out["dispositions"]]


class Outside(tr.Base):
    """pkg/far.py is tracked and unchanged, so the ledger never copies it into tree/."""
    base_files = dict(tr.BASE, **FAR)

    def setUp(self):
        super().setUp()
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "tree", "pkg", "far.py")))

    def test_a_cite_on_a_file_never_copied_is_accepted_when_the_pinned_blob_holds_the_quote(self):
        out = self.ingest(dict(tr.CAND, **FAR_CITE), {"inspected": ["pkg/far.py"]})
        self.assertEqual(reasons(out), [("accepted", None), ("accepted", None)])
        self.assertEqual(out["ids"], [tr.fid("pkg/far.py", "bug", "return 42")])
        self.assertEqual(self.doc()["inspected"]["areas"], ["pkg/far.py"])

    def test_a_path_outside_the_pinned_tree_is_rejected_as_not_in_the_reviewed_content(self):
        out = self.ingest(dict(tr.CAND, path="pkg/gone.py", line=1, quote="x"))
        self.assertEqual(reasons(out), [("rejected", "path-not-in-tree")])

    def test_a_quote_only_the_checkout_holds_is_rejected_with_its_own_reason(self):
        tp.write(self.repo, "src/calc.py", tr.FEAT.replace("a - b", "a + b"))  # edited after pinning
        tp.write(self.repo, "src/new.py", "fresh = 1\n")  # untracked: only the checkout has it
        out = self.ingest(tr.cand("return a + b", 5), dict(tr.CAND, path="src/new.py", line=1, quote="fresh = 1"),
                          tr.cand("return a - b", 5))
        self.assertEqual(reasons(out), [("rejected", "quote-from-checkout"), ("rejected", "quote-from-checkout"),
                                        ("accepted", None)])

    def test_a_refuted_verdict_citing_a_file_outside_the_copy_is_stored_refuted(self):
        with open(os.path.join(self.run_dir, "plan.json"), "w") as fh:
            json.dump({"caps": {"max_verifier_calls": 5}}, fh)
        (i,) = self.ingest(tr.CAND)["ids"]
        rc, out = self.rep("verdicts", tr.lines({"id": i, "verdict": "refuted", "evidence": [FAR_CITE], "reason": "r"}),
                           extra=["--batch", "1"])
        self.assertEqual(rc, 0, out)
        out = json.loads(out)
        self.assertEqual((out["refuted"], reasons(out)), ([i], [("accepted", None)]))
        with open(os.path.join(self.run_dir, "verdicts.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)[i]["cites"], [FAR_CITE])

    def test_a_refuted_verdict_citing_only_the_checkout_is_downgraded_with_its_own_reason(self):
        with open(os.path.join(self.run_dir, "plan.json"), "w") as fh:
            json.dump({"caps": {"max_verifier_calls": 5}}, fh)
        (i,) = self.ingest(tr.CAND)["ids"]
        tp.write(self.repo, "pkg/far.py", "def far():\n    return 7\n")
        rc, out = self.rep("verdicts", tr.lines({"id": i, "verdict": "refuted", "reason": "r",
                                                 "evidence": [dict(FAR_CITE, quote="return 7")]}), extra=["--batch", "1"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(reasons(json.loads(out)), [("downgraded", "quote-from-checkout")])

    def test_a_finding_that_calls_a_pinned_file_missing_is_downgraded(self):
        gone = tr.cand("return a - b", 5, title="pkg/far.py is missing", trigger="import of `pkg/far.py` fails: file not found")
        real = dict(tr.CAND, **FAR_CITE, title="pkg/gone.py is missing", trigger="import of pkg/gone.py fails")
        check = tr.cand("def load():", 1, title="load is missing a check", trigger="load() returns 1")
        out = self.ingest(gone, real, check)
        self.assertEqual(reasons(out), [("downgraded", "claims-missing"), ("accepted", None), ("accepted", None)])
        self.assertEqual(sorted(out["ids"]), sorted([tr.fid("pkg/far.py", "bug", "return 42"), tr.fid("src/calc.py", "bug", "def load():")]))
        items = {i["title"]: i for i in self.doc()["unverifiable"]}
        self.assertEqual(items["pkg/far.py is missing"]["reason"], "claims-missing")
        self.assertNotIn("downgraded", items["pkg/far.py is missing"])


class MissingSymbol(tr.Base):
    def test_a_finding_that_calls_a_ledger_symbol_undefined_is_downgraded(self):
        out = self.ingest(tr.cand("return a - b", 5, title="function load is not defined", trigger="NameError: name load"))
        self.assertEqual(reasons(out), [("downgraded", "claims-missing")])
        self.assertEqual(out["ids"], [])


class Worktree(tr.Base):
    """A worktree target: the pinned tree is a synthetic one built from the checkout at pin time."""

    def setUp(self):
        gitfix.FeatureRepo.setUp(self)
        tp.write(self.repo, "src/calc.py", tr.FEAT + "\ndef mul(a, b):\n    return a * a\n")
        self.run_dir = self.pin([])
        self.assertEqual(tp.load(self.run_dir)["kind"], "worktree")

    def test_editing_a_file_after_pinning_does_not_change_a_cite_check(self):
        tp.write(self.repo, "src/calc.py", tr.FEAT + "\ndef mul(a, b):\n    return a * b\n")
        out = self.ingest(tr.cand("return a * a", 8), tr.cand("return a * b", 8))
        self.assertEqual(reasons(out), [("accepted", None), ("rejected", "quote-from-checkout")])


class ProcessCount(tr.Base):
    """One ls-tree and one cat-file --batch per call, whatever the number of cites."""
    base_files = dict(tr.BASE, **{"pkg/f%02d.py" % n: "def f():\n    return %d\n" % n for n in range(20)})

    def cites(self, n):
        return [dict(tr.CAND, path="pkg/f%02d.py" % k, line=2, quote="return %d" % k, title="t%d" % k) for k in range(n)]

    def traced(self, sub, text, extra=()):
        log = os.path.join(self.tmp, "trace-%s-%d.json" % (sub, len(os.listdir(self.tmp))))
        rc, out, err = gitfix.cli("report.py", [sub, "--run", self.run_dir] + list(extra), self.repo, self.tmp, text,
                                  {"GIT_TRACE2_EVENT": log})
        self.assertEqual((rc, err), (0, ""), out)
        return json.loads(out), tp.starts(log) if os.path.exists(log) else []

    def test_ingest_runs_the_same_git_processes_for_one_cite_and_twenty(self):
        one, a = self.traced("ingest", tr.lines(*self.cites(1)))
        twenty, b = self.traced("ingest", tr.lines(*self.cites(20)))
        self.assertEqual((len(one["ids"]), len(twenty["ids"])), (1, 20))
        self.assertEqual(len(a), len(b))
        for argv in (a, b):
            self.assertEqual(sum(1 for x in argv if "ls-tree" in x), 1)
            self.assertEqual(sum(1 for x in argv if "cat-file" in x and "--batch" in x), 1)
            self.assertFalse([x for x in argv if "ls-tree" in x and "-r" in x])

    def test_verdicts_run_the_same_git_processes_for_one_cite_and_twenty(self):
        (i,) = self.ingest(tr.CAND)["ids"]
        line = {"id": i, "verdict": "confirmed", "reason": "r"}
        _, a = self.traced("verdicts", tr.lines(dict(line, evidence=self.cites(1))))
        out, b = self.traced("verdicts", tr.lines(dict(line, evidence=self.cites(20))))
        self.assertEqual(out["confirmed"], [i])
        self.assertEqual(len(a), len(b))
        for argv in (a, b):
            self.assertEqual(sum(1 for x in argv if "ls-tree" in x), 1)
            self.assertEqual(sum(1 for x in argv if "cat-file" in x and "--batch" in x), 1)


if __name__ == "__main__":
    unittest.main()
