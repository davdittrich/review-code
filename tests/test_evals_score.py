"""evals/run.py: scoring, the baseline gate, fixture checks and rescoring. Free: never invokes claude."""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(ROOT, "evals", "run.py")
spec = importlib.util.spec_from_file_location("evals_run", RUN)
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

CASE = {
    "prompt": "p", "base_files": {}, "change_files": {"a.py": "x\n" * 40},
    "defects": [{"id": "d1", "path": "a.py", "line": 10, "end_line": 12, "lane": "defects", "categories": ["bug"]},
                {"id": "d2", "path": "a.py", "line": 30, "lane": "standards"}],
    "decoys": [{"path": "a.py", "line": 20}], "accept": [{"path": "a.py", "line": 35}],
}


def f(line, sev="required", cat="bug", path="a.py", end=None):
    d = {"path": path, "line": line, "severity": sev, "category": cat}
    if end:
        d["end_line"] = end
    return d


def doc(*fs):
    return {"schema_version": 1, "findings": list(fs)}


class Score(unittest.TestCase):
    def test_tolerance_boundary(self):
        self.assertTrue(ev.score(doc(f(8)), CASE)["hit"]["d1"])
        self.assertTrue(ev.score(doc(f(14)), CASE)["hit"]["d1"])
        s = ev.score(doc(f(7)), CASE)
        self.assertFalse(s["hit"]["d1"])
        self.assertEqual(s["fp_other"], 1)
        self.assertFalse(ev.score(doc(f(15)), CASE)["hit"]["d1"])

    def test_range_overlap(self):
        self.assertTrue(ev.score(doc(f(1, end=8)), CASE)["hit"]["d1"])

    def test_category_must_match(self):
        s = ev.score(doc(f(10, cat="security")), CASE)
        self.assertFalse(s["hit"]["d1"])
        self.assertEqual(s["fp"], 1)
        self.assertTrue(ev.score(doc(f(30, cat="anything")), CASE)["hit"]["d2"])  # no categories listed

    def test_duplicates_collapse(self):
        s = ev.score(doc(f(10), f(11), f(12)), CASE)
        self.assertEqual((s["tp"], s["fp"]), (1, 0))

    def test_decoy_and_unseeded_fp(self):
        s = ev.score(doc(f(20), f(39)), CASE)
        self.assertEqual((s["fp_decoy"], s["fp_other"], s["tp"]), (1, 1, 0))
        self.assertEqual(ev.score(doc(f(10, path="b.py")), CASE)["fp"], 1)

    def test_accept_neutral(self):
        s = ev.score(doc(f(35)), CASE)
        self.assertEqual((s["tp"], s["fp"]), (0, 0))

    def test_nit_and_noted_ignored(self):
        s = ev.score(doc(f(10, sev="nit"), f(39, sev="noted")), CASE)
        self.assertEqual((s["tp"], s["fp"]), (0, 0))

    def test_empty_doc_and_error_run(self):
        s = ev.score(doc(), CASE)
        self.assertEqual((s["tp"], s["fp"]), (0, 0))
        self.assertIsNone(ev.score(None, CASE))
        self.assertIsNone(ev.score({"verdict": "x"}, CASE))

    def test_lane_filter_makes_other_lanes_neutral(self):
        s = ev.score(doc(f(10), f(30, cat="convention")), CASE, lane="standards")
        self.assertEqual(list(s["hit"]), ["d2"])
        self.assertEqual((s["tp"], s["fp"]), (1, 0))

    def test_also_location(self):
        case = dict(CASE, defects=[{"id": "d", "path": "a.py", "line": 5, "lane": "defects", "also": [{"path": "a.py", "line": 25}]}])
        self.assertTrue(ev.score(doc(f(26)), case)["hit"]["d"])

    def test_no_defect_case(self):
        case = dict(CASE, defects=[], decoys=[], accept=[])
        agg = ev.aggregate([ev.score(doc(), case), ev.score(doc(f(3)), case)])
        self.assertIsNone(agg["recall"])
        self.assertEqual(ev.gate(agg, case, None)[0], "RED")
        agg = ev.aggregate([ev.score(doc(), case)] * 2)
        self.assertEqual(ev.gate(agg, case, None)[0], "GREEN")

    def test_aggregate_pools_and_flags_errors(self):
        a = ev.aggregate([ev.score(doc(f(10), f(39)), CASE), None, ev.score(doc(f(30)), CASE)])
        self.assertEqual((a["n_runs"], a["errors"], a["hits"], a["n"]), (2, 1, 2, 4))
        self.assertEqual(a["defects"], {"d1": [1, 2], "d2": [1, 2]})
        self.assertAlmostEqual(a["precision"], 2 / 3)
        self.assertEqual(ev.gate(ev.aggregate([None]), CASE, None)[0], "ERROR")
        self.assertIsNone(ev.aggregate([ev.score(doc(), CASE)])["precision"])


