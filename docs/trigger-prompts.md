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

Hand check in the real interactive setup (D-06). Headless `claude -p` runs are not usable: the
user's session hooks take the first action, so the routing decision is never observed.

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
| T01 | review my latest diff | review-code | pending | pending | - |
| T02 | what's wrong with this function in src/parser.py | review-code | pending | pending | - |
| T03 | audit src/auth for bugs | review-code | pending | pending | - |
| T04 | critique the changes on this branch before I open a PR | review-code | pending | pending | - |
| T05 | review PR #123 | review-code | pending | pending | - |
| T06 | find bugs in my last commit | review-code | pending | pending | - |
| T07 | check this code for problems: the staged changes | review-code | pending | pending | - |
| T08 | do a code review of the files I changed today | review-code | pending | pending | - |
| T09 | is anything wrong with the error handling in src/api/client.ts | review-code | pending | pending | - |
| T10 | give me a critical review of the current working tree | review-code | pending | pending | - |
| N01 | write a function that parses ISO dates | not review-code | pending | pending | - |
| N02 | explain how src/auth works | not review-code | pending | pending | - |
| N03 | write a commit message for the staged changes | not review-code | pending | pending | - |
| N04 | write a PR description for this branch | not review-code | pending | pending | - |
| N05 | debug why the login test fails with a timeout | not review-code | pending | pending | - |
| N06 | refactor the parser to use async | not review-code | pending | pending | - |
| N07 | add tests for the date parser | not review-code | pending | pending | - |
| N08 | rename the variable cfg to config everywhere | not review-code | pending | pending | - |
| N09 | summarise what README.md says | not review-code | pending | pending | - |
| N10 | set up CI for this repository | not review-code | pending | pending | - |

## Summary

Pending the 20 user-run trials.

## Dispositions

Pending: one entry per FLAG row, recorded after the trials.
