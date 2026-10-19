"""Domain packs and the smell baseline: format, categories and ccr-contract coverage (LANE-06, LANE-02)."""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REFS = os.path.join(ROOT, "skills", "review-code", "references")
sys.path.insert(0, os.path.join(ROOT, "skills", "review-code", "scripts"))

import report  # noqa: E402

PACK_NAMES = ("security-owasp", "concurrency", "api-compat", "data-migrations", "llm-generated-code",
              "python", "r", "js-ts", "sql")
OWN_AXES = set(report.LANE_CATEGORIES["defects"]) | set(report.LANE_CATEGORIES["standards"])
BANNED = re.compile(r"slop|lazy|lazi", re.IGNORECASE)  # D-14: internal pejoratives never ship
BULLET_CAT = re.compile(r"\(`([a-z-]+)`\)\s*$")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def pack_path(name):
    return os.path.join(REFS, "packs", name + ".md")


def sections(text):
    """{heading: [lines]} for the `## ` sections, in file order."""
    out, cur = {}, None
    for line in text.splitlines()[1:]:
        if line.startswith("## "):
            cur = out.setdefault(line[3:].strip(), [])
        elif cur is not None:
            cur.append(line)
    return out


def bullets(lines):
    return [x for x in lines if x.startswith("- ")]


class Structure(unittest.TestCase):
    def packs(self):
        return [n for n in PACK_NAMES if os.path.exists(pack_path(n))]

    def test_pack_format(self):
        for name in self.packs():
            with self.subTest(pack=name):
                text = read(pack_path(name))
                lines = text.splitlines()
                self.assertEqual(lines[0], "# Pack: " + name)
                self.assertLessEqual(len(lines), 60)
                secs = sections(text)
                self.assertEqual(list(secs)[-3:], ["Checks", "Do not flag", "Sources"])
                self.assertGreaterEqual(len(bullets(secs["Do not flag"])), 2)
                self.assertGreaterEqual(len(bullets(secs["Sources"])), 1)
                self.assertGreaterEqual(len(bullets(secs["Checks"])), 2)

    def test_check_categories_are_on_a_lane_axis(self):
        for name in self.packs():
            for b in bullets(sections(read(pack_path(name)))["Checks"]):
                with self.subTest(pack=name, check=b[:50]):
                    m = BULLET_CAT.search(b)
                    self.assertIsNotNone(m, "check must end with its category in backticks")
                    self.assertIn(m.group(1), OWN_AXES)

    def test_smell_baseline(self):
        text = read(os.path.join(REFS, "smells.md"))
        self.assertEqual(text.splitlines()[0], "# Code smells")
        smells = bullets(sections(text)["Smells"])
        self.assertEqual(len(smells), 12)
        for b in smells:
            self.assertRegex(b, r"\(`smell`\)\s*$")
            self.assertIn("->", b)  # what it is -> how to fix it
        low = text.lower()
        for phrase in ("judgement call", "repo standard", "overrides", "tooling", "never a blocking"):
            self.assertIn(phrase, low)

    # documentation check: pins wording, not behaviour (WR-03)
    def test_no_internal_pejoratives(self):
        for path in [pack_path(n) for n in self.packs()] + [os.path.join(REFS, "smells.md")]:
            with self.subTest(path=os.path.basename(path)):
                self.assertIsNone(BANNED.search(read(path)))


class ContractCoverage(unittest.TestCase):
    """Every ccr-contract row that routes to a named pack has that pack's file (wording is a reviewer step)."""
    ROUTES = {"python pack": "python", "r pack": "r", "js/ts pack": "js-ts", "sql pack": "sql",
              "security pack": "security-owasp", "llm-generated-code pack": "llm-generated-code"}

    def routed(self):
        out = {}
        for line in read(os.path.join(ROOT, "docs", "ccr-contract.md")).splitlines():
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            if not line.startswith("| L") or len(cols) != 6:
                continue
            for key, stem in self.ROUTES.items():
                if key in cols[4].lower():
                    out.setdefault(stem, []).append(cols[0])
        return out

    def test_every_routed_pack_exists(self):
        routed = self.routed()
        self.assertEqual(set(routed), set(self.ROUTES.values()))  # parser found a row for each routed pack
        for stem, rows in routed.items():
            with self.subTest(pack=stem, rows=rows):
                self.assertTrue(os.path.exists(pack_path(stem)))

    def test_all_nine_packs_exist(self):
        packs_dir = os.path.join(REFS, "packs")
        self.assertEqual(sorted(f[:-3] for f in os.listdir(packs_dir) if f.endswith(".md")), sorted(PACK_NAMES))


if __name__ == "__main__":
    unittest.main()
