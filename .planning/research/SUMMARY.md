# Project Research Summary

**Project:** review-code (Critical Code Review Skill, SOTA)
**Domain:** Claude Code plugin shipping an LLM code-review skill: parallel read-only reviewer lanes, a per-finding verifier, and terminal / JSON / PR-comment / fix outputs
**Researched:** 2026-10-17
**Confidence:** MEDIUM overall (STACK HIGH; FEATURES, ARCHITECTURE, PITFALLS MEDIUM)

## Executive Summary

This is a Claude Code plugin, not a service. It bundles one lean `SKILL.md`, on-demand `references/`, a small set of plugin subagents, and an optional hooks file. Mature tools (Anthropic's managed Code Review and `/code-review`, Cursor Bugbot, CodeRabbit, Qodo, Cloudflare's published pipeline) converge on one skeleton: scope and eligibility gate, context gathering, parallel specialist finders, a per-finding verifier that tries to refute each candidate, dedupe and rank, then output projections. Follow that skeleton and differ on verification quality and evidence, not breadth.

Recommended approach: a main-thread orchestrator skill that fans out two read-only agents (one generic `finder` that takes a lens file, one `verifier`), with a retrieval-based context ledger instead of diff-only review. Findings are classed by evidence (what was traced or reproduced), not self-rated numbers. Output is one versioned JSON document from which the terminal report, PR review payload and fix plan are rendered. Posting is one pending review through plain `gh api`, always `COMMENT`, only after explicit confirmation. Fix mode is last and applies only verified findings, one at a time, with tests between each. The only runtime dependency is `gh`, and only for PR modes.

Main risks: verification that rubber-stamps or manufactures findings; prompt injection from reviewed content (PR text, comments, hidden HTML, file and branch names); unsafe PR writes (one out-of-hunk anchor rejects the whole review with 422); execution of untrusted PR code during reproduction or fix; licensing. Licensing is concrete: `docs/ccr.md` is labelled v1.3 with two authors, the upstream posit-dev skill is MIT with a notice that must be kept, and the v1.3-only additions have no verified licence. NOTICE and the licence decision belong in the first commit. Trust decisions (what lanes see, which tools they hold, when code may execute) must be settled in Phases 1 and 2, before any lane prompt is written.

## Key Findings

### Recommended Stack
Claude Code plugin format; Agent Skills spec for the skill; plugin subagents for lanes; `gh` for PR modes only. Plugin is Markdown and JSON, validated with `claude plugin validate --strict`. (STACK.md)

- **Plugin manifest** (`.claude-plugin/plugin.json`, plus `marketplace.json` with `source: "./"`): only `name` required; kebab-case; avoid `claude-` / `anthropic-` prefixes.
- **Agent Skill** (`skills/review/SKILL.md` + `references/`): body under 500 lines; iron law, verification gate and output switch in the first 5,000 tokens (compaction keeps only that).
- **Plugin subagents** (`agents/*.md`): read-only via `tools:`. Plugin agents ignore `hooks`, `mcpServers`, `permissionMode`, so safety cannot live in agent frontmatter.
- **`gh`**: `gh pr view/diff` to read; `gh api --input payload.json` for one atomic review. No `gh-pr-review` extension.
- **Own JSON findings schema v1**: one schema feeds JSON mode, PR-comment builder and fix mode.

Hard constraints:
- No `context: fork` on the main skill. `AskUserQuestion` is stripped from subagents, so confirm-before-post and confirm-before-fix run in the main thread.
- Permission prompts are not the confirmation gate (`auto`, `bypassPermissions` skip them; `-p` has no user).
- No top-level `bin/`. Use `scripts/` with `${CLAUDE_PLUGIN_ROOT}`; pass paths as arguments.
- Claude-Code-only frontmatter keys break claude.ai uploads. Position as a Claude Code plugin.
- Ship no hooks in v1. A JSON-parsing hook needs `jq`, `node` or `python`, none guaranteed.

