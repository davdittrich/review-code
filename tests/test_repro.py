"""repro.py on real gitfix repos: trust gate, throwaway worktree, env allowlist, in-place diff, escapes, timeout."""
import ast
import io
import json
import os
import subprocess
import re
import signal
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

import paths  # noqa: F401

SCRIPTS = paths.SCRIPTS

import gitfix  # noqa: E402
from gitfix import cli  # noqa: E402
import repro  # noqa: E402

FID = "F-0123456789"
PASS_SRC = "import sys\nprint('REPRO-%s')\nsys.exit(1)\n" % FID


class Base(gitfix.FeatureRepo):
    base_files, feat_files = {"src/calc.py": "V = 1\n"}, {"src/calc.py": "V = 2\n"}

    def start(self, arg="main", trust="own"):
        rc, out, _ = cli("target.py", [arg], self.repo, self.tmp)
        self.assertEqual(rc, 0, out)
        run = json.loads(out)["run"]
        self.assertEqual(cli("ledger.py", ["build", "--run", run], self.repo, self.tmp)[0], 0)
        if trust:
            self.assertEqual(cli("ledger.py", ["trust", "set", trust, "--run", run], self.repo, self.tmp)[0], 0)
        return run

    def repro(self, run, test, cmd, src=PASS_SRC, fid=FID, extra=None):
        rc, out, err = cli("repro.py", ["run", "--run", run, "--id", fid, "--test", test, "--"] + cmd, self.repo,
                           self.tmp, src.encode("utf-8"), extra)
        self.assertNotIn("Traceback", err)
        return rc, json.loads(out)

    def state(self):
        return (self.git("status", "--porcelain"), self.git("for-each-ref"),
                self.git("worktree", "list", "--porcelain"))

    def assert_clean(self, run):
        self.assertEqual([n for n in os.listdir(run) if n.startswith(("wt-", "repro-home", "repro-tmp"))], [])
        self.assertEqual(self.git("worktree", "list", "--porcelain").count("worktree "), 1)

    def record(self, run, fid=FID):
        with open(os.path.join(run, "repro", fid + ".json"), encoding="utf-8") as fh:
            return json.load(fh)


