"""fix.py on real gitfix repos: plan, baseline, begin, a simulated fixer edit, finish, report."""
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "review-code", "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import fix  # noqa: E402
import gitfix  # noqa: E402
import target  # noqa: E402

BASE = {"src/calc.py": "def load():\n    return 1\n"}
FEAT = "def load():\n    return 1\n\ndef add(a, b):\n    return a - b\n"  # add is lines 4 and 5
QUOTE = "return a - b"
CAND = {"path": "src/calc.py", "line": 5, "severity": "required", "category": "bug", "title": "add subtracts",
        "trigger": "add(2, 3) returns -1", "quote": QUOTE, "fix": "return a + b"}
CITE = {"path": "src/calc.py", "line": 5, "quote": QUOTE}


def cli(script, args, cwd, tmp, stdin=b"", extra=None):
    e = gitfix.env()
    e["TMPDIR"] = tmp
    e.update(extra or {})
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + args, cwd=cwd, env=e, input=stdin,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8")


def wire(o):
    """dict -> [[key, value], ...], recursively: the brace-free wire form the lanes use."""
    if isinstance(o, dict):
        return [[k, wire(v)] for k, v in o.items()]
    return [wire(v) for v in o] if isinstance(o, list) else o


def lines(*objs):
    return "\n".join(o if isinstance(o, str) else json.dumps(wire(o)) for o in objs) + "\n"


class Base(unittest.TestCase):
    base_files, feat_files = BASE, {"src/calc.py": FEAT}

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, self.base_files, "base")
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        gitfix.commit(self.repo, self.feat_files, "feat")

    def git(self, *a):
        return gitfix.run(["git"] + list(a), self.repo).strip()

    def tool(self, script, args, stdin=""):
        rc, out, err = cli(script, args, self.repo, self.tmp, stdin.encode("utf-8"))
        self.assertNotIn("Traceback", err)
        return rc, out

    def pin(self, arg="main", trust="own"):
        rc, out = self.tool("target.py", [arg])
        self.assertEqual(rc, 0, out)
        run = json.loads(out)["run"]
        self.assertEqual(self.tool("ledger.py", ["build", "--run", run])[0], 0)
        if trust:
            self.assertEqual(self.tool("ledger.py", ["trust", "set", trust, "--run", run])[0], 0)
        return run

    def ingest(self, run, *cands):
        rc, out = self.tool("report.py", ["ingest", "--run", run], lines(*(list(cands) + [{"inspected": ["src/calc.py"]}])))
        self.assertEqual(rc, 0, out)
        return json.loads(out)["ids"]

    def confirm(self, run, *pairs):
        """pairs: (id, cite) -> a confirmed verdict each."""
        rc, out = self.tool("report.py", ["verdicts", "--run", run], lines(*[
            {"id": i, "verdict": "confirmed", "evidence": [c], "reason": "r"} for i, c in pairs]))
        self.assertEqual(rc, 0, out)

    def cmd(self):
        return [sys.executable, "-B", "-c", "import sys; sys.path.insert(0, 'src'); import calc"]

    def fixrun(self, args, stdin=""):
        rc, out = self.tool("fix.py", args, stdin)
        last = [x for x in out.split("\n") if x][-1]
        return rc, out, json.loads(last)

    def plan(self, run, words=None):
        return self.fixrun(["plan", "--run", run], "".join(w + "\n" for w in (self.cmd() if words is None else words)))

    def baseline(self, run, digest):
        return self.fixrun(["baseline", "--run", run, "--digest", digest])

    def begin(self, run):
        return self.fixrun(["begin", "--run", run])

    def finish(self, run, seal, status="applied"):
        return self.fixrun(["finish", "--run", run, "--seal", seal, "--status", status])

    def report(self, run):
        return self.fixrun(["report", "--run", run])

    def edit(self, rel, old, new):
        """The simulated fixer: one in-place edit of one file."""
        path = os.path.join(self.repo, rel)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(old, new, 1))

    def refs(self):
        return self.git("rev-parse", "HEAD"), self.git("for-each-ref"), self.git("stash", "list")

    def ready(self, cand=CAND):
        """A pinned run with one confirmed finding, plan written and baseline green: (run, fid)."""
        run = self.pin()
        (fid,) = self.ingest(run, cand)
        self.confirm(run, (fid, dict(CITE, quote=cand["quote"], line=cand["line"])))
        return run, fid

    def go(self, run):
        rc, out, j = self.plan(run)
        self.assertEqual((rc, j["status"]), (0, "ok"), out)
        rc, _, j2 = self.baseline(run, j["digest"])
        self.assertEqual((rc, j2["status"]), (0, "ok"), j2)
        return j["digest"]


