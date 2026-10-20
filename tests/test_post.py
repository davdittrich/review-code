"""post.py on real target.py, ledger.py and report.py run dirs, against a dict-backed fake GitHub (`_cli` is patched)."""
import ast
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
import paths  # noqa: F401

SCRIPTS = paths.SCRIPTS

import gitfix  # noqa: E402
from gitfix import cli, fid, lines  # noqa: E402
import post  # noqa: E402
import sanitize  # noqa: E402
import target  # noqa: E402

PR_URL = "https://github.com/o/r/pull/7"
PR = "repos/o/r/pulls/7"
CANARY = "CANARY-FORGE-TEXT-7c1"  # every fake failure carries it on stderr; no stdout line may


def row(n):
    """Text of line n on the feat branch: lines 20 to 22 differ from main, lines above 30 are added."""
    if n > 30:
        return "w%03d = %d" % (n, n)
    return "v%02d = %d" % (n, -n if 20 <= n <= 22 else n)


def source(changed, extra, inject=None):
    rows = [(inject or {}).get(n) or row(n) if changed else "v%02d = %d" % (n, n) for n in range(1, 31)]
    rows += [row(n) for n in range(31, 31 + extra)] if changed else []
    return "\n".join(rows) + "\n"


class FakeGitHub:
    """Callable as post._cli(tool, args, cwd, stdin=None); records (method, endpoint without query, parsed body)."""

    def __init__(self, run):
        with open(os.path.join(run, "target.json"), encoding="utf-8") as fh:
            self.head = json.load(fh)["head_sha"]
        with open(os.path.join(run, "change.diff"), encoding="utf-8") as fh:
            self.diff = fh.read()
        self.calls, self.reviews, self.login, self.next_id = [], {}, "me", 100
        self.fail_map = {}  # "METHOD endpoint-prefix" -> HTTP status
        self.head_reads, self.moves_after = 0, None  # the head changes after this many head reads
        self.pr_state, self.draft, self.merged = "open", False, False
        self.stored_override = None  # callable(stored comments) -> comments GitHub "returns"

    def add_review(self, login, state="PENDING"):
        rid, self.next_id = self.next_id, self.next_id + 1
        self.reviews[rid] = {"id": rid, "state": state, "user": {"login": login}, "commit_id": self.head, "body": "", "comments": []}
        return rid

    def pending(self):
        return [r for r in self.reviews.values() if r["state"] == "PENDING"]

    def writes(self):
        return [c for c in self.calls if c[0] in ("POST", "PUT", "DELETE")]

    def rows(self):
        return {(p, n) for _, p, tag, _, n, _, _ in sanitize.walk(self.diff) if p and tag in " +"}

    def position(self, path, line):
        """GitHub's `position`: 1-based lines below the file's first @@ header, later @@ headers included (an independent
        count, so a wrong walk in post.py cannot agree with it by sharing code)."""
        for chunk in re.split(r"(?m)^(?=diff --git )", self.diff):
            m = re.search(r"(?m)^\+\+\+ b/(.*)$", chunk)
            if not m or m.group(1) != path:
                continue
            pos, n, hunks = 0, 0, 0
            for ln in chunk.split("\n"):
                h = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", ln)
                if h:
                    hunks, n = hunks + 1, int(h.group(1))
                    pos += hunks > 1
                elif hunks:
                    pos += 1
                    if ln[:1] in (" ", "+"):
                        if n == line:
                            return pos
                        n += 1
        raise AssertionError((path, line))

    def pending_shape(self, c):
        """What GitHub returned live for a comment of a PENDING review (2026-10-20): no line, side or original_line, a
        diff `position` instead, the commit id and body kept."""
        return {"path": c["path"], "line": None, "original_line": None, "side": None, "start_line": None,
                "position": self.position(c["path"], c["line"]), "commit_id": c["commit_id"], "body": c["body"]}

    @staticmethod
    def ok(obj):
        return 0, json.dumps(obj).encode(), b""

    @staticmethod
    def fail(code):
        return 1, b"", ("gh: %s (HTTP %d)" % (CANARY, code)).encode()

    @staticmethod
    def page(items, query):
        q = dict(kv.split("=") for kv in query.split("&") if kv)
        size, no = int(q.get("per_page", 30)), int(q.get("page", 1))
        return items[(no - 1) * size:no * size]

    def __call__(self, tool, args, cwd, stdin=None):
        assert tool == "gh", tool
        if args[:2] == ["pr", "diff"]:
            assert args == ["pr", "diff", PR_URL, "--color", "never"], args
            self.calls.append(("GET", "diff", None))
            return 0, self.diff.encode(), b""
        assert args[:4] == ["api", "--hostname", "github.com", "-X"], args
        method, endpoint = args[4], args[5]
        assert args[6:] == (["--input", "-"] if stdin is not None else []), args
        body = json.loads(stdin.decode("utf-8")) if stdin is not None else None
        if isinstance(body, dict) and "event" in body:
            assert body["event"] == "COMMENT", body
        path, _, query = endpoint.partition("?")
        self.calls.append((method, path, body))
        for key, code in self.fail_map.items():
            m, _, prefix = key.partition(" ")
            if method == m and path.startswith(prefix):
                return self.fail(code)
        return self.route(method, path, query, body)

    def route(self, method, path, query, body):
        if (method, path) == ("GET", "user"):
            return self.ok({"login": self.login})
        if (method, path) == ("GET", PR):
            moved = self.moves_after is not None and self.head_reads >= self.moves_after
            self.head_reads += 1
            return self.ok({"state": self.pr_state, "draft": self.draft, "merged": self.merged,
                            "head": {"sha": "f" * 40 if moved else self.head}})
        if path == PR + "/reviews" and method == "GET":
            return self.ok(self.page([{k: v for k, v in r.items() if k != "comments"} for r in self.reviews.values()], query))
        if path == PR + "/reviews" and method == "POST":
            rows = self.rows()
            if self.login in [r["user"]["login"] for r in self.pending()] or any((c["path"], c["line"]) not in rows or c["side"] != "RIGHT" for c in body["comments"]):
                return self.fail(422)
            rid, self.next_id = self.next_id, self.next_id + 1
            self.reviews[rid] = {"id": rid, "state": "PENDING", "user": {"login": self.login},
                                 "commit_id": body["commit_id"], "body": body["body"],
                                 "comments": [dict(c, commit_id=body["commit_id"]) for c in body["comments"]]}
            return self.ok({"id": rid, "state": "PENDING"})
        if path == PR + "/comments" and method == "GET":  # submitted review comments of every user; a pending review is private
            return self.ok(self.page([dict(c, user=r["user"]) for r in self.reviews.values() if r["state"] != "PENDING"
                                      for c in r["comments"]], query))
        m = re.fullmatch(re.escape(PR) + r"/reviews/(\d+)(/comments|/events)?", path)
        rev = self.reviews.get(int(m.group(1))) if m else None
        if rev is None:
            return self.fail(404)
        if m.group(2) == "/comments" and method == "GET":
            stored = rev["comments"] if rev["state"] != "PENDING" else [self.pending_shape(c) for c in rev["comments"]]
            stored = self.stored_override(stored) if self.stored_override else stored
            return self.ok(self.page(stored, query))
        if m.group(2) == "/events" and method == "POST":
            assert body == {"event": "COMMENT"}, body
            rev["state"] = "COMMENTED"
            return self.ok({"id": rev["id"], "state": rev["state"]})
        if m.group(2) is None and method == "GET":
            return self.ok({k: v for k, v in rev.items() if k != "comments"})
        if m.group(2) is None and method == "DELETE":
            if rev["state"] != "PENDING":
                return self.fail(422)
            del self.reviews[rev["id"]]
            return 0, b"", b""
        return self.fail(404)


class Base(unittest.TestCase):
    extra = 0  # lines appended after line 30 on the branch
    inject = None  # {line: text} replaces that line of src/calc.py on the branch

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, {"src/calc.py": source(False, 0)}, "base")
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        gitfix.commit(self.repo, {"src/calc.py": source(True, self.extra, self.inject)}, "feat")
        self.make_run()

    def make_run(self):
        rc, out, _ = cli("target.py", ["main"], self.repo, self.tmp)
        self.assertEqual(rc, 0, out)
        self.run_dir = json.loads(out)["run"]
        rc, out, _ = cli("ledger.py", ["build", "--run", self.run_dir], self.repo, self.tmp)
        self.assertEqual(rc, 0, out)

    def review(self, *specs, fix=None, unverifiable=()):
        """Ingest and confirm one Required finding per (line, category) or bare line; make the run a PR run. Ids by line.
        A line in `unverifiable` is ingested too, but its verdict is unverifiable."""
        specs = [s if isinstance(s, tuple) else (s, "bug") for s in specs]
        cands = [{"path": "src/calc.py", "line": n, "severity": "required", "category": cat, "title": "title %d" % n,
                  "trigger": "trigger %d" % n, "quote": row(n), "fix": fix or "fix %d" % n} for n, cat in specs]
        return self.ingest(cands, {n: (n, row(n)) for n, _ in specs}, unverifiable)

    def ingest(self, cands, cites, unverifiable=()):
        """cands: full candidate objects; cites: {line: (line, quote)} the one-line evidence per candidate. Ids by line."""
        cli("report.py", ["discard", "--run", self.run_dir], self.repo, self.tmp)  # a lane ingest after verdicts refuses (review-code-edk)
        rc, out, err = cli("report.py", ["ingest", "--run", self.run_dir], self.repo, self.tmp,
                           lines(*cands, {"inspected": ["src/calc.py"]}))
        self.assertEqual((rc, err), (0, ""), out)
        ids = {c["line"]: fid(c["path"], c["category"], c["quote"]) for c in cands}
        verdicts = [{"id": ids[n], "verdict": "unverifiable", "reason": "r"} if n in unverifiable else
                    {"id": ids[n], "verdict": "confirmed", "reason": "r",
                     "evidence": [{"path": "src/calc.py", "line": cites[n][0], "quote": cites[n][1]}]} for n in ids]
        rc, out, err = cli("report.py", ["verdicts", "--run", self.run_dir], self.repo, self.tmp, lines(*verdicts))
        self.assertEqual((rc, err), (0, ""), out)
        self.retarget(kind="pr", url=PR_URL, number=7)  # fixture shortcut: post.py reads only kind, url, number and head_sha
        self.fake = FakeGitHub(self.run_dir)
        return ids

    def retarget(self, **kw):
        path = os.path.join(self.run_dir, "target.json")
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
        rec.update(kw)
        target.write_private(path, json.dumps(rec, sort_keys=True, indent=2))

    def call(self, *argv, cli=None):
        """post.main in-process with the fake forge behind _cli; cli=False leaves the real _cli in place."""
        buf = io.StringIO()
        patch = contextlib.nullcontext() if cli is False else mock.patch.object(post, "_cli", cli or self.fake)
        with redirect_stdout(buf), patch:
            rc = post.main(list(argv))
        out = buf.getvalue()
        self.assertNotIn(CANARY, out)  # forge and tool text never reaches stdout
        return rc, out, json.loads(out.splitlines()[-1])

    def build(self):
        return self.call("build", "--run", self.run_dir)

    def payload_path(self):
        return os.path.join(self.run_dir, "post", "payload.json")

    def payload(self):
        with open(self.payload_path(), "rb") as fh:
            raw = fh.read()
        return raw, json.loads(raw)


