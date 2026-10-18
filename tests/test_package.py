"""scripts/check-package.sh must fail on the defects it claims to catch (mutation probes on a copied tree)."""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check-package.sh")


@unittest.skipUnless(all(shutil.which(t) for t in ("git", "jq", "claude", "tar")), "package gate tools missing")
class GateMutations(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tree = os.path.join(self._td.name, "tree")
        os.mkdir(self.tree)
        tar = subprocess.run(["git", "-C", ROOT, "archive", "--format=tar", "HEAD"], stdout=subprocess.PIPE, check=True)
        subprocess.run(["tar", "-x", "-C", self.tree], input=tar.stdout, check=True)

    def gate(self):
        p = subprocess.run(["sh", SCRIPT, self.tree], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return p.returncode, p.stdout.decode("utf-8")

    def test_gate_passes_on_clean_tree(self):
        rc, out = self.gate()
        self.assertEqual(rc, 0, out)

    def test_gate_checks_every_agent_file(self):
        # WR-07: an added lane with Bash must not ship unchecked
        with open(os.path.join(self.tree, "agents", "extra.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: extra\ndescription: x\ntools: Read, Bash\n---\nbody\n")
        rc, out = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL - agent-extra-tools", out)

    def test_gate_fails_when_listing_fails(self):
        # WR-06: a failing `find` must reach `bad hygiene-tracked`, not print ok
        fake = os.path.join(self._td.name, "bin")
        os.mkdir(fake)
        with open(os.path.join(fake, "find"), "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 1\n")
        os.chmod(os.path.join(fake, "find"), 0o755)
        p = subprocess.run(["sh", SCRIPT, self.tree], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env=dict(os.environ, PATH=fake + os.pathsep + os.environ["PATH"]))
        out = p.stdout.decode("utf-8")
        self.assertNotIn("ok - hygiene-tracked", out)
        self.assertIn("FAIL - hygiene-tracked", out)


if __name__ == "__main__":
    unittest.main()
