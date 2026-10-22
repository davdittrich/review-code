"""Re-review state (review-code-y6fi): prior.py, the post.py derivation and guard, target.py --since-last, report.py not_run."""
import json
import os
import unittest
from unittest import mock

import paths  # noqa: F401

import gitfix  # noqa: E402
import post  # noqa: E402
import prior  # noqa: E402
import report  # noqa: E402
import test_post as tp  # noqa: E402
import test_target as tt  # noqa: E402
from gitfix import cli, fid  # noqa: E402
from post import Refuse  # noqa: E402

A, B, C, D, E = ("F-%010x" % n for n in (1, 2, 3, 9, 5))
SHA, SHA2 = "a" * 40, "b" * 40


def rev(login, body, state="COMMENTED"):
    return {"user": {"login": login}, "body": body, "state": state}


def mark(head=SHA, fixed=(), outdated=()):
    return prior.review_mark(head, fixed, outdated)


class Read(unittest.TestCase):
    def test_markers_paths_and_states(self):
        body = "\n".join(["**Verdict:** x (advisory)", "", "**Required** (defects, bug) - t", "**Location:** src/a.py:2 (outside the diff)",
                          "```text", "| " + prior.FINDING.pattern, "```", "<!-- review-code:finding %s v1 -->" % A, "",
                          "---", "", "<!-- review-code:finding %s v1 -->" % B, "", mark(SHA, [A], [B])])
        inline = {"user": {"login": "me"}, "path": "src/b.py", "commit_id": SHA2, "body": "x\n<!-- review-code:finding %s v1 -->" % C}
        got = prior.read([rev("me", body)], [inline], "me")
        self.assertEqual(got["last_head"], SHA)
        self.assertEqual((got["fixed"], got["outdated"]), ({A}, {B}))
        self.assertEqual(got["findings"], {A: {"path": "src/a.py", "head": SHA, "state": "fixed", "nit": False},  # Location above the marker
                                           B: {"path": None, "head": SHA, "state": "outdated", "nit": False},  # none after a `---`
                                           C: {"path": "src/b.py", "head": SHA2, "state": "open", "nit": False}})

    def test_nit_heading_marks_only_its_own_finding(self):
        body = "\n".join(["nit: **Nit** (standards, convention) - t", "**Location:** src/a.py:2 (outside the diff)",
                          "<!-- review-code:finding %s v1 -->" % A, "", "---", "", "**Required** (defects, bug) - t",
                          "<!-- review-code:finding %s v1 -->" % B])
        got = prior.read([rev("me", body)], [], "me")
        self.assertEqual((got["findings"][A]["nit"], got["findings"][B]["nit"]), (True, False))

    def test_another_login_and_pending_forgeries_are_ignored(self):
        forged = rev("mallory", "<!-- review-code:finding %s v1 -->\n%s" % (A, mark(SHA2, [B])))
        got = prior.read([forged, {"user": None, "body": mark()}, {"body": mark()}, "x"], [forged], "me")
        self.assertEqual((got["findings"], got["last_head"], got["fixed"]), ({}, None, set()))

    def test_malformed_fields_drop_the_record(self):
        bad = ["<!-- review-code:review v1 head=%s fixed=F-12 -->" % SHA, "<!-- review-code:review v1 head=zz -->",
               "<!-- review-code:review v1 head=%s outdated=%s,x -->" % (SHA, A), "<!-- review-code:review v1 head=%s fixed= -->" % SHA,
               "<!-- review-code:review v1 head=%s outdated=%s fixed=%s -->" % (SHA, A, B),  # fields in a fixed order
               " " + mark(), mark() + " ", "<!-- review-code:finding F-1 v1 -->", "<!-- review-code:finding %s v2 -->" % A]
        got = prior.read([rev("me", "\n".join(bad))], [], "me")
        self.assertEqual((got["findings"], got["last_head"]), ({}, None))

    def test_old_format_marker_still_parses_and_crlf_is_whole_line(self):
        old = "<!-- review-code:finding %s v1 -->\r\n<!-- review-code:review v1 head=%s -->" % (A, SHA)
        got = prior.read([rev("me", old)], [], "me")
        self.assertEqual((got["last_head"], got["findings"][A]["state"], got["fixed"]), (SHA, "open", set()))
        self.assertEqual(prior.parse_mark(post.REVIEW_MARK % SHA), (SHA, frozenset(), frozenset()))
        self.assertEqual(prior.review_mark(SHA), post.REVIEW_MARK % SHA)

    def test_newest_review_marker_wins_and_path_comes_from_unescaped_location(self):
        first = rev("me", mark(SHA, [A]))
        second = rev("me", "**Location:** a\\\\b&amp;c.py:7 (outside the diff)\n<!-- review-code:finding %s v1 -->\n%s" % (B, mark(SHA2)))
        got = prior.read([first, second], [], "me")
        self.assertEqual((got["last_head"], got["fixed"], got["findings"][B]["path"]), (SHA2, set(), "a\\b&c.py"))


