"""Batched verification (review-code-buj): run lock, verify batches, verdicts --batch, status, not-ingested."""
import json
import os
import subprocess
import sys
import unittest

import paths  # noqa: F401

import gitfix  # noqa: E402
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
            self.assertEqual([o[0] for o in outs], [0, 0], (rnd, outs))
            with open(os.path.join(self.run_dir, "candidates.json"), encoding="utf-8") as fh:
                self.assertEqual(sorted(json.load(fh)["lanes"]), ["defects", "standards"], rnd)
            self.assertFalse([n for n in os.listdir(self.run_dir) if ".tmp" in n])


if __name__ == "__main__":
    unittest.main()
