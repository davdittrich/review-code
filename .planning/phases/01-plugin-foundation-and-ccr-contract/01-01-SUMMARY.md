---
phase: 01-plugin-foundation-and-ccr-contract
plan: 01
subsystem: packaging
tags: [claude-plugin, marketplace, skill, gate, shell]
requires: []
provides:
  - installable review-code plugin (v0.1.0 stub) from a self-hosted marketplace
  - scripts/check-package.sh package gate (PKG-01, PKG-03, tracked-file hygiene)
affects: [01-02, 01-03, 01-04, 01-05]
tech-stack:
  added: []
  patterns: ["gate exports tracked tree via git stash create + git archive", "fresh CLAUDE_CONFIG_DIR install test"]
key-files:
  created:
    - .claude-plugin/plugin.json
    - .claude-plugin/marketplace.json
    - skills/review-code/SKILL.md
    - scripts/check-package.sh
  modified:
    - .gitignore
key-decisions:
  - "Untrack bd-init tooling and pointer files via committed .gitignore plus git rm --cached (user choice, option a)"
  - "plugin.json carries author davdittrich now: strict validation fails without one; 01-02 may amend"
  - "Hygiene list extended with .cursor/, .agents/, .beads/ beyond the planned list"
requirements-completed: [PKG-01, PKG-03]
duration: 2 sessions
completed: 2026-10-18
status: complete
plan_head_before: 92bccca
plan_head_after: 266b88ac5fbf2fadd930206f956dfa83ba35724d
commits: 3
actuals:
  tokens: 2500
  tasks: 2
  commits: 3
---

# Phase 1 Plan 01: Plugin foundation Summary

Installable `review-code` plugin stub (two manifests, stub-with-contract SKILL.md) plus `scripts/check-package.sh`, a gate that validates and installs the tracked tree and enforces the PKG-03 properties the validator skips.

## What was built

- `.claude-plugin/plugin.json` (0.1.0, MIT, author davdittrich) and `.claude-plugin/marketplace.json` (`source: "./"`, no entry version).
- `skills/review-code/SKILL.md`: 56 lines; Iron law, Verification gate, Severity and evidence, Status; own wording; no `allowed-tools`, no invocation-disabling keys, no dynamic injection. States it is a v0.1.0 stub and claims no independent verification.
- `scripts/check-package.sh`: 21 checks, `21 ok, 0 FAIL`; shellcheck `-S warning` clean. Optional directory argument checks a tree as-is (used by probes).

## Verification

- `sh scripts/check-package.sh` exit 0, `21 ok, 0 FAIL`, includes `validate-strict`, `install-fresh`, `details-skill`.
- Mutation probe `name: wrong-name` gives `FAIL - skill-name-dir`. RED was observed first (probe exited without the line before the argument was implemented).
- Probe with tracked `AGENTS.md` in a throwaway repo gives `FAIL - hygiene-tracked: tracked: AGENTS.md`. (The probe needed `git add -A -f` in my run because the archived `.gitignore` ignores `/AGENTS.md`; the plan's `git add -A` form exits 1 for that reason, see deviations.)
- Shingle guard vs `docs/ccr.md` (sha256 0d4daffe...): 0 verbatim 8-grams.
- `claude plugin details review-code`: always-on ~85 tok total, review-code ~90 always-on, **~440 on-invoke** (well below 5,000).

## Deviations from Plan

**1. [Rule 3 - Blocking] strict validation failed on missing author** -- plan said omit author. Strict mode errors on the "No author information" warning. User chose option (a): added `author {"name":"davdittrich"}` (matches marketplace owner). 01-02 Task 1 may change it. Ticket review-code-qz4.14 (closed). Commit b186727.

**2. [Rule 3/4 - Blocking, user decision] tracked tooling files** -- commit ae43eed (bd init, after the plan was written) tracked root CLAUDE.md, AGENTS.md, .claude/settings.json, .codex/, .cursor/, .agents/ and .beads/. Root CLAUDE.md fails strict validation; all would ship under `source: "./"`. Returned a checkpoint; user chose gitignore + `git rm --cached`. Done in d33c673 (files stay on disk). `.beads/` was untracked too after confirming `bd show` / `bd close` still work. Ticket review-code-qz4.15 (closed). This pre-empts the 01-03 Task 1 policy decision for these paths.

**3. [Rule 2 - Scope] hygiene list extended** -- added `.cursor/`, `.agents/`, `.beads/` to the Task 2 banned list to match the final shipped list. `.gitignore` also ignores `/GEMINI.md`, `.opencode/`, `.wolf/`, `.serena/`, `.claude/{commands,rules,skills}/`, `.claude/settings*.json` and the seven third-party `docs/*.md` copies.

**4. [Rule 1 - Plan probe defect] AGENTS.md probe** -- the plan's verify command uses `git add -A` in an archive that now contains the new `.gitignore`, which ignores `/AGENTS.md`, so that command exits 1. I verified the same behavior with `git add -A -f`. The plan's probe text should be amended (suggest `-f`) in a later plan.

## Known Stubs

None in code. SKILL.md is an intentional v0.1.0 stub, declared in its Status section.

## Notes for later plans

- `.planning/` and `.claude/CLAUDE.md` remain tracked and ship (D-03, 01-03 decides acceptance).
- `.planning/.gsd-beads.lock` and `.planning/milestone.lock` are untracked and not ignored; `git add -A` by anyone would pick them up.
- Git-source install is unproven (no remote); directory-source install is proven.

## Self-Check: PASSED

Files exist, commits d33c673, b186727, 266b88a in history, gate exits 0.