class Derive(unittest.TestCase):
    def old(self, paths_, fixed=(), outdated=()):
        return {"findings": {i: {"path": p, "head": SHA, "state": "open"} for i, p in paths_.items()}, "fixed": set(fixed),
                "outdated": set(outdated)}

    def test_the_example(self):
        # review 1 posted F-1 (u.py), F-2 (c.py), F-3 (c.py); now F-3 is found again and a new F-9 sits on F-2's path
        d = prior.derive(self.old({A: "u.py", B: "c.py", C: "c.py"}), {C: "c.py", D: "c.py"}, {"u.py", "c.py"}, True, changed={"u.py", "c.py"})
        self.assertEqual((d["new"], d["open"], d["fixed"], d["outdated"], d["unknown"]), ({D}, {C}, {A}, {B: D}, set()))
        self.assertEqual((d["fixed_out"], d["outdated_out"]), ({A}, {B}))
        self.assertEqual(prior.render(d), "**Prior:** 1 open, 1 fixed (%s), 1 outdated (%s -> may be %s)" % (A, B, D))
        self.assertTrue(prior.PRIOR_LINE.fullmatch(prior.render(d)))

    def test_prior_nit_is_judged_only_by_a_nit_run(self):
        old = self.old({A: "u.py", B: "u.py"})
        old["findings"][A]["nit"] = True
        d = prior.derive(old, {}, {"u.py"}, True, changed={"u.py"}, nit_run=False)
        self.assertEqual((d["fixed"], d["unknown"]), ({B}, {A}))
        d = prior.derive(old, {}, {"u.py"}, True, changed={"u.py"}, nit_run=True)
        self.assertEqual((d["fixed"], d["unknown"]), ({A, B}, set()))

    def test_unknown_when_out_of_scope_unrecoverable_incomplete_or_unverifiable(self):
        old = self.old({A: "out.py", B: None, C: "c.py", D: "c.py"})
        d = prior.derive(old, {}, {"c.py"}, True, {D}, {"c.py"})
        self.assertEqual((d["fixed"], d["unknown"]), ({C}, {A, B, D}))
        d = prior.derive(old, {}, {"c.py"}, False, (), {"c.py"})
        self.assertEqual((d["fixed"], d["unknown"]), (set(), {A, B, C, D}))

    def test_absent_is_fixed_only_when_its_file_changed_since_the_prior_head(self):
        old = self.old({A: "u.py", B: "c.py"})
        d = prior.derive(old, {}, {"u.py", "c.py"}, True, (), {"u.py"})
        self.assertEqual((d["fixed"], d["unknown"], d["unchanged"]), ({A}, {B}, {B}))
        self.assertEqual(prior.render(d), "**Prior:** 1 fixed (%s), 1 unknown (%s: file unchanged since the last review)" % (A, B))
        self.assertTrue(prior.PRIOR_LINE.fullmatch(prior.render(d)))
        d = prior.derive(old, {}, {"u.py", "c.py"}, True)  # prior head not reachable: nothing is claimed fixed
        self.assertEqual((d["fixed"], d["unknown"], d["unchanged"]), (set(), {A, B}, set()))
        self.assertEqual(prior.render(d), "**Prior:** 2 unknown")

    def test_fixed_back_is_new_and_carried_ids_are_not_reported_again(self):
        d = prior.derive(self.old({A: "u.py", B: "u.py", C: "c.py"}, fixed={A, B}, outdated={C}), {A: "u.py"}, {"u.py", "c.py"}, True, (), {"u.py"})
        self.assertEqual((d["new"], d["open"], d["fixed"], d["outdated"], d["unknown"]), ({A}, set(), set(), {}, set()))
        self.assertEqual((d["fixed_out"], d["outdated_out"]), ({B}, {C}))
        self.assertIsNone(prior.render(d))


