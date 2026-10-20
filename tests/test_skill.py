"""SKILL.md procedure text that live runs showed is load-bearing."""
import ast
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
    BASELINE_CALL = "python3 /x/skills/review-code/scripts/fix.py baseline --run r --digest " + "0" * 64
    FINISH_CALL = "python3 /x/skills/review-code/scripts/fix.py finish --run r --seal " + "0" * 64 + " --status applied"

    def setUp(self):
        with open(SKILL, encoding="utf-8") as fh:
            self.text = fh.read()

    def allowed(self):
        fm = self.text.split("\n---", 1)[0]
        m = re.search(r"^allowed-tools:[ \t]*\n((?:[ \t]+- .*\n)+)", fm + "\n", re.M)
        self.assertIsNotNone(m, "allowed-tools must be a YAML list")
        return [ln.strip()[2:].strip() for ln in m.group(1).splitlines()]

    def test_allowed_tools_names_eight_entries(self):
        items = self.allowed()
        self.assertEqual(len(items), 8, items)
        for name in ("target.py", "ledger.py", "report.py", "plan.py", "post.py", "fix.py"):
            self.assertTrue(any(name in i for i in items), name)
        for i in items:
            self.assertNotIn("repro", i)
            self.assertNotIn("scripts/*", i)
            self.assertFalse(i.startswith(("Edit", "Write")), i)
            pat = re.fullmatch(r"Bash\((.*)\)", i)
            self.assertIsNotNone(pat, i)
            rx = ".*".join(re.escape(p) for p in pat.group(1).replace("${CLAUDE_SKILL_DIR}", "/x/skills/review-code")
                           .split("*"))
            for call in (self.REPRO_CALL, self.SUBMIT_CALL, self.BASELINE_CALL, self.FINISH_CALL):
                self.assertIsNone(re.fullmatch(rx, call), (i, call))
        self.assertEqual(sorted(i for i in items if "fix.py" in i),
                         ["Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/fix.py %s *)" % v for v in ("begin", "plan", "report")])

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
        self.assertIn("a `\"` as `\\u0022`", self.text)  # 02a.18: a quote inside a string value

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
    def test_verdict_step_reads_by_call_and_sends_one_verdicts_call_per_batch(self):
        for t in ("one Read call per file", "never a shell loop", "ONE call", "replaces all earlier verdicts", "--batch <batch>",
                  "repeat of a batch replaces only that batch"):
            self.assertIn(t, self.flat)

    def test_ingest_decision_comes_from_status_never_from_a_marker_line(self):  # review-code-zx6: a reply can print any marker line
        self.assertNotRegex(self.flat, r"beginning `review-code")
        for t in ("`missing_lanes`", "`missing_batches`", "stop and state them"):
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


class Phase6Procedure(unittest.TestCase):
    """06-03: --fix in step 1, the confirmed one-at-a-time fix loop in step 12, and the pre-approval limits."""

    def setUp(self):
        with open(SKILL, encoding="utf-8") as fh:
            self.text = fh.read()
        self.flat = " ".join(self.text.split())

    def test_steps_in_order(self):
        steps = ["report.py render", "post.py build --run", "fix.py plan --run", "fix.py baseline --run",
                 "fix.py begin --run", "review-code:fixer", "fix.py finish --run", "fix.py report --run"]
        at = [self.text.find(s) for s in steps]
        self.assertNotIn(-1, at, dict(zip(steps, at)))
        self.assertEqual(at, sorted(at), dict(zip(steps, at)))

    def test_fix_flag_in_step_1(self):
        step1 = self.text.split("\n1. Target.", 1)[1].split("\n2. Ledger.", 1)[0]
        self.assertIn("`--fix`", step1)
        self.assertIn("needs committed code", " ".join(step1.split()))

    def test_gate_words(self):
        for s in ("Apply and run tests", "not applied: confirmation needs an interactive session", "REVIEW_FIX_CMD",
                  '[["status","applied"]]', "--status declined", "one fixer call at a time", "references/fix.md",
                  "never post except through step 11", "Target code runs only through steps 9 and 12"):
            self.assertIn(s, self.flat)

    def test_fixer_call_is_the_brief_path_on_the_tiers_model(self):
        step12 = self.text.split("\n12. Fix", 1)[1].split("\n13. ", 1)[0]
        flat = " ".join(step12.split())
        for s in ("subagent_type` `review-code:fixer", "exactly the brief path", "`model`", "`effort`", "begin"):
            self.assertIn(s, flat)

    def test_edits_only_through_the_fixer(self):
        self.assertIn("never edit files except through the fixer in step 12", self.flat.lower())
        self.assertNotIn("Fix mode does not exist yet", self.text)

    def test_baseline_and_finish_are_not_pre_approved(self):
        fm = self.text.split("\n---", 1)[0]
        self.assertNotIn("fix.py baseline", fm)
        self.assertNotIn("fix.py finish", fm)
        self.assertNotIn("fix.py *", fm)


