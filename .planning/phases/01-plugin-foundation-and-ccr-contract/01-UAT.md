---
status: testing
phase: 01-plugin-foundation-and-ccr-contract
source: [01-VERIFICATION.md]
started: 2026-10-17T23:46:08Z
updated: 2026-10-17T23:46:08Z
---

## Current Test

number: 1
name: Interactive trigger hand check (D-06, plan 01-04 Task 2)
expected: |
  Run `claude --plugin-dir .` in the repo root. For each of T01-T10 and N01-N10 in docs/trigger-prompts.md: /clear, paste the prompt, note the first review-related Skill call (ignore the task-observer load), press Esc. review-code wins every T row (including T10 over critical-code-reviewer) and appears in no N row.
awaiting: user response

## Tests

### 1. Interactive trigger hand check (D-06)
expected: review-code wins T01-T10 and appears in no N row, in the user's own session with their hooks and no forcing prompt.
result: [pending]

### 2. Method sufficiency decision
expected: If test 1 is skipped, the user records acceptance of the headless forced-choice evidence (2 samples per prompt) for SC4 in the Dispositions section of docs/trigger-prompts.md or as an overrides entry with accepted_by set to the user. Until then PKG-03 in REQUIREMENTS.md is overstated.
result: [pending]

### 3. Publish-time install over github.com
expected: After a user-approved remote exists and a git ls-files audit is done, in a fresh CLAUDE_CONFIG_DIR with no insteadOf rewrite: claude plugin marketplace add davdittrich/review-code, then claude plugin install review-code@review-code; claude plugin details lists Skills (1) review-code.
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps
