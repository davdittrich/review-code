"""capture.py and hooks/hooks.json (gh#8): a lane or verifier reply kept byte-exact for report.py --agent."""
import contextlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

try:
    import pwd
except ImportError:  # Windows: capture.py falls back to expanduser there
    pwd = None

import paths  # noqa: F401

import capture  # noqa: E402
import gitfix  # noqa: E402

ROOT = os.path.dirname(paths.HERE)
AID = "aad7165a6e7d7a840"  # the shape of a live agent_id (spike review-code-oez)
MSG = '["a", "b\\"q\\""]\nsecond line \\ é \U0001f600\r\n\ttab'


def stop(msg=MSG, **kw):
    return dict({"hook_event_name": "SubagentStop", "agent_id": AID, "agent_type": "review-code:finder",
                 "last_assistant_message": msg}, **kw)


def handback(msg=MSG, **kw):
    return dict({"hook_event_name": "PostToolUse", "tool_name": "SubagentHandback", "agent_id": AID,
                 "agent_type": "review-code:finder", "tool_input": {"message": msg}}, **kw)


class Capture(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)
        self.repo = gitfix.make_repo(os.path.join(self.tmp, "repo"))
        self.caps = gitfix.captures(self.tmp)

    def fire(self, event, cwd=None, raw=None):
        rc, out, err = gitfix.cli("capture.py", [], cwd or self.repo, self.tmp,
                                  raw if raw is not None else json.dumps(event))
        self.assertEqual((rc, out, err), (0, "", ""))

    def stored(self, kind, aid=AID):
        with open(os.path.join(self.caps, "%s.%s.json" % (aid, kind)), encoding="utf-8") as fh:
            return json.load(fh)

    def names(self):
        return sorted(os.listdir(self.caps)) if os.path.isdir(self.caps) else []

    def test_handback_stores_the_tool_message_exactly(self):
        self.fire(handback())
        self.assertEqual(self.stored("handback"), {"agent_type": "review-code:finder", "message": MSG})
        self.assertEqual(self.names(), [AID + ".handback.json"])  # no temp file left
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.caps, AID + ".handback.json")).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.caps).st_mode), 0o700)

    def test_stop_stores_the_last_assistant_message_exactly(self):
        self.fire(stop())
        self.assertEqual(self.stored("stop"), {"agent_type": "review-code:finder", "message": MSG})

    def test_a_stop_after_a_handback_leaves_the_handback_text(self):
        self.fire(handback("the report"))
        self.fire(stop("closing words"))
        self.assertEqual(self.stored("handback")["message"], "the report")
        self.assertEqual(self.stored("stop")["message"], "closing words")

    def test_the_hook_runs_no_git(self):  # review-code-27u8: the location needs no repository, so no git seam
        log = os.path.join(self.tmp, "trace.json")
        rc, out, err = gitfix.cli("capture.py", [], self.repo, self.tmp, json.dumps(stop()), extra={"GIT_TRACE2_EVENT": log})
        self.assertEqual((rc, out, err), (0, "", ""))
        self.assertEqual(self.stored("stop")["message"], MSG)
        self.assertFalse(os.path.exists(log))

    def test_the_hook_needs_no_sibling_module(self):  # it imports none, so a copy on its own still stores
        alone = os.path.join(self.tmp, "alone")
        os.mkdir(alone)
        shutil.copy(os.path.join(paths.SCRIPTS, "capture.py"), alone)
        with open(os.path.join(alone, "target.py"), "w") as fh:  # a sibling that would print and exit at import
            fh.write('print("x")\nraise SystemExit(1)\n')
        e = dict(gitfix.env(), REVIEW_CODE_STATE_HOME=gitfix.state(self.tmp))
        p = subprocess.run([sys.executable, os.path.join(alone, "capture.py")], cwd=self.repo, env=e,
                           input=json.dumps(stop()).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))
        self.assertEqual(self.stored("stop")["message"], MSG)

    def test_a_later_event_of_a_kind_replaces_the_earlier_one(self):  # a resumed agent replies again
        self.fire(handback("first"))
        self.fire(handback("second"))
        self.assertEqual(self.stored("handback")["message"], "second")

    def test_every_lane_and_verifier_type_is_taken_and_nothing_else(self):
        for n in ("finder", "standards", "spec", "sota", "verifier", "spec-verifier"):
            self.fire(stop(agent_type="review-code:" + n))
            self.assertEqual(self.stored("stop")["agent_type"], "review-code:" + n)
        os.remove(os.path.join(self.caps, AID + ".stop.json"))
        for bad in ("", "review-code:fixer", "review-code:finder ", "Review-code:finder", "other:finder", "finder",
                    "review-code:finder\n", "xreview-code:finder", None, 7, ["review-code:finder"]):
            self.fire(stop(agent_type=bad))
            self.fire(handback(agent_type=bad))
        self.fire({k: v for k, v in stop().items() if k != "agent_type"})
        self.assertEqual(self.names(), [])

    def test_the_hook_makes_its_directory_and_nothing_in_the_repository(self):  # review-code-27u8
        self.fire(handback())
        self.fire(stop())
        self.assertEqual(self.names(), [AID + ".handback.json", AID + ".stop.json"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "review-code")))

    def test_a_malformed_agent_id_writes_nothing(self):
        for bad in ("", "../x", "a/b", "a b", "a.b", "x" * 65, "a\0b", "a\nb", "-x", None, 5, ["a"]):
            self.fire(stop(agent_id=bad))
            self.fire(handback(agent_id=bad))
        self.fire({k: v for k, v in stop().items() if k != "agent_id"})
        self.assertEqual(self.names(), [])

    def test_a_missing_or_non_string_message_writes_nothing(self):
        for bad in (None, 5, ["x"], {"k": "v"}):
            self.fire(stop(bad))
            self.fire(handback(bad))
        self.fire({k: v for k, v in stop().items() if k != "last_assistant_message"})  # a stop after a handback has none
        self.fire(dict(handback(), tool_input=None))
        self.fire({k: v for k, v in handback().items() if k != "tool_input"})
        self.assertEqual(self.names(), [])

    def test_other_events_and_tools_write_nothing(self):
        self.fire(dict(handback(), tool_name="Read"))
        self.fire(dict(handback(), hook_event_name="PreToolUse"))
        self.fire(dict(stop(), hook_event_name="SessionEnd"))
        self.assertEqual(self.names(), [])

    def test_bad_input_exits_zero_silently_and_a_non_repository_cwd_still_stores(self):
        for raw in ("", "not json", "[]", "null", '"s"', "\xff\xfe", "{" * 5000):
            self.fire(None, raw=raw)
        self.assertEqual(self.names(), [])
        outside = os.path.join(self.tmp, "plain")
        os.makedirs(outside)
        self.fire(handback(), cwd=outside)
        self.assertEqual(self.names(), [AID + ".handback.json"])
        self.assertEqual(os.listdir(outside), [])

    def test_the_hook_ages_out_stale_captures_and_keeps_fresh_ones(self):  # it runs outside the Bash sandbox
        os.makedirs(self.caps)
        for name, age in (("old.stop.json", capture.RETENTION_DAYS + 1), ("new.stop.json", capture.RETENTION_DAYS - 1)):
            p = os.path.join(self.caps, name)
            with open(p, "w") as fh:
                fh.write("{}")
            os.utime(p, (time.time() - age * 86400,) * 2)
        self.fire(stop())
        self.assertEqual(self.names(), [AID + ".stop.json", "new.stop.json"])

    @unittest.skipIf(pwd is None, "no account database (pwd) on this platform")
    def test_writer_and_reader_agree_when_home_differs(self):  # a SessionStart hook can change the Bash env only
        e = gitfix.env()
        e.pop("REVIEW_CODE_STATE_HOME", None)
        got = set()
        for home in ("/nonexistent/a", "/nonexistent/b"):  # only the path is computed: nothing is written
            p = subprocess.run([sys.executable, "-c", "import capture; print(capture.captures_dir())"], cwd=paths.SCRIPTS,
                               env=dict(e, HOME=home), stdout=subprocess.PIPE, check=True)
            got.add(p.stdout.decode().strip())
        self.assertEqual(got, {os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".local", "state", "review-code", "captures")})

    def test_a_lone_surrogate_survives_the_round_trip(self):
        self.fire(None, raw='{"hook_event_name":"SubagentStop","agent_id":"%s","agent_type":"review-code:spec",'
                            '"last_assistant_message":"a\\ud800b"}' % AID)
        self.assertEqual(self.stored("stop")["message"], "a\ud800b")


