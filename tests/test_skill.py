"""SKILL.md procedure text that live runs showed is load-bearing."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "review-code", "SKILL.md")


class AskUserQuestionLoading(unittest.TestCase):
    """AskUserQuestion is a deferred tool in Claude Code: until ToolSearch loads it the model sees it as absent
    and skips the ask-once questions (live runs, review-code-myo)."""

    def setUp(self):
        with open(SKILL, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_names_toolsearch_load_before_unavailable(self):
        m = re.search(r"ToolSearch[^\n]*select:AskUserQuestion", self.text)
        self.assertIsNotNone(m, "SKILL.md must tell the model to load AskUserQuestion with ToolSearch")

    def test_unavailable_only_after_failed_load(self):
        self.assertRegex(self.text, r"(?s)load it[^.]*before[^.]*unavailable")


class Procedure(unittest.TestCase):
    """03-04: lanes, verdicts, reproduction and render, and a pre-approval that leaves repro.py to the harness."""

    REPRO_CALL = "python3 /x/skills/review-code/scripts/repro.py run --run r --id F-0123456789 --test t.py -- python3 t.py"
    SUBMIT_CALL = "python3 /x/skills/review-code/scripts/post.py submit --run r --digest " + "0" * 64

    def setUp(self):
        with open(SKILL, encoding="utf-8") as fh:
            self.text = fh.read()

    def allowed(self):
        fm = self.text.split("\n---", 1)[0]
        m = re.search(r"^allowed-tools:[ \t]*\n((?:[ \t]+- .*\n)+)", fm + "\n", re.M)
        self.assertIsNotNone(m, "allowed-tools must be a YAML list")
        return [ln.strip()[2:].strip() for ln in m.group(1).splitlines()]

    def test_allowed_tools_names_five_scripts_not_repro_or_submit(self):
        items = self.allowed()
        self.assertEqual(len(items), 5, items)
        for name in ("target.py", "ledger.py", "report.py", "plan.py", "post.py"):
            self.assertTrue(any(name in i for i in items), name)
        for i in items:
            self.assertNotIn("repro", i)
            self.assertNotIn("scripts/*", i)
            pat = re.fullmatch(r"Bash\((.*)\)", i)
            self.assertIsNotNone(pat, i)
            rx = ".*".join(re.escape(p) for p in pat.group(1).replace("${CLAUDE_SKILL_DIR}", "/x/skills/review-code")
                           .split("*"))
            for call in (self.REPRO_CALL, self.SUBMIT_CALL):
                self.assertIsNone(re.fullmatch(rx, call), (i, call))

    def test_steps_in_order(self):
        steps = ["review-code:finder", "report.py ingest", "review-code:verifier", "report.py verdicts",
                 "repro.py run", "report.py render"]
        at = [self.text.find(s) for s in steps]
        self.assertNotIn(-1, at, dict(zip(steps, at)))
        self.assertEqual(at, sorted(at), dict(zip(steps, at)))

    def test_reply_lines_are_pair_arrays_not_objects(self):  # g1x.10: the Bash guard denies a brace then a quote
        self.assertIn("starts with `[`", self.text)
        self.assertNotIn("starts with `{`", self.text)
        self.assertIn("\\u007b", self.text)

    def test_delimiters_and_flags(self):
        for s in ("REVIEW_FINDINGS", "REVIEW_VERDICTS", "REPRO_TEST", "REPRO-<id>", "--json", "--sarif"):
            self.assertIn(s, self.text)


DESCRIPTION = """description: >
  Reviews code changes (diffs, commits, branches, pull requests, the working tree) or repository
  files and reports defects, each with file:line evidence, an evidence class and a severity. Use
  when a user asks for a code review, a critical or adversarial review, a critique, an audit or a
  check of code, or asks what is wrong with a change. Not for writing, explaining or debugging code.
