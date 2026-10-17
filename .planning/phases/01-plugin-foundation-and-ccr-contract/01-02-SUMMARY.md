---
phase: 01-plugin-foundation-and-ccr-contract
plan: 02
subsystem: licensing
tags: [license, notice, attribution, package-gate]
requires: [01-01]
provides:
  - LICENSE (MIT, holder davdittrich) and NOTICE (Posit notice verbatim, credits, D-08 accepted risk)
  - scripts/check-package.sh PKG-02 checks (6 ids)
affects: [03 Defects lane, any public release]
tech-stack:
  added: []
  patterns: ["gate fails when a credit or upstream notice is removed"]
key-files:
  created:
    - LICENSE
    - NOTICE
  modified:
    - scripts/check-package.sh
key-decisions:
  - "Copyright holder is `davdittrich` (user choice, 2026-10-18), matching plugin.json author and marketplace owner"
  - "Unverified licence of the v1.3 additions (Ziad Hassan) recorded in NOTICE as accepted risk D-08"
requirements-completed: [PKG-02]
completed: 2026-10-18
status: complete
plan_head_before: 843033b
plan_head_after: bbe5ab5
commits: 1
actuals:
  tokens: 2500
  tasks: 2
  commits: 1
---

# Phase 1 Plan 02: LICENSE and NOTICE Summary

MIT LICENSE (`Copyright (c) 2026 davdittrich`) and a NOTICE that reproduces Posit's MIT text verbatim, credits Garrick Aden-Buie and Ziad Hassan, and records the unverified v1.3 licence as accepted risk D-08, all enforced by six new gate checks.

## Task 1: copyright holder (checkpoint:decision)

User chose holder string `davdittrich` (verbatim) on 2026-10-18. No commit; bead review-code-qz4.3 closed.

## Task 2: LICENSE, NOTICE, gate (bead review-code-qz4.4, commit bbe5ab5)

- RED: added `license-mit`, `notice-posit-copyright`, `notice-permission`, `notice-credits`, `notice-accepted-risk`, `plugin-author`; gate showed 6 FAIL.
- GREEN: upstream text fetched via `gh api repos/posit-dev/skills/contents/LICENSE`; gate 27 ok, 0 FAIL; shellcheck clean.
- Removed-credit probe (`Ziad Hassan` replaced in a copy) printed `FAIL - notice-credits`; verbatim check printed `verbatim`; `jq -r .author.name` printed `davdittrich`; `grep -c D-08 NOTICE` printed 1.

## Deviations from Plan

**1. [Rule 1 - Bug] Accepted-risk sentence wrapped across lines**
- Found during: Task 2 GREEN run (`FAIL - notice-accepted-risk`)
- Issue: "has not been verified" was split by a line break, so the line-based grep missed it.
- Fix: rewrapped so the phrase sits on one line; gate then 27 ok.
- Files modified: NOTICE (before commit, same commit)

**2. plugin.json not edited.** `author.name` was already `davdittrich` from 01-01, so the plan's edit was a no-op.

**3. PKG-01** left untouched as instructed (already marked complete by earlier plans).

## Known Stubs

None.

## Self-Check: PASSED

LICENSE, NOTICE, scripts/check-package.sh exist; commit bbe5ab5 is an ancestor of HEAD.