class Location(unittest.TestCase):  # review-code-27u8: the hook's cwd and the run's repository are unrelated
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)
        self.env = {"HOME": os.path.join(self.tmp, "home"), "REVIEW_CODE_STATE_HOME": os.path.join(self.tmp, "state")}
        self.main = gitfix.make_repo(os.path.join(self.tmp, "main"))
        gitfix.commit(self.main, {"a.py": "a = 1\n"}, "base")
        gitfix.commit(self.main, {"a.py": "a = 2\n"}, "main")
        self.wt = os.path.join(self.tmp, "wt")
        gitfix.run(["git", "worktree", "add", "-q", "-b", "feat", self.wt], self.main)
        gitfix.commit(self.wt, {"a.py": "a = 3\n"}, "feat")
        self.other = gitfix.make_repo(os.path.join(self.tmp, "other"))
        gitfix.commit(self.other, {"b.py": "b = 1\n"}, "other")
        self.plain = os.path.join(self.tmp, "plain")
        os.mkdir(self.plain)

    def pin(self, cwd):
        import target
        buf = io.StringIO()
        with gitfix.isolated(self.tmp), contextlib.redirect_stdout(buf), mock.patch.dict(os.environ, self.env):
            self.assertEqual(target.main(["HEAD~1..HEAD"], cwd), 0)  # each checkout reviews its own head
        return json.loads(buf.getvalue())["run"]

    def take(self, run, aid):
        import report
        with mock.patch.dict(os.environ, self.env):
            return report._take(run, aid, "review-code:finder", False)

    def test_a_reply_is_found_whatever_the_hook_cwd_and_the_run_checkout(self):
        sub = os.path.join(self.main, "sub")
        os.mkdir(sub)
        runs = {"main": self.pin(self.main), "wt": self.pin(self.wt)}
        for i, cwd in enumerate((self.main, sub, self.wt, self.other, self.plain)):
            for where, run in runs.items():
                aid = "a%d%s" % (i, where)
                rc, out, err = gitfix.cli("capture.py", [], cwd, self.tmp, json.dumps(handback(agent_id=aid)), extra=self.env)
                self.assertEqual((rc, out, err), (0, "", ""))
                self.assertEqual(self.take(run, aid), MSG, (cwd, where))
        for repo in (self.main, self.other, self.plain):  # nothing lands in a repository the hook happens to run in
            self.assertFalse(os.path.exists(os.path.join(repo, ".git", "review-code", "captures")), repo)


