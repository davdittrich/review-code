"""--nit cap: human-written normative sources win the 40 extra slots over agent-written .okf files."""
import unittest

from test_ledger import Base


class NitPriority(Base):
    def files(self):
        okf = {".okf/c%02d.md" % i: "okf %d\n" % i for i in range(45)}
        return {"src/a.py": "pass\n", "GLOSSARY.md": "g\n", ".github/pull_request_template.md": "t\n",
                "docs/adr/0001.md": "a\n", "docs/adr/0002.md": "a\n", "docs/adr/0003.md": "a\n", **okf}

    def test_nit_cap_drops_only_the_path_last_okf_files(self):
        self.base(self.files())
        self.branch({"src/a.py": "x = 1\n"})
        _, led = self.build(nit=True)
        kept = {e["file"] for e in led["conventions"]}
        for f in ("GLOSSARY.md", ".github/pull_request_template.md", "docs/adr/0001.md", "docs/adr/0002.md",
                  "docs/adr/0003.md"):
            self.assertIn(f, kept)
        self.assertEqual(len(kept), 40)
        dropped = sorted(e["file"] for e in led["ignored"])
        self.assertEqual(dropped, [".okf/c%02d.md" % i for i in range(35, 45)])
        self.assertTrue(all(e["reason"] == "nit-cap" for e in led["ignored"]))

    def test_without_nit_nothing_changes(self):
        self.base(self.files())
        self.branch({"src/a.py": "x = 1\n"})
        _, led = self.build()
        self.assertEqual(led["conventions"], [])
        self.assertEqual(led["ignored"], [])


if __name__ == "__main__":
    unittest.main()