class GitHubTracer(Base):
    def setUp(self):
        super().setUp()
        self.ids = self.review(2, 20)  # line 2 lies outside every hunk; line 20 is a changed row

    def test_build_then_submit(self):
        rc, out, st = self.build()
        raw, payload = self.payload()
        self.assertEqual(rc, 0, out)
        self.assertEqual((st["status"], st["inline"], st["in_body"]), ("ok", 1, 1))
        self.assertEqual(st["digest"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.fake.writes(), [])  # build only reads
        self.assertEqual(oct(os.stat(self.payload_path()).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(os.path.dirname(self.payload_path())).st_mode & 0o777), "0o700")
        seen = len(self.fake.calls)
        rc, out, st = self.call("submit", "--run", self.run_dir, "--digest", st["digest"])
        self.assertEqual(rc, 0, out)
        self.assertEqual((st["status"], st["posted"], st["in_body"]), ("ok", 1, 1))
        tail = [(m, e) for m, e, _ in self.fake.calls[seen:]]
        at = tail.index(("POST", PR + "/reviews"))
        self.assertEqual(tail[at:], [("POST", PR + "/reviews"), ("GET", PR + "/reviews/100/comments"),
                                     ("GET", "diff"), ("GET", PR), ("GET", PR + "/reviews/100/comments"),
                                     ("POST", PR + "/reviews/100/events")])
        # create, read back, the diff for the positions, head, recount, event
        create = next(b for m, e, b in self.fake.calls[seen:] if (m, e) == ("POST", PR + "/reviews"))
        self.assertEqual(sorted(create), ["body", "comments", "commit_id"])  # no event key
        self.assertEqual(create["commit_id"], self.fake.head)
        self.assertEqual([{k: c[k] for k in ("path", "line", "side")} for c in create["comments"]],
                         [{"path": "src/calc.py", "line": 20, "side": "RIGHT"}])
        self.assertEqual(sorted(create["comments"][0]), ["body", "line", "path", "side"])
        self.assertIn(post.MARK % self.ids[20], create["comments"][0]["body"])
        self.assertNotIn(post.MARK % self.ids[2], create["comments"][0]["body"])
        self.assertIn(post.MARK % self.ids[2], create["body"])  # the out-of-hunk finding is in the review body
        self.assertEqual(self.fake.reviews[100]["state"], "COMMENTED")
        with open(os.path.join(self.run_dir, "post", "result.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["review_id"], 100)

    def test_preview_is_payload(self):
        rc, out, st = self.build()
        _, payload = self.payload()
        self.assertEqual(rc, 0, out)
        self.assertTrue(payload["body"].endswith(post.REVIEW_MARK % self.fake.head))
        self.assertIn(payload["body"], out)
        for c in payload["comments"]:
            self.assertIn(c["body"], out)
            self.assertTrue(c["body"].endswith(post.MARK % c["id"]))
            self.assertRegex(c["body"], r"(?m)^20: v20 = -20$")
        self.assertIn("== review body ==", out)
        self.assertIn("== inline 1/1: src/calc.py:20 (RIGHT) ==", out)
        self.assertIn("== not posted ==", out)
        self.assertEqual(post.MARK_RE.findall(payload["body"]), [self.ids[2]])
        self.assertIn("**Location:** src/calc.py:2 (outside the diff)", payload["body"])
        self.assertIn("Areas inspected: src/calc.py\n", payload["body"])  # CTX-02, shown in the preview with the rest of the body
        self.assertEqual(out.splitlines()[-1], json.dumps(st, sort_keys=True))  # the status line is last

    def test_body_caps_the_inspected_areas(self):
        n = post.report.MAX_AREAS
        text = post._body("v", [], [], 0, "h", ["a%d.py" % i for i in range(n + 3)])
        self.assertIn("a%d.py (+3 more)" % (n - 1), text)
        self.assertNotIn("a%d.py" % n, text)

    def test_digest_is_the_payload_hash_and_wrong_digest_writes_nothing(self):
        self.build()
        rc, out, st = self.call("submit", "--run", self.run_dir, "--digest", "0" * 64)
        self.assertEqual((rc, st["reason"]), (1, "digest-mismatch"))
        self.assertEqual(self.fake.writes(), [])

    def test_usage(self):
        for argv in ([], ["build"], ["build", "--run"], ["submit", "--run", "r"], ["submit", "--run", "r", "--digest", "xyz"],
                     ["build", "--run", "r", "extra"], ["post"]):
            rc, out, st = self.call(*argv)
            self.assertEqual((rc, st["status"], st["reason"], st["detail"]), (1, "refuse", "usage", post.USAGE), argv)
        self.assertEqual(self.fake.calls, [])


class Safety(Base):
    """Refusals, races and cleanup (Task 2); each failure leaves the forge as it was or names what it left behind."""

    def setUp(self):
        super().setUp()
        self.ids = self.review(2, 20)

    def confirmed(self):
        rc, out, st = self.build()
        self.assertEqual(rc, 0, out)
        self.fake.calls[:] = []
        self.fake.head_reads = 0
        return st["digest"]

    def submit(self, digest):
        return self.call("submit", "--run", self.run_dir, "--digest", digest)

    def refused(self, res, reason, detail=None):
        rc, out, st = res
        self.assertEqual((rc, st["status"], st["reason"]), (1, "refuse", reason), out)
        if detail is not None:
            self.assertEqual(st.get("detail"), detail)
        self.assertNotIn("digest", st)
        return st

    def deletes(self):
        return [c for c in self.fake.calls if c[0] == "DELETE"]

    def test_head_moved_at_a_before_the_diff(self):
        self.fake.moves_after = 0
        self.refused(self.build(), "head-moved")
        self.assertEqual(self.fake.calls, [("GET", PR, None)])  # nothing read after the first head read
        self.assertFalse(os.path.exists(self.payload_path()))

    def test_head_moved_at_b_after_the_reads(self):
        self.fake.moves_after = 1
        self.refused(self.build(), "head-moved")
        self.assertEqual(self.fake.writes(), [])
        self.assertFalse(os.path.exists(self.payload_path()))

    def test_head_moved_at_c_before_the_first_write(self):
        digest = self.confirmed()
        self.fake.moves_after = 0
        self.refused(self.submit(digest), "head-moved")
        self.assertEqual(self.fake.writes(), [])

    def test_head_moved_at_d_deletes_the_created_review(self):
        digest = self.confirmed()
        self.fake.moves_after = 1
        self.refused(self.submit(digest), "head-moved")
        self.assertEqual([c[:2] for c in self.fake.writes()], [("POST", PR + "/reviews"), ("DELETE", PR + "/reviews/100")])
        self.assertEqual(self.fake.pending(), [])
        self.assertFalse(any(c[1].endswith("/events") for c in self.fake.calls))

    def test_own_pending_review_refuses_and_is_never_deleted(self):
        rid = self.fake.add_review("me")
        self.refused(self.build(), "pending-review-exists")
        self.assertEqual(self.fake.writes(), [])
        self.assertIn(rid, self.fake.reviews)
        self.assertFalse(os.path.exists(self.payload_path()))

    def test_another_users_pending_review_does_not_block(self):
        self.fake.add_review("someone-else")
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"]), (0, "ok"), out)

    def test_pending_review_is_found_on_a_later_page(self):
        for _ in range(120):
            self.fake.add_review("someone-else", "COMMENTED")
        self.fake.add_review("me")
        self.refused(self.build(), "pending-review-exists")
        self.assertEqual([c[1:2] for c in self.fake.calls if c[1].endswith("/reviews")], [(PR + "/reviews",)] * 2)

    def test_pending_review_appearing_after_build_refuses_submit_before_any_write(self):
        digest = self.confirmed()
        self.fake.add_review("me")
        self.refused(self.submit(digest), "pending-review-exists")
        self.assertEqual(self.fake.writes(), [])

    def test_pages_stop_at_the_cap(self):
        full = mock.Mock(return_value=[{}] * post.PER_PAGE)
        with mock.patch.object(post, "_api", full):
            with self.assertRaises(post.Refuse) as cm:
                post._pages("gh", "github.com", "repos/o/r/pulls/7/reviews", ".")
        self.assertEqual((cm.exception.reason, full.call_count), ("too-many-comments", post.MAX_PAGES))

    def test_one_changed_byte_refuses_digest_mismatch_without_a_write(self):
        digest = self.confirmed()
        raw, _ = self.payload()
        with open(self.payload_path(), "wb") as fh:
            fh.write(raw.replace(b"title 20", b"title 21"))
        self.refused(self.submit(digest), "digest-mismatch")
        self.assertEqual(self.fake.calls, [])

    def test_payload_for_another_repository_refuses_even_with_a_recomputed_digest(self):
        self.confirmed()
        for key, value in (("host", "evil.example"), ("repo", "x/y"), ("number", 8), ("head_sha", "a" * 40)):
            with self.subTest(key):
                _, payload = self.payload()
                payload[key] = value
                data = json.dumps(payload, sort_keys=True, indent=2).encode()
                with open(self.payload_path(), "wb") as fh:
                    fh.write(data)
                self.refused(self.submit(hashlib.sha256(data).hexdigest()), "payload-mismatch")
                self.assertEqual(self.fake.writes(), [])
        data = b"[]"
        with open(self.payload_path(), "wb") as fh:
            fh.write(data)
        self.refused(self.submit(hashlib.sha256(data).hexdigest()), "payload-mismatch")

    def test_create_rejected_reports_only_the_status(self):
        digest = self.confirmed()
        self.fake.fail_map["POST " + PR + "/reviews"] = 422
        self.refused(self.submit(digest), "post-failed", "HTTP 422")  # the canary on stderr is checked in call()
        self.assertEqual(self.deletes(), [])  # nothing was created, nothing to delete

    def test_create_without_an_integer_id_refuses(self):
        digest = self.confirmed()
        real = self.fake.route
        self.fake.route = lambda m, p, q, b: self.fake.ok({"id": "100"}) if (m, p) == ("POST", PR + "/reviews") else real(m, p, q, b)
        self.refused(self.submit(digest), "post-failed")

    def lose_create_answer(self, answer, also=None):
        """The forge stores the review, but gh reports `answer` (an exit status or bad JSON); `also` runs right after."""
        real = self.fake.route

        def route(m, p, q, b):
            got = real(m, p, q, b)
            if (m, p) != ("POST", PR + "/reviews"):
                return got
            if also:
                also()
            return answer

        self.fake.route = route

    def test_a_lost_create_answer_deletes_the_review_it_made(self):
        for what, answer in (("exit status", self.fake.fail(502)), ("unparsable", (0, b"<html>", b""))):
            with self.subTest(what):
                digest = self.confirmed()
                self.lose_create_answer(answer)
                st = self.refused(self.submit(digest), "post-failed")
                self.assertEqual([c[:2] for c in self.deletes()], [("DELETE", PR + "/reviews/%d" % (self.fake.next_id - 1))])
                self.assertEqual(self.fake.pending(), [])
                self.fake.route = type(self.fake).route.__get__(self.fake)

    def test_a_lost_create_answer_never_deletes_a_review_the_user_started(self):
        digest = self.confirmed()
        self.lose_create_answer(self.fake.fail(502), also=lambda: self.fake.add_review("me"))
        self.refused(self.submit(digest), "post-failed", "HTTP 502")
        self.assertEqual(len(self.deletes()), 1)
        self.assertEqual([r["body"] for r in self.fake.pending()], [""])  # the user's own, no marker

    def test_a_lost_create_answer_with_no_listing_names_the_unknown(self):
        digest = self.confirmed()
        self.lose_create_answer(self.fake.fail(502), also=lambda: self.fake.fail_map.update({"GET " + PR + "/reviews": 500}))
        self.refused(self.submit(digest), "cleanup-failed", "pending review unknown")

    def test_a_comment_added_after_verify_refuses_before_the_event(self):
        digest = self.confirmed()
        listings = []

        def stored(comments):  # the user adds a comment in the web UI; it joins their one pending review
            listings.append(len(comments))
            return comments if len(listings) == 1 else comments + [dict(comments[0], body="typed in the web UI")]

        self.fake.stored_override = stored
        self.refused(self.submit(digest), "review-kept", "pending review 100")  # the user's comment is in it
        self.assertEqual(len(listings), 3)  # verify, recount, and cleanup's own check
        self.assertFalse(any(c[1].endswith("/events") for c in self.fake.calls))
        self.assertEqual(self.deletes(), [])
        self.assertEqual([r["id"] for r in self.fake.pending()], [100])

    def test_a_comment_added_before_a_head_move_at_d_keeps_the_review(self):
        digest = self.confirmed()
        listings = []

        def stored(comments):
            listings.append(1)
            return comments if len(listings) == 1 else comments + [dict(comments[0], body="typed in the web UI")]

        self.fake.stored_override = stored
        self.fake.moves_after = 1
        self.refused(self.submit(digest), "review-kept", "pending review 100")
        self.assertEqual(self.deletes(), [])
        self.assertEqual([r["id"] for r in self.fake.pending()], [100])

    def test_stored_comments_differ_deletes_the_review(self):
        digest = self.confirmed()
        self.fake.stored_override = lambda comments: []
        self.refused(self.submit(digest), "verify-failed")
        self.assertEqual(len(self.deletes()), 1)
        self.assertEqual(self.fake.pending(), [])

    def test_stored_comment_at_another_commit_refuses(self):
        digest = self.confirmed()
        self.fake.stored_override = lambda comments: [dict(c, commit_id="b" * 40) for c in comments]
        self.refused(self.submit(digest), "verify-failed")
        self.assertEqual(len(self.deletes()), 1)

    def test_stored_comment_at_another_position_refuses(self):
        digest = self.confirmed()
        self.fake.stored_override = lambda comments: [dict(c, position=c["position"] + 1) for c in comments]
        self.refused(self.submit(digest), "verify-failed")
        self.assertEqual(len(self.deletes()), 1)

    def test_stored_comment_on_another_path_refuses(self):
        digest = self.confirmed()
        self.fake.stored_override = lambda comments: [dict(c, path="src/other.py") for c in comments]
        self.refused(self.submit(digest), "verify-failed")

    def test_an_extra_stored_comment_refuses(self):
        digest = self.confirmed()
        self.fake.stored_override = lambda comments: comments + comments
        self.refused(self.submit(digest), "verify-failed")
        self.assertEqual(len(self.deletes()), 1)

    def test_stored_comment_without_its_marker_refuses_and_keeps_the_review(self):
        digest = self.confirmed()
        self.fake.stored_override = lambda comments: [dict(c, body="no marker here") for c in comments]
        self.refused(self.submit(digest), "review-kept", "pending review 100")  # not provably ours: never deleted
        self.assertEqual(self.deletes(), [])

    def test_stored_comment_with_a_marker_inside_a_longer_line_refuses(self):
        digest = self.confirmed()
        self.fake.stored_override = lambda comments: [dict(c, body="x " + c["body"].replace("\n", " ")) for c in comments]
        self.refused(self.submit(digest), "review-kept", "pending review 100")  # no whole-line marker: kept

    def test_a_stored_comment_that_does_carry_line_and_side_is_compared_on_them(self):
        digest = self.confirmed()
        self.fake.stored_override = lambda comments: [dict(c, line=20, side="RIGHT") for c in comments]
        rc, out, st = self.submit(digest)
        self.assertEqual((rc, st["status"]), (0, "ok"), out)
        self.fake.reviews.clear()
        digest = self.confirmed()
        self.fake.stored_override = lambda comments: [dict(c, line=21, side="RIGHT") for c in comments]
        self.refused(self.submit(digest), "verify-failed")

    def test_a_stored_comment_with_neither_line_nor_a_position_refuses(self):
        digest = self.confirmed()
        self.fake.stored_override = lambda comments: [dict(c, position=None) for c in comments]
        self.refused(self.submit(digest), "verify-failed")

    def test_failed_delete_names_the_review_and_the_next_build_refuses(self):
        digest = self.confirmed()
        self.fake.stored_override = lambda comments: []
        self.fake.fail_map["DELETE " + PR + "/reviews/"] = 500
        self.refused(self.submit(digest), "cleanup-failed", "pending review 100")
        self.assertEqual([r["id"] for r in self.fake.pending()], [100])
        self.fake.fail_map.clear()
        self.refused(self.build(), "pending-review-exists")  # no second review is stacked
        self.assertEqual(len(self.fake.pending()), 1)

    def test_event_error_deletes_the_review(self):
        digest = self.confirmed()
        self.fake.fail_map["POST " + PR + "/reviews/100/events"] = 500
        self.refused(self.submit(digest), "post-failed", "HTTP 500")
        self.assertEqual(len(self.deletes()), 1)
        self.assertEqual(self.fake.pending(), [])

    def event_lands_then(self, answer, also=None):
        """GitHub applies the COMMENT event, then gh reports `answer` (the answer was lost); `also` runs right after."""
        real = self.fake.route

        def route(m, p, q, b):
            got = real(m, p, q, b)
            if (m, p) != ("POST", PR + "/reviews/100/events"):
                return got
            if also:
                also()
            return answer

        self.fake.route = route

    def landed(self, rc, out, st):
        self.assertEqual((rc, st["status"], st.get("review_id")), (0, "ok", 100), out)
        self.assertEqual(self.deletes(), [])
        self.assertEqual(self.fake.reviews[100]["state"], "COMMENTED")

    def test_an_event_that_landed_with_its_answer_lost_reports_ok(self):
        digest = self.confirmed()
        self.event_lands_then(self.fake.fail(502))
        self.landed(*self.submit(digest))

    def test_an_event_that_landed_before_a_timeout_reports_ok(self):
        digest = self.confirmed()

        def cli(tool, args, cwd, stdin=None):
            got = self.fake(tool, args, cwd, stdin)
            if args[5:6] == [PR + "/reviews/100/events"]:  # gh hung after GitHub applied the event: _cli's timeout
                raise post.Refuse("gh-failed", "timeout")
            return got

        self.landed(*self.call("submit", "--run", self.run_dir, "--digest", digest, cli=cli))

    def test_an_interrupt_after_the_event_landed_reports_it_published(self):
        digest = self.confirmed()

        def cli(tool, args, cwd, stdin=None):
            got = self.fake(tool, args, cwd, stdin)
            if args[5:6] == [PR + "/reviews/100/events"]:  # Ctrl-C after GitHub applied the event
                raise KeyboardInterrupt
            return got

        self.refused(self.call("submit", "--run", self.run_dir, "--digest", digest, cli=cli), "post-partial", "published")
        self.assertEqual(self.fake.reviews[100]["state"], "COMMENTED")

    def test_an_event_with_a_lost_answer_and_no_state_is_unknown(self):
        digest = self.confirmed()
        self.event_lands_then(self.fake.fail(502), also=lambda: self.fake.fail_map.update({"GET " + PR + "/reviews/100": 500}))
        self.refused(self.submit(digest), "post-partial", "published unknown")
        self.assertEqual(self.deletes(), [])

    def test_interrupt_after_create_deletes_the_review(self):
        digest = self.confirmed()
        self.fake.stored_override = mock.Mock(side_effect=[KeyboardInterrupt, []])  # one Ctrl-C; cleanup then lists
        with self.assertRaises(KeyboardInterrupt):
            self.submit(digest)
        self.assertEqual(self.fake.pending(), [])

    def test_a_branch_run_is_not_a_forge_target(self):
        self.retarget(kind="branch")
        self.refused(self.build(), "not-a-forge-target")
        self.assertEqual(self.fake.calls, [])

    def test_gh_missing(self):
        with mock.patch.object(post.subprocess, "run", side_effect=FileNotFoundError):
            self.refused(self.call("build", "--run", self.run_dir, cli=False), "gh-missing")

    def test_a_hung_tool_times_out(self):
        hung = mock.Mock(side_effect=subprocess.TimeoutExpired(["gh"], target.CLI_TIMEOUT))
        with mock.patch.object(post.subprocess, "run", hung):
            self.refused(self.call("build", "--run", self.run_dir, cli=False), "gh-failed", "timeout")
        self.assertEqual(hung.call_args.kwargs["timeout"], target.CLI_TIMEOUT)

    def test_a_failed_result_write_after_the_event_still_reports_ok(self):
        digest = self.confirmed()
        real = target.write_private

        def write(path, text):
            if path.endswith("result.json"):
                raise PermissionError(path)
            return real(path, text)

        with mock.patch.object(post.target, "write_private", write):
            rc, out, st = self.submit(digest)
        self.assertEqual((rc, st["status"], st["review_id"]), (0, "ok", 100), out)
        self.assertEqual(self.fake.reviews[100]["state"], "COMMENTED")

    def test_closed_merged_and_draft_pull_requests_refuse(self):
        for state, merged, draft, reason in (("closed", False, False, "closed"), ("closed", True, False, "merged"),
                                             ("open", False, True, "draft")):
            with self.subTest(reason):
                self.fake.pr_state, self.fake.merged, self.fake.draft = state, merged, draft
                self.refused(self.build(), reason)
                self.assertEqual(self.fake.writes(), [])

    def test_crafted_target_urls_refuse_before_any_call(self):
        head = "0123456789abcdef0123456789abcdef01234567"
        for what, patch in (("owner starting with a dash", {"url": "https://github.com/-o/r/pull/7"}),
                            ("repo starting with a dash", {"url": "https://github.com/o/-r/pull/7"}),
                            ("three segments", {"url": "https://github.com/o/r/x/pull/7"}),
                            ("host starting with a dash", {"url": "https://-h/o/r/pull/7"}),
                            ("host with a space", {"url": "https://a b/o/r/pull/7"}),
                            ("odd segment character", {"url": "https://github.com/o/r;x/pull/7"}),
                            ("number differs from the url", {"number": 8}),
                            ("head is not an object id", {"head_sha": "-" * 40}),
                            ("head in capitals", {"head_sha": head.upper()})):
            with self.subTest(what):
                self.retarget(**patch)
                self.refused(self.build(), "bad-target-url")
                self.assertEqual(self.fake.calls, [])
                self.retarget(kind="pr", url=PR_URL, number=7, head_sha=self.fake.head)


class PositionAcrossHunks(Base):
    """A pending review's comments come back with a diff `position`; a second hunk's header counts as a line."""
    extra = 5  # lines 31 to 35 are added: a second hunk below the one around lines 20 to 22

    def test_submit_verifies_comments_in_both_hunks(self):
        self.review(20, 32)
        rc, out, st = self.call("build", "--run", self.run_dir)
        self.assertEqual((rc, st["inline"]), (0, 2), out)
        # counted by hand: hunk one is 12 rows, the second @@ is row 13, so line 32 is row 18 (a one-hunk count gives 17)
        self.assertEqual([self.fake.position("src/calc.py", n) for n in (20, 32)], [7, 18])
        rc, out, st = self.call("submit", "--run", self.run_dir, "--digest", st["digest"])
        self.assertEqual((rc, st["status"]), (0, "ok"), out)


class EmptyRun(Base):
    def test_zero_verified_findings_refuse_nothing_to_post(self):
        self.review()
        rc, out, st = self.build()
        self.assertEqual((rc, st["reason"]), (1, "nothing-to-post"), out)
        self.assertFalse(os.path.exists(self.payload_path()))
        self.assertEqual(self.fake.writes(), [])


class Anchors(Base):
    def test_first_and_last_row_of_a_hunk_are_inline_and_the_next_line_is_not(self):
        ids = self.review(17, 25, (26, "test"))  # the hunk's new side is lines 17 to 25
        rc, out, st = self.build()
        _, payload = self.payload()
        self.assertEqual((rc, st["inline"], st["in_body"]), (0, 2, 1), out)
        self.assertEqual([c["line"] for c in payload["comments"]], [17, 25])
        self.assertEqual(payload["in_body"], [ids[26]])
        self.assertEqual(post.MARK_RE.findall(payload["body"]), [ids[26]])

    def test_two_builds_of_one_run_are_byte_identical(self):
        self.review(2, 17, 20, 25, (26, "test"))
        _, _, first = self.build()
        raw1, _ = self.payload()
        _, _, second = self.build()
        raw2, _ = self.payload()
        self.assertEqual((raw1, first["digest"]), (raw2, second["digest"]))

    def test_findings_follow_report_order(self):
        ids = self.review(25, 17, 20)
        self.build()
        _, payload = self.payload()
        self.assertEqual([c["id"] for c in payload["comments"]], [ids[17], ids[20], ids[25]])

    def test_a_body_or_comment_over_the_limit_refuses_too_large(self):
        for line, where in ((2, "body"), (20, "comment")):
            with self.subTest(where):
                self.review(line, fix="x" * (post.MAX_BODY + 1))
                self.refused_too_large()

    def test_a_preview_over_the_cap_refuses_and_writes_nothing(self):
        self.review(2, 20, fix="x" * (target.MAX_PREVIEW // 2))  # each body is under MAX_BODY, the preview is not
        rc, out, st = self.build()
        self.assertEqual((rc, st["reason"]), (1, "preview-too-large"), out[-300:])
        self.assertEqual(out.count("\n"), 1)  # the status line only: no preview the harness would cut or spill
        self.assertFalse(os.path.exists(self.payload_path()))

    def refused_too_large(self):
        rc, out, st = self.build()
        self.assertEqual((rc, st["reason"]), (1, "too-large"), out)
        self.assertFalse(os.path.exists(self.payload_path()))
        self.assertEqual(self.fake.writes(), [])


TOKEN = "ghp_" + "a1" * 18  # built, never written whole: 36 characters after the prefix
OTHER_ID = "F-" + "ab" * 5


class TextSafety(Base):
    """Every string that reaches the forge is repository-derived: no command line, ping, image, raw HTML or secret."""

    inject = {20: 'tok = "%s"' % TOKEN, 21: "# <!-- review-code:finding %s v1 -->" % OTHER_ID}

    def adversarial(self):
        quote = self.inject[20] + "\n" + self.inject[21]
        cand = {"path": "src/calc.py", "line": 20, "end_line": 21, "severity": "required", "category": "bug",
                "title": "/approve", "trigger": "ping @alice about #12 and !3 ![x](http://e/p.png) <img src=x>",
                "quote": quote, "fix": "/merge now"}
        return self.ingest([cand], {20: (20, self.inject[20])})[20]

    def bodies(self):
        _, payload = self.payload()
        return [payload["body"]] + [c["body"] for c in payload["comments"]]

    def test_adversarial_end_to_end(self):
        fid_ = self.adversarial()
        rc, out, st = self.build()
        raw, payload = self.payload()
        self.assertEqual((rc, st["status"], st["redacted"]), (0, "ok", 1), out)
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(TOKEN, raw.decode("utf-8"))
        self.assertRegex(out, r"(?m)^== secret scan ==\ngithub-pat src/calc\.py:20$")
        for text in self.bodies():
            self.assertNotRegex(text, r"(?m)^\s*/")
            self.assertNotIn("<img", text)
            self.assertNotIn("![", text)
            for ref in ("@alice", "#12", "!3"):
                self.assertNotRegex(text, r"(?<!`)" + re.escape(ref))
        comment = payload["comments"][0]["body"]
        for ref in ("`@alice`", "`#12`", "`!3`", "&lt;img"):
            self.assertIn(ref, comment)
        self.assertIn("[REDACTED:github-pat]", comment)
        self.assertEqual(post.MARK_RE.findall(comment), [fid_])  # the spoofed marker sits behind its line-number prefix
        self.assertIn("21: # <!-- review-code:finding %s v1 -->" % OTHER_ID, comment)
        rc, out, st = self.call("submit", "--run", self.run_dir, "--digest", st["digest"])
        self.assertEqual((rc, st["status"]), (0, "ok"), out)
        self.assertNotIn(TOKEN, json.dumps(self.fake.calls) + json.dumps(self.fake.reviews))

    def test_guard(self):
        for bad in ("/approve", "  /merge now", "ok\n/submit_review approve", "\t/x", "a\r/approve", "<!-- hidden -->",
                    " " + post.MARK % OTHER_ID, post.MARK % OTHER_ID + " tail", post.REVIEW_MARK % ("a" * 40) + " "):
            with self.assertRaises(post.Refuse, msg=repr(bad)) as cm:
                post._guard(bad)
            self.assertEqual(cm.exception.reason, "unsafe-text")
        for good in (post.MARK % OTHER_ID, post.REVIEW_MARK % ("a" * 40), "```text\n20: /approve behind a prefix\n```",
                     "**Fix:** a // b", "**Fix:** /merge", "**Fix:** x\n" + post.MARK % OTHER_ID + "\n**Fix:** y"):
            post._guard(good)
        for text in ("a \\@x [y]", "\\\\#1 & <b> `c` \\`@d`", "&commat;x &#64;y \\[z](u)", "!x @o/t \\\\[w]"):
            post._guard("**Fix:** " + post._prose(text))  # whatever _prose writes, the guard accepts

    def test_rules(self):
        samples = {
            "aws-access-token": "AKIA" + "ABCDEFGH" * 2,
            "github-pat": "ghp_" + "a1" * 18,
            "github-oauth": "gho_" + "a1" * 18,
            "github-fine-grained-pat": "github_pat_" + "a1" * 41,
            "gitlab-pat": "glpat-" + "a1" * 10,
            "gcp-api-key": "AIza" + "a1" * 17 + "a",
            "slack-bot-token": "xoxb-" + "1" * 10 + "-" + "2" * 10 + "-abc",
            "stripe-access-token": "sk_live_" + "a1" * 6,
            "jwt": "eyJ" + "a" * 20 + ".eyJ" + "b" * 20 + "." + "c" * 12,
            "private-key": "-----BEGIN RSA " + "PRIVATE KEY-----",  # the marker line only: _render blanks the finding
            "generic-secret": 'password = "hunter2hunter2"',
        }
        self.assertEqual({r for r, _ in post.SECRET_RULES}, set(samples))
        for rule, sample in samples.items():
            text = "before %s after" % sample
            self.assertIn(rule, [r for r, _, _ in post._scan(text)], rule)
            red, rules = post._redact(text)
            self.assertEqual(rules, [rule], rule)
            self.assertEqual(red, "before [REDACTED:%s] after" % rule, rule)
        red, rules = post._redact("def f(x):\n    return x + 1  # ghp_short AKIA password = x\n")
        self.assertEqual((rules, post._scan(red)), ([], []))
        self.assertEqual(post._redact("tail\n-----END OPENSSH PRIVATE KEY-----")[1], ["private-key"])  # END alone counts
        two, rules = post._redact("a %s b %s c" % (samples["github-pat"], samples["gitlab-pat"]))
        self.assertEqual((two, rules), ("a [REDACTED:github-pat] b [REDACTED:gitlab-pat] c", ["github-pat", "gitlab-pat"]))

    def test_scanner_exception_refuses_scan_failed(self):
        self.adversarial()

        class Boom:
            def finditer(self, text):
                raise RuntimeError(TOKEN)

        with mock.patch.object(post, "SECRET_RULES", (("boom", Boom()),)):
            rc, out, st = self.build()
        self.assertEqual((rc, st["reason"]), (1, "scan-failed"))
        self.assertNotIn(TOKEN, out)
        self.assertFalse(os.path.exists(self.payload_path()))
        digest = self.build()[2]["digest"]
        self.fake.calls[:] = []
        with mock.patch.object(post, "SECRET_RULES", (("boom", Boom()),)):
            rc, out, st = self.call("submit", "--run", self.run_dir, "--digest", digest)
        self.assertEqual((rc, st["reason"]), (1, "scan-failed"))
        self.assertEqual(self.fake.calls, [])

    def edited(self, edit):
        """Build, rewrite payload.json through `edit`, recompute the digest and submit."""
        self.adversarial()
        self.build()
        _, payload = self.payload()
        edit(payload)
        with open(self.payload_path(), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True, indent=2))
        with open(self.payload_path(), "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        self.fake.calls[:] = []
        return self.call("submit", "--run", self.run_dir, "--digest", digest)

    def test_submit_rescans_for_a_secret(self):
        rc, out, st = self.edited(lambda p: p["comments"][0].update(body=p["comments"][0]["body"] + "\n**Fix:** " + TOKEN))  # a line shape build writes
        self.assertEqual((rc, st["reason"]), (1, "secret-found"))
        self.assertEqual(self.fake.calls, [])

    def test_submit_reguards_the_review_body(self):
        rc, out, st = self.edited(lambda p: p.update(body=p["body"] + "\n/approve"))
        self.assertEqual((rc, st["reason"]), (1, "unsafe-text"))
        self.assertEqual(self.fake.calls, [])

    def test_submit_reapplies_every_text_rule(self):
        self.adversarial()
        self.build()
        _, original = self.payload()
        body_edits = (("mention", "ping @org/team"), ("html", "<img src=x>"), ("link", "[text](https://e)"),
                      ("image", "![x](https://e/p.png)"), ("even backslash run", "\\\\[text](https://e)"),
                      ("entity", "&commat;x"), ("escaped backtick", "\\`[x](https://e)`"), ("raw ampersand", "a & b"),
                      ("epic behind an entity", "&amp;123"), ("label", "~bug"), ("quoted milestone", '%"rc 1"'),
                      ("snippet", "$45"), ("escaped issue bracket", "\\[issue:1]"), ("escaped wiki link", "\\[\\[Home]]"),
                      ("issue key", "GL-7"), ("project", "grp/proj>"),
                      ("a tilde fence hiding live markdown", "**Fix:** x\n~~~\n```text\n~~~\n"
                       "[click here](https://evil.example/login) @octocat <img src=x onerror=alert(1)>\n```"),
                      ("a backslash before a letter", "**Fix:** ]1\\GH-2"), ("a line build never writes", "    indented"),
                      ("an unprefixed line in the fence", "```text\n20: a\nplain\n```"))
        edits = [(what, lambda p, line=line: p.update(body=line + "\n" + p["body"])) for what, line in body_edits]
        edits.append(("a fence closed early by a longer backtick line", lambda p: p["comments"][0].update(
            body=p["comments"][0]["body"].replace("```text\n", "```text\n````\n[x](https://e)\n", 1))))
        edits.append(("an unclosed fence", lambda p: p["comments"][0].update(
            body=p["comments"][0]["body"].replace("\n```\n", "\n", 1))))
        for what, edit in edits:
            with self.subTest(what):
                payload = json.loads(json.dumps(original))
                edit(payload)
                data = json.dumps(payload, sort_keys=True, indent=2).encode()
                with open(self.payload_path(), "wb") as fh:
                    fh.write(data)
                self.fake.calls[:] = []
                rc, out, st = self.call("submit", "--run", self.run_dir, "--digest", hashlib.sha256(data).hexdigest())
                self.assertEqual((rc, st.get("reason")), (1, "unsafe-text"), out)
                self.assertEqual(self.fake.calls, [])

    def test_submit_reguards_every_comment(self):
        rc, out, st = self.edited(lambda p: p["comments"][0].update(body="<!-- x -->\n" + p["comments"][0]["body"]))
        self.assertEqual((rc, st["reason"]), (1, "unsafe-text"))
        self.assertEqual(self.fake.calls, [])


JWT = "eyJ" + "a" * 20 + ".eyJ" + "b" * 20 + "." + "c" * 12
LIVE_LINK = re.compile(r"(?<!\\)(?:\\\\)*\[[^\]\n]*\]\(")  # a `[` behind an even backslash run, then `](`: CommonMark links it


class Redaction(Base):
    """Secrets are redacted in each raw field before any escaping, so a redaction can never eat an escape."""

    def posted(self):
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"]), (0, "ok"), out)
        rc, out, sub = self.call("submit", "--run", self.run_dir, "--digest", st["digest"])
        self.assertEqual((rc, sub["status"]), (0, "ok"), out)
        _, payload = self.payload()
        return st, payload, json.dumps(self.fake.calls)

    def test_a_jwt_right_before_a_link_leaves_the_link_escaped(self):
        self.review(20, fix="use %s[click here](https://evil.example/login) to fix" % JWT)
        st, payload, calls = self.posted()
        self.assertEqual(st["redacted"], 1)
        body = payload["comments"][0]["body"]
        self.assertIn("**Fix:** use \\[REDACTED:jwt]\\[click here](https://evil.example/login) to fix", body)
        self.assertIsNone(LIVE_LINK.search(body), body)
        sent = [b["comments"][0]["body"] for m, e, b in self.fake.calls if (m, e) == ("POST", PR + "/reviews")]
        self.assertEqual(sent, [body])
        self.assertNotIn(JWT, calls)

    def test_a_backticked_secret_is_redacted_not_refused(self):
        self.review(20, fix="set token = `abcdefgh12` in the config")
        st, payload, calls = self.posted()
        self.assertEqual(st["redacted"], 1)
        self.assertIn("[REDACTED:generic-secret]", payload["comments"][0]["body"])
        self.assertNotIn("abcdefgh12", calls)

    def test_a_backslash_before_a_bracket_cannot_unescape_it(self):
        self.review(20, fix="see \\[x](https://evil.example) and \\\\[y](https://evil.example)")
        _, payload, _ = self.posted()
        body = payload["comments"][0]["body"]
        self.assertIsNone(LIVE_LINK.search(body), body)
        self.assertIn("**Fix:** see \\\\\\[x](https://evil.example)", body)


KEYS = ["MIIEowIBAAKCAQEA%dq7BFUwW3hvrJ" % n + "Zk9" * 8 for n in range(1, 6)]  # one key body line per case


class PrivateKey(Base):
    """A private-key marker anywhere in a finding blanks all of its free text: the key body is never found by regex."""

    inject = {2: "-----BEGIN PGP PRIVATE KEY BLOCK-----", 3: " ", 4: KEYS[0], 5: "-----END PGP PRIVATE KEY BLOCK-----",
              9: "-----BEGIN RSA PRIVATE KEY-----\r", 10: KEYS[1] + "\r", 11: "-----END RSA PRIVATE KEY-----\r",
              15: "key = '-----BEGIN RSA PRIVATE KEY-----\\n' +", 16: "'%s\\n' +" % KEYS[2],
              17: "'-----END RSA PRIVATE KEY-----'", 26: KEYS[4]}

    def cand(self, line, end, quote, **kw):
        return dict({"path": "src/calc.py", "line": line, "end_line": end, "severity": "required", "category": "bug",
                     "title": "title %d" % line, "trigger": "trigger %d" % line, "quote": quote, "fix": "fix"}, **kw)

    def test_no_key_body_line_is_posted(self):
        i = self.inject
        cands = [self.cand(2, 5, "\n".join(i[n] for n in range(2, 6))),  # PGP armor: a blank line after BEGIN
                 self.cand(9, 11, "\r\n".join(i[n].rstrip("\r") for n in range(9, 12))),  # CRLF
                 self.cand(15, 17, "\n".join(i[n] for n in range(15, 18))),  # JS string concatenation
                 self.cand(21, 21, row(21), fix="-----BEGIN RSA PRIVATE KEY-----\n%s\n-----END RSA PRIVATE KEY-----" % KEYS[3]),
                 self.cand(26, 26, KEYS[4], title="-----BEGIN RSA PRIVATE KEY-----")]  # header in title, body in quote
        self.ingest(cands, {c["line"]: (c["line"], c["quote"].split("\n")[0].rstrip("\r")) for c in cands})
        rc, out, st = self.build()
        raw, payload = self.payload()
        self.assertEqual((rc, st["status"]), (0, "ok"), out)
        self.assertEqual({h["where"] for h in payload["scan"] if h["rule"] == "private-key"},
                         {"src/calc.py:%d" % n for n in (2, 9, 15, 21, 26)})
        rc, sub_out, sub = self.call("submit", "--run", self.run_dir, "--digest", st["digest"])
        self.assertEqual((rc, sub["status"]), (0, "ok"), sub_out)
        for text in (out, raw.decode("utf-8"), json.dumps(self.fake.calls)):
            for k in KEYS:
                self.assertNotIn(k[:24], text)
            self.assertNotIn("PRIVATE KEY", text)


FORGED = "<!-- review-code:finding F-aaaaaaaaaa v1 -->"


class KeyFile(PrivateKey):
    """A key's marker line in one finding taints its file: another finding quoting only the body is blanked too."""

    inject = {20: "-----BEGIN RSA PRIVATE KEY-----", 21: KEYS[0], 22: KEYS[1], 23: "-----END RSA PRIVATE KEY-----"}

    def test_a_body_quoted_by_another_finding_is_not_posted(self):
        cands = [self.cand(20, 20, self.inject[20]), self.cand(21, 22, KEYS[0] + "\n" + KEYS[1], category="test")]
        cands[1]["title"] = "body only"
        self.ingest(cands, {c["line"]: (c["line"], c["quote"].split("\n")[0]) for c in cands})
        rc, out, st = self.build()
        raw, payload = self.payload()
        self.assertEqual((rc, st["status"]), (0, "ok"), out)
        self.assertEqual(sorted(h["where"] for h in payload["scan"]), ["src/calc.py:20", "src/calc.py:21"])
        for text in (out, raw.decode("utf-8")):
            for k in KEYS[:2]:
                self.assertNotIn(k[:24], text)
            self.assertNotIn("body only", text)

    test_no_key_body_line_is_posted = None


class LoneCR(Base):
    """A lone CR in a quote would end the `N: ` prefixed line on a forge that turns CR into LF."""

    inject = {20: "v20 = -20  # " + FORGED}

    def test_a_lone_cr_never_reaches_a_body(self):
        cand = {"path": "src/calc.py", "line": 20, "severity": "required", "category": "bug", "title": "t",
                "trigger": "t", "quote": "v20 = -20  #\r" + FORGED, "fix": "f"}
        own = self.ingest([cand], {20: (20, self.inject[20])})[20]
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"]), (0, "ok"), out)
        _, payload = self.payload()
        for body in [payload["body"]] + [c["body"] for c in payload["comments"]]:
            self.assertNotIn("\r", body)
        self.assertEqual(post.MARK_RE.findall(payload["comments"][0]["body"]), [own])
        self.assertIn("20: v20 = -20  #<U+000D>" + FORGED, payload["comments"][0]["body"])

    def test_submit_refuses_a_cr_in_an_edited_payload(self):
        self.review(20)
        rc, out, st = self.build()
        _, payload = self.payload()
        payload["comments"][0]["body"] = payload["comments"][0]["body"].replace("\n**Fix:**", "\r**Fix:**")
        data = json.dumps(payload, sort_keys=True, indent=2).encode()
        with open(self.payload_path(), "wb") as fh:
            fh.write(data)
        self.fake.calls[:] = []
        rc, out, st = self.call("submit", "--run", self.run_dir, "--digest", hashlib.sha256(data).hexdigest())
        self.assertEqual((rc, st["reason"]), (1, "unsafe-text"), out)
        self.assertEqual(self.fake.calls, [])


class Entities(Base):
    def test_a_named_entity_cannot_spell_a_mention_or_a_reference(self):
        self.review(20, fix="ping &commat;octocat about &num;12 and &excl;3, or &#64;x and &#x40;y")
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"]), (0, "ok"), out)
        fix = [ln for ln in self.payload()[1]["comments"][0]["body"].split("\n") if ln.startswith("**Fix:**")][0]
        self.assertIsNone(re.search(r"&(?!amp;)", re.sub(r"`[^`]*`", " ", fix)), fix)  # a code span shows `&` as written
        self.assertIn("`&commat`;octocat", fix)  # `&` followed by a word is a GitLab epic reference: a code span


class QuoteNumbers(Base):
    """A quote line is numbered only when it maps to one row of the reviewed file; otherwise it is posted unnumbered."""

    def cand(self, line, end, quote, category):
        return {"path": "src/calc.py", "line": line, "end_line": end, "severity": "required", "category": category,
                "title": "t", "trigger": "t", "quote": quote, "fix": "f"}

    def test_numbers_follow_the_file_rows(self):
        cands = [self.cand(20, 22, row(22), "bug"),  # the quote is the finding's last line
                 self.cand(21, 22, "v21 = -21 v22\n= -22", "test"),  # reflowed: a line break where the file has none
                 self.cand(22, 24, "= 2", "other")]  # in every row of 22 to 24: no single place
        ids = self.ingest(cands, {c["line"]: (c["line"] + 1, row(c["line"] + 1)) for c in cands})
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"]), (0, "ok"), out)
        bodies = {c["id"]: c["body"] for c in self.payload()[1]["comments"]}
        self.assertIn("\n22: v22 = -22\n", bodies[ids[20]])
        self.assertNotIn("20: ", bodies[ids[20]])
        for n, lines in ((21, ("| v21 = -21 v22", "| = -22")), (22, ("| = 2",))):
            body = bodies[ids[n]]
            self.assertIn("**Lines:** %d-%d (quoted without line numbers)" % (n, n + 1 if n == 21 else n + 2), body)
            for ln in lines:
                self.assertIn("\n%s\n" % ln, body)
            self.assertNotRegex(body, r"(?m)^\d+: ")
        rc, out, st = self.call("submit", "--run", self.run_dir, "--digest", st["digest"])
        self.assertEqual((rc, st["status"]), (0, "ok"), out)


SIGILS = ("&123", "~bug", '~"feature request"', "%v1.23", '%"release candidate"', "$45", "^alert", '*iteration:"s 1"',
          "[issue:12]", "[work_item:g/p/3]", "[[Home]]", "GL-7", "GH-8", "grp/proj>")


class GitLabSigils(Base):
    """GitLab links references in rendered text (docs.gitlab.com/user/markdown, GitLab-specific references): every
    documented sigil goes in a code span, which its reference filters skip; escaping alone renders the same text."""

    def test_every_reference_sigil_is_in_a_code_span(self):
        self.review(20, fix="see " + " and ".join(SIGILS))
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"]), (0, "ok"), out)
        fix = [ln for ln in self.payload()[1]["comments"][0]["body"].split("\n") if ln.startswith("**Fix:**")][0]
        for ref in SIGILS:
            self.assertIn("`%s`" % ref, fix)
        outside = re.sub(r"`[^`]*`", " ", fix)
        self.assertIsNone(re.search(r"[&~%$^]\w|[~%]\"|\*iteration|\[(?:issue|work_item)|\[\[|G[LH]-\d|/\w*>", outside), fix)


    def test_an_emphasis_underscore_does_not_hide_a_reference(self):
        self.review(20, fix="see _GH-12_ and __GL-7__ and _grp/proj>_ now")
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"]), (0, "ok"), out)
        fix = [ln for ln in self.payload()[1]["comments"][0]["body"].split("\n") if ln.startswith("**Fix:**")][0]
        outside = re.sub(r"`[^`]*`", " ", fix)
        for ref in ("GH-12", "GL-7", "proj>"):
            self.assertNotIn(ref, outside, fix)

    def test_a_reference_right_after_another_still_builds(self):
        self.review(20, fix="see GH-12GH-3 and GH-1x/y->> and 9GL-4 now")  # a code span ends a word for the forge
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"]), (0, "ok"), out)
        fix = [ln for ln in self.payload()[1]["comments"][0]["body"].split("\n") if ln.startswith("**Fix:**")][0]
        outside = re.sub(r"`[^`]*`", " ", fix)
        for ref in ("GH-3", "y->", "GL-4"):
            self.assertNotIn(ref, outside, fix)


class HtmlQuote(Base):
    inject = {20: "<!-- note -->"}

    def test_html_comment_in_quote_posts(self):
        cand = {"path": "src/calc.py", "line": 20, "severity": "required", "category": "bug", "title": "t", "trigger": "t",
                "quote": "<!-- note -->", "fix": "f"}
        self.ingest([cand], {20: (20, "<!-- note -->")})
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"], st["redacted"]), (0, "ok", 0), out)
        self.assertIn("\n20: <!-- note -->\n", self.payload()[1]["comments"][0]["body"])


class Idempotency(Base):
    """Re-posting adds nothing: only whole-line markers written by the authenticated user count."""

    def setUp(self):
        super().setUp()
        self.ids = self.review(2, 20)  # line 2 posts in the review body, line 20 inline

    def posted_once(self):
        rc, out, st = self.build()
        self.assertEqual(rc, 0, out)
        rc, out, st2 = self.call("submit", "--run", self.run_dir, "--digest", st["digest"])
        self.assertEqual(rc, 0, out)
        return st["digest"]

    def other_review(self, login, body="", comment=None, state="COMMENTED"):
        rid = self.fake.add_review(login, state)
        self.fake.reviews[rid]["body"] = body
        if comment is not None:
            self.fake.reviews[rid]["comments"].append({"path": "src/calc.py", "line": 20, "side": "RIGHT", "body": comment})
        return rid

    def payload_ids(self):
        _, payload = self.payload()
        return sorted(c["id"] for c in payload["comments"]) + sorted(payload["in_body"])

    def test_second_build_already_posted(self):
        self.posted_once()
        before = self.payload()[0]
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"], st["reason"]), (1, "refuse", "already-posted"), out)
        self.assertEqual(self.payload()[0], before)  # the refused build wrote nothing

    def test_second_submit_already_posted(self):
        digest = self.posted_once()
        writes = len(self.fake.writes())
        rc, out, st = self.call("submit", "--run", self.run_dir, "--digest", digest)
        self.assertEqual((rc, st["reason"]), (1, "already-posted"), out)
        self.assertEqual(len(self.fake.writes()), writes)  # no POST and no DELETE
        self.assertEqual([r["state"] for r in self.fake.reviews.values() if r["user"]["login"] == "me"], ["COMMENTED"])

    def test_new_head_posts_only_new(self):
        self.posted_once()
        old = self.fake
        gitfix.commit(self.repo, {"docs/n.txt": "n\n"}, "later")
        self.make_run()
        ids = self.review(2, 20, 24)
        self.fake.reviews, self.fake.next_id = old.reviews, old.next_id
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"], st["skipped"], st["inline"], st["in_body"]), (0, "ok", 2, 1, 0), out)
        self.assertEqual(self.payload_ids(), [ids[24]])
        self.assertEqual(sorted(self.payload()[1]["skipped"]), sorted([ids[2], ids[20]]))
        self.assertIn("already posted: 2", out)
        self.assertEqual(self.payload()[1]["body"].count("1 finding"), 1)  # the counts describe what this review posts

    def test_body_marker_counts(self):
        self.other_review("me", body="earlier\n" + post.MARK % self.ids[2] + "\nlater\n", comment=None)
        rc, out, st = self.build()
        self.assertEqual((rc, st["skipped"]), (0, 1), out)
        self.assertEqual(self.payload_ids(), [self.ids[20]])
        self.fake.reviews.clear()
        self.other_review("me", comment=post.MARK % self.ids[20] + "\r\n")  # a CRLF body from the web editor
        rc, out, st = self.build()
        self.assertEqual((rc, st["skipped"]), (0, 1), out)
        self.assertEqual(self.payload_ids(), [self.ids[2]])

    def test_spoofed_marker_ignored(self):
        self.other_review("mallory", body=post.MARK % self.ids[2], comment=post.MARK % self.ids[20])
        self.other_review("Me", body=post.MARK % self.ids[20])  # logins compare exactly, no case folding
        rc, out, st = self.build()
        self.assertEqual((rc, st["skipped"]), (0, 0), out)
        self.assertEqual(self.payload_ids(), sorted([self.ids[20], self.ids[2]]))

    def test_marker_must_be_whole_line(self):
        self.other_review("me", body="see " + post.MARK % self.ids[2] + " ok", comment="> " + post.MARK % self.ids[20])
        self.other_review("me", comment="20: " + post.MARK % self.ids[20])
        rc, out, st = self.build()
        self.assertEqual((rc, st["skipped"]), (0, 0), out)

    def test_pages(self):
        rid = self.other_review("me")
        cs = self.fake.reviews[rid]["comments"]
        cs += [{"path": "src/calc.py", "line": 20, "side": "RIGHT", "body": "c%d" % n} for n in range(150)]
        cs[120]["body"] = post.MARK % self.ids[20]  # on the second page
        rc, out, st = self.build()
        self.assertEqual((rc, st["skipped"]), (0, 1), out)
        self.assertEqual([c[1] for c in self.fake.calls].count(PR + "/comments"), 2)
        cs += [{"path": "src/calc.py", "line": 20, "side": "RIGHT", "body": "d"}] * (post.MAX_PAGES * post.PER_PAGE - 150)
        rc, out, st = self.build()
        self.assertEqual((rc, st["reason"]), (1, "too-many-comments"), out)


class Unverifiable(Base):
    extra = 5  # line 31 is an added row, so its unverifiable item is introduced and not dropped as pre-existing

    def test_unverifiable_count_only(self):
        ids = self.review(2, 20, 31, unverifiable=(31,))
        rc, out, st = self.build()
        raw, payload = self.payload()
        self.assertEqual((rc, st["unverifiable"]), (0, 1), out)
        for hidden in ("title 31", ids[31], "w031", "trigger 31"):
            self.assertNotIn(hidden, raw.decode("utf-8"))
        self.assertIn("Not posted: 1 unverifiable items", payload["body"])


class Notice(unittest.TestCase):
    def test_gitleaks_attribution(self):
        with open(os.path.join(os.path.dirname(SCRIPTS), "..", "..", "NOTICE"), encoding="utf-8") as fh:
            text = fh.read()
        for need in ("gitleaks", "https://github.com/gitleaks/gitleaks", "Copyright (c) 2019 Zachary Rice",
                     "config/gitleaks.toml"):
            self.assertIn(need, text)
        self.assertEqual(text.count("Permission is hereby granted"), 3)


class InlineCap(Base):
    extra = 97  # lines 31 to 127 are added

    def test_thirty_three_anchorable_findings_post_thirty_two_inline(self):
        lines_ = list(range(31, 128, 3))  # three lines apart, so ingest merges none of them
        self.assertEqual(len(lines_), 33)
        ids = self.review(*lines_)
        rc, out, st = self.build()
        _, payload = self.payload()
        self.assertEqual((rc, st["inline"], st["in_body"]), (0, post.MAX_INLINE, 1), out)
        self.assertEqual(payload["in_body"], [ids[lines_[-1]]])
        self.assertEqual(len(payload["comments"]), 32)


MR_URL = "https://gitlab.example.com/g/sub/r/-/merge_requests/5"
MR = "projects/g%2Fsub%2Fr/merge_requests/5"


class FakeGitLab:
    """Callable as post._cli(tool, args, cwd, stdin=None); records (method, endpoint without query, parsed body).
    Drafts are private to the user (id 42), as on GitLab; publishing moves every draft to the notes."""

    def __init__(self, run):
        with open(os.path.join(run, "target.json"), encoding="utf-8") as fh:
            self.head = json.load(fh)["head_sha"]
        with open(os.path.join(run, "change.diff"), encoding="utf-8") as fh:
            self.diff = fh.read()
        self.entries = []
        for chunk in re.split(r"(?m)^(?=diff --git )", self.diff):
            m = re.match(r"diff --git a/(\S+) b/(\S+)", chunk)
            if m:
                self.entries.append({"old_path": m.group(1), "new_path": m.group(2), "diff": chunk[chunk.index("@@ "):],
                                     "new_file": False, "renamed_file": False, "deleted_file": False,
                                     "collapsed": False, "too_large": False})
        self.context = {(p, n) for _, p, tag, _, n, _, _ in sanitize.walk(self.diff) if p and tag == " "}
        self.calls, self.drafts, self.notes, self.me_id, self.next_id = [], [], [], 42, 100
        self.fail_map = {}  # "METHOD endpoint-prefix" -> HTTP status
        self.mr_reads, self.moves_after, self.base_moves_after = 0, None, None  # the head or base moves after this many MR reads
        self.mr_state, self.draft, self.refs_empty = "opened", False, False
        self.drop_old_line, self.inject_foreign, self.fail_create_after = False, False, None
        self.creates, self.injected = 0, False

    def live_refs(self, reads):
        moved = self.moves_after is not None and reads >= self.moves_after
        base = self.base_moves_after is not None and reads >= self.base_moves_after
        return {"base_sha": "c" * 40 if base else "a" * 40, "start_sha": "a" * 40, "head_sha": "f" * 40 if moved else self.head}

    def writes(self):
        return [c for c in self.calls if c[0] in ("POST", "PUT", "DELETE")]

    def add_draft(self, note, author=42):
        d = {"id": self.next_id, "note": note, "position": None, "author": {"id": author}}
        self.next_id += 1
        self.drafts.append(d)
        return d

    @staticmethod
    def ok(obj):
        return 0, json.dumps(obj).encode(), b""

    @staticmethod
    def fail(code):
        return 1, b"", ("glab: %s (HTTP %d)" % (CANARY, code)).encode()

    @staticmethod
    def page(items, query):
        q = dict(kv.split("=") for kv in query.split("&") if kv)
        size, no = int(q.get("per_page", 30)), int(q.get("page", 1))
        return items[(no - 1) * size:no * size]

    def __call__(self, tool, args, cwd, stdin=None):
        assert tool == "glab", tool
        assert args[:4] == ["api", "--hostname", "gitlab.example.com", "-X"], args  # never the cwd remote or :fullpath
        method, endpoint = args[4], args[5]
        assert endpoint.startswith("projects/g%2Fsub%2Fr/") or endpoint.startswith("user"), endpoint
        assert args[6:] == (["--input", "-"] if stdin is not None else []), args
        body = json.loads(stdin.decode("utf-8")) if stdin is not None else None
        assert "reviewer_state" not in json.dumps(body), body
        path, _, query = endpoint.partition("?")
        self.calls.append((method, path, body))
        for key, code in self.fail_map.items():
            m, _, prefix = key.partition(" ")
            if method == m and path.startswith(prefix):
                return self.fail(code)
        return self.route(method, path, query, body)

    def route(self, method, path, query, body):
        if (method, path) == ("GET", "user"):
            return self.ok({"id": self.me_id})
        if (method, path) == ("GET", MR):
            reads, self.mr_reads = self.mr_reads, self.mr_reads + 1
            return self.ok({"state": self.mr_state, "draft": self.draft, "sha": self.live_refs(reads)["head_sha"],
                            "diff_refs": None if self.refs_empty else self.live_refs(reads)})
        if (method, path) == ("GET", MR + "/diffs"):
            return self.ok(self.page(self.entries, query))
        if (method, path) == ("GET", MR + "/notes"):
            return self.ok(self.page(self.notes, query))
        if (method, path) == ("GET", MR + "/draft_notes"):
            if self.inject_foreign and self.creates and not self.injected:
                self.injected = True
                self.add_draft("foreign draft, no marker")
            return self.ok(self.page(self.drafts, query))
        if (method, path) == ("POST", MR + "/draft_notes"):
            if self.fail_create_after is not None and self.creates >= self.fail_create_after:
                return self.fail(404)
            self.creates += 1
            pos = dict(body["position"]) if body.get("position") else None
            if pos and self.drop_old_line:
                pos.pop("old_line", None)
            d = self.add_draft(body["note"])
            d.update(position=pos, commit_id=body.get("commit_id"))
            return self.ok(d)
        if (method, path) == ("POST", MR + "/draft_notes/bulk_publish"):
            assert body is None, body  # no note and no review state
            for d in self.drafts:
                p = d["position"]
                if p and (p["new_path"], p["new_line"]) in self.context and "old_line" not in p:
                    return self.fail(500)  # GitLab accepts the draft and fails at publish
            self.notes += [{"id": d["id"], "body": d["note"], "author": d["author"], "position": d["position"]}
                           for d in self.drafts]
            self.drafts = []
            return 0, b"", b""
        m = re.fullmatch(re.escape(MR) + r"/draft_notes/(\d+)", path)
        if m and method == "DELETE":
            found = [d for d in self.drafts if d["id"] == int(m.group(1))]
            if not found:
                return self.fail(404)
            self.drafts.remove(found[0])
            return 0, b"", b""
        return self.fail(404)


class GitLabBase(Base):
    """The run of Base, retargeted to a merge request and served by FakeGitLab."""

    def ingest(self, cands, cites, unverifiable=()):
        ids = super().ingest(cands, cites, unverifiable)
        self.retarget(kind="mr", url=MR_URL, number=5)
        self.fake = FakeGitLab(self.run_dir)
        return ids

    def submit(self, digest):
        return self.call("submit", "--run", self.run_dir, "--digest", digest)

    def confirmed(self):
        rc, out, st = self.build()
        self.assertEqual(rc, 0, out)
        self.fake.calls[:] = []
        self.fake.mr_reads = 0
        return st["digest"]

    def refused(self, res, reason, detail=None):
        rc, out, st = res
        self.assertEqual((rc, st["status"], st["reason"]), (1, "refuse", reason), out)
        if detail is not None:
            self.assertEqual(st.get("detail"), detail)
        self.assertNotIn("digest", st)
        return st

    def deletes(self):
        return [c for c in self.fake.calls if c[0] == "DELETE"]

    def published(self):
        return [c for c in self.fake.calls if c[1].endswith("/bulk_publish")]


class GitLabTracer(GitLabBase):
    def setUp(self):
        super().setUp()
        self.ids = self.review(2, (19, "test"), 20)  # 2: outside every hunk; 19: unchanged context row; 20: added row

    def test_build_then_submit(self):
        rc, out, st = self.build()
        raw, payload = self.payload()
        self.assertEqual((rc, st["status"], st["inline"], st["in_body"]), (0, "ok", 2, 1), out)
        self.assertEqual(self.fake.writes(), [])
        self.assertTrue(all(m == "GET" for m, _, _ in self.fake.calls))
        seen = len(self.fake.calls)
        rc, out, st = self.submit(st["digest"])
        self.assertEqual((rc, st["status"], st["posted"], st["in_body"]), (0, "ok", 2, 1), out)
        tail = self.fake.calls[seen:]
        creates = [b for m, e, b in tail if (m, e) == ("POST", MR + "/draft_notes")]
        self.assertEqual(len(creates), 3)
        summary, context, added = creates
        self.assertEqual(sorted(summary), ["note"])  # no position
        self.assertIn(post.MARK % self.ids[2], summary["note"])
        refs = self.fake.live_refs(0)
        base = dict(refs, position_type="text", old_path="src/calc.py", new_path="src/calc.py")
        self.assertEqual(context["position"], dict(base, new_line=19, old_line=19))  # an unchanged row needs both lines
        self.assertEqual(added["position"], dict(base, new_line=20))  # an added row has no old line
        self.assertEqual((context["commit_id"], added["commit_id"]), (self.fake.head, self.fake.head))
        self.assertEqual([(m, e) for m, e, _ in tail[-4:]],  # verify, head, recount, publish
                         [("GET", MR + "/draft_notes"), ("GET", MR), ("GET", MR + "/draft_notes"),
                          ("POST", MR + "/draft_notes/bulk_publish")])
        self.assertIsNone(tail[-1][2])  # publish carries no body
        self.assertNotIn("reviewer_state", json.dumps(self.fake.calls))
        self.assertEqual(self.fake.drafts, [])
        notes = [n["body"] for n in self.fake.notes]
        self.assertEqual(len(notes), 3)
        for i in self.ids.values():
            self.assertEqual(sum(post.MARK % i in n for n in notes), 1)
        self.assertEqual(json.loads(out.splitlines()[-1])["review_id"], self.fake.notes[0]["id"])

    def test_preview_names_anchor_lines(self):
        rc, out, st = self.build()
        _, payload = self.payload()
        self.assertEqual(rc, 0, out)
        self.assertEqual(payload["diff_refs"], self.fake.live_refs(0))
        self.assertEqual((payload["forge"], payload["repo"], payload["number"]), ("gitlab", "g/sub/r", 5))
        for k, line in ((1, 19), (2, 20)):
            self.assertIn("== inline %d/2: src/calc.py:%d (RIGHT) ==" % (k, line), out)


class GitLabSafety(GitLabBase):
    """Refusals, races, own-draft cleanup and quick-action safety (Task 2)."""

    def setUp(self):
        super().setUp()
        self.ids = self.review(2, (19, "test"), 20)

    def test_a_users_own_draft_blocks_build_and_is_never_deleted(self):
        d = self.fake.add_draft("my own work in progress")
        self.refused(self.build(), "pending-drafts-exist")
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual(self.fake.drafts, [d])
        self.assertFalse(os.path.exists(self.payload_path()))

    def test_a_draft_appearing_after_build_refuses_submit_before_any_write(self):
        digest = self.confirmed()
        d = self.fake.add_draft("started after the build")
        self.refused(self.submit(digest), "pending-drafts-exist")
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual(self.fake.drafts, [d])

    def test_empty_diff_refs_refuse_pending(self):
        self.fake.refs_empty = True
        self.refused(self.build(), "diff-refs-pending")
        self.assertEqual(self.fake.calls, [("GET", MR, None)])

    def test_closed_merged_locked_and_draft_merge_requests_refuse(self):
        for state, draft, reason in (("closed", False, "closed"), ("merged", False, "merged"),
                                     ("locked", False, "locked"), ("opened", True, "draft")):
            with self.subTest(reason):
                self.fake.mr_state, self.fake.draft = state, draft
                self.refused(self.build(), reason)
                self.assertEqual(self.fake.writes(), [])

    def test_glab_missing(self):
        with mock.patch.object(post.subprocess, "run", side_effect=FileNotFoundError):
            self.refused(self.call("build", "--run", self.run_dir, cli=False), "glab-missing")

    def test_head_or_base_moved_at_a_before_the_diff(self):
        self.fake.moves_after = 0
        self.refused(self.build(), "head-moved")
        self.assertEqual(self.fake.calls, [("GET", MR, None)])

    def test_head_or_base_moved_at_b_after_the_reads(self):
        for hook in ("moves_after", "base_moves_after"):
            with self.subTest(hook):
                self.fake.mr_reads = 0
                setattr(self.fake, hook, 1)
                self.refused(self.build(), "head-moved")
                setattr(self.fake, hook, None)
                self.assertEqual(self.fake.writes(), [])

    def test_head_or_base_moved_at_c_before_the_first_write(self):
        digest = self.confirmed()
        for hook in ("moves_after", "base_moves_after"):
            with self.subTest(hook):
                self.fake.mr_reads = 0
                setattr(self.fake, hook, 0)
                self.refused(self.submit(digest), "head-moved")
                setattr(self.fake, hook, None)
                self.assertEqual(self.fake.writes(), [])

    def test_head_or_base_moved_at_d_deletes_only_this_payloads_drafts(self):
        digest = self.confirmed()
        for hook in ("moves_after", "base_moves_after"):
            with self.subTest(hook):
                self.fake.mr_reads, self.fake.calls[:] = 0, []
                setattr(self.fake, hook, 1)
                self.refused(self.submit(digest), "head-moved")
                setattr(self.fake, hook, None)
                self.assertEqual(len(self.deletes()), 3)
                self.assertEqual(self.published(), [])
                self.assertEqual(self.fake.drafts, [])

    def test_a_foreign_draft_between_create_and_verify_refuses_and_stays(self):
        digest = self.confirmed()
        self.fake.inject_foreign = True
        self.refused(self.submit(digest), "verify-failed")
        self.assertEqual(len(self.deletes()), 3)
        self.assertEqual([d["note"] for d in self.fake.drafts], ["foreign draft, no marker"])
        self.assertEqual(self.published(), [])

    def test_a_draft_added_after_verify_refuses_before_publish(self):
        digest = self.confirmed()
        real, listings = self.fake.route, []

        def route(m, p, q, b):
            if (m, p) == ("GET", MR + "/draft_notes") and self.fake.creates:
                listings.append(p)
                if len(listings) == 2:
                    self.fake.add_draft("typed in the GitLab UI during submit")
            return real(m, p, q, b)

        self.fake.route = route
        self.refused(self.submit(digest), "verify-failed")
        self.assertEqual(self.published(), [])
        self.assertEqual([d["note"] for d in self.fake.drafts], ["typed in the GitLab UI during submit"])

    def test_drafts_stored_with_crlf_still_verify(self):
        digest = self.confirmed()
        real = self.fake.route

        def route(m, p, q, b):
            if (m, p) == ("GET", MR + "/draft_notes"):
                for d in self.fake.drafts:
                    d["note"] = d["note"].rstrip("\r\n").replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n"
            return real(m, p, q, b)

        self.fake.route = route
        rc, out, st = self.submit(digest)
        self.assertEqual((rc, st["status"]), (0, "ok"), out)

    def test_a_context_draft_stored_without_old_line_never_reaches_publish(self):
        digest = self.confirmed()
        self.fake.drop_old_line = True
        self.refused(self.submit(digest), "verify-failed")
        self.assertEqual(self.published(), [])
        self.assertEqual(self.fake.drafts, [])

    def test_publish_error_reports_the_status_and_deletes_the_drafts(self):
        digest = self.confirmed()
        self.fake.fail_map["POST " + MR + "/draft_notes/bulk_publish"] = 500
        self.refused(self.submit(digest), "post-failed", "HTTP 500")
        self.assertEqual(len(self.deletes()), 3)
        self.assertEqual((self.fake.drafts, self.fake.notes), ([], []))

    def publish_one_then_fail(self, also=None):
        real = self.fake.route

        def route(m, p, q, b):
            if (m, p) != ("POST", MR + "/draft_notes/bulk_publish"):
                return real(m, p, q, b)
            d = self.fake.drafts.pop(0)  # GitLab published the summary draft, then failed on the next
            self.fake.notes.append({"id": d["id"], "body": d["note"], "author": d["author"], "position": d["position"]})
            if also:
                also()
            return self.fake.fail(500)

        self.fake.route = route

    def test_a_publish_that_fails_part_way_reports_what_is_public(self):
        self.fake.notes.append({"id": 1, "body": "an earlier note of mine\n" + post.REVIEW_MARK % self.fake.head,
                                "author": {"id": 42}})  # same head, an earlier review: not counted
        digest = self.confirmed()
        self.publish_one_then_fail()
        self.refused(self.submit(digest), "post-partial", "published 1 of 3")
        self.assertEqual(self.fake.drafts, [])  # the drafts left of this payload are deleted
        self.assertEqual(len(self.fake.notes), 2)

    def test_a_publish_failure_with_no_note_listing_is_unknown(self):
        digest = self.confirmed()
        self.publish_one_then_fail(also=lambda: self.fake.fail_map.update({"GET " + MR + "/notes": 500}))
        self.refused(self.submit(digest), "post-partial", "published unknown")

    def test_failed_delete_names_the_drafts_and_the_next_build_refuses(self):
        digest = self.confirmed()
        self.fake.fail_map["POST " + MR + "/draft_notes/bulk_publish"] = 500
        self.fake.fail_map["DELETE " + MR + "/draft_notes/"] = 500
        self.refused(self.submit(digest), "cleanup-failed", "drafts 100,101,102")
        self.assertEqual(len(self.fake.drafts), 3)
        self.fake.fail_map.clear()
        self.refused(self.build(), "pending-drafts-exist")

    def test_draft_notes_unavailable_leaves_nothing(self):
        digest = self.confirmed()
        self.fake.fail_map["POST " + MR + "/draft_notes"] = 404
        self.refused(self.submit(digest), "draft-notes-unavailable")
        self.assertEqual(self.fake.drafts, [])
        del self.fake.fail_map["POST " + MR + "/draft_notes"]
        self.fake.fail_create_after = 1  # the summary draft exists when the next create is refused
        self.refused(self.submit(digest), "draft-notes-unavailable")
        self.assertEqual(self.fake.drafts, [])
        self.assertEqual(len(self.deletes()), 1)

    def test_diffs_404_refuses_diff_unavailable(self):
        self.fake.fail_map["GET " + MR + "/diffs"] = 404
        self.refused(self.build(), "diff-unavailable")

    def test_collapsed_and_too_large_files_go_to_the_summary(self):
        for flag in ("collapsed", "too_large"):
            with self.subTest(flag):
                self.fake.entries[0][flag] = True
                rc, out, st = self.build()
                _, payload = self.payload()
                self.assertEqual((rc, st["inline"], st["in_body"]), (0, 0, 3), out)
                self.assertEqual(sorted(post.MARK_RE.findall(payload["body"])), sorted(self.ids.values()))
                self.fake.entries[0][flag] = False

    def test_a_path_with_a_line_break_is_skipped(self):
        evil = {"old_path": "src/x\ny.py", "new_path": "src/x\ny.py", "diff": "@@ -1 +1 @@\n-a\n+b\n", "new_file": False,
                "renamed_file": False, "deleted_file": False, "collapsed": False, "too_large": False}
        self.fake.entries.insert(0, evil)
        with mock.patch.object(post, "_cli", self.fake):
            text = post.GitLab(post._target(self.run_dir)).diff()
        self.assertNotIn("y.py", text)
        self.assertIn("diff --git a/src/calc.py b/src/calc.py\n--- a/src/calc.py\n+++ b/src/calc.py\n@@ -17,9 +17,9 @@", text)

    def test_every_call_names_the_host_and_the_encoded_project(self):
        digest = self.confirmed()
        self.assertEqual(self.submit(digest)[0], 0)  # the fake asserts --hostname and the project path on every call

    def test_a_note_by_another_author_does_not_skip(self):
        self.fake.notes.append({"id": 1, "body": post.MARK % self.ids[20], "author": {"id": 7}})
        rc, out, st = self.build()
        self.assertEqual((rc, st["skipped"], st["inline"]), (0, 0, 2), out)
        self.fake.notes.append({"id": 2, "body": post.MARK % self.ids[20], "author": {"id": 42}})
        rc, out, st = self.build()
        self.assertEqual((rc, st["skipped"], st["inline"]), (0, 1, 1), out)

    def test_after_a_full_publish_build_and_submit_add_nothing(self):
        rc, out, st = self.build()
        self.assertEqual(self.submit(st["digest"])[0], 0)
        before = (len(self.fake.notes), len(self.fake.writes()))
        self.refused(self.build(), "already-posted")
        self.refused(self.submit(st["digest"]), "already-posted")
        self.assertEqual((len(self.fake.notes), len(self.fake.writes())), before)


class GitLabQuickActions(GitLabBase):
    """GitLab runs a note line that starts with a slash as a command when the review is published."""

    inject = {20: "/merge", 21: "/submit_review approve"}

    def test_a_quick_action_never_starts_a_note_line(self):
        cand = {"path": "src/calc.py", "line": 20, "end_line": 21, "severity": "required", "category": "bug",
                "title": "/approve", "trigger": "/submit_review approve", "fix": "/merge now",
                "quote": "/merge\n/submit_review approve"}
        self.ingest([cand], {20: (20, "/merge")})
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"]), (0, "ok"), out)
        self.assertEqual(self.submit(st["digest"])[0], 0)
        self.assertEqual(len(self.fake.notes), 2)
        for n in self.fake.notes:
            self.assertNotRegex(n["body"], r"(?m)^\s*/")
        self.assertIn("20: /merge", self.fake.notes[1]["body"])  # quoted behind its line-number prefix


class EventConstant(unittest.TestCase):
    def test_no_other_review_event_in_any_string_constant(self):
        with open(os.path.join(SCRIPTS, "post.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertIn("COMMENT", strings)
        for s in strings:
            self.assertIsNone(re.search(r"approve|request[ _]?changes?|requested[ _]changes", s, re.I), s)
        self.assertEqual(post.EVENT, "COMMENT")

    def test_no_review_state_or_quick_action_in_any_string_constant(self):
        with open(os.path.join(SCRIPTS, "post.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                self.assertNotIn("reviewer_state", n.value)
                self.assertIsNone(re.search(r"/(?:un)?approve\b|/merge\b", n.value), n.value)


if __name__ == "__main__":
    unittest.main()
