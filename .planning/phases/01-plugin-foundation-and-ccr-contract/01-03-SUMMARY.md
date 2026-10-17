---
phase: 01-plugin-foundation-and-ccr-contract
plan: 03
subsystem: packaging
tags: [readme, gitignore, shipped-files, release-procedure]
requires: [01-01, 01-02]
provides:
  - README.md (install, floor, what ships, release, development, licence)
  - ignore policy final: .gitignore plus untracked tooling; lock files added
affects: [01-04, 01-05]
tech-stack:
  added: []
  patterns: ["tracked-file hygiene list mirrors .gitignore"]
key-files:
  created: [README.md]
  modified: [.gitignore, scripts/check-package.sh]
key-decisions:
  - "Shipped-file policy: gitignore (pre-resolved from the user's earlier choice, commit d33c673); .planning/ ships (D-03)"
  - "Publication timing: defer to ship time; the user named no owner/repo"
requirements-completed: [PKG-01]
duration: 1 session
completed: 2026-10-18
status: complete
plan_head_before: db0e4a56ee57b65ce1820ec8e9d4e193d7afa011
plan_head_after: db41dd136920d71c0815fb6f98418c715505fb3f
commits: 1
actuals:
  tokens: 1500
  tasks: 3
  commits: 1
---

# Phase 1 Plan 03: Shipped files and README Summary

README.md documenting local-directory install, version floor 2.1.246, release procedure and what ships; ignore list extended with the GSD lock files and mirrored in the gate's hygiene check.

## Decisions (pre-resolved, recorded verbatim)

**Task 1 (shipped-file policy, bead review-code-qz4.5): "gitignore".** Source: the user chose ".gitignore + git rm --cached" via AskUserQuestion on 2026-10-18; commit d33c673 implemented it. Not "revise-d03". Verified this session: `git ls-files` shows none of CLAUDE.md, AGENTS.md, GEMINI.md, .wolf/, .serena/, .codex/, .cursor/, .agents/, .opencode/, .beads/, .claude/{commands,rules,skills,settings.json}, docs/{ccr,matt,ponytail,caveman,cavecrew-reviewer,gsd-code-reviewer,code-review}.md. The plan's `git check-ignore` loop (extended with .cursor/, .agents/, .beads/) reports every path ignored. Remaining tracked and shipping by design: `.planning/` and `.claude/CLAUDE.md` (D-03).

**Task 2 (publication timing, bead review-code-qz4.6): "defer".** Source: orchestrator pre-resolution. The user did not name a repo, so publishing is deferred to ship time (the user can raise it then). This plan ran no `gh repo create`, `git push`, `gh release create` or `claude plugin tag`; `git remote -v` prints nothing.

## Task 3 (bead review-code-qz4.7, commit db41dd1)

- README.md written with sections: what it is, install, requirements, what ships, releasing, development, licence. Install shows the local directory form only (defer option: no GitHub form).
- Added `.planning/.gsd-beads.lock` and `.planning/milestone.lock` to `.gitignore` and to the gate's `banned` regex. They were untracked and would be picked up by `git add -A`. Other candidates (.wolf/, .serena/, .opencode/, GEMINI.md) were already ignored and covered by the gate regex or ignore list; no change.
- Verify: `sh scripts/check-package.sh` 27 ok, 0 FAIL; ignore loop printed no "not ignored"; README topic loop passed (`readme-ok`); `shellcheck scripts/check-package.sh` clean; `git remote -v` empty.

## PKG-01 status

Left `complete`. Evidence: strict validation and a fresh `CLAUDE_CONFIG_DIR` install from the directory-source marketplace pass in the gate (`validate-strict`, `install-fresh`, `details-skill`). Residual: the git clone-and-cache install path is not proven (01-RESEARCH Pattern 6); it needs a published remote. Ticketed as review-code-k4u (P3) for ship time.

## Deviations from Plan

**1. [Rule 2 - Hygiene] Lock files added to ignore list and gate.** `.planning/.gsd-beads.lock` and `.planning/milestone.lock` were untracked tooling that would ship if staged. Added to .gitignore and `banned`; the plan said leave the script unchanged without user path edits, but the orchestrator instructed adding such paths. Commit db41dd1.

**2. Verify command form.** The ignore loop was extended with .cursor/, .agents/, .beads/ (added by 01-01).

**3. README install section** omits the GitHub form (plan: add only for ship-time option), but mentions it will be added at release time.

**4. Commit prefix.** Commit is `docs(01-03)` since the change is README plus ignore entries.

## Known Stubs

None. (The skill itself is a documented v0.1.0 stub from 01-01.)

## Threat Flags

None.

## Self-Check: PASSED

README.md, .gitignore, scripts/check-package.sh exist; commit db41dd1 is an ancestor of HEAD; gate 27 ok.
