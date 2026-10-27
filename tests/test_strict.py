"""--strict (gh#82): a verifier-re-derived inferred concern is a finding that counts; same-class sweep (gh#86)."""
import json
import os
import unittest

import paths  # noqa: F401

import report  # noqa: E402
from gitfix import fid  # noqa: E402
from test_report import BASE2, FEAT2, Q_ADD, Q_MUL, Q_NEG, Base, cand, verdict, write_plan  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RE = "re-derived: mul reads b before the caller sets it"


class StrictBase(Base):
    base_files, feat_files = BASE2, {"src/calc.py": FEAT2}

    def run_strict(self, reason=RE, kind="unverifiable", **plan):
        """ADD: traced suggestion. MUL: a blocking security concern the verifier could read but not trace end to end."""
        write_plan(self.run_dir, **plan)
        self.ingest(cand(Q_ADD, 8, severity="suggestion"), cand(Q_MUL, 11, severity="blocking", category="security"),
                    {"inspected": ["src/calc.py"]})
        m = fid("src/calc.py", "security", Q_MUL)
        self.verdicts(verdict((Q_ADD, 8)), dict(verdict((Q_MUL, 11), kind, reason=reason), id=m))
        return self.doc(), m


class Strict(StrictBase):
    def test_default_keeps_the_inferred_concern_unverifiable(self):
        d, m = self.run_strict()
        self.assertEqual([i["id"] for i in d["unverifiable"]], [m])
        self.assertEqual((len(d["findings"]), d["verdict"]), (1, "Needs discussion"))
        self.assertNotIn("strict", d)
        self.assertNotIn("inferred", d)

    def test_strict_reports_it_as_a_finding_and_the_verdict_follows_its_severity(self):
        d, m = self.run_strict(strict=True)
        got = {i["id"]: i for i in d["findings"]}
        self.assertEqual((got[m]["evidence_class"], got[m]["severity"], got[m]["verified"]), ("inferred", "blocking", True))
        self.assertEqual((d["unverifiable"], d["verdict"], d["strict"], d["inferred"]), ([], "Needs changes", True, 1))
        rc, out = self.rep("render")
        self.assertIn("strict: 1 inferred", out)
        rc, out = self.rep("render", extra=["--format", "sarif"])
        (r,) = [x for x in json.loads(out)["runs"][0]["results"] if x["partialFingerprints"]["reviewCodeId/v1"] == m]
        self.assertEqual((r["level"], r["properties"]["evidenceClass"]), ("error", "inferred"))

    def test_a_suggestion_level_inferred_finding_does_not_change_the_verdict(self):
        write_plan(self.run_dir, strict=True)
        self.ingest(cand(Q_MUL, 11, severity="suggestion"), {"inspected": ["src/calc.py"]})
        self.verdicts(verdict((Q_MUL, 11), "unverifiable", reason=RE))
        d = self.doc()
        self.assertEqual((len(d["findings"]), d["verdict"], d["inferred"]), (1, "No blocking findings", 1))

    def test_a_partial_cite_drop_does_not_stop_the_lift(self):  # review-code-cp5i.11: the lift is set where it is decided
        write_plan(self.run_dir, strict=True)
        self.ingest(cand(Q_MUL, 11, severity="blocking", category="security"), {"inspected": ["src/calc.py"]})
        m = fid("src/calc.py", "security", Q_MUL)
        self.verdicts(dict(verdict((Q_MUL, 11), "unverifiable", reason=RE), id=m))
        path = os.path.join(self.run_dir, "verdicts.json")
        with open(path, encoding="utf-8") as fh:
            v = json.load(fh)
        v[m]["cites_dropped"] = 1
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(v, fh)
        d = self.doc()
        (f,) = d["findings"]
        self.assertEqual((f["id"], f["evidence_class"], f["severity"], d["inferred"]), (m, "inferred", "blocking", 1))
        self.assertTrue(f["reason"].startswith("cites-dropped-wire-token; " + report.STRICT_PREFIX), f["reason"])

    def handoff(self, **plan):
        write_plan(self.run_dir, **plan)
        out = self.ingest(cand(Q_MUL, 11, severity="blocking", category="security"), {"inspected": ["src/calc.py"]})
        with open(os.path.join(out["handoff"], out["ids"][0] + ".md"), encoding="utf-8") as fh:
            return fh.read()

    def test_the_verify_handoff_tells_the_verifier_the_prefix_only_under_strict(self):  # review-code-cp5i.7
        self.assertIn("answer unverifiable with the cite and start the reason with " + report.STRICT_PREFIX, self.handoff(strict=True))
        self.assertNotIn(report.STRICT_PREFIX, self.handoff())

    def test_serial_handoff_has_no_strict_line(self):  # review-code-cp5i.35
        self.assertNotIn(report.STRICT_PREFIX, self.handoff(strict=True, mode="serial"))
        self.assertIn(report.STRICT_PREFIX, self.handoff(strict=True))

    def test_the_verifier_agents_are_told_what_a_strict_line_means(self):
        for name in ("verifier.md", "spec-verifier.md"):
            with open(os.path.join(ROOT, "agents", name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("`strict:`", text, name)
            self.assertIn(report.STRICT_PREFIX, text, name)

    def test_a_verdict_that_did_not_re_derive_the_trigger_stays_unverifiable(self):
        d, m = self.run_strict(reason="could not trace it", strict=True)
        self.assertEqual(([i["id"] for i in d["unverifiable"]], d["inferred"], d["verdict"]), ([m], 0, "Needs discussion"))

    def test_a_refuted_concern_stays_unverifiable_under_strict(self):  # review-code-cp5i.6: gh#82 spec, not dropped
        d, m = self.run_strict(kind="refuted", reason="the guard on line 9 stops it", strict=True)
        self.assertEqual(([i["id"] for i in d["unverifiable"]], d["dropped"]["refuted"], d["inferred"]), ([m], 0, 0))
        self.assertTrue(d["unverifiable"][0]["reason"].startswith("refuted: "), d["unverifiable"][0]["reason"])
        self.assertEqual(d["verdict"], "Needs discussion")  # a blocking Defects concern left open is a question, not a pass

    def test_a_refuted_concern_is_still_dropped_without_strict(self):
        d, m = self.run_strict(kind="refuted")
        self.assertEqual((m in [i["id"] for i in d["findings"] + d["unverifiable"]], d["dropped"]["refuted"]), (False, 1))

    def test_a_refuted_serial_concern_is_still_dropped_under_strict(self):
        d, m = self.run_strict(kind="refuted", mode="serial", strict=True)
        self.assertEqual((m in [i["id"] for i in d["findings"] + d["unverifiable"]], d["dropped"]["refuted"]), (False, 1))

    def test_serial_mode_warns_and_counts_nothing_extra(self):
        d, m = self.run_strict(mode="serial", strict=True)
        self.assertEqual((d["findings"], [i["id"] for i in d["unverifiable"]].count(m)), ([], 1))
        self.assertNotIn("inferred", d)
        self.assertTrue(any("--strict" in g for g in d["lost_guarantees"]), d["lost_guarantees"])

    def test_nit_does_not_change_the_verdict_under_strict(self):
        write_plan(self.run_dir, strict=True, nit=True)
        self.ingest(cand(Q_ADD, 8, severity="suggestion"), cand(Q_MUL, 11, severity="suggestion"),
                    cand(Q_NEG, 14, severity="nit"), {"inspected": ["src/calc.py"]})
        self.verdicts(verdict((Q_ADD, 8)), verdict((Q_MUL, 11), "unverifiable", reason=RE), dict(verdict((Q_NEG, 14)), id=report._fid("defects", "src/calc.py", "bug", Q_NEG, "nit")))
        d = self.doc()
        self.assertEqual((d["verdict"], d["nit"], d["inferred"]), ("No blocking findings", 1, 1))


if __name__ == "__main__":
    unittest.main()
