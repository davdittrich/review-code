---
phase: 01-plugin-foundation-and-ccr-contract
plan: 05
subsystem: ccr-contract
tags: [contract, ccr, licensing, approval-gate]
requires: []
provides:
  - docs/ccr-contract.md, a user-approved keep-or-replace table for every ccr.md body line (PKG-04)
affects: [03 Defects lane, 04 domain packs, 05 report renderer]
tech-stack:
  added: []
  patterns: ["one row per ccr.md line, cited as L<n> against a sha256-pinned reference copy"]
key-files:
  created:
    - docs/ccr-contract.md
  modified: []
key-decisions:
  - "Mixed-provenance lines are marked v1.3-only (conservative for the D-08 accepted risk)"
  - "L24 stale-comment flag goes to the Standards lane"
  - "L100 accessibility stays Blocking but is demoted to Required below traced evidence"
  - "Iron law wording: Suspect to decide where to look; prove to decide what to report."
  - "Verdict labels: Needs changes / Needs discussion / No blocking findings (advisory, OUT-04)"
  - "L32 and L47 merge into one Ponytail check"
requirements-completed: [PKG-04]
duration: 2 sessions
completed: 2026-10-18
status: complete
plan_head_before: 709c73f59a10e81f6545b754a6fb03d14f226031
plan_head_after: e343c6725d332d4f3f55964033368ac47dc1b327
commits: 2
actuals:
  tokens: 9000
  tasks: 3
  commits: 2
---

# Phase 1 Plan 05: ccr.md keep-or-replace contract Summary

`docs/ccr-contract.md` gives every non-blank body line of the pinned ccr.md (93 lines, sha256 `0d4daffe...f8556`) a disposition, rationale, target and provenance, and the user approved it on 2026-10-18, so the Phase 3 Defects lane may now be built from it.

## What was built

- 93 table rows: keep 34, adapt 53, replace 6, drop 0. Wording is our own (D-09).
- Dispositions follow D-11 (iron law replaced, skepticism kept), D-12 (artifact-only for Defects and Standards; PR text only to Spec, untrusted), D-13 (tiers plus a separate evidence axis), D-14 (style demoted, theatre dropped), D-15 (language flags to Phase 4 packs, cross-file rules to the Defects lane).
- Accepted-risk section records D-08: the licence of the v1.3 additions credited to Ziad Hassan has not been verified; every v1.3-only row depends on the MIT assumption.
- Status line reads `approved 2026-10-18` with the verbatim `**Approval record:**`.

## Approval

User reply, recorded verbatim: "Approve with the 5 proposed defaults" (AskUserQuestion prompt, 2026-10-18). The user's global rules put approvals in a bd comment on the phase epic; bd had been initialised by the time of this plan, but the plan specified the contract Status line as the record (Phase 3 reads it), so the record lives there and here.

## Verification

- `coverage-ok`: all 93 body lines cited, re-run after the approval edit.
- `pin-ok`: sha256 matches the pinned hash.
- 93 rows, 0 bad (valid disposition and provenance in every row).
- 0 verbatim 8-grams versus docs/ccr.md.
- `grep -E '^\**Status:\** approved [0-9]{4}-[0-9]{2}-[0-9]{2}'` prints one line; `**Approval record:**` count is 1.

## Deviations from Plan

**1. [Process] Draft reworded after the first shingle run.** The first draft contained 4 verbatim 8-grams (L38, L101 wording); rewritten before commit. No impact.

**2. [Process] Checkpoint handled by the coordinator.** The executor returned the Task 2 checkpoint; the approval arrived as a coordinator message after the user answered the prompt, and Task 3 ran in the same agent. No user-visible effect.

Otherwise the plan was executed as written.

## Known Stubs

None.

## Threat Flags

None. T-01-13 (approval recorded with date and verbatim reply), T-01-14 (0 verbatim 8-grams) and T-01-15 (sha256 pin and coverage re-run after edit) are mitigated.

## Commits

- 18419b2 docs(01-05): draft ccr.md keep-or-replace contract
- e343c67 docs(01-05): record user approval of ccr.md contract

## Self-Check: PASSED

docs/ccr-contract.md exists; commits 18419b2 and e343c67 are ancestors of HEAD.