Version floor: Claude Code 2.1.246 is provisional (MEDIUM; tested on 2.1.302, `gh` 2.102.0). Avoid v1 features with newer minimums (`onFailure: "block"`, `--max-findings`, agent `omitClaudeMd`, `userConfig` options).

### Expected Features

Must have (table stakes):
- Per-finding verification pass before reporting: refute-by-default, fresh context, one finding per verifier. Unverified findings listed separately, never silently dropped.
- Precision bar and do-not-flag list: flag only provable defects, quotable repo-rule breaches, or clear bugs. Exclude pre-existing issues, linter-catchable items, deliberate lint-ignores, unchanged lines, input-dependent maybes.
- Introduced-vs-pre-existing attribution. Pre-existing never in Blocking.
- Severity tiers plus advisory verdict: keep ccr names Blocking / Required / Suggestions. Pre-existing is an attribute, not a tier.
- file:line precision and concrete fix. Suggestion blocks only when the fix fully resolves the issue.
- Full-context review: callers, tests, fixtures, config, CI, conventions inspected. Diff-only never satisfies the gate (user rule in `docs/code-review.md`).
- Repo conventions honoured: CLAUDE.md, AGENTS.md, CONTRIBUTING, base-branch `REVIEW.md`.
- Parallel lanes with dedupe; terminal report; versioned JSON; PR inline comments with consent; eligibility skips (closed, draft, already-reviewed, empty diff).
- Prompt-injection hygiene; read-only reviewers.

Should have (differentiators):
- Evidence-class confidence (reproduced > traced > inferred > speculative), with a derived 0-100 for JSON only.
- Execution-based reproduction where a test runner exists and trust allows it (v1.x).
- Spec axis with a "declined to judge" list. Discovery: issue refs in commits, user-given path, `docs/` or `specs/`. No spec means say so; never invent one.
- Two axes kept separate: Standards and Spec never reranked against each other.
- AI-authored code lane: dependency exists in registry, API exists in installed version, cross-file consistency, tests that assert nothing.
- Domain packs by path or file type: security/OWASP, concurrency, API compatibility, data and migrations, LLM-generated code, plus ccr's Python, R, JS/TS, SQL. Each pack has its own do-not-flag list.
- Base-ref review-rules file (`REVIEW.md`), nested by path.
- Effort tiers (quick / standard / deep) changing lane count, verifier strictness, reproduction and nits together.
- Fix mode: verified-only, one at a time, test after each. Ship last.

Defer (v1.x or v2+):
- Learned-rule memory (Bugbot, Greptile, CodeRabbit). Needs hosted state; out of scope.
- GitLab MR posting (JSON covers it). SARIF export (v1.x, if asked). Eval or seeded-bug corpus (out of scope by user decision). External second-opinion lane (optional hook, not a dependency). PR walkthroughs, sequence diagrams.

Refuse: auto-posting without confirmation; APPROVE / REQUEST_CHANGES events; "guilty until proven" as a finding generator; nit floods by default; raw model confidence as sole filter; whole-repo dumps into lane prompts; review rules read from the PR head; auto-applying every suggestion.

### Architecture Approach

Gate and scope, then context ledger, then finder lanes, then verifier, then aggregator, then projections. Gates (cost, post, fix) live only in the main thread. (ARCHITECTURE.md)

Components:
1. **Orchestrator** (SKILL.md, main thread): sequences stages, owns every user prompt, selects tier and lanes, runs `gh` and fix mode after confirmation.
2. **Scope and context gatherer** (cheap tier, scripts where possible): eligibility, pin `BASE...HEAD`, split and filter diff, risk tier, lane selection, `ledger.json` (callers, tests, fixtures, config, CI, docs, conventions, spec). Empty ledger fails the gate.
3. **Finder lanes** (parallel, read-only): read `context.md`, own diffs, one lens file; write `findings/<lane>.json`. Axes: Defects (correctness, security, tests, packs), Standards (conventions plus smell baseline), Spec (issue vs diff).
4. **Verifier** (one per finding, refute-by-default, read-only): receives only the finding (claim, file:line, claimed trigger) plus repo rules. Never sibling findings, finder reasoning, or PR description.
5. **Aggregator / judge**: dedupe within axis, recategorise, drop refuted, reasonableness filter, produce `report.json`.
6. **Projections** (terminal, JSON, PR payload, fix plan): pure functions of `report.json`; never re-open lanes.

