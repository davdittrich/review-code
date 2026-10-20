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


class BigRun(tr.Base):
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


class Batches(BigRun):
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


class Verdicts(BigRun):
    def setUp(self):
        super().setUp()
        self.ids = self.ingest(*[big(n) for n in range(1, 8)])["ids"]  # batch 1: five ids, batch 2: two
        self.by = {i: n for n in range(1, 8) for i in [tr.fid("src/big.py", "bug", "v%d = a - %d" % (n, n))]}
        self.first, self.second = (c["ids"] for c in self.calls())

    def line(self, i, kind="confirmed", cite=True):
        n = self.by[i]
        ev = [{"path": "src/big.py", "line": 3 * n - 1, "quote": "v%d = a - %d" % (n, n)}] if cite else []
        return {"id": i, "verdict": kind, "evidence": ev, "reason": "r"}

    def batch(self, k, *objs, rc=0):
        r, out = self.rep("verdicts", tr.lines(*objs), extra=["--batch", str(k)])
        self.assertEqual(r, rc, out)
        return json.loads(out)

    def stored(self):
        with open(os.path.join(self.run_dir, "verdicts.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_batches_accumulate_into_one_verdicts_file(self):
        self.batch(1, *[self.line(i) for i in self.first])
        out = self.batch(2, *[self.line(i, "unverifiable", False) for i in self.second])
        self.assertEqual((out["confirmed"], out["foreign"]), (sorted(self.first), 0))
        self.assertEqual(sorted(self.stored()), sorted(self.ids))
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "replies", "verify", "1.txt")))

    def test_foreign_id_is_counted_and_never_applied(self):
        out = self.batch(1, *[self.line(i) for i in self.first], self.line(self.second[0], "refuted"))
        self.assertEqual((out["foreign"], out["refuted"]), (1, []))
        self.assertNotIn(self.second[0], self.stored())

    def test_resending_a_batch_replaces_it(self):
        self.batch(1, *[self.line(i) for i in self.first])
        self.batch(1, self.line(self.first[0], "unverifiable", False))
        self.assertEqual(list(self.stored()), [self.first[0]])

    def test_batched_refuted_needs_a_cite_but_serial_keeps_todays_rule(self):
        out = self.batch(1, self.line(self.first[0], "refuted", False), self.line(self.first[1], "refuted"))
        self.assertEqual(out["refuted"], [self.first[1]])
        self.assertEqual(self.stored()[self.first[0]]["verdict"], "unverifiable")
        out = self.verdicts(self.line(self.first[0], "refuted", False))
        self.assertEqual(out["refuted"], [self.first[0]])

    def test_batched_refuted_cite_must_lie_in_the_candidates_own_path(self):  # review-code-8dt
        other = {"path": "src/other.py", "line": 2, "quote": "w1 = a + 1"}
        bad = dict(self.line(self.first[0], "refuted"), evidence=[other])
        out = self.batch(1, bad, self.line(self.first[1], "refuted"))
        self.assertEqual(out["refuted"], [self.first[1]])
        self.assertEqual(self.stored()[self.first[0]]["verdict"], "unverifiable")

    def test_batched_refuted_cite_cannot_serve_two_ids(self):  # review-code-8dt
        shared = [{"path": "src/big.py", "line": 2, "quote": "v1 = a - 1"}]
        a, b = (dict(self.line(i, "refuted"), evidence=shared) for i in self.first[:2])
        out = self.batch(1, a, b, self.line(self.first[2], "refuted"))
        self.assertEqual(out["refuted"], [self.first[2]])
        self.assertEqual([self.stored()[i]["verdict"] for i in self.first[:2]], ["unverifiable"] * 2)

    def test_verdicts_without_batch_refuse_in_a_parallel_plan(self):  # review-code-3k5
        self.batch(1, *[self.line(i) for i in self.first])
        with open(os.path.join(self.run_dir, "plan.json"), "w") as fh:
            json.dump({"mode": "parallel", "caps": {"max_verifier_calls": 60}}, fh)
        r, out = self.rep("verdicts", tr.lines(self.line(self.second[0])))
        self.assertEqual((r, json.loads(out)["reason"]), (1, "batch-required"))
        self.assertEqual(sorted(self.stored()), sorted(self.first))  # the stored batches survive

    def test_unknown_batch_refuses_and_stores_nothing(self):
        self.assertEqual(self.batch(9, self.line(self.first[0]), rc=1)["reason"], "bad-batch")
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "replies")))

    def test_serial_call_replaces_every_stored_batch(self):
        self.batch(1, *[self.line(i) for i in self.first])
        self.verdicts(self.line(self.second[0]))
        self.assertEqual(list(self.stored()), [self.second[0]])

    def test_ingest_and_discard_clear_the_stored_replies(self):
        self.batch(1, *[self.line(i) for i in self.first])
        r, out = self.rep("ingest", tr.lines(big(1)))  # review-code-edk: a lane reply after verification started refuses
        self.assertEqual((r, json.loads(out)["reason"]), (1, "late-reply"))
        self.rep("discard")
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "replies")))
        self.batch(1, self.line(self.ingest(big(1))["ids"][0]))
        self.rep("discard")
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "replies")))


