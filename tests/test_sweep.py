"""gh#86: a confirmed finding's quoted code is searched in the pinned tree; hits outside the diff ride under it and change no verdict."""
import unittest

import paths  # noqa: F401

import report  # noqa: E402
from test_report import BASE2, FEAT2, Q_ADD, Q_MUL, Base, cand, verdict, write_plan  # noqa: E402

LONG = "    return a - b  # " + "x" * 250
OTHER = "def sub(a, b):\n" + LONG + "\n" + "".join("\ndef s%d(a, b):\n    return a - b\n" % i for i in range(8))
# an added copy of the finding's line, inside the diff: never a same-class hit
FEAT = FEAT2 + "\ndef sub2(a, b):\n    return a - b\n"


class Sweep(Base):
    base_files, feat_files = dict(BASE2, **{"src/other.py": OTHER}), {"src/calc.py": FEAT}

    def verdict_of(self, kind="confirmed", **plan):
        write_plan(self.run_dir, **plan)
        self.ingest(cand(Q_ADD, 8), cand(Q_MUL, 11), {"inspected": ["src/calc.py"]})
        self.verdicts(verdict((Q_ADD, 8), kind), verdict((Q_MUL, 11)))
        return self.doc()

    def test_hits_outside_the_diff_ride_under_the_finding_capped_and_labelled(self):
        d = self.verdict_of(sweep=True)
        add = next(f for f in d["findings"] if f["quote"] == Q_ADD)
        hits = add["same_class"]
        self.assertEqual(len(hits), report.SWEEP_CAP)
        self.assertTrue(all(h["path"] == "src/other.py" and h["text"].strip().startswith(Q_ADD) for h in hits), hits)
        self.assertEqual(add["same_class_omitted"], 9 - report.SWEEP_CAP)  # 9 copies in other.py; the added copy in calc.py is in the diff
        mul = next(f for f in d["findings"] if f["quote"] == Q_MUL)
        self.assertNotIn("same_class", mul)  # its line occurs nowhere else
        rc, out = self.rep("render")
        self.assertIn("same class elsewhere (outside the diff): src/other.py:", out)

    def test_hit_text_longer_than_the_limit_is_cut(self):  # review-code-cp5i.44
        d = self.verdict_of(sweep=True)
        add = next(f for f in d["findings"] if f["quote"] == Q_ADD)
        self.assertTrue(add["same_class"], add)
        self.assertEqual(max(len(h["text"]) for h in add["same_class"]), report.SWEEP_TEXT)

    def test_the_sweep_is_off_unless_the_plan_asks(self):  # review-code-cp5i.29
        d = self.verdict_of()
        self.assertFalse(any("same_class" in f or "same_class_omitted" in f for f in d["findings"]))
        rc, out = self.rep("render")
        self.assertNotIn("same class elsewhere", out)

    def test_sweep_false_is_off_too(self):
        d = self.verdict_of(sweep=False)
        self.assertFalse(any("same_class" in f for f in d["findings"]))

    def test_the_sweep_changes_no_verdict_or_count(self):
        d = self.verdict_of(sweep=True)
        self.assertEqual((len(d["findings"]), d["verdict"], d["unverifiable"]), (2, "Needs discussion", []))

    def test_an_unverifiable_item_is_not_swept(self):
        d = self.verdict_of("unverifiable", sweep=True)
        (u,) = d["unverifiable"]
        self.assertNotIn("same_class", u)


class GrepHits(unittest.TestCase):
    def test_parses_nul_output_and_stops_at_a_torn_record(self):  # review-code-cp5i.25
        raw = b"T:a.py\x003\x00x = 1\nT:b/c.py\x0012\x00y\nT:torn\x00"
        self.assertEqual(report._grep_hits(raw, 2), [("a.py", 3, "x = 1"), ("b/c.py", 12, "y")])
        self.assertEqual(report._grep_hits(b"", 2), [])

    def test_hit_text_is_cut_at_the_named_length(self):  # review-code-cp5i.27
        self.assertEqual(report.SWEEP_TEXT, 200)


if __name__ == "__main__":
    unittest.main()