Patterns:
- File-based handoff: lanes return a path and count, not full output.
- Find (recall) then verify (precision) on different model tiers. This approximates cross-model review; it is not equivalent.
- Risk-tiered fan-out with effort rules written into the orchestrator prompt (agents over-spawn on trivial input otherwise).
- Context ledger as a hard gate; retrieval on demand, not pasted prompts.
- Untrusted-input boundary: PR text is data. Correctness, security and tests lanes get no PR description. Intent goes only to Spec, labelled untrusted.

Writers: `report.json` to (G2) pending review payload to GitHub; `report.json` to (G3) fix loop to working tree. Nothing else mutates.

Gates: G1 cost preview (full or deep only); G2 post (exact comment list, body-only items, target PR and HEAD SHA, "pending, not submitted"); G3 fix (ordered plan, confirmed findings with a fix, repro method, test command). Per-fix stop on test failure.

Serial fallback (required): if nested spawning fails or the skill runs inside a subagent, run lanes serially in the main context with a batch self-score in place of per-finding verifiers. Report `mode: serial` and the lost guarantees.

### Critical Pitfalls

1. **Verifier rubber-stamps or manufactures findings.** Seeing the finder's reasoning produces agreement; "explain and fix" prompts worsen over-rejection (arXiv 2603.00539). Reflexion loops raised recall but cut SNR sharply (arXiv 2603.11078). Prevention: fresh context; verifier gets only `{file:line, claim, trigger}`; binary falsifiable prompts; verifier must be able to output REFUTED (test with a planted false finding); no self-reflection loops, add lanes not iterations.

2. **Prompt injection and exfiltration via the output channel.** PR titles, hidden HTML comments and file names reached secrets and posted them in a public comment in documented incidents (Comment and Control, April 2026). Prevention: wrap reviewed content as data; strip hidden HTML, zero-width characters, image alt text before any lane; lanes read-only, no Bash, no `gh`; outbound payload preview with secret-pattern scan; no headless auto-trigger in v1; file and branch names untrusted.

3. **Anchoring on PR description and commit messages.** "Bug-free" framing cut vulnerability detection by 16 to 93 points across five of six models (arXiv 2603.18740, MEDIUM); 17 of 17 reintroduced vulnerabilities were accepted by real pipelines. Prevention: architectural. Detection lanes never receive PR text; redaction plus "ignore metadata" beat instruction alone.

4. **False positives and fatigue.** Reported acceptance rates are low (RevMate 8.1% / 7.2%, LOW, secondhand). Direction consistent: precision expensive, recall cheap. Prevention: evidence required for Blocking/Required (file:line, quoted code, trigger, guard checked); do-not-flag list; tier caps, Suggestions collapsed; verifier assigns severity against a rubric; nits opt-in.

5. **Unsafe PR posting.** One out-of-hunk anchor rejects the whole review (422). Comment spam triggers secondary rate limits. LLM approve or block is a policy failure. Prevention: validate every `(path, line, side)` against `gh pr diff` (`line`/`side`, never `position`); out-of-hunk findings go to body; one `POST /reviews` with `commit_id` = reviewed HEAD; event omitted (PENDING), `COMMENT` only after confirmation; refuse if HEAD changed; per-finding hidden marker for idempotency.

6. **Executing untrusted PR code.** Reproduction and post-fix tests run the PR's own tests, `package.json` scripts and hooks: arbitrary code with the user's credentials. Not solved by read-only lanes. Prevention: ask once per run whether code is trusted (own branch) or untrusted (external PR); untrusted means static trace only; execution only after confirming the exact command, preferably in a throwaway worktree with secrets stripped from env; never run install or build scripts for review.

Also in PITFALLS.md: fix-mode regressions (fix only cited file, no new deps, baseline tests before and after, one pass); cost blow-up (triage gate, lane caps, printed estimate); non-determinism (stable IDs; "not found" is not proof of absence); licensing; triggering and name collision.