class Grammar(tp.Base):
    def test_guard_accepts_the_new_lines_and_refuses_lookalikes(self):
        f = "F-" + "a" * 10
        for ok in ("**Prior:** 1 open, 2 fixed (%s, %s), 1 outdated (%s -> may be %s), 3 unknown" % (f, f, f, f),
                   "**Prior:** 2 unknown (%s, %s: file unchanged since the last review), 1 unknown" % (f, f),
                   "**State:** new", "**Not run:** defects@2, shard 3, repro, serial", mark(SHA, [f], [f])):
            post._guard("**Verdict:** x (advisory)\n" + ok)
        for bad in ("**Prior:** <b>x</b>", "**Prior:** 1 open see [x](http://e.test)", "**Prior:** 1 open (@team)",
                    "**Prior:** 1 fixed (F-1)", "**Prior:** 1 unknown (%s: file unchanged since the last review, @x)" % f,
                    "**Prior:** 1 unknown (%s: see http://e.test)" % f, "**Prior:** 1 unknown (F-1: file unchanged since the last review)", "**Prior:** 1 outdated (%s -> may be @x)" % f, "**Prior:** 1 open ", "**Prior:** `1 open`",
                    "**State:** still open", "**State:** new !", "**Not run:** rm -rf", "**Not run:** repro, @x", "**Not run:** ",
                    "<!-- review-code:review v1 head=%s fixed=%s,x -->" % (SHA, f), "  **Prior:** 1 open"):
            with self.assertRaises(Refuse, msg=bad) as cx:
                post._guard("**Verdict:** x (advisory)\n" + bad)
            self.assertEqual(cx.exception.reason, "unsafe-text")


