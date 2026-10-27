"""scripts/check-package.sh must fail on the defects it claims to catch (mutation probes on a copied tree)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check-package.sh")


GATE_TIMEOUT = 120  # seconds; the real gate takes about 1 s


@unittest.skipUnless(all(shutil.which(t) for t in ("git", "jq", "claude", "tar")), "package gate tools missing")
class GateMutations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # the gate runs on the committed HEAD, not the working tree: an uncommitted edit shows only after it is committed
        cls._base = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._base.cleanup)
        cls.pristine = os.path.join(cls._base.name, "tree")
        os.mkdir(cls.pristine)
        tar = subprocess.run(["git", "-C", ROOT, "archive", "--format=tar", "HEAD"], stdout=subprocess.PIPE, check=True)
        subprocess.run(["tar", "-x", "-C", cls.pristine], input=tar.stdout, check=True)
        # The mutation probes assert agent and hygiene FAIL ids, which no `claude` call decides. A stub that answers the
        # real calls' success shape (validate, add and install exit 0; details lists the skill) skips four node starts
        # per probe. The clean-tree test runs the real binary, and so does check-package.sh in CI.
        cls.stub = os.path.join(cls._base.name, "stub")
        os.mkdir(cls.stub)
        with open(os.path.join(cls.stub, "claude"), "w", encoding="utf-8") as fh:
            fh.write('#!/bin/sh\n[ "$2" = details ] && echo "  Skills (1)  review-code"\nexit 0\n')
        os.chmod(os.path.join(cls.stub, "claude"), 0o755)

    def setUp(self):  # the tests only read the pristine tree; each mutates its own copy
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tree = os.path.join(self._td.name, "tree")
        shutil.copytree(self.pristine, self.tree, symlinks=True)

    def gate(self, real=False):
        # the real run skips claude's background network calls (2.4 s down to 1.0 s wall, same four commands and exit codes)
        env = dict(os.environ, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1") if real else \
            dict(os.environ, PATH=self.stub + os.pathsep + os.environ["PATH"])
        try:  # the real run calls the claude CLI: a stalled CLI must fail this test, not hang the suite
            p = subprocess.run(["sh", SCRIPT, self.tree], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=GATE_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.fail("the package gate did not finish in %d s" % GATE_TIMEOUT)
        return p.returncode, p.stdout.decode("utf-8")

    def test_a_hung_gate_fails_the_test_instead_of_hanging_the_suite(self):  # review-code-1b4g
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sh", 1)) as run:
            with self.assertRaises(AssertionError):
                self.gate(real=True)
        self.assertIsNotNone(run.call_args.kwargs.get("timeout"))

    def test_gate_passes_on_clean_tree(self):
        self.tree = self.pristine  # read-only here: no copy
        rc, out = self.gate(real=True)
        self.assertEqual(rc, 0, out)

    def test_stubbed_gate_passes_on_clean_tree(self):  # the probes below mean something only if the stub alone fails nothing
        rc, out = self.gate()
        self.assertEqual(rc, 0, out)
        self.assertIn("0 FAIL", out)

    def test_gate_checks_every_agent_file(self):
        # WR-07: an added lane with Bash must not ship unchecked
        with open(os.path.join(self.tree, "agents", "extra.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: extra\ndescription: x\ntools: Read, Bash\n---\nbody\n")
        rc, out = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL - agent-extra-tools", out)

    def mutate_agent(self, name, old, new):
        path = os.path.join(self.tree, "agents", name + ".md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(old, new, 1))

    def test_gate_rejects_filesystem_tool_on_sota(self):  # LANE-05
        self.mutate_agent("sota", "tools: WebSearch, WebFetch", "tools: WebSearch, WebFetch, Read")
        rc, out = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL - agent-sota-tools", out)

    def test_gate_rejects_unlisted_mcp_tool_on_finder(self):  # review-code-7u1: named read-only MCP tools only
        self.mutate_agent("finder", "mcp__serena__get_symbols_overview", "mcp__serena__replace_content")
        rc, out = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL - agent-finder-tools", out)

    def test_gate_rejects_web_tool_on_standards(self):
        self.mutate_agent("standards", "mcp__codegraph__codegraph_explore\n", "mcp__codegraph__codegraph_explore, WebFetch\n")
        rc, out = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL - agent-standards-tools", out)

    def test_gate_requires_all_six_agents(self):
        os.remove(os.path.join(self.tree, "agents", "spec-verifier.md"))
        rc, out = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL - agent-spec-verifier-tools", out)

    def test_gate_rejects_write_on_fixer(self):  # FIX-04: the fixer edits, it never creates files
        self.mutate_agent("fixer", "tools: Read, Edit, Grep, Glob", "tools: Read, Edit, Grep, Glob, Write")
        rc, out = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL - agent-fixer-tools", out)

    def test_gate_rejects_bash_on_fixer(self):  # FIX-04: no shell for the only write-capable agent
        self.mutate_agent("fixer", "tools: Read, Edit, Grep, Glob", "tools: Read, Edit, Grep, Glob, Bash")
        rc, out = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL - agent-fixer-tools", out)

    def test_gate_requires_fixer(self):
        os.remove(os.path.join(self.tree, "agents", "fixer.md"))
        rc, out = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL - agent-fixer-tools", out)

    def test_gate_rejects_edit_on_finder(self):  # the lanes stay read-only; the fixer alone holds Edit
        self.mutate_agent("finder", "mcp__codegraph__codegraph_explore\n", "mcp__codegraph__codegraph_explore, Edit\n")
        rc, out = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL - agent-finder-tools", out)

    def test_gate_rejects_beads_database_files(self):  # review-code-9kt
        rels = (".dolt/noms/x", "sub/.dolt/x", "state.db", "a/.beads-credential-key", ".beads-credential-key")
        for rel in rels:  # all at once: the gate lists every banned file it finds, so one run proves each pattern
            path = os.path.join(self.tree, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("x\n")
        rc, out = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL - hygiene-tracked", out)
        hits = next(l for l in out.splitlines() if l.startswith("FAIL - hygiene-tracked")).split()
        for rel in rels:
            self.assertIn(rel, hits, rel)

    def test_gate_fails_when_listing_fails(self):
        # WR-06: a failing `find` must reach `bad hygiene-tracked`, not print ok
        fake = os.path.join(self._td.name, "bin")
        os.mkdir(fake)
        with open(os.path.join(fake, "find"), "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 1\n")
        os.chmod(os.path.join(fake, "find"), 0o755)
        p = subprocess.run(["sh", SCRIPT, self.tree], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env=dict(os.environ, PATH=fake + os.pathsep + self.stub + os.pathsep + os.environ["PATH"]))
        out = p.stdout.decode("utf-8")
        self.assertEqual(p.returncode, 1, out)
        self.assertNotIn("ok - hygiene-tracked", out)
        self.assertIn("FAIL - hygiene-tracked", out)


class OptimizedImport(unittest.TestCase):
    def test_modules_import_under_oo(self):  # review-code-cp5i.36: -OO strips docstrings, so __doc__ is None
        scripts = os.path.join(ROOT, "skills", "review-code", "scripts")
        for mod in ("ledger", "plan"):
            with self.subTest(mod=mod):
                r = subprocess.run([sys.executable, "-OO", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import " + mod, scripts],
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
