# Roadmap: Critical Code Review Skill

## Overview

The project starts from `docs/ccr.md` and ends with a portable Claude Code plugin that produces verified code reviews. Phase 1 settles packaging, licensing and the ccr.md keep-or-replace decisions. Phase 2 pins the review target, builds the context ledger and draws the trust boundary before any reviewer exists, including the once-per-run trust prompt that decides whether target code may ever be executed. Phase 3 delivers the core value end to end: one Defects lane, an independent per-finding verifier, execution-based reproduction for trusted code, and terminal, JSON and SARIF output rendered from one schema. Phase 4 widens the review to all lanes, domain packs and effort tiers, with cost shown and capped up front. The two mutating outputs come last. Phase 5 posts confirmed reviews to GitHub PRs and GitLab MRs; Phase 6 applies verified fixes, gated by the trust prompt and by tests.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Plugin Foundation and ccr Contract** - Installable, licensed plugin skeleton; user-approved keep-or-replace table for every ccr.md rule
- [ ] **Phase 2: Review Target, Context Ledger and Trust Boundary** - Target resolution, ref pinning, context ledger gate, untrusted-input stripping, read-only agents, trust prompt
- [ ] **Phase 3: Verified Review in the Terminal** - Defects lane plus per-finding verifier and reproduction, evidence classes and origin tags, terminal/JSON/SARIF rendered from versioned JSON
- [ ] **Phase 4: Full Lane Set, Domain Packs and Effort Tiers** - Standards, Spec, Ponytail and SOTA lanes; domain packs; tiers, caps, cost preview, sharding, serial fallback
- [ ] **Phase 5: PR and MR Review Posting** - One pending GitHub review or GitLab draft-note review, anchors validated, posted only after confirmation, never approving or blocking
- [ ] **Phase 6: Fix Mode** - Opt-in, trust-gated, one-at-a-time application of confirmed fixes with tests after each

## Phase Details

### Phase 1: Plugin Foundation and ccr Contract

**Goal**: The user can install a validly licensed plugin whose skill triggers on review requests without colliding with existing review skills, and has approved how every ccr.md rule is kept or replaced.
**Depends on**: Nothing (first phase)
**Requirements**: PKG-01, PKG-02, PKG-03, PKG-04
**Success Criteria** (what must be TRUE):

  1. `claude plugin validate --strict` passes, and the plugin installs from `marketplace.json` on a Claude Code setup that has never seen it.
  2. LICENSE and NOTICE carry Posit's MIT notice for the upstream ccr skill and credit Garrick Aden-Buie and Ziad Hassan; the skill frontmatter has a `license:` field.
  3. `SKILL.md` is under 500 lines, with the iron law and verification gate inside its first 5,000 tokens.
  4. The short, third-person description triggers on recorded should-trigger review prompts and stays silent on should-not-trigger prompts, checked against the user's installed review skills.
  5. The user has reviewed and approved a keep-or-replace table that covers every ccr.md rule (iron law, artifact-only, AI-authored scrutiny, cross-file and doc integrity, severity tiers) before any lane is built.

**Plans**: 3/5 plans executed

Plans:
**Wave 1**

- [x] 01-01-PLAN.md — Tracer: manifests, stub SKILL.md and check-package.sh gate (strict validate, fresh-config install, PKG-03 and hygiene checks)
- [x] 01-05-PLAN.md — docs/ccr-contract.md: keep-or-replace row for every ccr.md line, user approval checkpoint

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 01-02-PLAN.md — LICENSE and NOTICE with Posit notice, credits and D-08 accepted risk; plugin author (copyright-holder decision)
- [ ] 01-04-PLAN.md — docs/trigger-prompts.md: 20-prompt trigger and collision table, user-run trials, flagged losses

**Wave 3** *(blocked on Wave 2 completion)*

- [ ] 01-03-PLAN.md — Shipped-file policy and publication-timing decisions; ignore rules and README

### Phase 2: Review Target, Context Ledger and Trust Boundary

