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
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "review-code", "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import gitfix  # noqa: E402
import post  # noqa: E402
import sanitize  # noqa: E402
import target  # noqa: E402

PR_URL = "https://github.com/o/r/pull/7"
PR = "repos/o/r/pulls/7"
CANARY = "CANARY-FORGE-TEXT-7c1"  # every fake failure carries it on stderr; no stdout line may


def cli(script, args, cwd, tmp, stdin=""):
    e = gitfix.env()
    e["TMPDIR"] = tmp
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + args, cwd=cwd, env=e,
                       input=stdin.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8")


def wire(o):
    """dict -> [[key, value], ...], recursively: the brace-free wire form (g1x.10, the Bash guard denies a brace and a quote)."""
    if isinstance(o, dict):
        return [[k, wire(v)] for k, v in o.items()]
    return [wire(v) for v in o] if isinstance(o, list) else o


def lines(*objs):
    return "\n".join(o if isinstance(o, str) else json.dumps(wire(o)) for o in objs) + "\n"


def fid(path, category, quote):
    return "F-" + hashlib.sha256("\0".join((path, category, " ".join(quote.split()))).encode()).hexdigest()[:10]


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
        return {(p, n) for _, p, tag, _, n, _, _ in sanitize._walk(self.diff) if p and tag in " +"}

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
            stored = self.stored_override(rev["comments"]) if self.stored_override else rev["comments"]
            return self.ok(self.page(stored, query))
        if m.group(2) == "/events" and method == "POST":
            assert body == {"event": "COMMENT"}, body
            rev["state"] = "COMMENTED"
            return self.ok({"id": rev["id"], "state": rev["state"]})
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
        self.assertEqual(tail[at:], [("POST", PR + "/reviews"), ("GET", PR + "/reviews/100/comments"), ("GET", PR),
                                     ("POST", PR + "/reviews/100/events")])  # create, read back, head, event
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
        self.assertEqual(out.splitlines()[-1], json.dumps(st, sort_keys=True))  # the status line is last

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

    def test_interrupt_after_create_deletes_the_review(self):
        digest = self.confirmed()
        self.fake.stored_override = mock.Mock(side_effect=KeyboardInterrupt)
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
        for good in (post.MARK % OTHER_ID, post.REVIEW_MARK % ("a" * 40), "20: /approve behind a prefix",
                     "a // b", "**Fix:** /merge", "x\n" + post.MARK % OTHER_ID + "\ny"):
            post._guard(good)

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
            "private-key": "-----BEGIN RSA " + "PRIVATE KEY-----\n12: " + "A" * 40 + "\n13: " + "B" * 20 + "\n14: -----END RSA PRIVATE KEY-----",
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
        rc, out, st = self.edited(lambda p: p["comments"][0].update(body=p["comments"][0]["body"] + "\n" + TOKEN))
        self.assertEqual((rc, st["reason"]), (1, "secret-found"))
        self.assertEqual(self.fake.calls, [])

    def test_submit_reguards_the_review_body(self):
        rc, out, st = self.edited(lambda p: p.update(body=p["body"] + "\n/approve"))
        self.assertEqual((rc, st["reason"]), (1, "unsafe-text"))
        self.assertEqual(self.fake.calls, [])

    def test_submit_reguards_every_comment(self):
        rc, out, st = self.edited(lambda p: p["comments"][0].update(body="<!-- x -->\n" + p["comments"][0]["body"]))
        self.assertEqual((rc, st["reason"]), (1, "unsafe-text"))
        self.assertEqual(self.fake.calls, [])


class HtmlQuote(Base):
    inject = {20: "<!-- note -->"}

    def test_html_comment_in_quote_posts(self):
        cand = {"path": "src/calc.py", "line": 20, "severity": "required", "category": "bug", "title": "t", "trigger": "t",
                "quote": "<!-- note -->", "fix": "f"}
        self.ingest([cand], {20: (20, "<!-- note -->")})
        rc, out, st = self.build()
        self.assertEqual((rc, st["status"], st["redacted"]), (0, "ok", 0), out)
        self.assertIn("\n20: <!-- note -->\n", self.payload()[1]["comments"][0]["body"])


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


class EventConstant(unittest.TestCase):
    def test_no_other_review_event_in_any_string_constant(self):
        with open(os.path.join(SCRIPTS, "post.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertIn("COMMENT", strings)
        for s in strings:
            self.assertIsNone(re.search(r"approve|request[ _]?changes?|requested[ _]changes", s, re.I), s)
        self.assertEqual(post.EVENT, "COMMENT")


if __name__ == "__main__":
    unittest.main()
