"""ledger.py: context discovery, base-only conventions, head-tree materialization."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "review-code", "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import gitfix  # noqa: E402
import ledger  # noqa: E402

DIFF = ["--unified=3", "--no-ext-diff", "--no-textconv", "--no-color"]
INPLACE = ("staged", "unstaged", "worktree", "path", "repo")


def write_target(run, repo, kind, base_sha, head_sha, merge_base, included, untracked=()):
    """Write target.json and change.diff for a run dir straight from git, without running target.py."""
    os.makedirs(run, exist_ok=True)
    if kind in INPLACE:
        diff = gitfix.run(["git", "diff"] + DIFF + [head_sha, "--"], repo)
    else:
        diff = gitfix.run(["git", "diff"] + DIFF + [merge_base, head_sha, "--"], repo)
    with open(os.path.join(run, "change.diff"), "w", encoding="utf-8") as fh:
        fh.write(diff)
    rec = {"version": 1, "kind": kind, "repo": repo, "run": run, "arg": "", "number": None, "url": None,
           "base_sha": base_sha, "head_sha": head_sha, "merge_base": merge_base, "dirty": kind in INPLACE,
           "diff_cmd": ["git", "diff"] if kind not in ("path", "repo") else None,
           "commits": [] if kind in INPLACE else [head_sha], "included": list(included), "excluded": [],
           "untracked": list(untracked), "hidden_chars": [], "notes": [], "created": "2026-01-01T00:00:00Z"}
    with open(os.path.join(run, "target.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, sort_keys=True, indent=2)
    return rec


class Base(unittest.TestCase):
    BASE = {"README.md": "root readme\n"}

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)
        cm = gitfix.isolated(self.tmp)
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        self.run_dir = os.path.join(self.tmp, "run")

    def g(self, *a):
        return gitfix.run(["git"] + list(a), self.repo).strip()

    def base(self, files):
        return gitfix.commit(self.repo, files, "base")

    def branch(self, files):
        self.g("checkout", "-q", "-b", "feat")
        return gitfix.commit(self.repo, files, "feat")

    def names(self, mb, head):
        out = gitfix.run(["git", "diff", "--name-only", "-z", mb, head], self.repo)
        return [n for n in out.split("\0") if n]

    def build(self, kind="branch", included=None, untracked=(), head=None, mb=None):
        head = head or self.g("rev-parse", "HEAD")
        mb = mb or self.g("merge-base", "main", head)
        if included is None:
            included = self.names(mb, head)
        write_target(self.run_dir, self.repo, kind, mb, head, mb, included, untracked)
        out = ledger.build(self.run_dir)
        return out, self.load()

    def load(self):
        with open(os.path.join(self.run_dir, "ledger.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def read(self, rel):
        with open(os.path.join(self.run_dir, rel), encoding="utf-8") as fh:
            return fh.read()


def files_of(led, cat):
    return [e["file"] for e in led[cat]]


class Categories(Base):
    def test_ledger_full_categories(self):
        body = "".join("    x = %d\n" % i for i in range(12))
        self.base({
            "src/cfg.py": "def parse_cfg(s):\n" + body,
            "src/use.py": "from cfg import parse_cfg\nparse_cfg('x')\n",
            "tests/test_cfg.py": "from cfg import parse_cfg\n",
            "tests/fixtures/cfg.json": "{}\n",
            "pyproject.toml": "[project]\n",
            ".github/workflows/ci.yml": "on: push\n",
            "README.md": "docs for cfg.py\n",
            "docs/cfg.md": "call parse_cfg to read it\n",
            "docs/other.md": "unrelated\n",
        })
        self.branch({"src/cfg.py": "def parse_cfg(s):\n" + body.replace("x = 11", "x = 99")})
        out, led = self.build()
        self.assertEqual(led["label"], "full")
        self.assertEqual(out["label"], "full")
        self.assertEqual([(e["name"], e["file"]) for e in led["symbols"]], [("parse_cfg", "src/cfg.py")])
        self.assertEqual([(e["symbol"], e["file"], e["line"]) for e in led["callers"]],
                         [("parse_cfg", "src/use.py", 1), ("parse_cfg", "src/use.py", 2)])
        self.assertEqual(files_of(led, "tests"), ["tests/test_cfg.py"])
        self.assertEqual(files_of(led, "fixtures"), ["tests/fixtures/cfg.json"])
        self.assertEqual(files_of(led, "config"), ["pyproject.toml"])
        self.assertEqual(files_of(led, "ci"), [".github/workflows/ci.yml"])
        self.assertEqual(files_of(led, "docs"), ["README.md", "docs/cfg.md"])
        for cat in ("symbols", "callers", "tests", "fixtures", "config", "ci", "docs"):
            for e in led[cat]:
                self.assertTrue(e["how"], (cat, e))
        self.assertEqual(led["capped"], {c: False for c in
                                         ("symbols", "callers", "tests", "fixtures", "config", "ci", "docs")})

    def test_ledger_empty_is_triage(self):
        self.base({"notes.txt": "one\n"})
        self.branch({"notes.txt": "one\ntwo\n"})
        out, led = self.build()
        for cat in ("symbols", "callers", "tests", "fixtures", "config", "ci", "docs", "conventions", "ignored"):
            self.assertEqual(led[cat], [], cat)
        self.assertEqual(led["label"], "triage")
        self.assertEqual(out["status"], "ok")

    def test_ledger_empty_cli_exits_zero(self):
        self.base({"notes.txt": "one\n"})
        self.branch({"notes.txt": "one\ntwo\n"})
        head = self.g("rev-parse", "HEAD")
        mb = self.g("merge-base", "main", head)
        write_target(self.run_dir, self.repo, "branch", mb, head, mb, ["notes.txt"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ledger.main(["build", "--run", self.run_dir])
        self.assertEqual((rc, json.loads(buf.getvalue())["label"]), (0, "triage"))

    def test_ledger_caps(self):
        self.base({"README.md": "x\n"})
        self.branch({"src/many.py": "".join("def fn_%02d():\n    pass\n" % i for i in range(41))})
        _, led = self.build()
        self.assertEqual([e["name"] for e in led["symbols"]], ["fn_%02d" % i for i in range(40)])
        self.assertTrue(led["capped"]["symbols"])

    def test_ledger_caps_callers(self):
        self.base({"src/callers.py": "".join("target_fn()  # %d\n" % i for i in range(21))})
        self.branch({"src/lib.py": "def target_fn():\n    pass\n"})
        _, led = self.build()
        self.assertEqual(len(led["callers"]), 20)
        self.assertTrue(led["capped"]["callers"])
        self.assertEqual([e["line"] for e in led["callers"]], list(range(1, 21)))

    def test_ledger_caps_exactly_forty(self):
        self.base({"README.md": "x\n"})
        self.branch({"src/many.py": "".join("def fn_%02d():\n    pass\n" % i for i in range(40))})
        _, led = self.build()
        self.assertEqual(len(led["symbols"]), 40)
        self.assertFalse(led["capped"]["symbols"])

    def test_ledger_caps_other_categories(self):
        base = {"tests/test_cfg_%02d.py" % i: "x\n" for i in range(41)}
        self.base(base)
        self.branch({"src/cfg.py": "pass\n"})
        _, led = self.build()
        self.assertEqual(len(led["tests"]), 40)
        self.assertTrue(led["capped"]["tests"])

    def test_ledger_symbol_filters(self):
        self.base({"src/a.py": "pass\n"})
        self.branch({"src/a.py": "pass\ndef run():\n    pass\ndef get():\n    pass\ndef abc():\n    pass\n"
                                 "def real_name():\n    pass\nexport function jsName() {}\n"
                                 "pub fn rust_fn() {}\nclass Widget:\n    pass\n"})
        _, led = self.build()
        self.assertEqual([e["name"] for e in led["symbols"]], ["real_name", "jsName", "rust_fn", "Widget"])

    def test_encoding_odd_paths(self):
        newline_name = "dir/a\nb.txt"
        latin_name = "dir/caf\udce9.txt"  # byte 0xE9 on disk, read back with surrogateescape
        self.base({"README.md": "x\n"})
        self.g("checkout", "-q", "-b", "feat")
        gitfix.commit(self.repo, {newline_name: "one\n"}, "odd newline")
        try:
            with open(os.fsencode(self.repo) + b"/dir/caf\xe9.txt", "wb") as fh:
                fh.write(b"two\n")
        except OSError:
            latin_name = None
        if latin_name:
            gitfix.run(["git", "add", "-A"], self.repo)
            gitfix.run(["git", "commit", "-q", "-m", "latin"], self.repo)
        head = self.g("rev-parse", "HEAD")
        mb = self.g("merge-base", "main", head)
        raw = subprocess.run(["git", "diff", "--name-only", "-z", mb, head], cwd=self.repo,
                             stdout=subprocess.PIPE, env=gitfix.env(), check=True).stdout
        # target.py decodes names with replacement; this fixture decodes losslessly to test the ledger alone
        included = [n.decode("utf-8", "surrogateescape") for n in raw.split(b"\0") if n]
        write_target(self.run_dir, self.repo, "branch", mb, head, mb, included)
        ledger.build(self.run_dir)
        self.load()
        defects = self.read("inputs/defects.md")
        wanted = [newline_name] + ([latin_name] if latin_name else [])
        for name in wanted:
            self.assertIn(name, included)
            self.assertIn(json.dumps(name), defects)
        self.assertNotIn(newline_name, defects)

    def test_ledger_atomic_write(self):
        self.base({"notes.txt": "one\n"})
        self.branch({"notes.txt": "one\ntwo\n"})
        self.build()
        first = sorted(os.listdir(self.run_dir))
        ledger.build(self.run_dir)
        self.assertEqual(sorted(os.listdir(self.run_dir)), first)
        self.assertEqual(self.load()["version"], 1)
        self.assertFalse([n for n in os.listdir(self.run_dir) if n.startswith(".ledger.")])


if __name__ == "__main__":
    unittest.main()