class Gate(unittest.TestCase):
    def test_worked_numbers(self):
        # p0 = (7+1)/(8+2... ) -> choose hits_base/n_base giving p0 = 0.8: hits 3, n 3 -> 4/5
        p0 = (3 + 1) / (3 + 2)
        self.assertAlmostEqual(p0, 0.8)
        p = [ev.gate_p(h, 3, 3, 3) for h in (0, 1, 2, 3)]
        self.assertAlmostEqual(p[0], 0.008)
        self.assertAlmostEqual(p[1], 0.104)
        self.assertAlmostEqual(p[2], 0.488)
        self.assertAlmostEqual(p[3], 1.0)
        self.assertEqual([ev.status_of(x) for x in p], ["RED", "AMBER", "GREEN", "GREEN"])

    def test_gate_against_baseline(self):
        case = dict(CASE, defects=[CASE["defects"][0]], decoys=[], accept=[])
        base = {"hits": 3, "n": 3, "defects": {"d1": [3, 3]}}
        red = ev.aggregate([ev.score(doc(), case)] * 3)
        self.assertEqual(ev.gate(red, case, base)[0], "RED")
        self.assertEqual(ev.gate(red, case, None)[0], "NEW")

    def test_cheap_never_above_amber(self):
        base = {"hits": 3, "n": 6, "defects": {"d1": [3, 3], "d2": [0, 3]}}
        agg = ev.aggregate([ev.score(doc(f(30)), CASE, "standards")])
        st, p = ev.gate(agg, CASE, base, "standards")
        self.assertEqual(st, "AMBER")
        self.assertIsNotNone(p)


class Cases(unittest.TestCase):
    def test_five_cases_valid_and_lint_clean(self):
        cases = ev.load_cases()
        self.assertEqual(set(cases), {"defects-py-seeded", "claim-walk-pr", "seam-deferral", "enum-fix-stale-test", "clean-diff-negative"})
        for n, c in cases.items():
            self.assertEqual(ev.check_case(c), [], n)
            self.assertEqual(ev.lint_case(n, c), [], n)
        self.assertEqual(cases["clean-diff-negative"]["defects"], [])

    def test_lint_cli(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ev.main(["lint"]), 0)
        self.assertIn("0 problem", out.getvalue())

    def test_lint_rejects_leak_and_bad_schema(self):
        c = json.loads(json.dumps(CASE))
        c["change_files"]["a.py"] = "x\n" * 8 + "# seeded bug\n" + "x\n" * 31
        self.assertTrue(any("leaking" in e for e in ev.lint_case("ok-name", c)))
        c = json.loads(json.dumps(CASE))
        c["defects"][0]["line"] = 99
        self.assertTrue(ev.check_case(c))
        c = json.loads(json.dumps(CASE))
        c["defects"][1]["lane"] = "nope"
        self.assertTrue(ev.check_case(c))
        self.assertTrue(ev.check_case(dict(CASE, extra=1)))


class Driver(unittest.TestCase):
    def test_import_has_no_side_effect_and_baseline_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = "import importlib.util as u; s = u.spec_from_file_location('r', %r); m = u.module_from_spec(s); s.loader.exec_module(m)" % RUN
            p = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual((p.returncode, p.stdout, p.stderr, os.listdir(tmp)), (0, b"", b"", []))
        with open(os.path.join(ROOT, "evals", "baseline.json"), encoding="utf-8") as fh:
            self.assertIn("version", json.load(fh))

    def test_extract_doc(self):
        text = 'noise {"a": 1} ```json\n' + json.dumps(doc(f(10))) + '\n``` tail'
        self.assertEqual(len(ev.extract_doc(text)["findings"]), 1)
        self.assertIsNone(ev.extract_doc("no json here"))

    def test_rescore_canned_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = ev.load_cases("defects-py-seeded")["defects-py-seeded"]
            d = os.path.join(tmp, "defects-py-seeded")
            os.makedirs(d)
            hit = {"path": "app/pager.py", "line": 3, "severity": "required", "category": "bug"}
            for i, text in enumerate(["```json\n" + json.dumps(doc(hit)) + "\n```", "garbled"]):
                with open(os.path.join(d, "run%d.json" % i), "w") as fh:
                    json.dump({"text": text}, fh)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = ev.main(["--rescore", tmp, "--case", "defects-py-seeded"])
            self.assertEqual(rc, 0, out.getvalue())
            self.assertIn("recall 1/3", out.getvalue())
            self.assertIn("errors=1", out.getvalue())
            self.assertIn("slice-off-by-one=1/1", out.getvalue())
            self.assertEqual(ev.score(ev.extract_doc("garbled"), case), None)

    def test_noninteractive_needs_yes(self):
        if shutil.which("claude") is None:
            self.skipTest("claude absent: the earlier guard answers first")
        # stdin of the test runner is not a tty; the guard must stop before any paid call
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc = ev.main(["--case", "clean-diff-negative", "--runs", "1"]) if not os.isatty(0) else 2
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
