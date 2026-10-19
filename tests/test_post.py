"""post.py on real target.py, ledger.py and report.py run dirs, against a dict-backed fake GitHub (`_cli` is patched)."""
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


def source(changed, extra):
    rows = [row(n) if changed else "v%02d = %d" % (n, n) for n in range(1, 31)]
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

    def writes(self):
        return [c for c in self.calls if c[0] in ("POST", "PUT", "DELETE")]

    def rows(self):
        return {(p, n) for _, p, tag, _, n, _, _ in sanitize._walk(self.diff) if p and tag in " +"}

    @staticmethod
    def ok(obj):
        return 0, json.dumps(obj).encode(), b""

    @staticmethod
    def fail(code):
        return 1, b"", ("gh: Validation Failed (HTTP %d)" % code).encode()

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
        return self.route(method, path, query, body)

    def route(self, method, path, query, body):
        if (method, path) == ("GET", "user"):
            return self.ok({"login": self.login})
        if (method, path) == ("GET", PR):
            return self.ok({"state": "open", "draft": False, "merged": False, "head": {"sha": self.head}})
        if path == PR + "/reviews" and method == "GET":
            return self.ok(self.page([{k: v for k, v in r.items() if k != "comments"} for r in self.reviews.values()], query))
        if path == PR + "/reviews" and method == "POST":
            rows = self.rows()
            if any((c["path"], c["line"]) not in rows or c["side"] != "RIGHT" for c in body["comments"]):
                return self.fail(422)
            rid, self.next_id = self.next_id, self.next_id + 1
            self.reviews[rid] = {"id": rid, "state": "PENDING", "user": {"login": self.login},
                                 "commit_id": body["commit_id"], "body": body["body"],
                                 "comments": [dict(c, commit_id=body["commit_id"]) for c in body["comments"]]}
            return self.ok({"id": rid, "state": "PENDING"})
        m = re.fullmatch(re.escape(PR) + r"/reviews/(\d+)(/comments|/events)?", path)
        rev = self.reviews.get(int(m.group(1))) if m else None
        if rev is None:
            return self.fail(404)
        if m.group(2) == "/comments" and method == "GET":
            return self.ok(self.page(rev["comments"], query))
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

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, {"src/calc.py": source(False, 0)}, "base")
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        gitfix.commit(self.repo, {"src/calc.py": source(True, self.extra)}, "feat")
        rc, out, _ = cli("target.py", ["main"], self.repo, self.tmp)
        self.assertEqual(rc, 0, out)
        self.run_dir = json.loads(out)["run"]
        rc, out, _ = cli("ledger.py", ["build", "--run", self.run_dir], self.repo, self.tmp)
        self.assertEqual(rc, 0, out)

    def review(self, *specs):
        """Ingest and confirm one Required finding per (line, category) or bare line; make the run a PR run. Ids by line."""
        specs = [s if isinstance(s, tuple) else (s, "bug") for s in specs]
        cands = [{"path": "src/calc.py", "line": n, "severity": "required", "category": cat, "title": "title %d" % n,
                  "trigger": "trigger %d" % n, "quote": row(n), "fix": "fix %d" % n} for n, cat in specs]
        rc, out, err = cli("report.py", ["ingest", "--run", self.run_dir], self.repo, self.tmp,
                           lines(*cands, {"inspected": ["src/calc.py"]}))
        self.assertEqual((rc, err), (0, ""), out)
        ids = {n: fid("src/calc.py", cat, row(n)) for n, cat in specs}
        verdicts = [{"id": ids[n], "verdict": "confirmed", "reason": "r",
                     "evidence": [{"path": "src/calc.py", "line": n, "quote": row(n)}]} for n, _ in specs]
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

    def call(self, *argv, fake=None):
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch.object(post, "_cli", fake or self.fake):
            rc = post.main(list(argv))
        out = buf.getvalue()
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


if __name__ == "__main__":
    unittest.main()