## Conflicts Resolved Across the Four Files

| Topic | Conflict | Resolution |
|---|---|---|
| Agent file count | STACK: 4 agents. ARCHITECTURE: 2 (`finder.md` with lens file, `verifier.md`). | 2 agents (ARCHITECTURE). Lane differences are task-message data; model set per invocation. |
| Verifier tools | STACK allows restricted Bash. PITFALLS 4, 8 and ARCHITECTURE Pattern 7 forbid Bash in lanes. | Read, Grep, Glob only. Orchestrator runs commands, after the trust prompt. |
| Verification enum | FEATURES: reproduced / evidenced / refuted / unverifiable. ARCHITECTURE: confirmed / unconfirmed / refuted / unverified + `method`. PITFALLS: CONFIRMED / REFUTED / UNVERIFIABLE + ladder. | Two axes. Status: `confirmed \| refuted \| unverifiable`. Evidence class: `reproduced \| traced \| inferred \| speculative`. Only reproduced or traced may be Blocking. Lock in Phase 1. |
| Confidence number | PROJECT.md asks for a score. FEATURES, PITFALLS: derive from evidence. | Evidence class is the gate. Derived 0-100 for JSON only. Never self-rated. |
| Verifier model | STACK: `inherit`. ARCHITECTURE: higher tier. | Different tier from finders; record the actual model per verdict. No hard-coded model name in the plan. |
| Lane count | STACK 3-5; FEATURES 6; ARCHITECTURE scales with risk. | Risk-tiered fan-out. Phase 5 sets counts (no eval corpus). |
| Dedupe marker | STACK: per-review head-SHA marker. PITFALLS: per-finding fingerprint. | Both. Fingerprint for dedupe and re-review; head-SHA marker and refuse-on-mismatch for posting. |
| Verdict wording | ARCHITECTURE verdict includes "Approve" / "Request Changes". | Advisory terminal text only; never maps to GitHub `event`. |
| Headless posting | STACK: `--comment` opt-in under claude-code-action. PITFALLS 4: no headless auto-trigger in v1. | v1 headless is report or JSON only. CI posting is a user decision. |
| Flag name | ARCHITECTURE `--post`; STACK and FEATURES `--comment`. | `--comment` (matches bundled `/code-review`). |
| Severity tiers | ARCHITECTURE adds "Noted". Others: three tiers + pre-existing attribute. | Three tiers + `origin: introduced \| pre-existing`. Drop "Noted" unless needed. |
| Script runtime | ARCHITECTURE: scripts in `scripts/` (split, validate, payload). STACK: no jq/node/python guaranteed. | Open; Phase 2 research. Check `gh --jq` plus POSIX shell. |

## Implications for Roadmap

Order merges ARCHITECTURE's eight-step build order with PITFALLS' phase mapping. Trust decisions come first; they are expensive to change once prompts exist.

### Phase 1: Foundation, legal and contracts
- **Rationale:** schema, rubric and tool allowlists are the contract for everything after; licensing in first commit; naming affects triggering.
- **Delivers:** plugin skeleton (`validate --strict` passing); LICENSE and NOTICE with Posit MIT notice; name decision; schema v1 with two-axis status and evidence; severity rubric; do-not-flag list; ccr keep-or-replace table (user reviews); description checked against the user's real skill list.
- **Addresses:** precision bar, severity tiers, evidence model, JSON schema.
- **Avoids:** Pitfalls 13 (licensing), 14 (name collision, listing budget), 15 (ccr persona and iron law).
- **Research flag:** licence of v1.3-only additions needed before publishing that text. Otherwise standard.

### Phase 2: Scope, context ledger and trust boundary
- **Rationale:** context must exist before finders; untrusted-input boundary before any lane sees PR text.
- **Delivers:** eligibility and fail-closed ref pinning; diff split and filter (lockfiles, minified, generated); risk tier; context ledger with empty-ledger gate; `references/untrusted-input.md`; read-only per-lane tool allowlists; detection lanes never receive PR description or commit messages.
- **Addresses:** full-context review, skip checks, injection hygiene, cost controls.
- **Avoids:** Pitfalls 3, 4, 7.
- **Research flag:** NEEDS RESEARCH. Portable caller and test discovery without codegraph; script runtime decision.

