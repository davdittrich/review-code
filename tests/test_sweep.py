"""gh#86: a confirmed finding's quoted code is searched in the pinned tree; hits outside the diff ride under it and change no verdict."""
import unittest

import paths  # noqa: F401

import report  # noqa: E402
from test_report import BASE2, FEAT2, Q_ADD, Q_MUL, Base, cand, verdict  # noqa: E402

OTHER = "def sub(a, b):\n    return a - b\n" + "".join("\ndef s%d(a, b):\n    return a - b\n" % i for i in range(8))
# an added copy of the finding's line, inside the diff: never a same-class hit
FEAT = FEAT2 + "\ndef sub2(a, b):\n    return a - b\n"


class Sweep(Base):
    base_files, feat_files = dict(BASE2, **{"src/other.py": OTHER}), {"src/calc.py": FEAT}

    def verdict_of(self, kind="confirmed"):
        self.ingest(cand(Q_ADD, 8), cand(Q_MUL, 11), {"inspected": ["src/calc.py"]})
        self.verdicts(verdict((Q_ADD, 8), kind), verdict((Q_MUL, 11)))
        return self.doc()

    def test_hits_outside_the_diff_ride_under_the_finding_capped_and_labelled(self):
        d = self.verdict_of()
        add = next(f for f in d["findings"] if f["quote"] == Q_ADD)
        hits = add["same_class"]
        self.assertEqual(len(hits), report.SWEEP_CAP)
        self.assertTrue(all(h["path"] == "src/other.py" and h["text"].strip() == Q_ADD for h in hits), hits)
        self.assertEqual(add["same_class_omitted"], 9 - report.SWEEP_CAP)  # 9 copies in other.py; the added copy in calc.py is in the diff
        mul = next(f for f in d["findings"] if f["quote"] == Q_MUL)
        self.assertNotIn("same_class", mul)  # its line occurs nowhere else
        rc, out = self.rep("render")
        self.assertIn("same class elsewhere (outside the diff): src/other.py:", out)

    def test_the_sweep_changes_no_verdict_or_count(self):
        d = self.verdict_of()
        self.assertEqual((len(d["findings"]), d["verdict"], d["unverifiable"]), (2, "Needs discussion", []))

    def test_an_unverifiable_item_is_not_swept(self):
        d = self.verdict_of("unverifiable")
        (u,) = d["unverifiable"]
        self.assertNotIn("same_class", u)


if __name__ == "__main__":
    unittest.main()