class Rereview(tp.Base):
    """Two files, two runs on two heads (a rebase between them), one fake forge that keeps its reviews across both."""

    def setUp(self):
        self._td = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        util = self.util = "".join("u%02d = %d\n" % (n, n) for n in range(1, 11))
        gitfix.commit(self.repo, {"src/calc.py": tp.source(False, 0), "src/util.py": util}, "base")
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        gitfix.commit(self.repo, {"src/calc.py": tp.source(True, 5), "src/util.py": util.replace("u05 = 5", "u05 = 50")}, "feat")
        self.make_run()
        self.ids = {}

    def cand(self, path, line, quote, category="bug"):
        c = {"path": path, "line": line, "severity": "required", "category": category, "title": "t%d" % line,
             "trigger": "x%d" % line, "quote": quote, "fix": "f"}
        self.ids[(path, line, category)] = fid(path, category, quote)
        return c

    def run1(self):
        cs = [self.cand("src/util.py", 5, "u05 = 50"), self.cand("src/calc.py", 20, tp.row(20)), self.cand("src/calc.py", 33, tp.row(33))]
        self.ingest(cs, {n: (n, tp.row(n)) for n in (5, 20, 33)})

    def rebase(self):
        """A new head (the old fake's reviews carry over), a new run: both files are still in the diff."""
        old = self.fake
        gitfix.commit(self.repo, {"src/calc.py": tp.source(True, 5) + "w036 = 36\n",
                                  "src/util.py": self.util.replace("u05 = 5", "u05 = 50").replace("u10 = 10", "u10 = 11")}, "rebased")
        self.make_run()
        self.keep = old

    def run2(self, cands, cites):
        self.ingest(cands, cites)
        self.fake.reviews, self.fake.next_id = self.keep.reviews, self.keep.next_id

    def post(self):
        rc, out, st = self.build()
        self.assertEqual(rc, 0, out)
        rc, out, sub = self.call("submit", "--run", self.run_dir, "--digest", st["digest"])
        self.assertEqual(rc, 0, out)
        return st

    def body(self):
        return self.payload()[1]["body"]

    def test_first_review_has_head_marker_only(self):
        self.run1()
        rc, out, st = self.build()
        body = self.body()
        self.assertTrue(body.endswith(post.REVIEW_MARK % self.fake.head), body)
        self.assertNotIn("**Prior:**", body)
        self.assertNotIn("**State:**", body)
        self.assertEqual(st["inline"], 3)

    def test_rereview_after_fixes_and_a_rebase(self):
        self.run1()
        self.post()
        a, b, c = (self.ids[k] for k in (("src/util.py", 5, "bug"), ("src/calc.py", 20, "bug"), ("src/calc.py", 33, "bug")))
        self.rebase()
        d = fid("src/calc.py", "performance", tp.row(20))
        self.run2([self.cand("src/calc.py", 20, tp.row(20), "performance"), self.cand("src/calc.py", 33, tp.row(33))],
                  {20: (20, tp.row(20)), 33: (33, tp.row(33))})
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"], st["inline"], st["skipped"]), (0, "ok", 1, 1), out)
        body, payload = self.body(), self.payload()[1]
        self.assertIn("**Prior:** 1 open, 1 fixed (%s), 1 outdated (%s -> may be %s)" % (a, b, d), body)
        self.assertTrue(body.endswith(prior.review_mark(self.fake.head, [a], [b])), body)
        self.assertEqual([c_["id"] for c_ in payload["comments"]], [d])
        self.assertIn("**State:** new", payload["comments"][0]["body"])
        self.assertEqual(payload["skipped"], [c])
        rc, out, sub = self.call("submit", "--run", self.run_dir, "--digest", st["digest"])
        self.assertEqual((rc, sub["status"]), (0, "ok"), out)
        # the same run again: nothing is new and no prior state changed
        rc, out, st = self.build()
        self.assertEqual((rc, st["reason"]), (1, "already-posted"), out)
        # everything fixed: a body-only status review is allowed, and carries the fixed ids forward
        self.keep = self.fake  # calc.py changes again, so its two findings gone from this run count as fixed
        gitfix.commit(self.repo, {"src/calc.py": tp.source(True, 5) + "w036 = 36\nw038 = 38\n"}, "calc again")
        self.make_run()
        self.run2([], {})
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"], st["inline"], st["in_body"]), (0, "ok", 0, 0), out)
        self.assertIn("2 fixed (%s, %s)" % tuple(sorted((c, d))), self.body())
        rc, out, sub = self.call("submit", "--run", self.run_dir, "--digest", st["digest"])
        self.assertEqual((rc, sub["status"]), (0, "ok"), out)
        self.assertEqual(self.fake.writes()[-1][0], "POST")
        rc, out, st = self.build()  # and a retry of the status review is already posted
        self.assertEqual((rc, st["reason"]), (1, "already-posted"), out)

    def test_a_fixed_finding_that_comes_back_is_posted_again(self):
        self.run1()
        self.post()
        self.rebase()
        self.run2([self.cand("src/calc.py", 33, tp.row(33))], {33: (33, tp.row(33))})
        rc, out, st = self.build()  # src/util.py:5 and src/calc.py:20 are gone: 2 fixed
        self.assertIn("2 fixed", self.body())
        rc, out, sub = self.call("submit", "--run", self.run_dir, "--digest", st["digest"])
        self.assertEqual(rc, 0, out)
        a = self.ids[("src/util.py", 5, "bug")]
        self.keep = self.fake
        gitfix.commit(self.repo, {"src/calc.py": tp.source(True, 5) + "w036 = 36\nw037 = 37\n"}, "again")
        self.make_run()
        self.run2([self.cand("src/util.py", 5, "u05 = 50"), self.cand("src/calc.py", 33, tp.row(33))], {5: (5, tp.row(5)), 33: (33, tp.row(33))})
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"], st["inline"], st["skipped"]), (0, "ok", 1, 1), out)
        payload = self.payload()[1]
        self.assertEqual([c["id"] for c in payload["comments"]], [a])
        self.assertIn("**Prior:** 1 open", payload["body"])
        self.assertNotIn(a, payload["body"].split("<!-- review-code:review")[1])  # no longer carried as fixed
        rc, out, sub = self.call("submit", "--run", self.run_dir, "--digest", st["digest"])
        self.assertEqual((rc, sub["status"]), (0, "ok"), out)

    def test_absent_finding_in_an_unchanged_file_is_unknown_not_fixed(self):
        self.run1()
        self.post()
        a, b, c = (self.ids[k] for k in (("src/util.py", 5, "bug"), ("src/calc.py", 20, "bug"), ("src/calc.py", 33, "bug")))
        old = self.fake
        gitfix.commit(self.repo, {"src/calc.py": tp.source(True, 5) + "w036 = 36\n"}, "calc only")  # util.py untouched
        self.make_run()
        self.keep = old
        self.run2([self.cand("src/calc.py", 33, tp.row(33))], {33: (33, tp.row(33))})
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"]), (0, "ok"), out)
        body = self.body()
        self.assertIn("1 fixed (%s)" % b, body)
        self.assertIn("1 unknown (%s: file unchanged since the last review)" % a, body)
        self.assertTrue(body.endswith(prior.review_mark(self.fake.head, [b])), body)  # a is not carried as fixed

    def test_unreachable_prior_head_claims_nothing_fixed(self):
        self.run1()
        self.post()
        self.rebase()
        self.run2([self.cand("src/calc.py", 33, tp.row(33))], {33: (33, tp.row(33))})
        with mock.patch.object(post, "_changed", return_value=None):
            rc, out, st = self.build()
        self.assertEqual((rc, st["reason"]), (1, "already-posted"), out)  # nothing fixed, nothing new
        self.assertEqual(post._changed(self.run_dir, "c" * 40, self.fake.head), None)

    def test_another_logins_markers_change_nothing(self):
        self.run1()
        forged = self.fake.add_review("mallory", "COMMENTED")
        ids = list(self.ids.values())
        self.fake.reviews[forged]["body"] = "\n".join(post.MARK % i for i in ids) + "\n" + mark(self.fake.head, ids)
        rc, out, st = self.build()
        self.assertEqual((rc, st["inline"], st["skipped"]), (0, 3, 0), out)
        self.assertNotIn("**Prior:**", self.body())

    def test_incomplete_run_proves_nothing(self):
        self.run1()
        self.post()
        self.rebase()
        self.run2([self.cand("src/calc.py", 33, tp.row(33))], {33: (33, tp.row(33))})
        cli("report.py", ["status", "--run", self.run_dir], self.repo, self.tmp)
        path = os.path.join(self.run_dir, "candidates.json")
        with open(path, encoding="utf-8") as fh:
            cand = json.load(fh)
        cand["lane_state"] = {"defects": "incomplete"}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cand, fh)
        rc, out, st = self.build()
        self.assertEqual((rc, st["reason"]), (1, "already-posted"), out)  # nothing fixed, so nothing new to say