### Phase 3: One lane end to end, plus terminal report
- **Rationale:** one lane proves schema, ledger and output before parallelism multiplies defects.
- **Delivers:** correctness lane ported from ccr.md with keep-or-replace applied; terminal report (Summary / Blocking / Required / Suggestions / Verdict, withheld count); SKILL.md under 500 lines.
- **Addresses:** terminal report, file:line precision, conventions.
- **Avoids:** Pitfalls 6, 15.
- **Research flag:** standard.

### Phase 4: Verifier and trust model
- **Rationale:** verifier is the core value; trust prompt must exist before any execution.
- **Delivers:** per-finding read-only verifier, fresh context, refute-by-default, verdict with evidence class and recorded model; refuted dropped and counted; unverifiable listed separately; trust prompt (trusted vs untrusted); static trace default; anti-sycophancy rule (retract only on new code or test evidence).
- **Addresses:** verification, evidence-class confidence, opt-in reproduction.
- **Avoids:** Pitfalls 1, 2, 8, 16 (warn when session wrote the diff).
- **Research flag:** NEEDS RESEARCH. Static-trace verdict contents; when dynamic repro is safe. Planted-false-finding fixture.

### Phase 5: Parallel lanes, aggregation, packs, effort tiers
- **Rationale:** parallelism after verifier, since it multiplies raw findings.
- **Delivers:** lanes (correctness, security, tests, spec, standards) plus packs by path and file type; merge and dedupe before verification; aggregator producing `report.json`; effort tiers; caps on lanes, verifier calls, turns; G1 cost preview; serial fallback with `mode: serial`.
- **Addresses:** lanes, spec axis, packs, AI-authored lane, effort tiers, dedupe.
- **Avoids:** Pitfalls 5, 6, 11.
- **Research flag:** measurement after build, not research. Cost telemetry in report.

### Phase 6: JSON output and PR posting
- **Rationale:** projections after `report.json` is stable; posting is highest external blast radius.
- **Delivers:** versioned JSON (stable IDs; deterministic ordering); PR read via `gh pr view/diff`; anchor validation; out-of-hunk to body; one pending review via `gh api --input`; `COMMENT` only after confirmation; hidden markers; SHA-mismatch refusal; outbound secret scan; G2 dry-run payload; base-ref `REVIEW.md`.
- **Addresses:** JSON mode, PR comments with consent, re-review awareness.
- **Avoids:** Pitfalls 2, 5, 9, 12.
- **Research flag:** NEEDS RESEARCH. `line`/`side`/`start_line` for multi-line and renamed files; fork PR permissions; existing-comment dedupe.

### Phase 7: Fix mode
- **Rationale:** highest risk; depends on verified findings, trust model, test discovery.
- **Delivers:** G3 plan (confirmed findings with a fix only); clean-tree or stash precondition; order Blocking, simple, complex; per finding: reproduce, apply, run suite, next; halt and revert on test failure; per-finding status (fixed / skipped / no-change-needed); no commits or pushes; no new deps, no lockfile, CI or config edits; unconfirmed returned to user.
- **Addresses:** verified-only, one-at-a-time, test-gated apply.
- **Avoids:** Pitfalls 8, 10.
- **Research flag:** NEEDS RESEARCH. Test-command discovery from CI and config; edit behaviour under permission modes. Background lanes must not edit.

### Phase Ordering Rationale
- Trust and contracts first (1, 2): untrusted-input boundary, tool allowlists and schema shape every prompt.
- Verifier before parallelism (4 before 5).
- Output after canonical report (6 after 5).
- Mutation last (6, 7).
- Reference packs can be authored in parallel from Phase 3 (they load on demand).