class Tracer(Base):
    def test_one_fix_end_to_end(self):
        run, fid = self.ready()
        before = self.refs()
        rc, out, j = self.plan(run)
        self.assertEqual((rc, j["status"], j["apply"]), (0, "ok", 1), out)
        self.assertRegex(j["digest"], "^[0-9a-f]{64}$")
        head = out.split("\n")[0]
        self.assertTrue(head.startswith("review-code fix plan: 1 to apply, 0 skipped; tests: "), head)
        for need in (fid, "src/calc.py:5", "sonnet/medium", " ".join(self.cmd()[:2])):
            self.assertIn(need, out)
        fixdir = os.path.join(run, "fix")
        self.assertEqual(stat.S_IMODE(os.stat(fixdir).st_mode), 0o700)
        pj = os.path.join(fixdir, "plan.json")
        self.assertEqual(stat.S_IMODE(os.stat(pj).st_mode), 0o600)
        with open(pj, "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), j["digest"])
        self.assertFalse(os.path.exists(os.path.join(fixdir, "state.json")))
        rc, _, b = self.baseline(run, j["digest"])
        self.assertEqual((rc, b["status"]), (0, "ok"), b)
        rc, _, bg = self.begin(run)
        self.assertEqual((rc, bg["status"], bg["id"], bg["path"], bg["line"], bg["model"], bg["effort"]),
                         (0, "ok", fid, "src/calc.py", 5, "sonnet", "medium"), bg)
        self.assertRegex(bg["seal"], "^[0-9a-f]{64}$")
        self.assertEqual(os.path.dirname(bg["brief"]), fixdir)
        self.edit("src/calc.py", QUOTE, "return a + b")
        rc, _, f = self.finish(run, bg["seal"])
        self.assertEqual((rc, f["status"], f["outcome"]), (0, "ok", "fixed"), f)
        self.assertEqual(self.begin(run)[2]["status"], "done")
        rc, text, r = self.report(run)
        self.assertEqual((rc, r["status"], r["fixed"]), (0, "ok", 1), text)
        self.assertIn("%s src/calc.py:5 fixed" % fid, text)
        with open(os.path.join(self.repo, "src/calc.py"), encoding="utf-8") as fh:
            self.assertIn("return a + b", fh.read())
        self.assertEqual(self.refs(), before)
        self.assertEqual(self.git("status", "--porcelain"), "M src/calc.py")

    def test_failing_tests_revert_byte_exact(self):
        run, fid = self.ready()
        self.go(run)
        path = os.path.join(self.repo, "src/calc.py")
        with open(path, "rb") as fh:
            old = fh.read()
        mode = os.stat(path).st_mode
        before = self.refs()
        seal = self.begin(run)[2]["seal"]
        self.edit("src/calc.py", QUOTE, "return a +")
        rc, _, f = self.finish(run, seal)
        self.assertEqual((rc, f["status"], f["outcome"]), (1, "halted", "reverted-tests"), f)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), old)
        self.assertEqual(os.stat(path).st_mode, mode)
        rc, _, b = self.begin(run)
        self.assertEqual((rc, b), (1, {"status": "refuse", "reason": "halted"}))
        self.assertEqual(self.refs(), before)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_baseline_red_edits_nothing(self):
        run, _ = self.ready()
        rc, _, j = self.plan(run, [sys.executable, "-B", "-c", "raise SystemExit(3)"])
        rc, _, b = self.baseline(run, j["digest"])
        self.assertEqual((rc, b["status"], b["reason"], b["exit"]), (1, "halted", "baseline-red", 3), b)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.begin(run)[2]["reason"], "halted")

    def test_brief_fences_untrusted_text(self):
        run, fid = self.ready()
        self.go(run)
        bg = self.begin(run)[2]
        with open(bg["brief"], encoding="utf-8") as fh:
            brief = fh.read()
        self.assertTrue(brief.startswith("# Fix finding %s\n" % fid), brief)
        self.assertIn(json.dumps(os.path.join(self.repo, "src/calc.py")), brief)
        nonces = set(re.findall(r"<<<UNTRUSTED-([0-9a-f]+)\n", brief))
        self.assertEqual(len(nonces), 1)
        (n,) = nonces
        for label, text in (("title", "add subtracts"), ("trigger", "add(2, 3) returns -1"), ("quote", QUOTE),
                            ("fix", "return a + b")):
            self.assertRegex(brief, r"(?m)^%s:\n<<<UNTRUSTED-%s\n%s\n%s>>>$" % (label, n, re.escape(text), n))
        pre = os.path.join(run, "fix", fid + ".pre")
        with open(pre, "rb") as fh, open(os.path.join(self.repo, "src/calc.py"), "rb") as cur:
            self.assertEqual(fh.read(), cur.read())
        self.assertEqual(stat.S_IMODE(os.stat(pre).st_mode), 0o600)

    def test_no_history_writing_git_call(self):
        with open(os.path.join(SCRIPTS, "fix.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), feature_version=(3, 9))
        bad = {"commit", "push", "add", "stash", "reset", "checkout", "switch", "merge", "rebase", "tag", "fetch",
               "pull", "am"}
        self.assertEqual([n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value in bad], [])


class Units(unittest.TestCase):
    def test_tier(self):
        simple = {"category": "bug", "severity": "required", "line": 5, "end_line": 5, "fix": "return a + b"}
        self.assertEqual(fix._tier(simple), ("sonnet", "medium"))
        self.assertEqual(fix._tier(dict(simple, category="security")), ("opus", "high"))
        self.assertEqual(fix._tier(dict(simple, severity="blocking")), ("opus", "high"))
        self.assertEqual(fix._tier(dict(simple, end_line=9)), ("sonnet", "medium"))
        self.assertEqual(fix._tier(dict(simple, end_line=10)), ("opus", "high"))
        self.assertEqual(fix._tier(dict(simple, fix="x" * 300)), ("sonnet", "medium"))
        self.assertEqual(fix._tier(dict(simple, fix="x" * 301)), ("opus", "high"))

    def test_seal_reads_files_only(self):
        with tempfile.TemporaryDirectory() as d:
            fid = "F-0123456789"
            os.mkdir(os.path.join(d, "fix"))
            cfg, attr = os.path.join(d, "config"), os.path.join(d, "attributes")
            files = {os.path.join(d, "fix", "plan.json"): b"{}", os.path.join(d, "fix", fid + ".pre"): b"pre",
                     cfg: b"[core]\n", attr: b"* text\n"}
            state = os.path.join(d, "fix", "state.json")
            files[state] = json.dumps({"git_files": [cfg, attr], "x": 1}).encode()
            for p, data in files.items():
                with open(p, "wb") as fh:
                    fh.write(data)
            with mock.patch.object(target, "git_bytes", side_effect=AssertionError("git call")) as g, \
                    mock.patch("subprocess.Popen", side_effect=AssertionError("process")), \
                    mock.patch("subprocess.run", side_effect=AssertionError("process")):
                base = fix._seal(d, fid)
                self.assertRegex(base, "^[0-9a-f]{64}$")
                self.assertEqual(fix._seal(d, fid), base)
                for p, data in files.items():
                    with open(p, "wb") as fh:
                        fh.write(data + (b"\n" if p != state else b" "))
                    self.assertNotEqual(fix._seal(d, fid), base, p)
                    with open(p, "wb") as fh:
                        fh.write(data)
                os.remove(os.path.join(d, "fix", fid + ".pre"))
                self.assertNotEqual(fix._seal(d, fid), base)
            self.assertEqual(g.call_count, 0)

    def test_py39_grammar(self):
        with open(os.path.join(SCRIPTS, "fix.py"), encoding="utf-8") as fh:
            ast.parse(fh.read(), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