"""
# 04-02 probe decision line (fan-out waiting rule); also pins review-code-g1x.11, the "foreground Agent call" wording.
FAN_OUT = ("issue all calls of a wave in one message; wait until every call of the wave has returned or notified "
           "completion before the next step")


class Phase4Procedure(unittest.TestCase):
    """04-08: plan, cost gate, lanes, verifiers and serial mode in SKILL.md."""

    def setUp(self):
        with open(SKILL, encoding="utf-8") as fh:
            self.text = fh.read()
        self.flat = " ".join(self.text.split())

    def test_steps_in_order(self):
        steps = ["target.py --stdin", "ledger.py build", "ledger.py trust", "plan.py plan", "review-code:finder",
                 "report.py ingest", "review-code:verifier", "review-code:spec-verifier", "report.py verdicts",
                 "repro.py run", "report.py render"]
        at = [self.text.find(s) for s in steps]
        self.assertNotIn(-1, at, dict(zip(steps, at)))
        self.assertEqual(at, sorted(at), dict(zip(steps, at)))

    def test_delimiters_flags_and_plan_keys(self):
        for s in ("REVIEW_WORDS", "REVIEW_PLAN", "REVIEW_FINDINGS", "REVIEW_VERDICTS", "REPRO_TEST", "--json", "--sarif",
                  "--quick", "--standard", "--deep", "--sota", "--no-sota", "--serial", "--ai"):
            self.assertIn(s, self.text)
        block = re.search(r"<<'REVIEW_PLAN'\n(.*?)\n\s*REVIEW_PLAN", self.text, re.S).group(1)
        keys = ["default_tier", "finder_model", "finder_effort", "verifier_model", "verifier_effort",
                "max_verifier_calls", "max_shards", "max_packs"]
        self.assertEqual([ln.strip() for ln in block.splitlines()], ["%s=${user_config.%s}" % (k, k) for k in keys])

    def test_spec_file_is_never_opened(self):
        lines = [ln for ln in self.text.splitlines() if "inputs/spec.md" in ln]
        self.assertTrue(lines)
        for ln in lines:
            self.assertRegex(ln, r"(?i)\bnever\b", ln)

    # documentation check: pins wording, not behaviour (WR-03)
    def test_fan_out_wording(self):
        self.assertIn(FAN_OUT, self.flat)
        self.assertNotRegex(self.text, r"(?i)foreground Agent")

    # documentation check (review-code-cmp, review-code-csd): a compound read and a split verdicts call both failed live
    def test_verdict_step_reads_by_call_and_sends_one_verdicts_call(self):
        for t in ("one Read call per file", "never a shell loop", "ONE quoted", "replaces the previous `verdicts.json`"):
            self.assertIn(t, self.flat)

    def test_cost_gate_and_serial(self):
        for s in ("`confirm` is true", "\"Run\"", "\"Stop\"", "--serial", "self-score", "mode: serial", "wave_size",
                  "nesting-depth or concurrent-limit"):
            self.assertIn(s, self.flat)

    def test_shape(self):
        self.assertLess(len(self.text.splitlines()), 500)
        head = self.text.encode("utf-8")[:15000].decode("utf-8", "ignore")
        self.assertIn("## Iron law", head)
        self.assertIn("## Verification gate", head)
        self.assertIn(DESCRIPTION, self.text)
        self.assertRegex(self.text, r"(?m)^compatibility: Claude Code >= 2\.1\.292")


class Phase5Procedure(unittest.TestCase):
    """05-01: --comment, the SCOPE-05 stop and the confirmed post in step 11."""

    def setUp(self):
        with open(SKILL, encoding="utf-8") as fh:
            self.text = fh.read()
        self.flat = " ".join(self.text.split())

    def test_steps_in_order(self):  # --run keeps post.py build out of the allowed-tools frontmatter entry
        steps = ["report.py render", "post.py build --run", "post.py submit --run"]
        at = [self.text.find(s) for s in steps]
        self.assertNotIn(-1, at, dict(zip(steps, at)))
        self.assertEqual(at, sorted(at), dict(zip(steps, at)))

    def test_comment_flag_and_confirmation_words(self):
        for s in ("--comment", "Post as COMMENT", "Do not post", "applies only to PR and MR targets",
                  "not posted: confirmation needs an interactive session"):
            self.assertIn(s, self.flat)
        self.assertNotIn("Never post to a PR", self.flat)
        self.assertIn("never post except through step 11", self.flat)

    def test_comment_is_removed_in_step_1_and_checked_before_any_lane(self):
        step1 = self.text.split("\n1. Target.", 1)[1].split("\n2. Ledger.", 1)[0]
        self.assertIn("`--comment`", step1)
        self.assertIn("applies only to PR and MR targets", step1)

    def test_submit_is_not_pre_approved(self):
        fm = self.text.split("\n---", 1)[0]
        self.assertIn("post.py build *", fm)
        self.assertNotIn("post.py submit", fm)
        self.assertNotIn("post.py *", fm)


if __name__ == "__main__":
    unittest.main()