**Goal**: For any review target, the skill pins exactly what is under review, writes a full context ledger, and records whether the target code may be executed. Untrusted text and write tools are kept away from reviewer agents before any lane runs.
**Depends on**: Phase 1
**Requirements**: SCOPE-01, SCOPE-02, CTX-01, CTX-03, CTX-04, CTX-05, CTX-06, CTX-07, REPR-02
**Success Criteria** (what must be TRUE):

  1. The user can invoke the skill with a PR or MR number or URL, a commit, a commit range, a branch diff, staged or unstaged changes, files or directories, or the whole repo. The skill states the resolved target kind and its pinned refs (`BASE...HEAD`, or `HEAD` plus working-tree state), and asks once only when the invocation is ambiguous. Closed, draft and empty changes are skipped with a stated reason; lockfiles, minified files and generated files are excluded from the target.
  2. Before any lane runs, ledger files on disk list callers, tests, fixtures, config, CI, docs, conventions (CLAUDE.md, AGENTS.md, CONTRIBUTING, base-ref `REVIEW.md`) and spec. An empty ledger refuses the review or labels it triage. A `REVIEW.md` that exists or differs only on the PR head is ignored.
  3. PR description and commit messages appear only in the Spec lane's input file, labelled untrusted. Hidden HTML, zero-width characters and image alt text are stripped from all reviewed text, and no other lane input file contains PR text.
  4. The finder and verifier agent files declare a read-only `tools:` allowlist that excludes Bash and `gh`.
  5. The user is asked exactly once per run whether the target is their own code or an external PR, and the answer is recorded with the run. An external-PR answer marks the run static-trace-only, so no test, script or hook from the target is executed for the rest of the run; an own-code answer still shows each command for confirmation before it runs.

**Plans**: TBD

### Phase 3: Verified Review in the Terminal

**Goal**: The user runs a review and gets a report in which every finding has been independently checked, reproduced where that is safe, classed by evidence and attributed to its origin. This is the project's core value delivered end to end with one lane, as terminal, JSON and SARIF output.
**Depends on**: Phase 2 (Defects lane build also requires the Phase 1 keep-or-replace approval; reproduction requires the Phase 2 trust prompt)
**Requirements**: LANE-01, VERI-01, VERI-02, VERI-03, VERI-04, VERI-05, VERI-06, OUT-01, OUT-02, OUT-05, CTX-02, SCOPE-03, REPR-01
**Success Criteria** (what must be TRUE):

  1. Reviewing a change runs a Defects lane (correctness, security, tests, ported from ccr.md under the approved table). The terminal report shows Summary / Blocking / Required / Suggestions / Verdict, an advisory verdict, and the areas inspected. Reviewing a directory or the whole repo yields whole-file findings anchored at file:line, and the report states that the target is not a change.
  2. The terminal report, the JSON output and a SARIF 2.1.0 export all render from one versioned JSON document (schema v1, stable finding IDs, deterministic order). Each SARIF result carries the finding's evidence class, severity and origin as result properties.
  3. Each candidate finding is checked by a fresh-context, read-only verifier that receives only claim, file:line and trigger. A planted false finding comes back `refuted` and is dropped and counted; `unverifiable` findings appear in their own list.
  4. Every finding shows an evidence class and an origin (`introduced`, `pre-existing` or `existing`); no Blocking finding is `inferred` or `speculative`. The JSON 0-100 score is derived from the evidence class, and items on the do-not-flag list never appear.
  5. On trusted code with a test runner, a verifier-confirmed traced finding can be reproduced: the main-thread orchestrator shows the minimal failing test command, runs it after confirmation in a throwaway worktree with secrets stripped from the environment, and a failing-as-claimed run upgrades the finding to `reproduced`. On a run marked external PR, nothing from the target is executed and findings stay at `traced` or below.

**Plans**: TBD

### Phase 4: Full Lane Set, Domain Packs and Effort Tiers

