"""Standards lane: how two conflicting conventions/ copies are resolved (prompt text is the product)."""
import unittest

import test_lanes as L


class Precedence(L.Standards):  # inherits the Standards tests: frontmatter, example line ingests, no forbidden words
    def para(self):
        i = self.text.find("Conflicting rules")
        self.assertGreaterEqual(i, 0, "no Conflicting rules paragraph")
        return self.text[i:self.text.index("\n2. `comment`", i)]

    def test_default_order(self):
        p = self.para()
        idx = [p.index(w) for w in ("CONTRIBUTING", "docs/adr", "CLAUDE.md")]
        self.assertEqual(idx, sorted(idx))

    def test_declared_order_beats_default(self):
        p = self.para().lower()
        self.assertIn("declare", p)
        self.assertIn("default", p)
        self.assertRegex(p, r"declared[^.]*(beats|over|wins|first)|(beats|over|wins|first)[^.]*declared")

    def test_trigger_names_the_lower_file(self):
        p = self.para()
        self.assertIn("`trigger`", p)
        self.assertIn("lower", p)

    def test_declaration_must_sit_in_a_listed_copy(self):
        self.assertIn("listed", self.para())

    def test_lean(self):
        self.assertLessEqual(len(self.para().splitlines()), 8)


if __name__ == "__main__":
    unittest.main()
