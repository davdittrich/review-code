"""capture.py and hooks/hooks.json (gh#8): a lane or verifier reply kept byte-exact for report.py --agent."""
import json
import os
import re
import stat
import tempfile
import unittest

import paths  # noqa: F401

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
        self.state = os.path.join(self.repo, ".git", "review-code")
        self.caps = os.path.join(self.state, "captures")

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
        os.makedirs(self.state)
        self.fire(handback())
        self.assertEqual(self.stored("handback"), {"agent_type": "review-code:finder", "message": MSG})
        self.assertEqual(self.names(), [AID + ".handback.json"])  # no temp file left
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.caps, AID + ".handback.json")).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.caps).st_mode), 0o700)

    def test_stop_stores_the_last_assistant_message_exactly(self):
        os.makedirs(self.state)
        self.fire(stop())
        self.assertEqual(self.stored("stop"), {"agent_type": "review-code:finder", "message": MSG})

    def test_a_stop_after_a_handback_leaves_the_handback_text(self):
        os.makedirs(self.state)
        self.fire(handback("the report"))
        self.fire(stop("closing words"))
        self.assertEqual(self.stored("handback")["message"], "the report")
        self.assertEqual(self.stored("stop")["message"], "closing words")

    def test_a_later_event_of_a_kind_replaces_the_earlier_one(self):  # a resumed agent replies again
        os.makedirs(self.state)
        self.fire(handback("first"))
        self.fire(handback("second"))
        self.assertEqual(self.stored("handback")["message"], "second")

    def test_every_lane_and_verifier_type_is_taken_and_nothing_else(self):
        os.makedirs(self.state)
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

    def test_a_missing_state_directory_writes_and_creates_nothing(self):
        self.fire(handback())
        self.fire(stop())
        self.assertFalse(os.path.exists(self.state))

    def test_a_malformed_agent_id_writes_nothing(self):
        os.makedirs(self.state)
        for bad in ("", "../x", "a/b", "a b", "a.b", "x" * 65, "a\0b", "a\nb", "-x", None, 5, ["a"]):
            self.fire(stop(agent_id=bad))
            self.fire(handback(agent_id=bad))
        self.fire({k: v for k, v in stop().items() if k != "agent_id"})
        self.assertEqual(self.names(), [])
        self.assertEqual(os.listdir(self.state), [])

    def test_a_missing_or_non_string_message_writes_nothing(self):
        os.makedirs(self.state)
        for bad in (None, 5, ["x"], {"k": "v"}):
            self.fire(stop(bad))
            self.fire(handback(bad))
        self.fire({k: v for k, v in stop().items() if k != "last_assistant_message"})  # a stop after a handback has none
        self.fire(dict(handback(), tool_input=None))
        self.fire({k: v for k, v in handback().items() if k != "tool_input"})
        self.assertEqual(self.names(), [])

    def test_other_events_and_tools_write_nothing(self):
        os.makedirs(self.state)
        self.fire(dict(handback(), tool_name="Read"))
        self.fire(dict(handback(), hook_event_name="PreToolUse"))
        self.fire(dict(stop(), hook_event_name="SessionEnd"))
        self.assertEqual(self.names(), [])

    def test_bad_input_and_a_non_repository_exit_zero_silently(self):
        os.makedirs(self.state)
        for raw in ("", "not json", "[]", "null", '"s"', "\xff\xfe", "{" * 5000):
            self.fire(None, raw=raw)
        outside = os.path.join(self.tmp, "plain")
        os.makedirs(outside)
        self.fire(handback(), cwd=outside)
        self.assertEqual(self.names(), [])
        self.assertEqual(os.listdir(outside), [])

    def test_a_lone_surrogate_survives_the_round_trip(self):
        os.makedirs(self.state)
        self.fire(None, raw='{"hook_event_name":"SubagentStop","agent_id":"%s","agent_type":"review-code:spec",'
                            '"last_assistant_message":"a\\ud800b"}' % AID)
        self.assertEqual(self.stored("stop")["message"], "a\ud800b")


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
