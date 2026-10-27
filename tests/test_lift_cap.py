"""review-code-cp5i.30 .43: a --strict lift keeps the candidate's severity but never past the cap a traced finding of its category gets."""
import json
import unittest

import paths  # noqa: F401

from gitfix import lines  # noqa: E402
from test_report import BASE2, FEAT2, Q_MUL, Base, cand, verdict, write_plan  # noqa: E402
from test_strict import RE  # noqa: E402


class LiftCap(Base):
    base_files, feat_files = BASE2, {"src/calc.py": FEAT2}

    def lifted(self, category, severity, lane):
        write_plan(self.run_dir, strict=True)
        rc, out = self.rep("ingest", lines(cand(Q_MUL, 11, severity=severity, category=category), {"inspected": ["src/calc.py"]}),
                           extra=["--lane", lane])
        self.assertEqual(rc, 0, out)
        (m,) = json.loads(out)["ids"]
        self.verdicts(dict(verdict((Q_MUL, 11), "unverifiable", reason=RE), id=m))
        return self.doc()

    def test_a_lifted_smell_stays_a_suggestion_and_leaves_the_verdict_clean(self):
        d = self.lifted("smell", "blocking", "standards")
        (f,) = d["findings"]
        self.assertEqual((f["severity"], d["verdict"]), ("suggestion", "No blocking findings"))

    def test_a_lifted_bug_at_blocking_reports_required(self):  # review-code-cp5i.43
        d = self.lifted("bug", "blocking", "defects")
        (f,) = d["findings"]
        self.assertEqual((f["severity"], d["verdict"]), ("required", "Needs discussion"))

    def test_a_lifted_security_finding_stays_blocking(self):
        d = self.lifted("security", "blocking", "defects")
        (f,) = d["findings"]
        self.assertEqual((f["severity"], d["verdict"]), ("blocking", "Needs changes"))


if __name__ == "__main__":
    unittest.main()
