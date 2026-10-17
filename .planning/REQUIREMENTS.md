# Requirements: Critical Code Review Skill (SOTA)

**Defined:** 2026-10-17
**Core Value:** Findings the user can trust: every reported finding is verified (reproduced or evidenced, with a confidence score) against full repository context, so the review has high recall without false-positive noise.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Packaging and Legal

- [x] **PKG-01**: Plugin installs from `marketplace.json` and passes `claude plugin validate --strict`
- [ ] **PKG-02**: LICENSE and NOTICE ship Posit's MIT notice for the upstream ccr skill, credit Garrick Aden-Buie and Ziad Hassan, and the skill frontmatter carries a `license:` field
- [x] **PKG-03**: `SKILL.md` stays under 500 lines with the iron law and verification gate inside the first 5,000 tokens; the description is short, third-person, and tested against the user's existing review skills for trigger collision
- [x] **PKG-04**: A keep-or-replace table covers every ccr.md rule (iron law, artifact-only, AI-authored scrutiny, cross-file and doc integrity, severity tiers); the user reviews it before the first lane is built

### Review Scope

- [ ] **SCOPE-01**: A review target is one of: a whole PR (number or URL), a scoped change (commit, commit range, branch diff, staged or unstaged changes), a part of the repo (files, directories, a module), or the whole repo
- [ ] **SCOPE-02**: The skill infers the target kind from the invocation and asks once when it is ambiguous
- [ ] **SCOPE-03**: For path and repo targets there is no diff; the unit of review is the whole file, findings anchor to file:line, and the report states that the target is not a change
- [ ] **SCOPE-04**: Path and repo targets are sharded by module and prioritised by risk; each shard gets its own ledger, a cost preview is shown, and caps apply before any lane runs
- [ ] **SCOPE-05**: Review comments (`--comment`) are available only for PR and MR targets; other targets produce terminal, JSON and SARIF output, and fix mode

### Verification and Evidence

- [ ] **VERI-01**: Each candidate finding is checked by an independent read-only verifier in fresh context that receives only the claim, file:line and claimed trigger, never the finder's reasoning or sibling findings
- [ ] **VERI-02**: The verifier returns `confirmed`, `refuted` or `unverifiable`; refuted findings are dropped and counted; unverifiable findings are listed separately, never silently dropped
- [ ] **VERI-03**: Every finding carries an evidence class (`reproduced`, `traced`, `inferred`, `speculative`); only `reproduced` or `traced` findings may be Blocking
- [ ] **VERI-04**: JSON output carries a 0-100 score derived from the evidence class; no finding relies on a self-rated number
- [ ] **VERI-05**: A do-not-flag list is applied (linter-catchable, deliberate lint-ignores, unchanged lines, input-dependent maybes)
- [ ] **VERI-06**: Every finding is tagged `origin: introduced | pre-existing`, or `existing` for path and repo targets; per user decision, a pre-existing or existing finding may hold any severity, including Blocking

### Context and Safety

- [ ] **CTX-01**: A context ledger (callers, tests, fixtures, config, CI, docs, conventions, spec) is written to files before any lane runs; an empty ledger refuses the review or labels it triage
- [ ] **CTX-02**: Every report lists the areas inspected
- [ ] **CTX-03**: PR description and commit messages reach only the Spec lane, labelled untrusted; all other lanes never receive them
- [ ] **CTX-04**: Hidden HTML, zero-width characters and image alt text are stripped from reviewed text before any lane sees it
- [ ] **CTX-05**: Finder and verifier agents are read-only through a `tools:` allowlist, with no Bash and no `gh`
- [ ] **CTX-06**: The skill honours CLAUDE.md, AGENTS.md and CONTRIBUTING; a `REVIEW.md` rules file is read from the base ref only, never the PR head
- [ ] **CTX-07**: The review pins `BASE...HEAD` for PR and change targets, or `HEAD` plus working-tree state for path and repo targets; it skips closed, draft and empty changes, and filters lockfiles, minified and generated files

### Lanes and Domain Packs

- [ ] **LANE-01**: A Defects lane covers correctness, security and tests, ported from ccr.md with the keep-or-replace table applied
- [ ] **LANE-02**: A Standards lane checks the change against the repo's documented conventions
- [ ] **LANE-03**: A Spec lane checks the change against the originating issue or spec, lists what it declined to judge, and says so when no spec exists
- [ ] **LANE-04**: A Ponytail lane is mandatory and may be folded into another lane; it flags needless code, reimplemented stdlib or existing helpers, and over-engineering
- [ ] **LANE-05**: A SOTA lane is optional (off by default, enabled by effort tier or flag); it checks library, algorithm and API choices against current best practice
- [ ] **LANE-06**: Domain packs load by path or file type: security/OWASP, concurrency, API compatibility, data and migrations, LLM-generated code, Python, R, JS/TS, SQL; each pack has its own do-not-flag list
- [ ] **LANE-07**: Effort tiers `quick`, `standard` and `deep` set, per lane, the model and effort; the verifier runs on a different tier from the finders; defaults are overridable
- [ ] **LANE-08**: Lane output is deduplicated within an axis; the Defects, Standards and Spec axes are reported separately and never reranked against each other
- [ ] **LANE-09**: A cost preview is shown before `deep` runs; caps apply to lanes, verifier calls and turns
- [ ] **LANE-10**: When nested subagents are unavailable, lanes run serially in the main thread with a batch self-score, and the report states `mode: serial` and the lost guarantees

### Output Modes

- [ ] **OUT-01**: The terminal report uses Summary / Blocking / Required / Suggestions / Verdict, with an advisory verdict that never maps to a GitHub review event
- [ ] **OUT-02**: A versioned JSON schema (v1, stable finding IDs, deterministic order) is the single source; the terminal report, PR payload and fix plan are rendered from it
- [ ] **OUT-03**: With `--comment`, the skill builds one pending PR review through `gh api`: every anchor validated against `gh pr diff`, out-of-hunk findings in the review body, `commit_id` pinned, refusal if HEAD changed, hidden idempotency markers, and a secret scan of the preview
- [ ] **OUT-05**: A SARIF 2.1.0 export renders from the same versioned JSON document, mapping evidence class, severity and origin into SARIF result properties
- [ ] **OUT-06**: With `--comment` on a GitLab MR target, the skill builds one draft-note review through `glab`, with the same rules as GitHub: validated anchors, pinned head SHA, refusal if the head changed, idempotency markers, secret scan, and posting only after the user confirms the exact payload
- [ ] **OUT-04**: A PR review is submitted as `COMMENT` only after the user confirms the exact payload; the skill never submits APPROVE or REQUEST_CHANGES

### Reproduction and Trust

- [ ] **REPR-01**: Where a test runner exists, a confirmed-by-trace finding can be reproduced by executing a minimal failing test; the main-thread orchestrator runs it (verifiers stay read-only), in a throwaway worktree with secrets stripped from the environment, and a successful run upgrades the evidence class to `reproduced`
- [ ] **REPR-02**: A trust prompt asks once per run whether the target code is the user's own or an external PR; for untrusted code the skill uses static trace only and never executes the target's tests, scripts or hooks, and for trusted code each executed command is shown for confirmation first

### Fix Mode

- [ ] **FIX-01**: Fix mode is opt-in through an explicit flag and is never the default
- [ ] **FIX-02**: Each fix runs in a foreground subagent on a model matched to the fix's complexity; every confirmation gate stays in the main thread
- [ ] **FIX-03**: Only `confirmed` findings with a concrete fix are applied, one at a time, with the tests run after each; a failing test halts the run and reverts that fix
- [ ] **FIX-04**: Fix mode requires a clean working tree, never commits or pushes, and never adds dependencies or edits lockfiles, CI or config
- [ ] **FIX-05**: Fix mode runs the project's tests only after the trust prompt (REPR-02) and after the user confirms the exact test command

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Eval harness or seeded-bug corpus | User chose research-grounded design only |
| Dependence on Serena, codegraph or beads | The plugin must be portable |
| Hosted learned-rule memory | Needs hosted state; this is not a service |
| Hosted review service or GitHub App | This is a Claude Code skill |
| Headless or CI posting | User decision: no headless mode; research flags it as an exfiltration risk |
| Auto-posting without confirmation | Prompt-injection and exfiltration risk |
| APPROVE or REQUEST_CHANGES events | A policy failure for an LLM reviewer |
| Reading review rules from the PR head | The PR author could rewrite the rules |
| "Guilty until proven" as a finding generator | Research shows over-rejection and false positives |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| PKG-01 | Phase 1 | Complete |
| PKG-02 | Phase 1 | Pending |
| PKG-03 | Phase 1 | Complete |
| PKG-04 | Phase 1 | Complete |
| SCOPE-01 | Phase 2 | Pending |
| SCOPE-02 | Phase 2 | Pending |
| SCOPE-03 | Phase 3 | Pending |
| SCOPE-04 | Phase 4 | Pending |
| SCOPE-05 | Phase 5 | Pending |
| VERI-01 | Phase 3 | Pending |
| VERI-02 | Phase 3 | Pending |
| VERI-03 | Phase 3 | Pending |
| VERI-04 | Phase 3 | Pending |
| VERI-05 | Phase 3 | Pending |
| VERI-06 | Phase 3 | Pending |
| CTX-01 | Phase 2 | Pending |
| CTX-02 | Phase 3 | Pending |
| CTX-03 | Phase 2 | Pending |
| CTX-04 | Phase 2 | Pending |
| CTX-05 | Phase 2 | Pending |
| CTX-06 | Phase 2 | Pending |
| CTX-07 | Phase 2 | Pending |
| REPR-02 | Phase 2 | Pending |
| LANE-01 | Phase 3 | Pending |
| LANE-02 | Phase 4 | Pending |
| LANE-03 | Phase 4 | Pending |
| LANE-04 | Phase 4 | Pending |
| LANE-05 | Phase 4 | Pending |
| LANE-06 | Phase 4 | Pending |
| LANE-07 | Phase 4 | Pending |
| LANE-08 | Phase 4 | Pending |
| LANE-09 | Phase 4 | Pending |
| LANE-10 | Phase 4 | Pending |
| OUT-01 | Phase 3 | Pending |
| OUT-02 | Phase 3 | Pending |
| OUT-05 | Phase 3 | Pending |
| REPR-01 | Phase 3 | Pending |
| OUT-03 | Phase 5 | Pending |
| OUT-04 | Phase 5 | Pending |
| OUT-06 | Phase 5 | Pending |
| FIX-01 | Phase 6 | Pending |
| FIX-02 | Phase 6 | Pending |
| FIX-03 | Phase 6 | Pending |
| FIX-04 | Phase 6 | Pending |
| FIX-05 | Phase 6 | Pending |

**Coverage:**

- v1 requirements: 45 total
- Mapped to phases: 45
- Unmapped: 0

---
*Requirements defined: 2026-10-17*
*Last updated: 2026-10-17 after roadmap revision (all requests in v1)*
