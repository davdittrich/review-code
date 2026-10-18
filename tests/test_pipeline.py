"""End-to-end tracer: target.py then ledger.py on a real branch fixture."""
import ast
import io
import json
import os
import re
import stat
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

CANARY = "CANARY-COMMIT-7d2"
TARGET_KEYS = {"status", "reason", "detail", "kind", "base_sha", "head_sha", "merge_base", "number",
               "run", "included_count", "excluded_count", "untracked_count", "candidates"}
LEDGER_KEYS = {"status", "reason", "run", "label", "spec_status", "counts"}
RLO = chr(0x202E)


def cli(script, args, cwd, tmp):
    e = gitfix.env()
    e["TMPDIR"] = tmp
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + args, cwd=cwd, env=e,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8")


class Fixture(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, {"src/cfg.py": "def load():\n    return 1\n", "CLAUDE.md": "# rules\n"}, "base")
        self.base = gitfix.run(["git", "rev-parse", "HEAD"], self.repo).strip()
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], self.repo)

    def git(self, *a):
        return gitfix.run(["git"] + list(a), self.repo).strip()

    def feature(self, files=None, message="feat: add parse_cfg\n\n" + CANARY):
        files = files or {"src/cfg.py": "def load():\n    return 1\n\ndef parse_cfg(s):\n    return s\n"}
        return gitfix.commit(self.repo, files, message)

    def pin(self, arg="main"):
        rc, out, _ = cli("target.py", [arg], self.repo, self.tmp)
        return rc, json.loads(out)

    def build(self, run, extra=()):
        rc, out, _ = cli("ledger.py", ["build", "--run", run] + list(extra), self.repo, self.tmp)
        return rc, out


