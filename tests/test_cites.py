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

    def test_a_path_too_long_to_look_up_is_rejected_with_its_own_reason(self):  # review-code-df4z
        for n in (4095, 4096):  # names of 4095 bytes still go to ls-tree (and are absent); 4096 never does
            path = "/".join(["d" * 254] * 17)[:n]
            out = self.ingest(dict(tr.CAND, path=path, line=1, quote="x"))
            self.assertEqual(reasons(out), [("rejected", "path-not-in-tree" if n == 4095 else "path-too-long")], n)

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
    """review-code-fr02: a symbol the ledger knows is defined in some file; a finding that calls it undefined or not imported
    is about the cited file and stays a finding (only a claim that a file is missing is judged against the pinned tree)."""

    def test_a_finding_that_calls_a_ledger_symbol_undefined_in_the_cited_file_is_accepted(self):
        out = self.ingest(tr.cand("return a - b", 5, title="function load is not defined", trigger="NameError: name load"),
                          tr.cand("def load():", 1, title="undefined name load", trigger="load is missing from the imports"))
        self.assertEqual(reasons(out), [("accepted", None), ("accepted", None)])
        self.assertEqual(len(out["ids"]), 2)


class MissingSymbolNamedLikeAFile(tr.Base):
    """review-code-1luq: a claim about an undefined symbol is no claim that a file is missing, even when a pinned file
    has the symbol's name (an extensionless `load` or `Makefile`)."""
    base_files = dict(tr.BASE, load="#!/bin/sh\n", **{"os.path": "x\n"})

    def test_an_undefined_symbol_claim_stays_a_finding_when_a_pinned_file_has_its_name(self):
        out = self.ingest(tr.cand("return a - b", 5, title="undefined name load", trigger="NameError at call time"),
                          tr.cand("def load():", 1, title="load is not defined", trigger="NameError at call time"),
                          tr.cand("def add(a, b):", 4, title="function load is undefined", trigger="NameError at call time"))
        self.assertEqual(reasons(out), [("accepted", None)] * 3)

    def test_a_file_claim_still_downgrades(self):
        out = self.ingest(tr.cand("return a - b", 5, title="missing file load", trigger="open fails"),
                          tr.cand("def load():", 1, title="load is missing", trigger="open fails"))
        self.assertEqual(reasons(out), [("downgraded", "claims-missing")] * 2)

    def test_a_symbol_missing_from_the_imports_or_scope_stays_a_finding(self):  # review-code-8suk
        out = self.ingest(tr.cand("return a - b", 5, title="load is missing from the imports", trigger="NameError"),
                          tr.cand("def load():", 1, title="bug", trigger="`load` is missing from scope."),
                          tr.cand("def add(a, b):", 4, title="load is missing in the module namespace", trigger="NameError"))
        self.assertEqual(reasons(out), [("accepted", None)] * 3)

    def test_a_dotted_symbol_missing_from_the_imports_stays_a_finding_when_a_pinned_file_has_its_name(self):  # review-code-zpmi
        out = self.ingest(tr.cand("return a - b", 5, title="os.path is missing from the imports", trigger="NameError"),
                          tr.cand("def load():", 1, title="bug", trigger="`os.path` is missing from scope."))
        self.assertEqual(reasons(out), [("accepted", None)] * 2)

    def test_a_dotted_file_claim_still_downgrades(self):
        out = self.ingest(tr.cand("return a - b", 5, title="missing file os.path", trigger="x"),
                          tr.cand("def load():", 1, title="os.path is missing", trigger="x"),
                          tr.cand("def add(a, b):", 4, title="src/calc.py is missing from the imports", trigger="x"))
        self.assertEqual(reasons(out), [("downgraded", "claims-missing")] * 3)

    def test_a_thing_missing_inside_a_pinned_file_stays_a_finding(self):  # review-code-87o1.4 (F-4c7ad0e079)
        out = self.ingest(tr.cand("return a - b", 5, title="Error handling in src/calc.py is missing", trigger="x"),
                          tr.cand("def load():", 1, title="Test for src/calc.py is missing", trigger="x"),
                          tr.cand("def add(a, b):", 4, title="bug", trigger="The guard in the `src/calc.py` is missing."))
        self.assertEqual(reasons(out), [("accepted", None)] * 3)

    def test_a_pinned_file_named_as_the_subject_still_downgrades(self):
        out = self.ingest(tr.cand("return a - b", 5, title="src/calc.py is missing", trigger="x"),
                          tr.cand("def load():", 1, title="The file src/calc.py is missing", trigger="x"),
                          tr.cand("def add(a, b):", 4, title="Origin src/calc.py does not exist", trigger="x"),  # "in" only as a word
                          tr.cand("return 1", 2, title="missing file src/calc.py", trigger="x"))
        self.assertEqual(reasons(out), [("downgraded", "claims-missing")] * 4)

    def test_a_file_missing_from_the_imports_or_the_repo_still_downgrades(self):
        out = self.ingest(tr.cand("return a - b", 5, title="src/calc.py is missing from the imports", trigger="x"),
                          tr.cand("def load():", 1, title="load is missing from the repo", trigger="x"))
        self.assertEqual(reasons(out), [("downgraded", "claims-missing")] * 2)


