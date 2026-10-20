"""Batched verification (review-code-buj): run lock, verify batches, verdicts --batch, status, not-ingested."""
import json
import os
import subprocess
import sys
import unittest

import paths  # noqa: F401

import gitfix  # noqa: E402
import report  # noqa: E402
import test_report as tr  # noqa: E402


class Locking(tr.Lanes):
    def spawn(self, lane, text):
        e = gitfix.env()
        e["TMPDIR"] = self.tmp
        p = subprocess.Popen([sys.executable, os.path.join(tr.SCRIPTS, "report.py"), "ingest", "--run", self.run_dir,
                              "--lane", lane], cwd=self.repo, env=e, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        p.stdin.write(text.encode())
        p.stdin.close()
        return p

    def test_two_processes_ingesting_different_lanes_keep_both(self):
        for rnd in range(6):  # the race is a window, so repeat it
            a = self.spawn("defects", tr.lines(tr.mc(2), {"inspected": ["src/calc.py"]}))
            b = self.spawn("standards", tr.lines(tr.mc(4, "lean"), {"inspected": ["src/calc.py"]}))
            outs = [(p.wait(), p.stdout.read().decode(), p.stderr.read().decode()) for p in (a, b)]
            for p in (a, b):
                p.stdout.close()
                p.stderr.close()
            self.assertEqual([o[0] for o in outs], [0, 0], (rnd, outs))
            with open(os.path.join(self.run_dir, "candidates.json"), encoding="utf-8") as fh:
                self.assertEqual(sorted(json.load(fh)["lanes"]), ["defects", "standards"], rnd)
            self.assertFalse([n for n in os.listdir(self.run_dir) if ".tmp" in n])


BIG = "def f(a):\n" + "".join("    v%d = a - %d\n    pass\n    pass\n" % (i, i) for i in range(1, 61))
OTHER = "def g(a):\n" + "".join("    w%d = a + %d\n" % (i, i) for i in range(1, 4))


def big(n):
    """Candidate n (1..60) of src/big.py: three lines apart, so none merge."""
    return dict(tr.CAND, path="src/big.py", line=3 * n - 1, quote="v%d = a - %d" % (n, n), title="t%d" % n)


class Batches(tr.Base):
    base_files = {"src/big.py": "def f(a):\n    return a\n"}
    feat_files = {"src/big.py": BIG, "src/other.py": OTHER}

    def setUp(self):
        super().setUp()
        self.set_cap(60)

    def set_cap(self, n):
        with open(os.path.join(self.run_dir, "plan.json"), "w") as fh:
            json.dump({"caps": {"max_verifier_calls": n}}, fh)

    def calls(self):
        with open(os.path.join(self.run_dir, "candidates.json"), encoding="utf-8") as fh:
            return json.load(fh)["verify_calls"]

    def test_sixty_ids_on_one_path_make_twelve_calls_of_five(self):
        out = self.ingest(*[big(n) for n in range(1, 61)])
        calls = self.calls()
        self.assertEqual([len(c["ids"]) for c in calls], [5] * 12)
        self.assertEqual([c["batch"] for c in calls], list(range(1, 13)))
        self.assertEqual([i for c in calls for i in c["ids"]], out["ids"])  # queue order kept
        hand = out["handoff"]
        for c in calls:
            self.assertEqual(c["agent"], "review-code:verifier")
            self.assertEqual(c["prompt"], "Verify. Handoffs:\n" + "\n".join(
                os.path.join(hand, i + ".md") for i in c["ids"]))
            self.assertTrue(all(os.path.isfile(os.path.join(hand, i + ".md")) for i in c["ids"]))

    def test_batches_never_mix_paths_and_the_cap_still_cuts_ids(self):
        other = dict(tr.CAND, path="src/other.py", line=2, quote="w1 = a + 1")
        out = self.ingest(big(1), big(2), other, big(3))
        self.assertEqual(sorted(len(c["ids"]) for c in self.calls()), [1, 3])
        self.set_cap(2)
        out = self.ingest(big(1), big(2), other, big(3))
        self.assertEqual((len(out["ids"]), out["capped"], sum(len(c["ids"]) for c in self.calls())), (2, 2, 2))

    def test_spec_ids_use_the_spec_verifier(self):
        calls = report._batches([dict(id="F-a", axis="defects", path="p", shard=None),
                                 dict(id="F-b", axis="spec", path="p", shard=None),
                                 dict(id="F-c", axis="defects", path="p", shard=2)], "/h")
        self.assertEqual([(c["agent"], c["ids"]) for c in calls], [
            ("review-code:verifier", ["F-a"]), ("review-code:spec-verifier", ["F-b"]), ("review-code:verifier", ["F-c"])])


if __name__ == "__main__":
    unittest.main()