class Status(Verdicts):
    def status(self, rc=0):
        r, out = self.rep("status")
        self.assertEqual(r, rc, out)
        return json.loads(out)

    def snapshot(self):
        out = {}
        for d, _, fs in os.walk(self.run_dir):
            for f in fs:
                if "tree" not in d:
                    with open(os.path.join(d, f), "rb") as fh:
                        out[os.path.join(d, f)] = fh.read()
        return out

    def test_status_lists_ids_calls_and_what_is_missing_without_writing(self):
        before = self.snapshot()
        st = self.status()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((st["ids"], st["spec_ids"], st["confirmed"], st["missing_batches"], st["missing_lanes"]),
                         (self.ids, [], [], [1, 2], []))
        self.assertEqual([c["ids"] for c in st["verify_calls"]], [self.first, self.second])
        self.assertTrue(st["verify_calls"][0]["prompt"].startswith("Verify. Handoffs:\n/"))
        self.assertEqual(st["lane_state"], {"defects": "incomplete"})
        self.batch(1, *[self.line(i) for i in self.first])
        st = self.status()
        self.assertEqual((st["missing_batches"], st["confirmed"]), ([2], sorted(self.first)))
        self.verdicts(self.line(self.second[0]))  # a serial reply covers every batch
        self.assertEqual(self.status()["missing_batches"], [])

    def test_status_before_ingest_refuses(self):
        os.remove(os.path.join(self.run_dir, "candidates.json"))
        self.assertEqual(self.status(rc=1)["reason"], "no-candidates")

    def test_a_planned_lane_never_ingested_renders_not_ingested(self):
        with open(os.path.join(self.run_dir, "plan.json"), "w") as fh:
            json.dump({"caps": {"max_verifier_calls": 60},
                       "calls": [{"lane": "defects", "shard": None}, {"lane": "standards", "shard": None},
                                 {"lane": "defects", "shard": 2}]}, fh)
        self.assertEqual(self.status()["missing_lanes"], ["defects@2", "standards"])
        self.assertEqual(self.doc()["lanes"], {"defects": "incomplete", "defects@2": "not-ingested",
                                               "standards": "not-ingested"})
        r, text = self.rep("render")
        for n in ("defects@2", "standards"):
            self.assertIn("lane %s not-ingested: its reply was never ingested" % n, text)


class CleanGate(tr.Lanes):  # review-code-0c4
    def plan(self, *lanes):
        with open(os.path.join(self.run_dir, "plan.json"), "w") as fh:
            json.dump({"caps": {"max_verifier_calls": 10}, "calls": [{"lane": n, "shard": None} for n in lanes]}, fh)

    def verdict(self):
        d = self.doc()
        r, text = self.rep("render")
        return d["verdict"], text

    def test_defects_or_spec_lane_not_ingested_is_not_clean(self):
        for planned in (("defects", "standards"), ("defects", "spec")):
            self.plan(*planned)
            self.lane(planned[1], {"inspected": ["src/calc.py"]})
            verdict, text = self.verdict()
            self.assertEqual(verdict, "Needs discussion", planned)
            self.assertIn("lane %s not-ingested" % planned[0], text)

    def test_incomplete_defects_lane_is_not_clean(self):
        self.plan("defects")
        self.lane("defects")
        self.assertEqual(self.verdict()[0], "Needs discussion")

    def test_complete_lanes_read_clean(self):
        self.plan("defects", "standards")
        self.lane("defects", {"inspected": ["src/calc.py"]})
        self.lane("standards")  # an incomplete standards lane stays advisory
        self.assertEqual(self.verdict()[0], "No blocking findings")


if __name__ == "__main__":
    unittest.main()