**Goal**: A review runs every relevant lane at a user-chosen effort tier, loads domain packs by file type, keeps the axes separate, and shows and caps cost before running.
**Depends on**: Phase 3
**Requirements**: LANE-02, LANE-03, LANE-04, LANE-05, LANE-06, LANE-07, LANE-08, LANE-09, LANE-10, SCOPE-04
**Success Criteria** (what must be TRUE):

  1. The report has separate Defects, Standards and Spec sections, deduplicated within each axis and never reranked against each other. The Spec section lists what it declined to judge, or says that no spec exists. Ponytail findings (needless code, reimplemented stdlib or helpers, over-engineering) are always produced, as their own lane or folded into another.
  2. A change touching, for example, a SQL migration or a Python file loads the matching domain pack and its do-not-flag list, and unrelated packs stay unloaded. The SOTA lane runs only when the tier or a flag enables it.
  3. The user picks `quick`, `standard` or `deep`. Per-lane model and effort follow the tier, the verifier runs on a different tier from the finders, and user overrides take effect.
  4. Before a `deep` run or a directory or repo review, the user sees a cost preview. Directory and repo targets are split into module shards, ordered by risk, each with its own ledger. Caps on lanes, verifier calls and turns are enforced.
  5. When nested subagents are unavailable, lanes run serially in the main thread with a batch self-score, and the report states `mode: serial` and the guarantees lost.

**Plans**: TBD

### Phase 5: PR and MR Review Posting

**Goal**: The user can post a review to a GitHub PR or a GitLab MR as one pending review whose exact content they confirm first, and which never approves or blocks the change.
**Depends on**: Phase 4
**Requirements**: OUT-03, OUT-04, OUT-06, SCOPE-05
**Success Criteria** (what must be TRUE):

  1. `--comment` on a GitHub PR target builds one pending review through `gh api`, rendered from the same JSON document as the terminal report, with `commit_id` pinned. Every inline anchor is valid against `gh pr diff`, and out-of-hunk findings go into the review body.
  2. The user sees the exact payload and its secret-scan result before anything is submitted. Submission happens as `COMMENT` only after confirmation; APPROVE and REQUEST_CHANGES are never sent.
  3. Posting is refused if the PR HEAD changed since the review. Posting the same review again does not duplicate comments (hidden idempotency markers).
  4. `--comment` on a GitLab MR target builds one draft-note review through `glab` from the same JSON document, under the same rules: anchors validated against the MR diff, head SHA pinned, refusal if the head changed, no duplicates on re-post, and the exact payload plus secret-scan result confirmed before anything is published.
  5. `--comment` on a target that is neither a PR nor an MR is refused; terminal, JSON, SARIF and fix output remain available.

**Plans**: TBD

### Phase 6: Fix Mode

**Goal**: The user can opt in to applying verified fixes one at a time, each guarded by tests that run only on trusted code, without the skill committing, pushing or touching dependencies.
**Depends on**: Phase 5
**Requirements**: FIX-01, FIX-02, FIX-03, FIX-04, FIX-05
**Success Criteria** (what must be TRUE):

  1. Fixes are applied only when the user passes the explicit fix flag; a default review never edits files.
  2. Fix mode refuses to start on a dirty working tree or on a run the trust prompt marked as an external PR, and the user confirms the exact test command before it runs.
  3. The fix plan is rendered from the same JSON document as the report. Only `confirmed` findings with a concrete fix are applied, one at a time, in a foreground subagent on a model matched to the fix's complexity, with every confirmation gate in the main thread.
  4. Tests run after each fix; a failing test halts the run and reverts that fix.
  5. After a fix run there are no new commits or pushes, and no dependency, lockfile, CI or config file has changed.

**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Plugin Foundation and ccr Contract | 3/5 | In Progress|  |
| 2. Review Target, Context Ledger and Trust Boundary | 0/TBD | Not started | - |
| 3. Verified Review in the Terminal | 0/TBD | Not started | - |
| 4. Full Lane Set, Domain Packs and Effort Tiers | 0/TBD | Not started | - |
| 5. PR and MR Review Posting | 0/TBD | Not started | - |
| 6. Fix Mode | 0/TBD | Not started | - |