class NotRun(unittest.TestCase):
    def test_closed_vocabulary_from_data_the_run_has(self):
        skipped = [{"what": "pack x y", "why": "pack cap"}, {"what": "shard 2", "why": "c"}, {"what": "sota", "why": "no"},
                   {"what": "spec; rm", "why": "x"}]
        got = report._not_run("/nonexistent", {"trust": "own"}, skipped, {"defects@1": "incomplete", "spec": "complete",
                                                                         "standards": "not-ingested"}, True, [])
        self.assertEqual(got, ["defects@1", "serial", "shard 2", "sota", "standards"])
        self.assertEqual(report._not_run("/nonexistent", {"trust": "external"}, [], {}, False, []), ["repro"])

    def test_repro_when_own_ledger_lists_tests_and_no_outcome_file(self):
        f = [{"id": A}]
        own = {"trust": "own"}
        self.assertEqual(report._not_run("/nonexistent", own, [], {}, False, f), [])  # no tests or config in the ledger
        self.assertEqual(report._not_run("/nonexistent", dict(own, tests=[{"file": "t.py"}]), [], {}, False, f), ["repro"])
        self.assertEqual(report._not_run("/nonexistent", dict(own, tests=[{"file": "t.py"}]), [], {}, True, f), ["serial"])  # step 9 never runs in serial mode

    def test_outcome_file_clears_repro(self):
        import tempfile
        with tempfile.TemporaryDirectory() as run:
            os.makedirs(os.path.join(run, "repro"))
            with open(os.path.join(run, "repro", A + ".json"), "w", encoding="utf-8") as fh:
                json.dump({"outcome": "not-reproduced"}, fh)
            self.assertEqual(report._not_run(run, {"trust": "own", "tests": [1]}, [], {}, False, [{"id": A}]), [])


class NotRunReport(tp.Base):
    def test_document_text_and_posted_body(self):
        self.review(20)
        doc = report.build(self.run_dir)
        self.assertEqual(doc["not_run"], ["repro"])  # external trust; no spec is the normal case, not a failure to run
        self.assertIn("\nNot run: repro\n", report.render(self.run_dir, "text"))
        self.assertEqual(json.loads(report.render(self.run_dir, "json"))["not_run"], ["repro"])
        rc, out, st = self.build()
        self.assertEqual(rc, 0, out)
        self.assertIn("\n**Not run:** repro\n", self.payload()[1]["body"])