### Research Flags
Needs research: Phase 2 (caller/test discovery; script runtime), Phase 4 (static-trace contents; safe dynamic repro), Phase 6 (line/side semantics; fork PRs; comment dedupe), Phase 7 (test-command discovery; edit behaviour).
Standard: Phase 1 (licence is a legal review item), Phase 3, Phase 5 (tuning is measurement).

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Formats read from official docs and installed plugins. Version floor 2.1.246 provisional. `SubagentStop` loop-guard field unverified. |
| Features | MEDIUM | Vendor docs and repo files; academic numbers are preprints or abstracts; most vendor numbers LOW, not used as targets. |
| Architecture | MEDIUM | Skeleton corroborated across Anthropic, Cloudflare, plugin sources. Thresholds are starting points. Greptile and CodeRabbit internals unverified. |
| Pitfalls | MEDIUM | Incidents and GitHub API behaviour corroborated. Cost, fatigue, FP rates mostly vendor or secondary (LOW). |

Overall: MEDIUM. Direction well supported; quantitative thresholds not; no eval corpus by user decision.

### Gaps to Address
- **Licence of ccr v1.3 additions (blocks Phase 1 publishing):** `docs/ccr.md` has no `license:` field; authors Garrick Aden-Buie and Ziad Hassan. Upstream posit-dev skill is MIT (Posit PBC) and must keep its notice. ClawHub listing by `@ziad-hsn` is MIT-0; relationship to upstream unestablished. Diff `docs/ccr.md` against posit main.
- **Naming:** `review-code` (STACK) vs `critical-review` (ARCHITECTURE). Do not reuse `critical-code-reviewer`. `review` may collide with bundled `/review`. Decide in Phase 1; test triggering.
- **Triggering budget:** listing truncates descriptions when over budget. Check `--debug` and `/skill-doctor`.
- **Script runtime:** unresolved; blocks Phase 2 portability.
- **Version floor:** 2.1.246 MEDIUM; confirm at build.
- **Verifier tools:** read-only chosen; confirm static trace suffices for cross-file and test-coverage claims.
- **Evidence-class to number:** design choice, not validated.

## Open Decisions (user)
1. Disclosure line ("review assisted by...") in posted reviews. Conflicts with the no-AI-attribution rule. Recommend default off, configurable. Credit in README, NOTICE, frontmatter regardless.
2. Plugin's own licence. MIT simplest. Apache-2.0 only if substantial Apache text is incorporated. Do not copy `anthropics/claude-code` command text (all rights reserved); do not borrow `claude-security`.
3. Plugin and skill name; avoid `review`?
4. Headless/CI posting in v1: recommend report and JSON only.
5. Smoke fixtures (about 10 should-trigger, 10 should-not, one injection) as scope boundary, not a corpus.
6. Upstream drift: adopt posit main's accessibility and external-PR workflow?
7. ccr.md keep-or-replace table reviewed before Phase 3.

## Sources
Primary (HIGH): Claude Code docs (plugins reference, marketplace, publishing, skills, subagents, hooks, code review); agentskills.io spec; GitHub REST pulls/reviews; installed plugins on disk (Anthropic code-review, pr-review-toolkit, security-guidance; claude-security not copied); claude-code-action action.yml and docs/usage.md; local `docs/ccr.md`, `docs/code-review.md`, `docs/matt.md`, `PROJECT.md`.
Secondary (MEDIUM): arXiv 2603.00539, 2603.11078, 2603.26130, 2603.18740, 2502.20747, 2509.01494, 2603.23448, 2508.18771, 2406.10279; Cloudflare AI code review; Anthropic multi-agent research; Cursor Bugbot; Qodo PR-Agent docs; Comment and Control incident reporting (SecurityWeek, CSA, Vectara); posit-dev/skills SKILL.md and LICENSE (MIT).
Tertiary (LOW, direction only): vendor benchmark numbers (Qodo, Anthropic, Graphite, Greptile, CodeRabbit); Augment aggregator figures; RevMate; practitioner blogs.

Full URL lists are in each research file.

---
*Research completed: 2026-10-17*
*Ready for roadmap: yes, with Open Decisions flagged to the user*