class Portable(unittest.TestCase):
    def test_the_module_imports_without_pwd(self):  # PR #92 review: discovery must not fail on Windows
        code = "import sys; sys.modules['pwd'] = None; import test_capture; print(test_capture.pwd)"
        p = subprocess.run([sys.executable, "-c", code], cwd=paths.HERE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"None\n", b""))


class Hooks(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8") as fh:
            self.hooks = json.load(fh)["hooks"]

    def test_two_exec_form_hooks_run_the_capture_script(self):
        self.assertEqual(sorted(self.hooks), ["PostToolUse", "SubagentStop"])
        for event, matcher in (("SubagentStop", None), ("PostToolUse", "^SubagentHandback$")):
            (entry,) = self.hooks[event]
            (hook,) = entry["hooks"]
            self.assertEqual((hook["type"], hook["command"]), ("command", "python3"))
            (arg,) = hook["args"]  # a user_config reference fails the whole hook when the option is unset (live, 2.1.306)
            self.assertEqual(arg, "${CLAUDE_PLUGIN_ROOT}/skills/review-code/scripts/capture.py")
            if matcher:
                self.assertEqual(entry["matcher"], matcher)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "skills", "review-code", "scripts", "capture.py")))

    def test_stop_matcher_selects_exactly_the_lane_and_verifier_agents(self):
        m = re.compile(self.hooks["SubagentStop"][0]["matcher"])
        for n in ("finder", "standards", "spec", "sota", "verifier", "spec-verifier"):
            self.assertTrue(m.search("review-code:" + n), n)
        for n in ("review-code:fixer", "review-code:spec-finder", "other:finder", "finder", "review-code:finder2",
                  "xreview-code:finder"):
            self.assertFalse(m.search(n), n)


if __name__ == "__main__":
    unittest.main()
