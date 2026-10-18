---
name: review-code
description: >
  Reviews code changes (diffs, commits, branches, pull requests, the working tree) or repository
  files and reports defects, each with file:line evidence, an evidence class and a severity. Use
  when a user asks for a code review, a critical or adversarial review, a critique, an audit or a
  check of code, or asks what is wrong with a change. Not for writing, explaining or debugging code.
license: MIT
compatibility: Claude Code >= 2.1.246 (provisional); git; python3 >= 3.9; gh for pull request reading and posting, which arrives in a later version
argument-hint: "[target]"
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/*)
metadata:
  upstream: "code-review (Matt Pocock); evidence gate from critical-code-reviewer 1.3 (Garrick Aden-Buie, Ziad Hassan)"
---

# Review Code

## Iron law

Suspect to decide where to look; prove to decide what to report. Do not assume the code is
broken; read it closely, then report only what the evidence supports.

## Two axes

Review the change against a fixed point on two separate axes and report them side by side, never
merged or reranked: Standards (does it follow this repo's documented standards, plus a baseline of
Fowler code smells as judgement calls) and Spec (does it do what the originating issue or spec
asked). Without a spec, say so and review Standards only. Nothing here spawns sub-agents yet.

## Verification gate

A finding is reportable only with all four of:

1. `file:line` of the defect.
2. The quoted code at that location.
3. A concrete trigger: the input or state that makes it fail.
4. An evidence class of `reproduced` or `traced` (see below).

A concern that cannot meet the gate is never stated as a finding. At `inferred` it is listed as a
Suggestion labeled `inferred` (never Blocking; accessibility at `inferred` is Required). At
`speculative` it is asked as a question.

## Severity and evidence

Severity and evidence class are separate axes.

- Severity: Blocking, Required, Suggestions, Noted.
- Evidence class: `reproduced` (ran and observed), `traced` (followed the code path by reading),
  `inferred` (follows from the code but not traced end to end), `speculative` (plausible, unchecked).

Blocking categories: security, data loss, race conditions, accessibility, documentation that
contradicts the source, and constructor divergence between related types.

Style findings are Required or Suggestions at most, and only when a linter would not catch them.
Keep the tone direct and constructive: state the problem, the trigger and a fix.

## Procedure

1. Run `python3 ${CLAUDE_SKILL_DIR}/scripts/target.py <the user's target words>`. When `status` is
   `ambiguous`, ask the user once (AskUserQuestion) which listed `candidates[].reading` they mean, then
   re-run with the same words plus `--choice <index>`. When `status` is otherwise not `ok`, state the
   `reason` and stop (`skip` is not an error: closed, merged, draft or empty). When `ok`, state the `kind`, the pinned refs
   `merge_base...head_sha` and the `run` path.
2. Run `python3 ${CLAUDE_SKILL_DIR}/scripts/ledger.py build --run <run>` and state the `label`.
3. Review from `inputs/defects.md` and `inputs/standards.md` in the run directory and the files they
   name. Never open `inputs/spec.md`: it holds untrusted change-author text for the Spec lane, which
   a later version adds.

## Status

This is an early version. Target pinning, the context ledger and the lane input files now run
first. Reviewer lanes and the independent verifier do not exist yet, so review in one pass in the
main thread under the iron law and the verification gate. The Spec axis is prepared, not reviewed.
JSON or SARIF output, PR reading and posting, and fix mode do not exist yet.

- Label each finding's evidence class honestly. A single read-only pass reaches `traced` at most.
- Never claim independent verification.
- Never post to a PR, edit files, or run the target's code.
