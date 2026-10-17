---
phase: 01-plugin-foundation-and-ccr-contract
fixed_at: 2026-10-17T23:36:03Z
review_path: .planning/phases/01-plugin-foundation-and-ccr-contract/01-REVIEW.md
iteration: 1
findings_in_scope: 7
fixed: 7
skipped: 0
status: all_fixed
---

# Phase 1: Code Review Fix Report

**Fixed at:** 2026-10-17T23:36:03Z
**Source review:** .planning/phases/01-plugin-foundation-and-ccr-contract/01-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 7 (WR-01..WR-07; Info findings out of scope, IN-03/IN-04 not coupled)
- Fixed: 7
- Skipped: 0

Verification ran in the main checkout on master (no worktree). After every fix `sh scripts/check-package.sh` exited 0 with 27 ok, 0 FAIL, and `shellcheck -S warning scripts/check-package.sh` was clean. Gate fixes (WR-01, WR-02, WR-03) were done test-first: a mutation probe failed to fail (RED) before the change and failed correctly (GREEN) after. Each finding has a bd ticket under epic review-code-qz4.

## Fixed Issues

### WR-01: Gate `hygiene-tracked` reads the caller's repo, not the tree under test

**Files modified:** `scripts/check-package.sh`
**Commit:** 345e794
**Ticket:** review-code-qz4.18
**Applied fix:** hygiene-tracked now lists the tree under test (`find` over the exported or given directory, excluding `.git`) instead of `git ls-files` in the caller's cwd. The export branch resolves the repo root from the script's own location (`git -C "$(dirname "$0")/.."`), so the gate gives the same answer from any cwd. Probes: banned `AGENTS.md` in a copied tree now fails (was ok); non-git cwd with directory argument and with no argument now passes (was FAIL / fatal).

### WR-02: Licence checks pass with the permission notice missing

**Files modified:** `scripts/check-package.sh`
**Commit:** abc3511
**Ticket:** review-code-qz4.19
**Applied fix:** `license-mit` and `notice-permission` both require the permission grant, the notice-inclusion clause and the warranty disclaimer. Probes: LICENSE cut to 3 lines, LICENSE or NOTICE without the disclaimer, NOTICE without the inclusion clause now each fail (all were ok).

### WR-03: `.claude/CLAUDE.md` tracked and ships; gate and README said it could not

**Files modified:** `.claude/CLAUDE.md` (untracked via `git rm --cached`, file kept on disk), `.gitignore`, `scripts/check-package.sh`, `README.md`
**Commit:** 10a8e8a
**Ticket:** review-code-qz4.20
**Applied fix:** untracked and ignored `.claude/CLAUDE.md`, added `^\.claude/CLAUDE\.md$` to the gate's banned list, and rewrote the README "What ships" paragraph to list exactly what ships, including the `.planning/` record kept on purpose (D-03). `.planning/` was not untracked. The absolute personal path at `.planning/PROJECT.md:37` was not edited; ticket review-code-qz4.25 files it.

### WR-04: Verification gate admits `speculative` findings

**Files modified:** `skills/review-code/SKILL.md`
**Commit:** 7187a10
**Ticket:** review-code-qz4.21
**Applied fix:** gate item 4 now requires evidence class `reproduced` or `traced`; anything at `inferred` or `speculative` is asked as a question or listed as an unverified note, never stated as a finding. The old Blocking-only sentence is subsumed. Status: fixed: requires human verification (policy wording, not mechanically checkable). Note for the human: this is stricter than some contract rows (docs/ccr-contract.md L79 "at most `inferred`; Suggestions", L100 accessibility "demoted to Required when evidence is below `traced`"), which still describe reportable `inferred` findings. Reconcile the contract or the stub before the lane phases rely on it.

### WR-05: Iron-law wording differs from the approved wording

**Files modified:** `skills/review-code/SKILL.md`
**Commit:** 3673301
**Ticket:** review-code-qz4.22
**Applied fix:** iron law now opens with the approved sentence "Suspect to decide where to look; prove to decide what to report." (contract Resolved questions item 3), followed by the existing "do not assume broken" sentence.

### WR-06: trigger-prompts.md contradicted itself on method and misattributed T10 acceptance

**Files modified:** `docs/trigger-prompts.md`
**Commit:** 4530883
**Ticket:** review-code-qz4.23
**Applied fix:** Method now states the as-run headless procedure first, including that the appended system prompt forced a Skill call (bias toward invoking a skill; own session hooks not exercised). The interactive hand check moved to "Planned hand check (not performed)". The T10 disposition now reads accepted by the orchestrator under the user's no-interaction instruction, not yet reviewed by the user, to be confirmed. Trial results unchanged. Deviation: the plan 01-04 / review-code-qz4.10 disposition verify awk requires the literal prefix `- T10: accepted by user <date>, reason:`; it no longer matches by design because that prefix would be false. The row-count (20) and verdict/flag awks still pass. The plan files were not edited.

### WR-07: Description and `compatibility` advertise PR support the stub lacks

**Files modified:** `skills/review-code/SKILL.md`, `README.md`, `docs/trigger-prompts.md`
**Commit:** 6719d4d
**Ticket:** review-code-qz4.24
**Applied fix:** `description` left byte-identical (verified against e00bbe3). `compatibility` and the Status section now say PR targets route here but the PR is not fetched and PR reading and posting arrive in a later version; README requirements aligned. SKILL.md blob id in docs/trigger-prompts.md updated to `9f902cbb68d5c858659afe690cfa35e9d9c03c10` (matches `git rev-parse HEAD:skills/review-code/SKILL.md`), with a note that the trials ran on blob 26ec113 and the description is unchanged so the results stand.

## Skipped Issues

None.

## Follow-ups

- review-code-qz4.25: absolute personal path in `.planning/PROJECT.md:37` ships to installers (open, not fixed here by instruction).
- Contract vs WR-04 policy mismatch noted above (not ticketed; decide with the user).
- T10 disposition needs user confirmation.

---

_Fixed: 2026-10-17T23:36:03Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
