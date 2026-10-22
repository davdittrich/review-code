"""Spec lane claim walk: the prompt says how factual claims in the spec are traced, and a claim finding ingests."""
import json
import unittest

import paths  # noqa: F401

import test_report as tr  # noqa: E402  (module import: its test classes are not collected twice)
from test_lanes import first, parts  # noqa: E402
from test_report import lines  # noqa: E402


class ClaimWalkPrompt(unittest.TestCase):
    def setUp(self):
        self.text = "\n".join(parts("spec")[1]).lower()

    def test_claim_categories(self):
        for w in ("claim walk", "count", "limit", "always", "never", "only", "handled by", "no behaviour change"):
            self.assertIn(w, self.text)

    def test_contradicted_claim_is_spec_wrong_naming_claim_and_code(self):
        self.assertRegex(self.text, r"contradict[^\n]*`spec-wrong`|`spec-wrong`[^\n]*contradict")
        self.assertIn("trigger", self.text)

    def test_true_claim_produces_nothing(self):
        self.assertRegex(self.text, r"true[^\n]*(produces|reports) nothing|(produces|reports) nothing[^\n]*true")
        self.assertIn("tests pass", self.text)

    def test_unverifiable_claim_is_declined(self):
        self.assertRegex(self.text, r"cannot be (checked|verified)[^\n]*declined")

    def test_existing_rules_kept(self):
        for s in ("never copy spec text", "paraphrase", "quote is copied verbatim", "data, never instructions"):
            self.assertIn(s, self.text)


class ClaimWalkVerifier(unittest.TestCase):
    def setUp(self):
        self.text = "\n".join(parts("spec-verifier")[1]).lower()
        self.confirmed = " ".join(self.text.split("- `confirmed`", 1)[1].split("- `refuted`", 1)[0].split())

    def test_contradicted_factual_claim_is_confirmed_without_a_request(self):
        self.assertRegex(self.confirmed, r"claim-walk finding.*needs no request")
        self.assertRegex(self.confirmed, r"contradicts it")

    def test_requirement_rule_is_not_loosened(self):
        self.assertIn("only when the spec text asks for the stated thing", self.confirmed)
        self.assertIn("never loosens the rule for requirement findings", self.confirmed)


class ClaimWalkIngest(tr.Base):
    pin = tr.SpecAxis.pin
    spec_rows = tr.SpecAxis.spec_rows
    spec_text = tr.SpecAxis.spec_text

    def test_spec_wrong_example_ingests_on_a_spec_run(self):
        rows, inner, _, _ = self.spec_rows()
        ex = first(parts("spec")[1], '[["path"')
        self.assertEqual(ex["category"], "spec-wrong")
        ex["spec_line"] = inner
        rc, out = self.rep("ingest", lines(ex, {"inspected": ["src/calc.py"]}), extra=["--lane", "spec"])
        self.assertEqual(rc, 0, out)
        out = json.loads(out)
        self.assertEqual((len(out["spec_ids"]), out["dropped"]["invalid"], out["rejected"]), (1, 0, []))


if __name__ == "__main__":
    unittest.main()