class InvisibleEntryPoints(tr.Lanes):
    """review-code-kiq3: every other place a lane's raw quote enters (a repaired control character, a lost line's id) reads
    it through report._esc as well, so an invisible character matches the escaped rows and a retry recovers the line."""
    base_files = dict(tr.MBASE, **{"pkg/re.py": 'pat = r"\\bx\u200b"\n', "pkg/zw.py": "x = 1\u200b\n",
                                   "REVIEW.md": tr.RULE_REVIEW + "\n"})
    feat_files = {"src/calc.py": tr.MFEAT["src/calc.py"]}

    def test_a_quote_read_back_after_a_control_character_repair_is_escaped_too(self):
        out = self.lane("defects", dict(tr.CAND, path="pkg/re.py", line=1, end_line=1, quote='pat = r"\x08x\u200b"'))
        self.assertEqual((len(out["ids"]), out["dropped"]["invalid"]), (1, 0), out)
        with open(os.path.join(self.run_dir, "candidates.json"), encoding="utf-8") as fh:
            (c,) = json.load(fh)["candidates"]
        self.assertEqual(c["quote"], 'pat = r"\\bx<U+200B>"')

    def test_a_lost_line_with_a_raw_invisible_quote_is_recovered_by_the_retry_that_carries_its_finding(self):
        conv = dict(tr.CAND, path="pkg/zw.py", line=1, end_line=1, quote="x = 1\u200b", category="convention",
                    rule_source="conventions/REVIEW.md")
        ins = {"inspected": ["pkg/zw.py"]}
        out = self.lane("standards", dict(conv, rule="Always subtract where the name says add."), ins)
        self.assertEqual([r["check"] for r in out["rejected"]], ["rule-not-in-conventions"])
        self.assertEqual(self.doc()["lost"], {"standards": 1})
        self.lane("standards", dict(conv, rule=tr.RULE_REVIEW), ins)
        d = self.doc()
        self.assertEqual((d.get("lost"), d["recovered"]), (None, {"standards": 1}))


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
        self.assertEqual(tr.refusal(self.refused("ingest", tr.lines(tr.CAND))), {"status": "refuse", "reason": "no-pin"})

    def test_verdicts_refuse_with_one_json_line_naming_the_old_run(self):
        with open(os.path.join(self.run_dir, "plan.json"), "w") as fh:
            json.dump({"caps": {"max_verifier_calls": 5}}, fh)
        (i,) = self.ingest(tr.CAND)["ids"]
        self.age()
        out = self.refused("verdicts", tr.lines({"id": i, "verdict": "confirmed", "evidence": [], "reason": "r"}), ["--batch", "1"])
        self.assertEqual(tr.refusal(out), {"status": "refuse", "reason": "no-pin"})


