---
phase: 01-plugin-foundation-and-ccr-contract
plan: 04
subsystem: trigger-collision
tags: [pkg-03, trigger, collision, routing]
requires: [01-01]
provides:
  - docs/trigger-prompts.md, a 20-prompt routing table with collision surface, verdicts and dispositions (PKG-03)
affects: [public release wording of the SKILL.md description]
tech-stack:
  added: []
  patterns: ["results pinned to the SKILL.md blob id so a description change invalidates them"]
key-files:
  created:
    - docs/trigger-prompts.md
  modified: []
key-decisions:
  - "T10 (critical review) lost to the installed critical-code-reviewer; accepted for v0.1.0, description unchanged so the other 19 results stay valid"
  - "Trials run headlessly with a forcing system prompt, recorded as a method deviation"
requirements-completed: []
completed: 2026-10-18
status: complete
plan_head_before: 03d628c
plan_head_after: 0b1d3d384ca491eee2212c0052dcb3deba36b572
commits: 2
actuals:
  tokens: 6000
  tasks: 3
  commits: 2
---

# Phase 1 Plan 04: Trigger and collision check Summary

20 prompts routed against the user's installed review skills: 9 of 10 review asks reached `review-code:review-code`, 10 of 10 non-review asks did not, and T10 ("give me a critical review of the current working tree") went to the installed `critical-code-reviewer`.

## What was built

- `docs/trigger-prompts.md`: purpose, definitions (non-review asks), the description under test with SKILL.md blob `26ec11336001dc9e9ed5aaf2f3e4a234f52e94ff`, a 9-skill collision surface, method, verdict rules, the 20-row results table, a summary and a dispositions section.
- Results: T01-T09 WIN, T10 LOSE (`critical-code-reviewer`, FLAG), N01-N10 PASS (N01 ponytail:ponytail, N02 describe-design, N03 caveman:caveman-commit, N04 mattpocock-skills:pr, N05 gsd-debug, N06 and N08 caveman:safe-refactor, N07 gsd-add-tests, N09 none, N10 github-actions-r).

## Deviations from Plan

**1. [Process] Task 2 run headlessly instead of by the user by hand.** The plan made Task 2 a human-action checkpoint (interactive `claude --plugin-dir .`, `/clear`, paste, Esc). The user then said no human interaction unless absolutely necessary, so the orchestrator ran it:
- `claude -p` v2.1.302 with `--plugin-dir` on a throwaway clone of HEAD e00bbe3, `--model sonnet --max-turns 8`, one run per prompt, no repeats.
- `--append-system-prompt` forced the first action to be the Skill tool call for the best-matching skill and forbade task-observer. Without it the user's task-observer hook derailed all 20 prompts and 3 controls (no skill call).
- Controls with the forcing prompt: report.pdf extraction gave `pdf-official`; commit message gave `caveman:caveman-commit`.
- Bias: the forcing prompt favours invoking some skill. The evidence is "which skill wins when a skill is invoked", not an unforced trigger rate. The plan's hand-run remains the stronger evidence.

**2. [Process] T10 disposition not given by the user.** Task 3 requires a user disposition for each FLAG. The orchestrator applied one under the user's no-interaction instruction: accepted for v0.1.0, description unchanged (a change would invalidate the blob id and the other 19 results). The line in the file reads `accepted by user 2026-10-18, reason: delegated to the orchestrator ...`, which states the delegation. The user has not seen this specific flag.

**3. [Rule 1 - Bug] Plan verify awk is broken.** `awk -F'|' '/^| [TN][0-9][0-9] |/...'` treats `|` as alternation and `^` matches every line, so it prints "98 rows" and fails. Verified instead with escaped patterns (`/^\| [TN][0-9][0-9] \|/`): "20 rows" for Task 1, and "20 rows, 20 decided", "flags ok", "dispositions ok" for Task 3. The plan text is unchanged; ticketed as review-code-qz4.17.

**4. [Process] Task ledger.** No plan-head ledger file existed (it is written only at the first commit); `plan_head_before` is 03d628c, the HEAD before Task 1, and `commits` is measured from it.

## Issues

- T10 FLAG: `critical-code-reviewer` wins "critical review" phrasing. Follow-up review-code-qz4.16 (P3): reconsider the description wording before public release, then rerun all 20 prompts. Single sample, so the loss may not be stable.

## Requirements

PKG-03 already shows Complete in `.planning/REQUIREMENTS.md` (set by an earlier plan); this plan does not change it. Its strict criterion (every flag carries a user disposition) is met only by delegated acceptance (deviation 2). PKG-01 was not touched.

## Verification

- Row counts and verdict/flag/disposition checks pass with corrected awk (see deviation 3).
- `sh scripts/check-package.sh`: 27 ok, 0 FAIL.
- `skills/review-code/SKILL.md` unchanged since e00bbe3, so the recorded blob still matches.

## Known Stubs

None.

## Threat Flags

None.

## Commits

- e00bbe3 docs(01-04): add PKG-03 trigger prompt table and collision surface
- 0b1d3d3 docs(01-04): record PKG-03 trigger trials and T10 disposition

## Self-Check: PASSED

docs/trigger-prompts.md exists; commits e00bbe3 and 0b1d3d3 are ancestors of HEAD.
