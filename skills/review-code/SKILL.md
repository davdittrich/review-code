---
name: review-code
description: >
  Reviews code changes (diffs, commits, branches, pull requests) or repository files and reports
  defects, each with file:line evidence, an evidence class and a severity. Use when a user asks to
  review, critique, audit or check code, or asks what is wrong with a change. Not for writing,
  explaining or debugging code.
license: MIT
compatibility: Claude Code >= 2.1.246 (provisional); git; gh for pull request targets
argument-hint: "[target]"
metadata:
  upstream: "critical-code-reviewer 1.3 (Garrick Aden-Buie, Ziad Hassan)"
---

# Review Code

## Iron law

Skepticism chooses where to look. Verified evidence decides what gets reported. Do not assume the
code is broken; read it closely, then report only what the evidence supports.

## Verification gate

A finding is reportable only with all four of:

1. `file:line` of the defect.
2. The quoted code at that location.
3. A concrete trigger: the input or state that makes it fail.
4. An evidence class of `reproduced` or `traced` (see below).

A concern that cannot meet the gate, including any at `inferred` or `speculative`, is asked as a
question or listed as an unverified note and never stated as a finding.

## Severity and evidence

Severity and evidence class are separate axes.

- Severity: Blocking, Required, Suggestions, Noted.
- Evidence class: `reproduced` (ran and observed), `traced` (followed the code path by reading),
  `inferred` (follows from the code but not traced end to end), `speculative` (plausible, unchecked).

Blocking categories: security, data loss, race conditions, accessibility, documentation that
contradicts the source, and constructor divergence between related types.

Style findings are Required or Suggestions at most, and only when a linter would not catch them.
Keep the tone direct and constructive: state the problem, the trigger and a fix.

## Status

This is the v0.1.0 stub. Reviewer lanes, the context ledger, the independent verifier, JSON or SARIF
output, PR posting and fix mode do not exist yet. When invoked, say so to the user, then do a
single-pass review in the main thread under the iron law and the verification gate.

- Label each finding's evidence class honestly. A single read-only pass reaches `traced` at most.
- Never claim independent verification.
- Never post to a PR, edit files, or run the target's code.