class HiddenName(tr.Base):
    """review-code-s9zi: a file whose name holds a hidden character is never copied or scanned (hidden-name). A cite under its raw
    name is accepted (the pinned blob holds it); one under the escaped name the lanes are shown is rejected."""
    RAW = "src/x" + "".join(chr(0xE0000 + ord(c)) for c in "PWNED") + ".py"
    base_files = tr.BASE
    feat_files = {"src/calc.py": tr.FEAT, RAW: "y = 1\n"}

    def test_a_cite_on_a_hidden_named_file_is_accepted_under_the_raw_name_and_rejected_under_the_shown_one(self):
        shown = "src/x<TAGS:5>.py"
        out = self.ingest(*(dict(tr.CAND, path=p, line=1, quote="y = 1") for p in (self.RAW, shown)))
        self.assertEqual(reasons(out), [("accepted", None), ("rejected", "path-not-in-tree")])

    def test_render_json_keeps_the_raw_name_on_finding_and_evidence_while_text_shows_it_escaped(self):  # review-code-vfyu
        out = self.ingest(dict(tr.CAND, path=self.RAW, line=1, quote="y = 1"), {"inspected": ["src/calc.py"]})
        self.verdicts({"id": out["ids"][0], "verdict": "confirmed", "reason": "r",
                       "evidence": [{"path": self.RAW, "line": 1, "quote": "y = 1"}]})
        d = self.doc()
        self.assertEqual(len(d["findings"]), 1)
        f = d["findings"][0]
        self.assertEqual((f["path"], [e["path"] for e in f["evidence"]]), (self.RAW, [self.RAW]))
        rc, text = self.rep("render")
        self.assertEqual(rc, 0, text)
        self.assertIn("src/x<TAGS:5>.py:1", text)
        self.assertNotIn(self.RAW, text)


class ArgMax(tr.Base):
    """review-code-eixz: the cited paths go on one ls-tree argv, so a reply naming more path bytes than the kernel's ARG_MAX
    killed ingest. ls-tree reads no paths from a file or stdin, so they now run in chunks: one process per budget of path bytes."""

    def over(self):
        n = os.sysconf("SC_ARG_MAX") // 4000 + 2  # paths of 4000 bytes, none in the tree: more bytes than any one argv may carry
        return [dict(tr.CAND, path="d%04d/%s" % (k, "x" * 3990), title="t%d" % k) for k in range(n)]

    def test_a_reply_citing_more_path_bytes_than_arg_max_is_judged_not_crashed(self):
        out = self.ingest(*self.over())
        self.assertEqual({(d["disposition"], d["reason"]) for d in out["dispositions"]}, {("rejected", "path-not-in-tree")})
        self.assertEqual(out["ids"], [])

    def test_a_path_no_file_can_have_is_too_long_not_an_error(self):
        out = self.ingest(dict(tr.CAND, path="d/" + "x" * 200000), tr.CAND)
        self.assertEqual(reasons(out), [("rejected", "path-too-long"), ("accepted", None)])

    def test_the_chunks_cost_one_ls_tree_per_budget_of_path_bytes_not_one_per_path(self):
        log = os.path.join(self.tmp, "trace.json")
        rc, out, err = gitfix.cli("report.py", ["ingest", "--run", self.run_dir], self.repo, self.tmp, tr.lines(*self.over()),
                                  {"GIT_TRACE2_EVENT": log})
        self.assertEqual((rc, err), (0, ""), out)
        lists = [x for x in tp.starts(log) if "ls-tree" in x]
        self.assertGreater(len(lists), 1)
        self.assertLess(len(lists), 2 * len(self.over()) * 4000 // 100000)  # about one per 100000 path bytes, not one per path


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

    # review-code-omyf: the synthetic tree of an in-place pin is unreferenced, so a gc that prunes now removes it mid-run
    def test_a_pruned_pinned_tree_refuses_with_its_own_reason(self):
        self.git("gc", "-q", "--prune=now")
        rc, out = self.rep("ingest", tr.lines(tr.cand("return a * a", 8)))
        self.assertEqual((rc, tr.refusal(out)), (1, {"status": "refuse", "reason": "pin-pruned"}))

    def test_a_failing_ls_tree_with_the_root_tree_present_stays_bad_ref(self):
        sub = self.git("rev-parse", tp.load(self.run_dir)["pinned_tree"] + ":src")  # a subtree the listing must read: damage it
        loose = os.path.join(self.repo, ".git", "objects", sub[:2], sub[2:])
        os.chmod(loose, 0o600)
        os.remove(loose)
        rc, out = self.rep("ingest", tr.lines(tr.cand("return a * a", 8)))
        self.assertEqual((rc, tr.refusal(out)), (1, {"status": "refuse", "reason": "bad-ref"}))


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