class Tracer(Fixture):
    def test_tracer_branch_run(self):
        self.feature()
        refs = self.git("for-each-ref")
        rc, t = self.pin()
        self.assertEqual((rc, t["status"], t["kind"]), (0, "ok", "branch"))
        self.assertEqual(t["base_sha"], self.git("rev-parse", "main"))
        self.assertEqual(t["head_sha"], self.git("rev-parse", "HEAD"))
        self.assertEqual(t["merge_base"], self.git("merge-base", "main", "HEAD"))
        run = t["run"]
        self.assertTrue(os.path.basename(run).startswith("review-code."))
        # A3 failed: the run dir lives under the git dir, not under $TMPDIR
        gp = os.path.realpath(os.path.join(self.repo, self.git("rev-parse", "--git-path", "review-code")))
        self.assertEqual(os.path.dirname(os.path.realpath(run)), gp)
        self.assertEqual(stat.S_IMODE(os.stat(run).st_mode), 0o700)
        rc, _ = self.build(run)
        self.assertEqual(rc, 0)
        for name in ("target.json", "change.diff", "ledger.json", "inputs/spec.md", "inputs/defects.md",
                     "inputs/standards.md"):
            self.assertTrue(os.path.isfile(os.path.join(run, name)), name)
        with open(os.path.join(run, "conventions", "CLAUDE.md"), "rb") as fh:
            self.assertEqual(fh.read(), self.git("show", "main:CLAUDE.md").encode() + b"\n")
        hits = []
        for d, _, fs in os.walk(run):
            for f in fs:
                p = os.path.join(d, f)
                with open(p, "rb") as fh:
                    if CANARY.encode() in fh.read():
                        hits.append(os.path.relpath(p, run))
        self.assertEqual(hits, ["inputs/spec.md"])
        with open(os.path.join(run, "inputs", "spec.md"), encoding="utf-8") as fh:
            spec = fh.read()
        self.assertRegex(spec, r"(?s)<<<UNTRUSTED-([0-9a-f]+)\n.*" + CANARY + r".*\n\1>>>")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("for-each-ref"), refs)
        with open(os.path.join(run, "ledger.json"), encoding="utf-8") as fh:
            led = json.load(fh)
        self.assertEqual((led["label"], led["trust"], led["trust_asked"]), ("full", "unset", False))
        self.assertEqual(led["conventions"][0]["file"], "CLAUDE.md")
        self.assertFalse(led["conventions"][0]["differs_on_head"])

    def test_tracer_no_target_execution(self):
        self.feature()
        marker = os.path.join(self.tmp, "ran")
        evil = os.path.join(self.tmp, "evil.sh")
        with open(evil, "w") as fh:
            fh.write("#!/bin/sh\ntouch '%s'\ncat \"$@\" 2>/dev/null\n" % marker)
        os.chmod(evil, 0o755)
        hooks = os.path.join(self.tmp, "hooks")
        os.makedirs(hooks)
        for h in ("post-checkout", "post-merge", "pre-commit", "reference-transaction"):
            with open(os.path.join(hooks, h), "w") as fh:
                fh.write("#!/bin/sh\ntouch '%s'\n" % marker)
            os.chmod(os.path.join(hooks, h), 0o755)
        for k, v in (("diff.external", evil), ("core.fsmonitor", evil), ("core.hooksPath", hooks),
                     ("diff.evil.textconv", evil)):
            self.git("config", k, v)
        with open(os.path.join(self.repo, ".gitattributes"), "w") as fh:
            fh.write("*.py diff=evil\n")
        e = gitfix.env()
        e.update(TMPDIR=self.tmp, GIT_EXTERNAL_DIFF=evil)
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "target.py"), "main"], cwd=self.repo, env=e,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        run = json.loads(p.stdout)["run"]
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "ledger.py"), "build", "--run", run],
                           cwd=self.repo, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(os.path.exists(marker), "an external program ran")
        with open(os.path.join(run, "change.diff"), encoding="utf-8") as fh:
            self.assertIn("parse_cfg", fh.read())

    def test_tracer_hidden_char(self):
        name = "src/CANARY-NAME-5c1.py"
        self.feature({name: "x = 1\n# a" + RLO + "b\n"})
        with gitfix.isolated(self.tmp):
            buf = io.StringIO()
            import target
            with redirect_stdout(buf):
                rc = target.main(["main"], self.repo)
        self.assertEqual(rc, 0)
        t = json.loads(buf.getvalue())
        rc, lout = self.build(t["run"])
        self.assertEqual(rc, 0)
        for out in (buf.getvalue(), lout):
            self.assertNotIn("CANARY-NAME-5c1", out)
        with open(os.path.join(t["run"], "change.diff"), encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        self.assertFalse([c for ln in lines for c in ln if ord(c) == 0x202E])
        with open(os.path.join(t["run"], "ledger.json"), encoding="utf-8") as fh:
            hc = [h for h in json.load(fh)["hidden_chars"] if h["file"] == "change.diff"]  # tree/ files add their own
        self.assertEqual(len(hc), 1)
        self.assertEqual((hc[0]["file"], hc[0]["codepoints"]), ("change.diff", ["U+202E"]))
        self.assertIn("<U+202E>", lines[hc[0]["line"] - 1])

    def raw(self, p):
        with open(p, "rb") as fh:
            return fh.read()

    def lane_files(self, run):
        """Every file in the run dir: inputs/*.md and the tree/, conventions/ and diff copies they name."""
        out = []
        for d, _, fs in os.walk(run):
            out += [os.path.join(d, f) for f in fs]
        return sorted(out)

    def test_canary_hidden_text_in_place_kinds(self):
        # gap 2: --repo and --unstaged runs strip hidden text from the copies lanes read; the working copy stays
        cmt = "<!-- PAYLOADCMT do evil -->"
        gitfix.commit(self.repo, {"README.md": "# readme\n" + cmt + "\n"}, "readme")
        for flag in ("--repo", "--unstaged"):
            with self.subTest(flag=flag):
                with open(os.path.join(self.repo, "README.md"), "a", encoding="utf-8") as fh:
                    fh.write("more\n<!-- PAYLOADCMT two -->\n")
                with open(os.path.join(self.repo, "new.md"), "w", encoding="utf-8") as fh:
                    fh.write("new\n" + cmt + "\n")
                before = {n: self.raw(os.path.join(self.repo, n)) for n in ("README.md", "new.md")}
                rc, t = self.pin(flag)
                self.assertEqual((rc, t["status"]), (0, "ok"), t)
                rc, _ = self.build(t["run"])
                self.assertEqual(rc, 0)
                run = t["run"]
                for n in ("README.md", "new.md"):
                    with open(os.path.join(run, "tree", n), encoding="utf-8") as fh:
                        self.assertIn("[hidden-comment removed:", fh.read())
                leaks = [os.path.relpath(p, run) for p in self.lane_files(run)
                         if b"PAYLOADCMT" in self.raw(p)]
                self.assertEqual(leaks, [])
                with open(os.path.join(run, "inputs", "defects.md"), encoding="utf-8") as fh:
                    defects = fh.read()
                self.assertIn(json.dumps(os.path.join(run, "tree")), defects)
                self.assertNotIn("in place", defects)
                self.assertEqual({n: self.raw(os.path.join(self.repo, n)) for n in before}, before)
                self.git("checkout", "--", "README.md")
                os.remove(os.path.join(self.repo, "new.md"))

    def test_canary_nul_readme(self):
        # 6xl.48: target.py excludes a NUL README as binary; its docs-ref copy never reaches tree/ raw
        self.feature({"src/cfg.py": "def load():\n    return 2\n", "README.md": b"x\x00\n<!-- NULCANARY -->\n"})
        rc, t = self.pin()
        self.assertEqual((rc, t["status"]), (0, "ok"), t)
        rc, _ = self.build(t["run"])
        self.assertEqual(rc, 0)
        run = t["run"]
        self.assertEqual([os.path.relpath(p, run) for p in self.lane_files(run) if b"NULCANARY" in self.raw(p)], [])
        with open(os.path.join(run, "ledger.json"), encoding="utf-8") as fh:
            self.assertIn({"path": "README.md", "reason": "binary"}, json.load(fh)["tree"]["skipped"])

    def assertNoNamePayload(self, run):
        """No raw or losslessly encoded plane-14 payload in any run-dir file name or content."""
        for d, dirs, fs in os.walk(run):
            for n in dirs + fs:
                self.assertFalse(any(0xE0000 <= ord(c) <= 0xE0FFF for c in n), repr(n))
            for f in fs:
                data = self.raw(os.path.join(d, f))
                for enc in (b"\xf3\xa0", b"\\udb40", b"\\363\\240", b"\\U000e0", b"U+E00"):
                    self.assertNotIn(enc, data, (f, enc))

    def test_canary_tags_named_file(self):
        # 6xl.51: a Tags-named file and an E0080..E00FF-named file never spell their payload to a lane
        tags = "".join(chr(0xE0000 + ord(c)) for c in "PWNED")
        high = "".join(chr(0xE0080 + i) for i in range(6))
        evil, evil2 = "src/x" + tags + ".py", "notes" + high + ".md"
        self.feature({"src/cfg.py": "def load():\n    return 2\n", evil: "y = 1\n", evil2: "note\n<!-- HT_HIDNAME -->\n"})
        shown, shown2 = "src/x<TAGS:5>.py", "notes<TAGS:6>.md"
        for arg in ("main", "--repo"):
            with self.subTest(arg=arg):
                rc, t = self.pin(arg)
                self.assertEqual((rc, t["status"]), (0, "ok"), t)
                rc, _ = self.build(t["run"])
                self.assertEqual(rc, 0)
                run = t["run"]
                self.assertNoNamePayload(run)
                with open(os.path.join(run, "target.json"), encoding="utf-8") as fh:
                    tj = json.load(fh)
                self.assertIn(shown, tj["included"])
                self.assertIn({"file": shown, "name": True, "codepoints": ["TAGS:5"]}, tj["hidden_chars"])
                with open(os.path.join(run, "ledger.json"), encoding="utf-8") as fh:
                    led = json.load(fh)
                # 6xl.53: the content stays reviewable under a stand-in name, stripped like tree/
                for n, copy, text in ((shown2, "1.md", "note\n[hidden-comment removed: 19 chars]\n"),
                                      (shown, "2.py", "y = 1\n")):
                    self.assertIn({"path": n, "reason": "hidden-name", "copy": "hidden-names/" + copy},
                                  led["tree"]["skipped"])
                    with open(os.path.join(run, "hidden-names", copy), encoding="utf-8") as fh:
                        self.assertEqual(fh.read(), text)
                self.assertIn({"file": shown2, "line": 2, "kind": "comment", "length": 19}, led["hidden_text"])
                with open(os.path.join(run, "inputs", "defects.md"), encoding="utf-8") as fh:
                    defects = fh.read()
                self.assertIn('- "%s"' % shown, defects)
                self.assertIn('- "%s": hidden-name, copy %s' % (shown, json.dumps(os.path.join(run, "hidden-names", "2.py"))),
                              defects)
                self.assertEqual([os.path.relpath(p, run) for p in self.lane_files(run) if b"HT_HIDNAME" in self.raw(p)], [])
                if arg == "main":
                    with open(os.path.join(run, "change.diff"), encoding="utf-8") as fh:
                        diff = fh.read()
                    self.assertIn("diff --git a/%s b/%s" % (shown, shown), diff)
                    self.assertIn("+y = 1", diff)

    def test_escaped_name_collision_keeps_both_files(self):
        # 6xl.56: a file literally named a<U+200B>.md next to a\u200b.md: both contents stay reachable
        self.feature({"src/cfg.py": "def load():\n    return 2\n", "a\u200b.md": "HIDDENNAMED body\n",
                      "a<U+200B>.md": "LITERALNAMED body\n"})
        for arg in ("main", "--repo"):
            with self.subTest(arg=arg):
                rc, t = self.pin(arg)
                self.assertEqual((rc, t["status"]), (0, "ok"), t)
                rc, _ = self.build(t["run"])
                self.assertEqual(rc, 0)
                run = t["run"]
                with open(os.path.join(run, "ledger.json"), encoding="utf-8") as fh:
                    skipped = json.load(fh)["tree"]["skipped"]
                copies = [k["copy"] for k in skipped if k["path"] == "a<U+200B>.md"]
                self.assertEqual(len(set(copies)), 2, skipped)
                bodies = set()
                for c in copies:
                    with open(os.path.join(run, c), encoding="utf-8") as fh:
                        bodies.add(fh.read())
                self.assertEqual(bodies, {"HIDDENNAMED body\n", "LITERALNAMED body\n"})

    def test_hidden_named_link_and_submodule_name_no_copy(self):
        # 6xl.57: a skipped hidden-named symlink or submodule never lists a hidden-names/ copy
        os.symlink("src/cfg.py", os.path.join(self.repo, "lnk\u200b.py"))
        self.git("add", "lnk\u200b.py")
        self.git("update-index", "--add", "--cacheinfo", "160000,%s,sub\u200bmod" % self.base)
        self.feature({"src/cfg.py": "def load():\n    return 2\n"})
        rc, t = self.pin("main")
        self.assertEqual((rc, t["status"]), (0, "ok"), t)
        rc, _ = self.build(t["run"])
        self.assertEqual(rc, 0)
        run = t["run"]
        with open(os.path.join(run, "ledger.json"), encoding="utf-8") as fh:
            skipped = json.load(fh)["tree"]["skipped"]
        self.assertIn({"path": "lnk<U+200B>.py", "reason": "symlink"}, skipped)
        self.assertIn({"path": "sub<U+200B>mod", "reason": "submodule"}, skipped)
        for k in skipped:
            if "copy" in k:
                self.assertTrue(os.path.isfile(os.path.join(run, k["copy"])), k)
        with open(os.path.join(run, "inputs", "defects.md"), encoding="utf-8") as fh:
            self.assertNotIn("hidden-names", fh.read())

    def test_non_utf8_name_is_flagged_and_copied(self):
        # 6xl.60: undecodable name bytes are a hidden_chars name finding; the content stays reachable
        try:
            with open(os.fsencode(self.repo) + b"/caf\xe9.py", "wb") as fh:
                fh.write(b"latin_body = 1\n")
        except OSError:
            self.skipTest("file system refuses non-UTF-8 names")
        self.git("add", "-A")
        self.feature({"src/cfg.py": "def load():\n    return 2\n"})
        for arg in ("main", "--repo"):
            with self.subTest(arg=arg):
                rc, t = self.pin(arg)
                self.assertEqual((rc, t["status"]), (0, "ok"), t)
                rc, _ = self.build(t["run"])
                self.assertEqual(rc, 0)
                with open(os.path.join(t["run"], "ledger.json"), encoding="utf-8") as fh:
                    led = json.load(fh)
                self.assertIn({"file": "caf<BYTES:1>.py", "name": True, "codepoints": ["BYTES:1"]}, led["hidden_chars"])
                copy = [k["copy"] for k in led["tree"]["skipped"] if k["path"] == "caf<BYTES:1>.py"]
                with open(os.path.join(t["run"], copy[0]), encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), "latin_body = 1\n")

    def test_cr_only_comment_lines_stripped_everywhere(self):
        # 6xl.71: a lone CR ends a line, so a [//]: # comment between CR-only blank lines is hidden on GitHub
        self.feature({"src/cfg.py": "def load():\n    return 2\n", "notes.md": "a\r\r[//]: # (BRKCANARY)\r\rb\r",
                      "mix.md": "a\n\r[//]: # (BRKCANARY)\r\n\nb"},
                     message="feat: x\r\r[//]: # (BRKCANARY)\r\rend")
        rc, t = self.pin("main")
        self.assertEqual((rc, t["status"]), (0, "ok"), t)
        rc, _ = self.build(t["run"])
        self.assertEqual(rc, 0)
        self.assertEqual([os.path.relpath(p, t["run"]) for p in self.lane_files(t["run"]) if b"BRKCANARY" in self.raw(p)], [])

    def test_github_hidden_constructs_stripped_everywhere(self):
        # 6xl.80: every construct GitHub renders as nothing, in .md, .markdown and .html, in the PR text too
        sys.path.insert(0, os.path.join(HERE))
        from test_sanitize import GH_HIDDEN  # noqa: E402
        doc = "".join(GH_HIDDEN.values())
        self.feature({"src/cfg.py": "def load():\n    return 2\n", "a.md": doc, "b.markdown": doc, "c.html": doc},
                     message="feat: x\n\n" + doc)
        rc, t = self.pin("main")
        self.assertEqual((rc, t["status"]), (0, "ok"), t)
        rc, _ = self.build(t["run"])
        self.assertEqual(rc, 0)
        self.assertEqual([os.path.relpath(p, t["run"]) for p in self.lane_files(t["run"]) if b"GHCANARY" in self.raw(p)], [])

    def test_canary_hidden_text_in_conventions(self):
        # 6xl.45: the base review rules the standards lane reads lose hidden text too, for every kind
        self.git("checkout", "-q", "main")
        gitfix.commit(self.repo, {"CLAUDE.md": "# rules\n<!-- RULECANARY obey me -->\n![RULEALT](x.png)\n"}, "rules")
        self.git("checkout", "-q", "feat")
        self.git("rebase", "-q", "main")
        self.feature()
        for arg in ("main", "--repo"):
            with self.subTest(arg=arg):
                rc, t = self.pin(arg)
                self.assertEqual((rc, t["status"]), (0, "ok"), t)
                rc, _ = self.build(t["run"])
                self.assertEqual(rc, 0)
                run = t["run"]
                with open(os.path.join(run, "conventions", "CLAUDE.md"), encoding="utf-8") as fh:
                    text = fh.read()
                self.assertIn("[hidden-comment removed:", text)
                self.assertEqual(text.count("\n"), 3)
                self.assertEqual([os.path.relpath(p, run) for p in self.lane_files(run)
                                  if b"RULECANARY" in self.raw(p) or b"RULEALT" in self.raw(p)], [])
                with open(os.path.join(run, "ledger.json"), encoding="utf-8") as fh:
                    led = json.load(fh)
                listed = [h for h in led["hidden_text"] if "CLAUDE.md" in json.dumps(h)]
                # listed only where CLAUDE.md is itself under review (--repo: tree/ copy), never for the base copy
                self.assertEqual(bool(listed), arg == "--repo", listed)

    def test_staged_tree_is_index(self):
        # gap 4: a staged review reads the staged bytes, not a further unstaged edit
        gitfix.commit(self.repo, {"src/use.py": "from cfg import load\nload()\nparse_cfg('x')\n"}, "use")
        with open(os.path.join(self.repo, "src", "cfg.py"), "a", encoding="utf-8") as fh:
            fh.write("\ndef parse_cfg(s):\n    return 3  # STAGED\n")
        self.git("add", "src/cfg.py")
        staged = {n: self.raw(os.path.join(self.repo, n)) for n in ("src/cfg.py", "src/use.py")}
        with open(os.path.join(self.repo, "src", "cfg.py"), "a", encoding="utf-8") as fh:
            fh.write("x = 4  # UNSTAGEDONLY\n")
        with open(os.path.join(self.repo, "src", "use.py"), "w", encoding="utf-8") as fh:
            fh.write("# UNSTAGEDONLY\n# shift\n" + staged["src/use.py"].decode())
        rc, t = self.pin("--staged")
        self.assertEqual((rc, t["status"], t["kind"]), (0, "ok", "staged"), t)
        rc, _ = self.build(t["run"])
        self.assertEqual(rc, 0)
        run = t["run"]
        self.assertEqual(self.raw(os.path.join(run, "tree", "src", "cfg.py")), staged["src/cfg.py"])
        self.assertEqual(self.raw(os.path.join(run, "tree", "src", "use.py")), staged["src/use.py"])
        self.assertEqual([os.path.relpath(p, run) for p in self.lane_files(run) if b"UNSTAGEDONLY" in self.raw(p)], [])
        with open(os.path.join(run, "ledger.json"), encoding="utf-8") as fh:
            led = json.load(fh)
        self.assertEqual(led["tree"]["source"], "index")
        # callers come from the index, so their lines match tree/ (the working copy has it on line 5)
        self.assertIn({"symbol": "parse_cfg", "file": "src/use.py", "line": 3, "how": "git grep -w -F"}, led["callers"])

    def test_canary_hidden_text_in_changed_prose(self):
        # A4: neither the diff nor the tree copy of a changed .md carries the hidden payload to the main thread
        notes = "# notes\n<!-- HIDDENCMT -->\n![ALTINJECT](x.png)\nmentions parse_cfg\n"
        self.feature({"notes.md": notes, "src/cfg.py": "def load():\n    return 1\n\ndef parse_cfg(s):\n    return s  # <!-- CODECMT -->\n"})
        rc, t = self.pin()
        rc, out = self.build(t["run"])
        self.assertEqual(rc, 0)
        seen = {}
        for d, _, names in os.walk(t["run"]):
            for n in names:
                with open(os.path.join(d, n), encoding="utf-8", errors="replace") as fh:
                    seen[os.path.relpath(os.path.join(d, n), t["run"])] = fh.read()
        for rel, text in seen.items():
            self.assertNotIn("HIDDENCMT", text, rel)
            self.assertNotIn("ALTINJECT", text, rel)
        self.assertIn("CODECMT", seen["change.diff"])
        with open(os.path.join(t["run"], "ledger.json"), encoding="utf-8") as fh:
            led = json.load(fh)
        self.assertEqual(sorted((h["file"], h["kind"]) for h in led["hidden_text"]),
                         [("change.diff", "alt-text"), ("change.diff", "comment"), ("notes.md", "alt-text"), ("notes.md", "comment")])
        self.assertLessEqual(set(json.loads(out)), LEDGER_KEYS)

    def test_tracer_stdout_minimal(self):
        self.feature()
        rc, t = self.pin()
        self.assertLessEqual(set(t), TARGET_KEYS)
        rc, out = self.build(t["run"])
        self.assertLessEqual(set(json.loads(out)), LEDGER_KEYS)
        self.git("checkout", "-q", "main")  # clean tree on the default branch: nothing to infer
        cases = [(["-x"], "option-like-argument"), (["no-such-ref"], "bad-ref"), ([], "no-target")]
        for args, reason in cases:
            rc, out, _ = cli("target.py", args, self.repo, self.tmp)
            o = json.loads(out)
            self.assertEqual((rc, o["status"], o["reason"]), (1, "refuse", reason))
            self.assertLessEqual(set(o), TARGET_KEYS)
        before = os.listdir(os.path.dirname(t["run"]))
        rc, out, _ = cli("target.py", ["HEAD"], self.repo, self.tmp)
        o = json.loads(out)
        self.assertEqual((rc, o["status"], o["reason"]), (1, "skip", "empty-diff"))
        self.assertEqual(os.listdir(os.path.dirname(t["run"])), before)

    def test_tracer_py39_grammar(self):
        for f in ("target.py", "ledger.py", "sanitize.py"):
            with open(os.path.join(SCRIPTS, f), encoding="utf-8") as fh:
                ast.parse(fh.read(), filename=f, feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()


class HostileUserConfig(unittest.TestCase):
    """6xl.61/.62: run-dir output must not depend on the user's own git config or cwd. The hostile settings
    sit in a real global config file (GIT_CONFIG_GLOBAL), as a user's ~/.gitconfig would; keep this class."""

    KINDS = (["main"], ["main..feat"], ["HEAD^!"], [], ["--staged"], ["--unstaged"], ["--repo"], ["src"])
    CANARIES = (b"SUBJ_TWO_CANARY", b"EXTCANARY", b"TEXTCONVCANARY", b"\x1b[", b"FILTERCANARY")

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)
        sub = gitfix.make_repo(os.path.join(self.tmp, "subrepo"))
        gitfix.commit(sub, {"f": "1\n"}, "SUBJ_ONE")
        s1 = gitfix.run(["git", "rev-parse", "HEAD"], sub).strip()
        gitfix.commit(sub, {"f": "2\n"}, "SUBJ_TWO_CANARY")
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        gitfix.commit(self.repo, {"src/cfg.py": "def load_cfg():\n    return 1\n", "src/deep/d.py": "d = 1\n",
                                  "top.py": "t = 1\n", "src/use.py": "u = load_cfg()\n", "data.txt": "one\n", "docs/old.md": "".join(
                                      "line %d\n" % i for i in range(30)), "docs/old2.md": "".join(
                                      "row %d\n" % i for i in range(30)), "notes.md": "n\n"}, "base")
        self.git("-c", "protocol.file.allow=always", "submodule", "-q", "add", sub, "sm")
        self.git("-C", "sm", "checkout", "-q", s1)
        self.git("add", "sm")
        self.git("commit", "-q", "-m", "sub at s1")
        self.git("checkout", "-q", "-b", "feat")
        self.git("-C", "sm", "checkout", "-q", "main")
        self.git("add", "sm")
        self.git("mv", "docs/old.md", "docs/new.md")
        self.git("mv", "docs/old2.md", "docs/new2.md")
        gitfix.commit(self.repo, {"src/cfg.py": "def load_cfg():\n    return 2\n", "src/deep/d.py": "d = 2\n",
                                  "top.py": "t = 2\n", "data.txt": "two\n", "docs/new.md": "".join(
                                      "line %d\n" % i for i in range(30)).replace("line 20", "line 20 edited"),
                                  "notes.md": "n\n<!-- HTCMT -->\n"},
                      "feat: ok " + "".join(chr(0xE0000 + ord(c)) for c in "PAYLOADTAGS"))  # 6xl.81
        # dirty state for the in-place kinds
        with open(os.path.join(self.repo, "src", "cfg.py"), "a") as fh:
            fh.write("# staged\n")
        self.git("add", "src/cfg.py")
        with open(os.path.join(self.repo, "top.py"), "a") as fh:
            fh.write("# unstaged\n")
        with open(os.path.join(self.repo, "src", "deep", "new.py"), "w") as fh:
            fh.write("n = 1\n")
        bin_ = os.path.join(self.tmp, "bin")
        os.makedirs(bin_)
        for name, word in (("ext.sh", "EXTCANARY"), ("conv.sh", "TEXTCONVCANARY")):
            with open(os.path.join(bin_, name), "w") as fh:
                fh.write("#!/bin/sh\necho %s\n" % word)
            os.chmod(os.path.join(bin_, name), 0o755)
        with open(os.path.join(self.tmp, "attrs"), "w") as fh:
            fh.write("*.py diff=hostile\n*.txt -diff\n")
        with open(os.path.join(self.tmp, "order"), "w") as fh:
            fh.write("top.py\n*\n")
        self.hostile = os.path.join(self.tmp, "hostile.gitconfig")
        with open(self.hostile, "w") as fh:
            fh.write("[diff]\n\tsubmodule = log\n\trelative = true\n\tignoreSubmodules = all\n"
                     "\texternal = %s\n\tnoprefix = true\n\trenames = false\n\tmnemonicPrefix = true\n"
                     "\talgorithm = patience\n\tcontext = 0\n\tinterHunkContext = 10\n\tindentHeuristic = false\n"
                     "\tsuppressBlankEmpty = true\n\torderFile = %s\n\twordRegex = .\n\trenameLimit = 1\n"
                     "[diff \"hostile\"]\n\ttextconv = %s\n"
                     "[color]\n\tui = always\n\tdiff = always\n\tgrep = always\n"
                     "[core]\n\tquotePath = true\n\tattributesFile = %s\n\tpager = cat\n\tabbrev = 4\n"
                     "[grep]\n\tcolumn = true\n\tlineNumber = true\n\tpatternType = perl\n"
                     "[log]\n\tshowSignature = true\n[i18n]\n\tlogOutputEncoding = UTF-16\n"
                     "[user]\n\tname = U\n\temail = u@example.invalid\n"
                     "[filter \"evil\"]\n\tclean = %s\n\tsmudge = %s\n\trequired = true\n"
                     % (os.path.join(bin_, "ext.sh"), os.path.join(self.tmp, "order"),
                        os.path.join(bin_, "conv.sh"), os.path.join(self.tmp, "attrs"),
                        os.path.join(bin_, "filter.sh"), os.path.join(bin_, "filter.sh")))
        self.filter_ran = os.path.join(self.tmp, "filter-ran")
        with open(os.path.join(bin_, "filter.sh"), "w") as fh:  # 6xl.74: a clean filter must never run
            fh.write("#!/bin/sh\ntouch %s\necho FILTERCANARY\n" % self.filter_ran)
        os.chmod(os.path.join(bin_, "filter.sh"), 0o755)

    def git(self, *a):
        return gitfix.run(["git"] + list(a), self.repo).strip()

    def run_kind(self, words, cwd, hostile):
        e = gitfix.env()
        e["TMPDIR"] = self.tmp
        if hostile:  # 6xl.74: pathspec-mode variables must not change or break the review
            e.update(GIT_ICASE_PATHSPECS="1", GIT_GLOB_PATHSPECS="1")
        info = os.path.join(self.repo, ".git", "info", "attributes")
        if hostile:
            e["GIT_CONFIG_GLOBAL"] = self.hostile
            # 6xl.69: unversioned repo-local attributes are user state too; the exclusion rule is the merge base's
            os.makedirs(os.path.dirname(info), exist_ok=True)
            with open(info, "w") as fh:
                fh.write("* -diff filter=evil\n*.md linguist-generated\n*.py linguist-vendored\n")
            self.addCleanup(lambda: os.path.exists(info) and os.remove(info))
        try:
            return self._run_kind(words, cwd, e)
        finally:
            if os.path.exists(info):
                os.remove(info)

    def _run_kind(self, words, cwd, e):
        out = {}
        for script, args in (("target.py", words), ("ledger.py", None)):
            if args is None:
                args = ["build", "--run", out["target"]["run"]]
            p = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + args, cwd=cwd, env=e,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out[script[:-3]] = json.loads(p.stdout.decode("utf-8"))
            out[script[:-3] + "_raw"] = p.stdout
            if out[script[:-3]].get("status") != "ok":
                break
        return out

    def snapshot(self, run):
        def rd(rel):
            with open(os.path.join(run, rel), "rb") as fh:
                return fh.read()
        tj, led = json.loads(rd("target.json")), json.loads(rd("ledger.json"))
        spec = re.sub(rb"[0-9a-f]{16}>>>", b"nonce>>>", re.sub(rb"UNTRUSTED-[0-9a-f]+", b"UNTRUSTED-nonce", rd(
            os.path.join("inputs", "spec.md")))).replace(run.encode(), b"RUN")
        return {"diff": rd("change.diff"), "spec": spec,
                "target": {k: tj[k] for k in ("kind", "base_sha", "head_sha", "merge_base", "included", "excluded",
                                              "untracked", "hidden_chars", "hidden_text")},
                "ledger": {k: led[k] for k in ("symbols", "callers", "tests", "docs", "hidden_text", "hidden_chars")}}

    def leaks(self, run, raw):
        found = [(b, "stdout") for b in self.CANARIES if b in raw]
        for d, _, fs in os.walk(run):
            for f in fs:
                fp = os.path.join(d, f)
                if os.path.relpath(fp, run) == os.path.join("inputs", "spec.md"):
                    continue
                with open(fp, "rb") as fh:
                    data = fh.read()
                found += [(b, os.path.relpath(fp, run)) for b in self.CANARIES if b in data]
        return found

    def pairs(self, cwd):
        """(words, clean run, hostile run from cwd) for every target kind."""
        for words in self.KINDS:
            clean = self.run_kind(words, self.repo, False)
            self.assertEqual(clean["target"]["status"], "ok", (words, clean["target"]))
            here = [os.path.relpath(os.path.join(self.repo, "src"), cwd)] if words == ["src"] else words  # cwd-relative
            bad = self.run_kind(here, cwd, True)
            self.assertEqual(bad["target"]["status"], "ok", (words, os.path.relpath(cwd, self.repo), bad["target"]))
            self.assertEqual(bad["ledger"]["status"], "ok", (words, bad["ledger"]))
            yield words, clean, bad

    def test_changed_files_independent_of_user_config_and_cwd(self):
        # 6xl.62: diff.relative from a subdirectory and diff.ignoreSubmodules=all must not drop changed files
        for cwd in (self.repo, os.path.join(self.repo, "src", "deep")):
            for words, clean, bad in self.pairs(cwd):
                with self.subTest(words=words, cwd=os.path.relpath(cwd, self.repo)):
                    want = {k: v for k, v in self.snapshot(clean["target"]["run"])["target"].items()
                            if k in ("included", "excluded", "untracked")}
                    got = {k: v for k, v in self.snapshot(bad["target"]["run"])["target"].items()
                           if k in ("included", "excluded", "untracked")}
                    self.assertEqual(got, want)
                    if words == ["main"]:
                        self.assertIn("sm", got["included"])
                        self.assertIn("top.py", got["included"])
                        with open(os.path.join(bad["ledger"]["run"], "ledger.json"), encoding="utf-8") as fh:
                            self.assertIn("src/use.py", [e["file"] for e in json.load(fh)["callers"]])  # 6xl.73: grep -a

    def test_output_independent_of_user_config(self):
        # 6xl.61: diff.submodule=log, external diff, textconv, colour, prefixes, context and algorithm settings
        # never reach a run-dir file; the whole change.diff, target and ledger lists equal a clean-config run
        for cwd in (self.repo, os.path.join(self.repo, "src", "deep")):
            for words, clean, bad in self.pairs(cwd):
                with self.subTest(words=words, cwd=os.path.relpath(cwd, self.repo)):
                    run = bad["target"]["run"]
                    self.assertEqual(self.leaks(run, bad["target_raw"] + bad["ledger_raw"]), [])
                    self.assertEqual(self.snapshot(run), self.snapshot(clean["target"]["run"]))
                    self.assertFalse(os.path.exists(self.filter_ran), "a user clean filter ran")

    def test_submodule_log_subjects_never_leak(self):
        # 6xl.61: diff.submodule=log alone (no ignoreSubmodules masking it) would print submodule commit subjects
        with open(self.hostile, "w") as fh:
            fh.write("[diff]\n\tsubmodule = log\n[user]\n\tname = U\n\temail = u@example.invalid\n")
        for words in (["main"], ["HEAD^!"]):
            with self.subTest(words=words):
                bad = self.run_kind(words, self.repo, True)
                self.assertEqual(bad["target"]["status"], "ok", bad["target"])
                with open(os.path.join(bad["target"]["run"], "change.diff"), "rb") as fh:
                    self.assertIn(b"Subproject commit", fh.read())
                self.assertEqual(self.leaks(bad["target"]["run"], bad["target_raw"]), [])