class Tracer(Base):
    def test_own_reproduces_and_leaves_no_trace(self):
        run = self.start()
        before = self.state()
        rc, out = self.repro(run, "tests/test_repro_case.py", [sys.executable, "tests/test_repro_case.py"])
        self.assertEqual((rc, out["status"], out["outcome"]), (0, "ok", "reproduced"))
        self.assertNotEqual(out["exit"], 0)
        self.assertEqual(self.record(run), {"outcome": "reproduced", "exit": out["exit"], "mode": "run"})
        self.assert_clean(run)
        self.assertEqual(self.state(), before)

    def test_external_and_unset_run_nothing(self):
        for trust, reason in (("external", "trust-external"), (None, "trust-unset")):
            run = self.start(trust=trust)
            before = self.state()
            rc, out = self.repro(run, "t.py", [sys.executable, "t.py"])
            self.assertEqual((rc, out), (1, {"status": "refuse", "reason": reason}))
            self.assert_clean(run)
            self.assertFalse(os.path.exists(os.path.join(run, "repro")))
            self.assertEqual(self.state(), before)

    def test_bad_id_and_usage(self):
        run = self.start()
        self.assertEqual(self.repro(run, "t.py", ["true"], fid="F-XYZ")[1]["reason"], "bad-id")
        rc, out, _ = cli("repro.py", ["run", "--run", run], self.repo, self.tmp)
        self.assertEqual((rc, json.loads(out)["reason"]), (1, "usage"))

    def test_a_nested_target_json_refuses_no_target_like_a_corrupt_one(self):  # review-code-oved
        run = self.start()
        rc, out, _ = gitfix.nested_reads_as_corrupt(
            self, os.path.join(run, "target.json"),
            lambda: cli("repro.py", ["run", "--run", run, "--id", FID, "--test", "tests/test_calc.py", "--", "true"],
                        self.repo, self.tmp, PASS_SRC.encode("utf-8")))
        self.assertEqual((rc, json.loads(out)), (1, {"status": "refuse", "reason": "no-target"}))

    def test_sigterm_during_cleanup_still_removes_the_worktree(self):
        # review-code-6f3w: _term raising SystemExit inside run()'s finally aborted the cleanup loop
        run = self.start()
        real, hit = repro.target.git_bytes, []

        def term_once(args, *a, **k):
            if args[:2] == ["worktree", "remove"] and not hit:
                hit.append(1)
                os.kill(os.getpid(), signal.SIGTERM)
            return real(args, *a, **k)

        out = io.StringIO()
        with mock.patch.object(repro, "_execute", return_value={"outcome": "reproduced", "exit": 1}), \
                mock.patch.object(repro.target, "git_bytes", side_effect=term_once), redirect_stdout(out):
            rc = repro.main(["run", "--run", run, "--id", FID, "--test", "t.py", "--", "true"],
                            stdin=io.BytesIO(PASS_SRC.encode("utf-8")))
        self.assertEqual((rc, json.loads(out.getvalue())["reason"]), (143, "interrupted"))
        self.assert_clean(run)
        self.assertFalse(os.path.exists(os.path.join(run, "repro", FID + ".json")))

    def test_sigterm_before_the_mask_leaves_no_worktree(self):
        # review-code-vb7w: the mask used to start inside finally; a signal just before it aborted the cleanup
        run = self.start()
        real, hit = signal.pthread_sigmask, []

        def term_before_block(how, mask):
            if how == signal.SIG_BLOCK and not hit and os.path.isdir(os.path.join(run, "wt-" + FID)):
                hit.append(1)
                os.kill(os.getpid(), signal.SIGTERM)
            return real(how, mask)

        out = io.StringIO()
        with mock.patch.object(repro, "_execute", return_value={"outcome": "reproduced", "exit": 1}), \
                mock.patch.object(repro.signal, "pthread_sigmask", side_effect=term_before_block), redirect_stdout(out):
            rc = repro.main(["run", "--run", run, "--id", FID, "--test", "t.py", "--", "true"],
                            stdin=io.BytesIO(PASS_SRC.encode("utf-8")))
        self.assertEqual((rc, json.loads(out.getvalue())["reason"]), (143, "interrupted"))
        self.assert_clean(run)

    def test_sigterm_during_setup_is_held_until_the_run_then_cleaned_up(self):
        run = self.start()
        real, hit = repro.target.git_bytes, []

        def term_once(args, *a, **k):
            if args[:2] == ["worktree", "add"] and not hit:
                hit.append(1)
                os.kill(os.getpid(), signal.SIGTERM)
            return real(args, *a, **k)

        out = io.StringIO()
        with mock.patch.object(repro, "_execute", return_value={"outcome": "reproduced", "exit": 1}), \
                mock.patch.object(repro.target, "git_bytes", side_effect=term_once), redirect_stdout(out):
            rc = repro.main(["run", "--run", run, "--id", FID, "--test", "t.py", "--", "true"],
                            stdin=io.BytesIO(PASS_SRC.encode("utf-8")))
        self.assertEqual((rc, json.loads(out.getvalue())["reason"]), (143, "interrupted"))
        self.assert_clean(run)

    def test_sigterm_cleans_up_and_kills_the_test_group(self):
        run = self.start()
        marker = os.path.join(self.tmp, "pid")
        cmd = ["/bin/sh", "-c", "echo $$ > %s; sleep 30" % marker]
        e = gitfix.env()
        e["TMPDIR"] = self.tmp
        p = subprocess.Popen([sys.executable, os.path.join(SCRIPTS, "repro.py"), "run", "--run", run, "--id", FID,
                              "--test", "t.py", "--"] + cmd, cwd=self.repo, env=e, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p.stdin.write(PASS_SRC.encode("utf-8"))
        p.stdin.close()
        p.stdin = None  # review-code-wvw8: Python 3.9's communicate() flushes a closed stdin and raises ValueError
        deadline = time.monotonic() + 20
        while not os.path.exists(marker) or not open(marker).read().strip():
            self.assertLess(time.monotonic(), deadline, "the test never started")
            time.sleep(0.05)
        pgid = int(open(marker).read())
        p.terminate()
        p.communicate(timeout=20)
        self.assertEqual(p.returncode, 143)
        self.assert_clean(run)
        deadline = time.monotonic() + 3
        while True:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                break
            self.assertLess(time.monotonic(), deadline, "the process group is still alive")
            time.sleep(0.05)


class Rules(Base):
    def test_env_allowlist_and_home(self):
        run = self.start()
        src = ("import os, sys\n"
               "ok = 'FAKE_SECRET_TOKEN' not in os.environ and os.environ['HOME'].startswith(sys.argv[1])\n"
               "print('REPRO-%s' if ok else 'leak')\nsys.exit(1 if ok else 0)\n" % FID)
        rc, out = self.repro(run, "t.py", [sys.executable, "t.py", run], src, extra={"FAKE_SECRET_TOKEN": "x"})
        self.assertEqual(out["outcome"], "reproduced")

    def test_sentinel_and_exit_both_required(self):
        run = self.start()
        for i, src in enumerate(("import sys\nsys.exit(1)\n", "print('REPRO-%s')\n" % FID)):
            self.assertEqual(self.repro(run, "t.py", [sys.executable, "t.py"], src)[1]["outcome"], "not-reproduced")

    def test_staged_carries_index_only(self):
        with open(os.path.join(self.repo, "src/calc.py"), "w") as fh:
            fh.write("V = 3\n")
        self.git("add", "src/calc.py")
        with open(os.path.join(self.repo, "src/calc.py"), "w") as fh:
            fh.write("V = 4\n")
        run = self.start("--staged")
        src = ("import sys\nv = open('src/calc.py').read()\n"
               "if v == 'V = 3\\n':\n    print('REPRO-%s')\n    sys.exit(1)\n" % FID)
        self.assertEqual(self.repro(run, "t.py", [sys.executable, "t.py"], src)[1]["outcome"], "reproduced")

    def test_unstaged_carries_tracked_not_untracked(self):
        with open(os.path.join(self.repo, "src/calc.py"), "w") as fh:
            fh.write("V = 5\n")
        with open(os.path.join(self.repo, "scratch.txt"), "w") as fh:
            fh.write("x")
        run = self.start("--unstaged")
        src = ("import os, sys\n"
               "if open('src/calc.py').read() == 'V = 5\\n' and not os.path.exists('scratch.txt'):\n"
               "    print('REPRO-%s')\n    sys.exit(1)\n" % FID)
        self.assertEqual(self.repro(run, "t.py", [sys.executable, "t.py"], src)[1]["outcome"], "reproduced")

    def test_path_escapes_refused(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.repo, "link"))
        self.git("add", "link")
        self.git("commit", "-q", "-m", "link")
        run = self.start()
        for test in ("../x.py", "/abs/x.py", "a/../../x.py", "link/t.py"):
            rc, out = self.repro(run, test, [sys.executable, "t.py"])
            self.assertEqual((rc, out), (1, {"status": "refuse", "reason": "bad-test-path"}), test)
            self.assert_clean(run)
        self.assertEqual(os.listdir(outside), [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x.py")))

    def test_staged_symlink_escapes_refused(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        victim = os.path.join(self.tmp, "victim.txt")
        with open(victim, "w") as fh:
            fh.write("keep\n")
        os.symlink(outside, os.path.join(self.repo, "link"))  # (a) symlinked directory
        os.symlink(victim, os.path.join(self.repo, "t.py"))  # (b) symlinked last path part
        self.git("add", "link", "t.py")
        for test in ("link/pwn.py", "t.py"):
            with self.subTest(test=test):
                run = self.start("--staged")
                rc, out = self.repro(run, test, [sys.executable, "-c", "print(1)"])
                self.assertEqual((rc, out), (1, {"status": "refuse", "reason": "bad-test-path"}))
                self.assert_clean(run)
                self.assertFalse(os.path.exists(os.path.join(run, "repro")))
        self.assertEqual(os.listdir(outside), [])
        with open(victim) as fh:
            self.assertEqual(fh.read(), "keep\n")

    def test_timeout_kills_process_group(self):
        run = self.start()
        out = io.StringIO()
        bound = 1
        t0 = time.monotonic()
        with mock.patch.object(repro, "TIMEOUT", bound), gitfix.isolated(self.tmp), redirect_stdout(out):
            rc = repro.main(["run", "--run", run, "--id", FID, "--test", "t.py", "--", "/bin/sh", "-c",
                             "sleep 30 & sleep 30"], stdin=io.BytesIO(b"x"))
        self.assertLess(time.monotonic() - t0, bound + 5 + 5)
        self.assertEqual((rc, json.loads(out.getvalue())["outcome"]), (0, "timeout"))
        self.assertEqual(self.record(run), {"outcome": "timeout", "exit": None, "mode": "run"})
        self.assert_clean(run)

    def test_py39_grammar(self):
        with open(os.path.join(SCRIPTS, "repro.py"), encoding="utf-8") as fh:
            ast.parse(fh.read(), feature_version=(3, 9))


class Signals(unittest.TestCase):
    ARGV = ["run", "--run", "r", "--id", FID, "--test", "t.py", "--base", "--", "true", "t.py"]
    OK = {"status": "ok", "id": FID, "outcome": "reproduced", "exit": 1}

    def call(self, **kw):
        out = io.StringIO()
        with mock.patch.object(repro, "run", **kw), redirect_stdout(out):
            rc = repro.main(self.ARGV)
        return rc, json.loads(out.getvalue())

    def test_handlers_restored_after_main(self):
        before = {n: signal.getsignal(getattr(signal, n)) for n in ("SIGTERM", "SIGHUP")}
        self.call(return_value=self.OK)
        self.assertEqual({n: signal.getsignal(getattr(signal, n)) for n in before}, before)

    def test_non_main_thread_is_not_an_internal_error(self):
        res = []
        t = threading.Thread(target=lambda: res.append(self.call(return_value=self.OK)))
        t.start()
        t.join()
        self.assertEqual(res[0][1]["status"], "ok")

    def test_sigterm_prints_a_refusal(self):
        rc, out = self.call(side_effect=lambda *a: os.kill(os.getpid(), signal.SIGTERM))
        self.assertEqual((rc, out), (143, {"status": "refuse", "reason": "interrupted"}))

    def test_skill_bash_timeout_fits_timeout(self):
        with open(os.path.join(os.path.dirname(SCRIPTS), "SKILL.md"), encoding="utf-8") as fh:
            ms = int(re.search(r"Bash `timeout` of (\d+)", fh.read()).group(1))
        self.assertLessEqual(repro.TIMEOUT + 60, ms // 1000)  # 60 s for the group kill, worktree removal and sweep


class Execute(unittest.TestCase):
    def test_interrupt_kills_process_group(self):
        seen = []

        def boom(proc, *a, **k):
            seen.append(proc.pid)
            raise KeyboardInterrupt

        with mock.patch.object(subprocess.Popen, "communicate", autospec=True, side_effect=boom):
            with self.assertRaises(KeyboardInterrupt):
                repro._execute(["/bin/sh", "-c", "sleep 30 & sleep 30"], "/", {"PATH": os.environ["PATH"]}, FID)
        deadline = time.monotonic() + 3
        while True:
            try:
                os.killpg(seen[0], 0)
            except ProcessLookupError:
                break
            self.assertLess(time.monotonic(), deadline, "the process group is still alive")
            time.sleep(0.05)

    def test_timeout_argument_and_tail(self):
        env = {"PATH": os.environ["PATH"]}
        t0 = time.monotonic()
        res = repro._execute(["sleep", "30"], "/", env, FID, timeout=1)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(res, {"outcome": "timeout", "exit": None, "tail": ""})
        res = repro._execute(["/bin/sh", "-c", "i=0; while [ $i -lt 3000 ]; do echo line$i; i=$((i+1)); done; exit 3"],
                             "/", env, FID)
        self.assertEqual((res["outcome"], res["exit"]), ("not-reproduced", 3))
        self.assertEqual(len(res["tail"]), 2000)
        self.assertTrue(res["tail"].endswith("line2999\n"))


if __name__ == "__main__":
    unittest.main()
