# Trigger and collision check (PKG-03)

## Purpose

PKG-03 trigger and collision check for the `review-code` skill description (roadmap success
criterion 4; decisions D-04, D-05, D-06). Routing is model behaviour and probabilistic, so every
result below is an observation, not a guarantee. A different model, session or skill set can route
differently.

## Definitions

- Should-trigger: a code-review ask (review, critique, audit, find bugs, what is wrong with a
  change). `review-code` is expected to win every one of these, including over the installed
  `critical-code-reviewer` (D-04).
- Should-not-trigger: non-review asks (write, explain, debug, refactor code; commit messages; PR
  descriptions). Never a lighter review ask: lighter review asks are should-trigger (D-04).

## Description under test

SKILL.md git blob id: `26ec11336001dc9e9ed5aaf2f3e4a234f52e94ff`
(`git rev-parse HEAD:skills/review-code/SKILL.md`). A later description change invalidates the
results below.

```text
Reviews code changes (diffs, commits, branches, pull requests) or repository files and reports
defects, each with file:line evidence, an evidence class and a severity. Use when a user asks to
review, critique, audit or check code, or asks what is wrong with a change. Not for writing,
explaining or debugging code.
```

## Collision surface

Names confirmed against the skill list of the executing session (2026-10-18). Trigger language is
paraphrased from each skill's listed description.

| Skill | Its trigger language | Overlap with review-code |
|-------|----------------------|--------------------------|
| `critical-code-reviewer` | "review code", "critique my code", "find issues", "what's wrong"; adversarial review | near-total; D-04 wants review-code to win |
| `code-review` (bundled, `/review` alias) | review the current diff or a PR, branch or path; effort levels; `--comment`, `--fix` | total on diff and PR reviews |
| `ponytail:ponytail-review` | "review this", "code review", "review my PR", "is this over-engineered" | high |
| `caveman:caveman-review` | "review this PR", "review the diff"; one-line findings | high |
| `mattpocock-skills:code-review` | review changes since a fixed point; Standards and Spec axes | medium |
| `gsd-code-review` | review source files changed during a phase | medium (GSD workflow context) |
| `open-code-review:review` | run the OCR CLI and apply fixes | low to medium |
| `security-review` | security-only review of pending changes | partial |
| `simplify` | reuse and cleanup of changed code, no bug hunt | partial |

## Method

The 20 trials were run headlessly by the orchestrator, not in a hand-driven interactive session,
because the user asked for no human interaction. The hand check that D-06 prescribes was not
performed (see "Planned hand check" below).

- Each prompt: `claude -p` v2.1.302, `--plugin-dir` on a throwaway clone of HEAD `e00bbe3`,
  `--model sonnet --max-turns 8`, one run per prompt, no repeats.
- Added `--append-system-prompt "Routing test. Your first action must be the Skill tool call for
  the single best-matching listed skill, if any matches the request. Do not call task-observer. Do
  nothing else."`. Without it the user's task-observer hook derailed the run and all 20 prompts and
  3 positive controls returned no skill call.
- Controls with that prompt worked: "extract all the text and tables from report.pdf" gave
  `pdf-official`; "write a commit message for the staged changes" gave `caveman:caveman-commit`.
- Bias: the forcing prompt pushes toward invoking a skill at all. Read the results as "which skill
  wins when a skill is invoked", not as an unforced trigger rate. A hand run in an interactive
  session remains the stronger evidence.
- Verdicts: should-trigger WIN only for `review-code:review-code`; should-not-trigger PASS for any
  other result.
- Routing was forced: the appended system prompt tells the model its first action must be a Skill
  call for the best-matching listed skill, so the trials are biased toward invoking a skill and
  measure which skill wins, not whether any skill would trigger unprompted. The user's own session
  hooks (the task-observer load) are not exercised.

### Planned hand check (not performed)

Hand check in the real interactive setup (D-06), which would observe routing under the user's own
session hooks (headless runs without a routing-forcing prompt are derailed by those hooks).

1. In a terminal at the repository root, run `claude --plugin-dir .` so the plugin loads beside the
   installed skills.
2. For each prompt below: run `/clear`, paste the prompt, note the first review-related Skill tool
   call (ignore the task-observer load that the global instructions mandate), press Esc once the
   choice is visible.
3. Record the skill name, or `none`.

## Verdict rules

- Should-trigger rows (T): WIN (review-code invoked), LOSE (another skill invoked; name it), NONE
  (no skill invoked).
- Should-not-trigger rows (N): PASS (review-code not invoked), FAIL (review-code invoked).
- Flag is FLAG for LOSE, NONE and FAIL, else `-`. A FLAG row names the winning skill.

## Results

| ID | Prompt | Expected | Skill invoked | Verdict | Flag |
|----|--------|----------|---------------|---------|------|
| T01 | review my latest diff | review-code | review-code:review-code | WIN | - |
| T02 | what's wrong with this function in src/parser.py | review-code | review-code:review-code | WIN | - |
| T03 | audit src/auth for bugs | review-code | review-code:review-code | WIN | - |
| T04 | critique the changes on this branch before I open a PR | review-code | review-code:review-code | WIN | - |
| T05 | review PR #123 | review-code | review-code:review-code | WIN | - |
| T06 | find bugs in my last commit | review-code | review-code:review-code | WIN | - |
| T07 | check this code for problems: the staged changes | review-code | review-code:review-code | WIN | - |
| T08 | do a code review of the files I changed today | review-code | review-code:review-code | WIN | - |
| T09 | is anything wrong with the error handling in src/api/client.ts | review-code | review-code:review-code | WIN | - |
| T10 | give me a critical review of the current working tree | review-code | critical-code-reviewer | LOSE | FLAG |
| N01 | write a function that parses ISO dates | not review-code | ponytail:ponytail | PASS | - |
| N02 | explain how src/auth works | not review-code | describe-design | PASS | - |
| N03 | write a commit message for the staged changes | not review-code | caveman:caveman-commit | PASS | - |
| N04 | write a PR description for this branch | not review-code | mattpocock-skills:pr | PASS | - |
| N05 | debug why the login test fails with a timeout | not review-code | gsd-debug | PASS | - |
| N06 | refactor the parser to use async | not review-code | caveman:safe-refactor | PASS | - |
| N07 | add tests for the date parser | not review-code | gsd-add-tests | PASS | - |
| N08 | rename the variable cfg to config everywhere | not review-code | caveman:safe-refactor | PASS | - |
| N09 | summarise what README.md says | not review-code | none | PASS | - |
| N10 | set up CI for this repository | not review-code | github-actions-r | PASS | - |

## Summary

Trial date: 2026-10-18. Description blob `26ec11336001dc9e9ed5aaf2f3e4a234f52e94ff`.

- Should-trigger: 9 WIN, 1 LOSE, 0 NONE.
- Should-not-trigger: 10 PASS, 0 FAIL.
- Flagged: T10 ("give me a critical review of the current working tree"), won by the user's
  installed `critical-code-reviewer`.

Caveat: these are forced-choice observations (see Method), not an unforced trigger rate.

## Dispositions

- T10: accepted by the orchestrator 2026-10-18 under the user's instruction to avoid human interaction; the user has not reviewed this disposition and should confirm it before it is relied on; reason: accepted for v0.1.0, collision with the user's installed critical-code-reviewer on the phrase "critical review"; description left unchanged so the other 19 results stay valid; follow-up ticket reconsiders the wording before public release.
