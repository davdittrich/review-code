# Critical Code Review Skill (SOTA)

## What This Is

A new standalone Claude Code plugin shipping a code-review skill. It starts from `docs/ccr.md` (critical-code-reviewer v1.3, by Garrick Aden-Buie and Ziad Hassan) and extends it into a state-of-the-art, comprehensive reviewer. Improvements come from other existing review skills and from online research. Users are developers who run reviews on diffs, branches and PRs.

## Core Value

Findings the user can trust: every reported finding is verified (reproduced or evidenced, with a confidence score) against full repository context, so the review has high recall without false-positive noise.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Skill is a lean `SKILL.md` core plus on-demand `references/` files (language packs, security, severity rubric, output templates), following progressive disclosure
- [ ] Parallel subagent reviewer lanes (for example correctness, security, spec/standards) plus a verification pass that reproduces or evidences each finding and assigns a confidence score before reporting
- [ ] Full-context review: callers, tests, fixtures, config, CI, docs and repo conventions are inspected; a diff-only review never satisfies the gate
- [ ] Spec and standards axes: change checked against the originating issue/spec and the repo's documented conventions
- [ ] Wider languages and domains: security/OWASP, concurrency, API compatibility, data and migrations, LLM-generated code, on top of ccr.md's Python, R, JS/TS, SQL
- [ ] Output modes: terminal report (Summary / Blocking / Required / Suggestions / Verdict), PR inline comments via `gh` with confirmation first, machine-readable JSON, and a fix mode that applies verified fixes one at a time and runs tests after each
- [ ] Every ccr.md rule is kept or deliberately replaced (iron law, artifact-only, AI-authored-code scrutiny, cross-file and doc-vs-source integrity, severity tiers)
- [ ] Design is research-grounded: each major addition traces to a cited source (existing review skills, papers, tool docs), recorded in a research log
- [ ] Credit to the original ccr.md authors and license compatibility are checked and preserved

### Out of Scope

- Empirical eval harness or seeded-bug corpus — user chose research-grounded design only; revisit if quality needs proof
- Dependence on local tooling (Serena, codegraph, beads) — the plugin must be portable; these may be optional accelerators only
- A hosted review service or GitHub App — this is a Claude Code skill, not a product

## Context

- Greenfield repo `/home/dd/projects/review-code`; `docs/` holds reference material: `ccr.md` (the base), `code-review.md` (user's full-context rule and "orphaned coverage" smell), `matt.md` (two-axis Standards/Spec review with parallel subagents), `gsd-code-reviewer.md`, `ponytail.md`, `caveman.md`, `cavecrew-reviewer.md`
- The user expects extensive online research; sources are chosen by the agent (existing public review skills and plugins, code-review research, and tool docs)
- Target runtime is Claude Code (skills, subagents, plugins)

## Constraints

- **Runtime**: Claude Code skill and plugin format — it must load and trigger correctly
- **Portability**: standalone plugin repo, no dependence on local tooling — it has to work on any machine
- **Attribution**: keep credit for the ccr.md authors and respect any licenses of borrowed material
- **Evidence**: claims in the skill rest on cited research, not on training-data recall
- **Size**: core `SKILL.md` stays lean; detail is loaded on demand

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Skill + references + subagent lanes | User chose the most comprehensive shape | — Pending |
| Standalone plugin repo | Portable and publishable | — Pending |
| Research-grounded, no eval corpus | User's choice; fastest | — Pending |
| All four output modes (terminal, PR comments, JSON, fix) | User's choice | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-10-17 after initialization*
