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


    def test_a_downgraded_neighbour_never_absorbs_a_real_finding(self):
        gone = tr.cand("return a - b", 5, title="pkg/far.py is missing", severity="blocking")
        out = self.ingest(gone, tr.cand("def add(a, b):", 4))
        self.assertEqual(out["ids"], [tr.fid("src/calc.py", "bug", "def add(a, b):")])
        self.assertEqual(out["merged"], 0)


class Invisible(tr.Base):
    """gh#52: lanes read unchanged files raw from the checkout, so a quote may carry an invisible character the stored rows
    hold escaped. The quote is escaped once at the boundary; hidden prose stays uncitable."""
    base_files = dict(tr.BASE, **{"pkg/zw.py": "x = 1​\n", "docs/note.md": "a\n<!-- hidden note -->\nb\n"})

    def test_a_raw_invisible_character_in_a_quote_matches_and_is_stored_escaped(self):
        out = self.ingest(dict(tr.CAND, path="pkg/zw.py", line=1, quote="x = 1​"))
        self.assertEqual(reasons(out), [("accepted", None)])
        with open(os.path.join(self.run_dir, "candidates.json"), encoding="utf-8") as fh:
            (c,) = json.load(fh)["candidates"]
        self.assertEqual(c["quote"], "x = 1<U+200B>")
        self.assertEqual(out["ids"], [tr.fid("pkg/zw.py", "bug", "x = 1<U+200B>")])

    def test_a_verdict_cite_with_a_raw_invisible_character_is_stored_escaped(self):
        with open(os.path.join(self.run_dir, "plan.json"), "w") as fh:
            json.dump({"caps": {"max_verifier_calls": 5}}, fh)
        (i,) = self.ingest(tr.CAND)["ids"]
        rc, out = self.rep("verdicts", tr.lines({"id": i, "verdict": "refuted", "reason": "r",
                                                 "evidence": [{"path": "pkg/zw.py", "line": 1, "quote": "x = 1​"}]}),
                           extra=["--batch", "1"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out)["refuted"], [i])
        with open(os.path.join(self.run_dir, "verdicts.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)[i]["cites"], [{"path": "pkg/zw.py", "line": 1, "quote": "x = 1<U+200B>"}])

    def test_a_quote_of_hidden_prose_is_not_at_the_line_not_from_the_checkout(self):
        out = self.ingest(dict(tr.CAND, path="docs/note.md", line=2, quote="hidden note"))
        self.assertEqual(reasons(out), [("rejected", "quote-not-at-line")])


class MissingSymbol(tr.Base):
    def test_a_finding_that_calls_a_ledger_symbol_undefined_is_downgraded(self):
        out = self.ingest(tr.cand("return a - b", 5, title="function load is not defined", trigger="NameError: name load"))
        self.assertEqual(reasons(out), [("downgraded", "claims-missing")])
        self.assertEqual(out["ids"], [])


class OldRun(tr.Base):
    """review-code-1kf3: a run directory older than the pin (14-day retention) has no pinned_tree to judge a cite against."""

    def age(self):
        tj = tp.load(self.run_dir)
        for k in ("pinned_tree", "pinned_head", "checkout_head", "difference", "difference_count", "integrity"):
            tj.pop(k, None)
        with open(os.path.join(self.run_dir, "target.json"), "w", encoding="utf-8") as fh:
            json.dump(tj, fh)

    def refused(self, sub, text, extra=()):
        rc, out, err = gitfix.cli("report.py", [sub, "--run", self.run_dir] + list(extra), self.repo, self.tmp, text)
        self.assertEqual((rc, err, out.count("\n")), (1, "", 1), out)
        return json.loads(out)

    def test_ingest_refuses_with_one_json_line_naming_the_old_run(self):
        self.age()
        self.assertEqual(self.refused("ingest", tr.lines(tr.CAND)), {"status": "refuse", "reason": "no-pin"})

    def test_verdicts_refuse_with_one_json_line_naming_the_old_run(self):
        with open(os.path.join(self.run_dir, "plan.json"), "w") as fh:
            json.dump({"caps": {"max_verifier_calls": 5}}, fh)
        (i,) = self.ingest(tr.CAND)["ids"]
        self.age()
        out = self.refused("verdicts", tr.lines({"id": i, "verdict": "confirmed", "evidence": [], "reason": "r"}), ["--batch", "1"])
        self.assertEqual(out, {"status": "refuse", "reason": "no-pin"})


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