class SerialModeNotice(unittest.TestCase):
    """06.1-01, audit cross item 1: steps 11 and 12 say serial mode is why post and fix always refuse."""

    def setUp(self):
        with open(SKILL, encoding="utf-8") as fh:
            self.text = fh.read()

    def step(self, start, end):
        return " ".join(self.text.split(start, 1)[1].split(end, 1)[0].split())

    def test_step_11_names_serial_mode_and_nothing_to_post(self):
        step11 = self.step("\n11. Post", "\n12. Fix")
        self.assertIn("serial mode", step11)
        self.assertIn("`nothing-to-post`", step11)

    def test_step_12_names_serial_mode_and_nothing_to_fix(self):
        step12 = self.step("\n12. Fix", "\n13. ")
        self.assertIn("serial mode", step12)
        self.assertIn("`nothing-to-fix`", step12)

    def test_serial_mode_is_blamed_only_for_the_nothing_to_refusals(self):  # 02a.21: dirty-tree, trust-* and others can refuse first
        step11 = self.step("\n11. Post", "\n12. Fix")
        step12 = self.step("\n12. Fix", "\n13. ")
        self.assertNotIn("always refuses", step11 + " " + step12)
        self.assertIn("`reason` is `nothing-to-post`", step11)
        self.assertIn("`reason` is `nothing-to-fix`", step12)

    def test_refusal_tables_and_readme_carry_the_serial_mode_cause(self):  # 02a.21
        refs = os.path.join(ROOT, "skills", "review-code", "references")
        for name, row in (("posting.md", "| `nothing-to-post` |"), ("fix.md", "| `nothing-to-fix` |")):
            with open(os.path.join(refs, name), encoding="utf-8") as fh:
                line = [ln for ln in fh if ln.startswith(row)][0]
            self.assertIn("serial mode", line, name)
            self.assertNotIn("always", line, name)  # 02a.26: dirty-tree, trust-* and others can refuse first
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            self.assertIn("`nothing-to-post`", fh.read().split("## Serial mode", 1)[1].split("\n## ", 1)[0])

    def test_readme_wire_form_names_both_guard_triggers(self):  # 02a.22
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            line = [ln for ln in fh if ln.startswith("Wire form:")][0]
        self.assertIn("backslash-escaped quote", line)
        self.assertIn("\\u0022", line)

    def test_posting_md_has_one_live_recheck_bullet_inside_a7(self):  # 02a.24: no second bullet repeating A7's tail
        with open(os.path.join(ROOT, "skills", "review-code", "references", "posting.md"), encoding="utf-8") as fh:
            bullets = [ln for ln in fh if ln.startswith("- ")]
        self.assertEqual(len([b for b in bullets if "Re-checked live on GitHub" in b]), 1)
        self.assertEqual(len([b for b in bullets if b.count("refuses `already-posted`")]), 1)

    def test_headless_comment_on_a_pr_is_documented_as_unsupported(self):  # 02a.18 option C
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            line = [ln for ln in fh if ln.startswith("`--comment` on a PR or MR target")][0]
        self.assertIn("Headless `claude -p --comment` on a PR is unsupported", line)
        self.assertIn("review-code-02a.18", line)
        with open(os.path.join(ROOT, "skills", "review-code", "references", "posting.md"), encoding="utf-8") as fh:
            self.assertIn("unsupported", [ln for ln in fh if ln.startswith("Only with `--comment`")][0])

    def test_step_12_begin_loop_also_leaves_on_halted(self):  # 02a.19: an interrupted begin prints status halted
        self.assertIn("`done`, `refuse` or `halted` leave the loop", self.step("\n12. Fix", "\n13. "))