class SinceLast(tt.PRWorld):
    def setUp(self):
        super().setUp()
        src2 = os.path.join(self.tmp, "src2")
        gitfix.make_repo(src2)
        gitfix.run(["git", "fetch", "-q", self.bare, "refs/pull/7/head"], src2)
        gitfix.run(["git", "checkout", "-q", "--detach", "FETCH_HEAD"], src2)
        gitfix.commit(src2, {"src/new.py": "z = 1\n"}, "second pr commit")
        self.head2 = gitfix.run(["git", "rev-parse", "HEAD"], src2).strip()
        gitfix.run(["git", "push", "-q", "--force", self.bare, "HEAD:refs/pull/7/head"], src2)
        self.mainx = gitfix.run(["git", "rev-parse", "main"], self.bare).strip()  # moved on after the clone: not an ancestor

    def gh_with(self, last, login="me", other_login="me"):
        pr = self.payload(headRefOid=self.head2)
        reviews = [] if last is None else [{"user": {"login": other_login}, "state": "COMMENTED", "body": mark(last)}]

        def fake(args, cwd):
            if args[0] == "pr":
                return pr
            self.assertEqual(args[:3], ["api", "--hostname", "github.com"], args)
            if args[3] == "user":
                return {"login": login}
            self.assertIn("/pulls/7/reviews?per_page=100&page=1", args[3])
            return reviews
        return mock.Mock(side_effect=fake)

    def test_ancestor_reviews_only_the_range(self):
        rc, out = tt.call(["7", "--since-last"], self.repo, self.tmp, gh=self.gh_with(self.pr))
        self.assertEqual((rc, out["status"], out["merge_base"], out["head_sha"]), (0, "ok", self.main, self.head2), out)  # the true merge base stays: rules are never read from the last push
        self.assertEqual(out["since_last"], "reviewing since your last review, %s..%s" % (self.pr[:12], self.head2[:12]))
        rec = tt.load(os.path.join(self.repo, ".git", "review-code", os.path.basename(out["run"])))
        self.assertEqual((rec["included"], rec["since_last"] in rec["notes"]), (["src/new.py"], True))
        self.assertEqual(rec["diff_base"], self.pr)

    def test_non_ancestor_unknown_or_missing_review_falls_back_to_full(self):
        for last, gh in ((self.mainx, self.gh_with(self.mainx)), ("c" * 40, self.gh_with("c" * 40)), (None, self.gh_with(None)),
                         ("forged", self.gh_with(self.pr, other_login="mallory"))):
            rc, out = tt.call(["7", "--since-last"], self.repo, self.tmp, gh=gh)
            self.assertEqual((rc, out["status"], out["merge_base"]), (0, "ok", self.main), (last, out))
            self.assertTrue(out["since_last"].startswith("full review: "), out)

    def test_unreadable_reviews_fall_back_to_full(self):
        def fake(args, cwd):
            if args[0] == "pr":
                return self.payload(headRefOid=self.head2)
            raise target_refuse()
        target_refuse = lambda: tt.target.Refuse("gh-failed", "x")  # noqa: E731
        rc, out = tt.call(["7", "--since-last"], self.repo, self.tmp, gh=mock.Mock(side_effect=fake))
        self.assertEqual((rc, out["status"], out["since_last"][:12]), (0, "ok", "full review:"), out)

    def test_without_the_flag_nothing_reads_reviews(self):
        gh = self.gh_with(self.pr)
        rc, out = tt.call(["7"], self.repo, self.tmp, gh=gh)
        self.assertEqual((rc, "since_last" in out), (0, False))
        self.assertEqual([c[0][0][0] for c in gh.call_args_list], ["pr"])

    def test_gitlab_and_other_targets_refuse(self):
        glab = mock.Mock(side_effect=AssertionError("glab called"))
        rc, out = tt.call(["https://gitlab.com/g/s/r/-/merge_requests/5", "--since-last"], self.repo, self.tmp, glab=glab)
        self.assertEqual((rc, out["status"], out["reason"]), (1, "refuse", "since-last-unsupported"), out)
        rc, out = tt.call(["main", "--since-last"], self.repo, self.tmp)
        self.assertEqual((rc, out["reason"]), (1, "since-last-unsupported"), out)


if __name__ == "__main__":
    unittest.main()
