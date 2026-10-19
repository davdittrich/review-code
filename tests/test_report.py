"""report.py on real target.py and ledger.py run dirs: finder line -> verdict -> schema v1 document."""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "review-code", "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import gitfix  # noqa: E402
import report  # noqa: E402

BASE = {"src/calc.py": "def load():\n    return 1\n"}
FEAT = "def load():\n    return 1\n\ndef add(a, b):\n    return a - b\n"  # lines 3 to 5 are added
CAND = {"path": "src/calc.py", "line": 5, "severity": "required", "category": "bug", "title": "add subtracts",
        "trigger": "add(2, 3) returns -1", "quote": "return a - b", "fix": "FIXTEXT-q1"}
CITE = {"path": "src/calc.py", "line": 5, "quote": "return a - b"}


def cli(script, args, cwd, tmp, stdin=""):
    e = gitfix.env()
    e["TMPDIR"] = tmp
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + args, cwd=cwd, env=e,
                       input=stdin.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8")


def lines(*objs):
    return "\n".join(o if isinstance(o, str) else json.dumps(o) for o in objs) + "\n"


class Base(unittest.TestCase):
    pin_args = ["main"]

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, BASE, "base")
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)
        gitfix.commit(self.repo, {"src/calc.py": FEAT}, "feat")
        self.run_dir = self.pin(self.pin_args)

    def pin(self, args):
        rc, out, _ = cli("target.py", args, self.repo, self.tmp)
        self.assertEqual(rc, 0, out)
        run = json.loads(out)["run"]
        rc, out, _ = cli("ledger.py", ["build", "--run", run], self.repo, self.tmp)
        self.assertEqual(rc, 0, out)
        return run

    def rep(self, sub, stdin="", extra=()):
        rc, out, err = cli("report.py", [sub, "--run", self.run_dir] + list(extra), self.repo, self.tmp, stdin)
        self.assertEqual(err, "")
        return rc, out

    def ingest(self, *objs):
        rc, out = self.rep("ingest", lines(*objs))
        self.assertEqual(rc, 0, out)
        return json.loads(out)

    def verdicts(self, *objs):
        rc, out = self.rep("verdicts", lines(*objs))
        self.assertEqual(rc, 0, out)
        return json.loads(out)

    def doc(self):
        rc, out = self.rep("render", extra=["--format", "json"])
        self.assertEqual(rc, 0, out)
        return json.loads(out)


class Tracer(Base):
    def test_finder_line_to_json_document(self):
        out = self.ingest("prose from the finder, ignored", CAND, {"inspected": ["src/calc.py"]})
        self.assertEqual((out["status"], len(out["ids"])), ("ok", 1))
        fid = out["ids"][0]
        self.assertRegex(fid, "^" + report.ID_RE + "$")
        self.assertEqual(out["dropped"], {"invalid": 0, "do_not_flag": 0})
        hand = os.path.join(self.run_dir, "handoff", fid + ".md")
        self.assertEqual(out["handoff"], os.path.dirname(hand))
        with open(hand, encoding="utf-8") as fh:
            body = fh.read()
        self.assertRegex(body, r"(?s)<<<UNTRUSTED-([0-9a-f]+)\n.*add subtracts.*\n\1>>>")
        self.assertRegex(body, r"(?s)<<<UNTRUSTED-([0-9a-f]+)\n.*add\(2, 3\) returns -1.*\n\1>>>")
        for need in (fid, "src/calc.py:5"):
            self.assertIn(need, body)
        for banned in ("FIXTEXT-q1", "return a - b", "required", "category", "severity"):
            self.assertNotIn(banned, body)
        v = self.verdicts({"id": fid, "verdict": "confirmed", "evidence": [CITE], "reason": "r"})
        self.assertEqual((v["status"], v["confirmed"]), ("ok", [fid]))
        d = self.doc()
        self.assertEqual(d["schema_version"], 1)
        (f,) = d["findings"]
        self.assertEqual((f["id"], f["evidence_class"], f["score"], f["verified"], f["origin"]),
                         (fid, "traced", 75, True, "introduced"))
        self.assertEqual(d["inspected"]["areas"], ["src/calc.py"])
        self.assertEqual(d["unverifiable"], [])


if __name__ == "__main__":
    unittest.main()