class FixDocs(unittest.TestCase):
    """06-03: fix.md names every refusal and outcome fix.py can emit; the pointers, README and limits say what the code does."""

    FIX = os.path.join(ROOT, "skills", "review-code", "scripts", "fix.py")
    REFS = os.path.join(ROOT, "skills", "review-code", "references")
    # reasons that do not go through a Refuse call in fix.py: the trust gate's, a halted baseline's, the guards'
    EXTRA = {"baseline-red", "baseline-interrupted", "no-command", "trust-unset", "trust-external", "python-too-old",
             "internal-error"}

    def read(self, *parts):
        with open(os.path.join(*parts), encoding="utf-8") as fh:
            return fh.read()

    def flat(self, *parts):
        return " ".join(self.read(*parts).split())

    def names(self):
        found = set(self.EXTRA)
        for node in ast.walk(ast.parse(self.read(self.FIX))):
            if isinstance(node, ast.Call) and node.args and getattr(node.func, "attr", getattr(node.func, "id", None)) == "Refuse":
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    found.add(node.args[0].value)
        ns = {}
        exec(compile(ast.Module([n for n in ast.parse(self.read(self.FIX)).body
                                 if isinstance(n, ast.Assign) and n.targets[0].id == "OUTCOMES"], []), self.FIX, "exec"), ns)
        return found | set(ns["OUTCOMES"]), set(ns["OUTCOMES"])

    def test_every_reason_and_outcome_documented(self):
        doc = self.read(self.REFS, "fix.md")
        found, outcomes = self.names()
        self.assertGreater(len(found), 25, found)  # the walk found the Refuse calls
        self.assertEqual(len(outcomes), 10, outcomes)
        missing = sorted(r for r in found if "`%s`" % r not in doc)
        self.assertEqual(missing, [], "fix.md has no backticked entry for these refusal reasons or outcomes")

    def test_pointers(self):
        self.assertIn("references/fix.md", self.read(SKILL))
        self.assertIn("references/fix.md", self.read(self.REFS, "untrusted-input.md"))

    def test_readme(self):
        text = self.read(ROOT, "README.md")
        for s in ("--fix", "references/fix.md", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"):
            self.assertIn(s, text)
        self.assertNotIn("fix mode does not exist yet", text)

    def test_foreground_limit_documented(self):
        for flat in (self.flat(self.REFS, "fix.md"), self.flat(ROOT, "README.md")):
            self.assertIn("one fixer call at a time", flat)
            self.assertIn("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1", flat)
            self.assertIn("background", flat)

    def test_new_import_limit_documented(self):
        flat = self.flat(self.REFS, "fix.md")
        self.assertIn("file-level", flat)
        self.assertIn("new import", flat)

    def test_ignored_files_limit_documented(self):
        for flat in (self.flat(self.REFS, "fix.md"), self.flat(ROOT, "README.md")):
            self.assertIn("files git ignores", flat)

    def test_untrusted_input(self):
        flat = self.flat(self.REFS, "untrusted-input.md")
        self.assertIn("steps 9 and 12", flat)
        self.assertNotIn("runs only through step 9 of the skill", flat)
        self.assertIn("fix/", self.read(self.REFS, "untrusted-input.md"))


class PostingDocs(unittest.TestCase):
    """05-04: posting.md names every refusal post.py can emit; the pointers and the README caveats exist."""

    POST = os.path.join(ROOT, "skills", "review-code", "scripts", "post.py")
    REFS = os.path.join(ROOT, "skills", "review-code", "references")

    def read(self, *parts):
        with open(os.path.join(*parts), encoding="utf-8") as fh:
            return fh.read()

    def reasons(self):
        """The first string of every Refuse(...) call; `tool + suffix` expands for gh and glab; the explicit extras are
        the reasons that do not go through Refuse (python-too-old, internal-error) and the forge-state backstop."""
        found = {"python-too-old", "internal-error", "closed", "merged", "draft", "locked"}
        for node in ast.walk(ast.parse(self.read(self.POST))):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            fn, arg = node.func, node.args[0]
            if not (getattr(fn, "id", None) == "Refuse" or getattr(fn, "attr", None) == "Refuse"):
                continue
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add(arg.value)
            elif isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add) and isinstance(arg.right, ast.Constant):
                found.update(tool + arg.right.value for tool in ("gh", "glab"))
        return found

    def test_every_reason_documented(self):
        doc = self.read(self.REFS, "posting.md")
        found = self.reasons()
        self.assertGreater(len(found), 30, found)  # the walk found the Refuse calls
        missing = sorted(r for r in found if "`%s`" % r not in doc)
        self.assertEqual(missing, [], "posting.md has no backticked entry for these refusal reasons")

    def test_pointers(self):
        self.assertIn("references/posting.md", self.read(SKILL))
        untrusted = self.read(self.REFS, "untrusted-input.md")
        self.assertIn("references/posting.md", untrusted)
        self.assertRegex(" ".join(untrusted.split()), r"post\.py submit. is not pre-approved")

    def test_readme(self):
        text = self.read(ROOT, "README.md")
        for s in ("--comment", "glab", "untested against a live GitLab", "references/posting.md"):
            self.assertIn(s, text)
        for s in ("posting comes in a later version", "PR posting and fix mode do not exist yet"):
            self.assertNotIn(s, text)

    def test_cleanup_window_is_documented(self):
        """R2-WR-02: GitHub's DELETE has no precondition, so a comment added after cleanup's listing goes with the review."""
        flat = " ".join(self.read(self.REFS, "posting.md").split())
        self.assertIn("between cleanup's listing and its delete is deleted with the review", flat)

    def test_review_kept_never_says_submit_it_as_is(self):
        """R2-IN-03: a kept review may hold plugin comments that verify rejected; submitting it would publish them."""
        row = [ln for ln in self.read(self.REFS, "posting.md").split("\n") if ln.startswith("| `review-kept` |")][0]
        self.assertNotIn("submit or delete that pending review yourself", row)
        self.assertIn("remove the plugin's comments", row)

    def test_skill_shape(self):
        self.assertLess(len(self.read(SKILL).splitlines()), 500)

    def test_verify_reads_the_live_diff(self):
        """`gh pr diff` returns the current head's diff; posting.md and post.py must not say it is the pinned head's."""
        for text in (self.read(self.REFS, "posting.md"), self.read(self.POST)):
            flat = " ".join(text.split())
            self.assertNotIn("diff` for the pinned head", flat)
            self.assertNotIn("from the pinned head's own diff", flat)
        self.assertIn("live `gh pr diff`", " ".join(self.read(self.REFS, "posting.md").split()))


if __name__ == "__main__":
    unittest.main()
