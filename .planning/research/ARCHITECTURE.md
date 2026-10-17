# Architecture Research

**Domain:** LLM-agent code-review skill/plugin (Claude Code), parallel reviewer lanes + verifier
**Researched:** 2026-10-17
**Confidence:** MEDIUM (see "Confidence and Gaps"; confidence tags below come from `classify-confidence`: plain web providers = LOW, cross-verified = MEDIUM; items marked [M] are corroborated by 2+ independent sources, [L] are single-source or secondary)

## Standard Architecture

Every SOTA system studied converges on the same skeleton, regardless of vendor: **gate/scope -> context gathering -> parallel specialist finders -> per-finding verification -> dedupe/rank/judge -> output projection(s)**. The differences are in how much is a fixed pipeline vs an agent loop, and where the confirmation gate sits.

### System Overview

```
 USER / CI  -- target (diff | branch | PR# | path), flags (--post, --fix, --json, effort)
     |
 ┌───▼───────────────────────────────────────────────────────────────────────┐
 │ L0  ORCHESTRATOR (SKILL.md, main thread, foreground)                       │
 │     owns: ALL user prompts / confirmation gates, fix mode, posting         │
 ├───────────────────────────────────────────────────────────────────────────┤
 │ L1  SCOPE + CONTEXT     (cheap tier; deterministic scripts where possible) │
 │     eligibility -> pin refs -> split diff -> risk tier -> lane selection   │
 │     -> context ledger (callers, tests, config, CI, docs, rules, spec)      │
 │     writes: $RUN/context.md  $RUN/diffs/<path>.diff  $RUN/ledger.json     │
 ├───────────────────────────────────────────────────────────────────────────┤
 │ L2  FINDER LANES (parallel subagents, mid tier, read-only)                 │
 │   DEFECTS axis:  correctness | security | tests | +domain packs            │
 │   STANDARDS axis: repo conventions + smell baseline                        │
 │   SPEC axis:      originating issue/spec vs diff                           │
 │     each reads context.md + its own diffs, writes $RUN/findings/<lane>.json│
 ├───────────────────────────────────────────────────────────────────────────┤
 │ L3  VERIFIER (1 subagent PER finding, top tier, refute-by-default)         │
 │     reads ONE finding + code; returns confirmed|unconfirmed|refuted        │
 ├───────────────────────────────────────────────────────────────────────────┤
 │ L4  AGGREGATOR / JUDGE (main thread or top-tier agent)                     │
 │     dedupe (within axis) -> recategorize -> reasonableness filter -> verdict│
 │     single source of truth: $RUN/report.json  (Finding[] + verdict)        │
 ├───────────────────────────────────────────────────────────────────────────┤
 │ L5  OUTPUT PROJECTIONS (pure functions of report.json)                     │
 │     terminal (ccr format) | JSON | PR review payload | fix plan            │
 └───────────────────────────────────────────────────────────────────────────┘
        │ gate G2 (user approves comments)      │ gate G3 (user approves fixes)
        ▼                                       ▼
   gh api POST /pulls/{n}/reviews          fix loop (main thread, one at a time,
   (PENDING draft, never APPROVE)           tests after each, revert on failure)

 Stores (all under one per-run scratch dir $RUN, never inside the repo):
   context.md | ledger.json | diffs/ | findings/*.json | verdicts/*.json | report.json
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation (evidence) |
|-----------|----------------|-----------------------------------|
| Orchestrator | Sequence stages, enforce gates, pick effort/tier, own every user prompt | SKILL.md procedure. Anthropic `/code-review` runs the review as a background forked subagent and returns findings to the main thread [M] |
| Eligibility/Scope | Skip closed/draft/trivial/already-reviewed; pin base...HEAD; fail fast on bad ref or empty diff | Haiku eligibility step in Anthropic's plugin; Matt's "fail here, not inside two parallel sub-agents" [M] |
| Context gatherer ("cartographer") | Build the context ledger: changed symbols -> callers, tests, fixtures, config, CI, docs, conventions, spec | alexop Phase 0 cartographer (Explore agent, schema-bound) [L]; Qodo "context collector" + intent agent before analysis [L]; CodeRabbit codegraph/code index [L]; Greptile prebuilt call graph [M] |
| Finder lanes | Each hunts one defect class/axis within its own context window | Anthropic: 4 parallel agents; Cloudflare: up to 7 specialists; Qodo 2.0: expert agents with dedicated contexts; Matt: 2 axes in parallel [M] |
| Verifier | Independently try to refute each candidate finding against the code | Anthropic command: one validation subagent per issue (Opus for bugs, Sonnet for CLAUDE.md); ultrareview: "every finding independently reproduced and verified"; alexop: 1 fresh Opus per finding [M] |
| Aggregator/Judge | Dedupe, recategorize, drop speculative/convention-contradicted findings, set verdict | Cloudflare coordinator judge pass (dedupe, re-categorize, reasonableness filter); Qodo judge agent [M] |
| Output projections | Render one canonical report into terminal / JSON / PR / fix plan | OCR: markdown artifacts + optional GitHub post; Anthropic: terminal summary then `--comment` [M] |
| Poster | Map findings to diff positions, batch into one review, honor gate | GitHub REST `POST /pulls/{n}/reviews` (see Integration Points) |
| Fixer | Apply verified fixes one at a time, test after each | Matt/`docs/code-review.md` "reproduce before fix"; superpowers receiving-code-review (verify, then one item at a time, test each) [M] |

## Recommended Project Structure

Standalone plugin repo. Plugin name must not start with `claude-`/`anthropic-` (validator error) [M: plugins-reference]. Suggested name `critical-review` (decision, see Open Questions). Only `plugin.json` lives in `.claude-plugin/`; everything else at root.

```
critical-review/
├── .claude-plugin/plugin.json      # name, version, author credit (ccr authors), license
├── LICENSE / NOTICE                # attribution for ccr.md (Aden-Buie, Hassan) + license check
├── skills/review/
│   ├── SKILL.md                    # LEAN (<500 lines): stages, gates, effort table, pointers
│   ├── references/                 # one level deep from SKILL.md; >100 lines => TOC at top
│   │   ├── finding-schema.md       # Finding/Verdict/Report fields + severity mapping (loaded by every stage)
│   │   ├── severity-rubric.md      # ccr tiers + verdict rubric
│   │   ├── signal-rules.md         # "what NOT to flag" shared block for all finders
│   │   ├── context-ledger.md       # what the gatherer must inspect and record
│   │   ├── lanes/                  # one lens file per lane (loaded by that lane only)
│   │   │   ├── correctness.md  security.md  tests.md  spec.md  standards.md
│   │   │   └── concurrency.md  api-compat.md  data-migrations.md  llm-code.md  docs-vs-source.md
│   │   ├── langs/                  # python.md r.md js-ts.md sql.md ... (loaded per detected language)
│   │   ├── verifier.md             # refute-by-default protocol
│   │   ├── output-terminal.md  output-json.md  output-pr.md  fix-mode.md
│   │   └── untrusted-input.md      # delimiting/sanitizing rules for PR text
│   └── scripts/                    # deterministic, no LLM: split-diff, risk-tier, validate-findings,
│                                   #   map-lines-to-diff, build-review-payload
└── agents/
    ├── finder.md                   # ONE generic read-only finder; lane = lens file path in task message
    └── verifier.md                 # ONE read-only verifier
```

### Structure Rationale

- **Two agent files, many lens files.** The subagent `model` is resolvable per invocation (per-invocation param beats frontmatter) [M: sub-agents docs], so lane specialization and model tier are *data* passed in the task message, not separate agent files. Fewer files to maintain; lenses stay progressive-disclosure references. (Ponytail: smallest structure that works.)
- **SKILL.md < 500 lines, references one level deep, TOC on >100-line files.** Direct from Anthropic's skill-authoring guidance; deeper nesting makes Claude `head -100` partial reads [M: platform docs].
- **Scripts for fragile/deterministic work** (diff splitting, line mapping, schema validation, payload build): executed, not read; saves tokens, removes LLM variance [M: best-practices "Provide utility scripts"].
- **Per-run scratch dir outside the repo** keeps the working tree clean (fix mode requires a clean tree) and matches "write temporary files only to scratchpad".
- **Plugin agents ignore `hooks`, `mcpServers`, `permissionMode`** [M: sub-agents docs] -> safety cannot be delegated to agent frontmatter; restrict via `tools:` allowlist and keep every mutation in the orchestrator.

## Architectural Patterns

### Pattern 1: Orchestrator-Workers with file-based handoff
**What:** Lanes write structured findings to files and return only a path + count; the orchestrator never re-prints full lane output.
**When to use:** Always (>=2 lanes).
**Trade-offs:** + avoids "game of telephone" loss and token copying (Anthropic multi-agent research system [M]); + Cloudflare writes per-file patches to a diff directory and one `shared-mr-context.txt` instead of embedding, because duplicating shared context across 7 reviewers multiplies cost ~7x [L, single source]. - Needs a fixed schema and a validator.
**Example:**
```text
orchestrator -> finder(task): "lens=$PLUGIN/references/lanes/security.md
                               ctx=$RUN/context.md diffs=$RUN/diffs/ out=$RUN/findings/security.json"
finder      -> returns: "findings/security.json: 3 findings (1 blocking)"
```

### Pattern 2: Find (recall) then Verify (precision), one verifier per finding
**What:** Lanes are tuned for recall; a separate agent per finding, on a different tier, assumes the finding is wrong and tries to refute it with `file:line` evidence.
**When to use:** Every Blocking/Required finding. Suggestions may skip verification and be tagged `unverified` (logged, not dropped) - the alexop design [L].
**Trade-offs:** + Anthropic (command + service + ultrareview), Cloudflare judge, Qodo judge, CodeRabbit verification agents all converge here [M]. + alexop: a first version with a *batch* coordinator kept 43 of 43 findings ("verification theater"); per-finding, cross-model, refute-by-default dropped 14 false positives [L]. - Cost scales with finding count -> cap verified set (see Token Budget).
**Cross-model caveat:** Greptile measured same-model review worse than cross-model (recall ~50-54% same vs ~60-62% cross on P0/P1) [L: vendor-internal]. Cross-*vendor* routing is not portable inside Claude Code; the portable approximation is a different **tier** (Opus verifier over Sonnet finders). State this as an approximation, not an equivalent.

### Pattern 3: Three axes, lanes merge only inside an axis
**What:** Axes = Defects, Standards, Spec. Lanes inside Defects (correctness, security, tests, domain packs) dedupe and rank together. Standards and Spec are reported in separate sections and never merged or reranked across axes.
**Why:** Resolves the conflict between Matt's "do not merge or rerank across axes" (a change can pass one and fail the other) and Cloudflare/Anthropic's dedupe-and-rank (needed because overlapping lanes flag the same bug). Both are right at different granularity.

### Pattern 4: Risk-tiered fan-out with explicit effort rules in the prompt
**What:** Cheap deterministic classifier picks how many lanes run; the rules are written into the orchestrator prompt because agents mis-judge effort (Anthropic: spawned 50 subagents for trivial queries until effort-scaling rules were embedded) [M].
**Reference tiers (Cloudflare):** trivial (<=10 changed lines, <=20 files) = coordinator + 1 generalist; lite (<=100 lines, <=20 files) = 4; full (>100 lines or >50 files) = up to 7+; security-sensitive paths (`auth/`, `crypto/`) always force full [L: single detailed source; thresholds are a starting point, not validated].
**Anthropic analogue:** "large or complex changes get more agents and a deeper read; trivial ones a lightweight pass"; org-level Quick/Standard/Deep levels [M].

### Pattern 5: Context ledger as a hard gate (diff-only never passes)
**What:** Before any finder runs, a gatherer must produce `ledger.json` listing, per changed symbol: callers inspected, tests found, fixtures/config touched, CI jobs, docs mentioning it, conventions files, spec source. The final report prints the ledger summary as evidence. Empty ledger = gate fails, review aborts or is labeled "triage only".
**Why:** `docs/code-review.md` forbids diff-only gates; SWR-Bench found "lack of contextual understanding" is the top false-positive cause (48% of 100 sampled FPs) [M: arXiv]; Anthropic's bug agent is deliberately diff-only ("do not read extra context") which buys precision but conflicts with the project rule -> here the *ledger* supplies context and finders are instructed to flag only what they can ground in it.
**Portability:** Greptile's prebuilt graph / CodeRabbit's code index are service-side and not portable. Use on-demand `grep`/`git grep`/LSP; treat codegraph/Serena as optional accelerators only (PROJECT.md constraint).

### Pattern 6: Single canonical report; outputs are projections
**What:** Aggregator emits `report.json` (schema below). Terminal report, JSON mode, PR payload, and fix plan are pure renderings of it. Prevents the four modes from diverging.

### Pattern 7: Gates live only in the main thread, after lanes return
**What:** Subagents lose `AskUserQuestion` and can't prompt [M: sub-agents docs]; background subagent prompts surface in the main session. Therefore: lanes and verifiers are read-only and non-interactive; G1 (cost/scope), G2 (post), G3 (fix) are orchestrator steps.

### Pattern 8: Untrusted-input boundary
**What:** PR title/body/comments/diff text are data, never instructions. Wrap in delimited blocks, strip boundary tags case-insensitively (Cloudflare strips `mr_input`, `mr_body`, ... tags [L]); read rules files (`REVIEW.md`, `AGENTS.md`, `CLAUDE.md`) from the **base** branch only, as Anthropic does for `REVIEW.md` ("never from the pull request's own changes") [M]. Research shows PR titles, HTML comments, delimiter injection, and PR-supplied `AGENTS.override.md` have all steered review agents into exfiltration or false approval [L->M: CSA research note + arXiv]. See `references/untrusted-input.md`.
**Resolves ccr "artifact only" vs. PR description:** correctness/security/tests lanes get *no* PR description (judge the code only). Intent (issue/spec) goes only to the Spec lane, labeled untrusted.

## Data Flow

### Request Flow

```
target + flags
   ↓
[G0 eligibility/scope] -- bad ref / empty diff / draft / already reviewed --> stop with reason
   ↓ pin BASE_SHA...HEAD_SHA (three-dot, merge-base)  [Matt step 1]
[risk tier + lane selection]  (script: lines, files, sensitive paths, languages)
   ↓
[context gatherer] --> context.md, diffs/, ledger.json   (gate: ledger non-empty)
   ↓
[G1 cost/scope confirm]  only if tier=full+deep, or est. tokens > threshold
   ↓ fan-out (<=20 concurrent; serial fallback)
[finders x N] --> findings/<lane>.json   (schema-validated by script; invalid => retry once, then drop lane w/ note)
   ↓ merge candidates, pre-dedupe by (path, overlapping range)   [script]
[verifiers x M] --> verdicts/<id>.json   (M capped; Suggestions skip)
   ↓ keep confirmed; unconfirmed => listed separately; refuted => dropped (counted)
[aggregator/judge] --> report.json  (dedupe within axis, recategorize, reasonableness filter, verdict)
   ↓
terminal report (default)  ──┬── --json: emit report.json
                             ├── --post: [G2] -> build payload -> PENDING review
                             └── --fix : [G3] -> fix loop
```

### Finding Schema (everything depends on this; the JSON mode is this, serialized)

```jsonc
// Finding (finder output, then enriched by verifier/aggregator)
{
  "id": "F-0007",                       // stable within run; fingerprint = hash(path, root-cause)
  "axis": "defects | standards | spec",
  "lane": "correctness | security | tests | concurrency | api-compat | data | llm-code | docs | standards | spec",
  "category": "bug|security|performance|maintainability|test|style|documentation|spec-gap|scope-creep|other", // superset of OCR delegate enum
  "severity": "blocking | required | suggestion | noted",   // ccr tiers; see mapping table
  "origin": "introduced | pre-existing",                     // Anthropic's third marker (purple "pre-existing")
  "path": "src/auth/session.ts",
  "start_line": 142, "end_line": 148,   // new-file lines; null if not line-addressable
  "title": "Token refresh races with logout",
  "why": "failure mode, not just the symptom",
  "evidence": [ {"path":"src/auth/logout.ts","line":31,"quote":"..."} ],   // file:line citations; REQUIRED
  "fix": { "text": "...", "patch": "unified diff or null", "committable": false }, // committable ONLY if applying it fully resolves
  "spec_ref": "issue #45 line: ...",          // spec axis only: quoted requirement
  "standard_ref": "CONTRIBUTING.md: rule",    // standards axis only; smell baseline => "judgement call"
  // --- set by verifier ---
  "verification": { "status": "confirmed | unconfirmed | refuted | unverified",
                    "method": "static-trace | repro-test | cannot-determine",
                    "evidence": [ ... ], "confidence": 0-100, "model": "opus" },
  // --- set by aggregator ---
  "dup_of": null, "reported": true
}
// Report: { "target":{base,head,...}, "tier":"lite", "ledger_summary":{...}, "findings":[...],
//           "dropped":{"refuted":n,"filtered":n}, "verdict":"Request Changes|Needs Discussion|Approve",
//           "coverage":{"lanes_run":[...], "lanes_skipped":[{lane,reason}]} }
```

**Severity mapping (so ccr tiers survive and external formats are derivable):**

| ccr tier (kept) | Anthropic service | Cloudflare | OCR delegate | superpowers |
|-----------------|-------------------|------------|--------------|-------------|
| Blocking | Important (red) | critical | critical/high | Critical |
| Required | Important/Nit boundary (repo-tunable) | warning | medium | Important |
| Suggestions | Nit (yellow) | suggestion | low | Minor |
| Noted (mention once, capped) | Nit capped by REVIEW.md | - | low | Minor |
| `origin: pre-existing` | Pre-existing (purple) | - | - | - |

**Confidence stays derived, not self-asserted.** Position: the *gate* is categorical (`confirmed` with cited evidence); the 0-100 number is reporting-only. Evidence: Anthropic's own plugin evolved from README "score 0-100, drop <80" to the current command's binary validation subagents with no numeric threshold [M: README vs command, both fetched]; Qodo's self-reflection 0-10 score + threshold is the cheap single-context alternative (second LLM call over the whole batch, 0 = incorrect/removed, optional cutoff `suggestions_score_threshold`) [M: PR-Agent docs, primary]. Use the Qodo-style batch score only in the serial fallback.

### State Management

No persistent state in v1. Re-review continuity comes from embedding a finding fingerprint in each posted comment and reading prior bot comments (treated as untrusted data). Anthropic's later reviews "focus on what changed since the last review" and auto-resolve fixed threads; Cloudflare omits fixed findings and re-emits unfixed ones [M]. Learned-rule stores (Bugbot, Greptile memory, Qodo, CodeRabbit learnings) need persistence/telemetry and are out of scope; a base-branch `REVIEW.md` is the portable substitute.

### Key Data Flows

1. **Intent flow:** issue/spec text -> Spec lane only (untrusted). Never to Defects lanes.
2. **Rules flow:** base-branch `REVIEW.md`/`CLAUDE.md`/`CONTRIBUTING.md` -> Standards lane + signal-rules overrides to all lanes.
3. **Evidence flow:** finder `evidence[]` -> verifier (re-checks citations) -> report -> PR comment "Why this was flagged" (Anthropic collapsed section) / terminal "File:Line - Problem - Fix - Why".
4. **Mutation flow:** report.json -> (G2) payload -> GitHub; report.json -> (G3) fix loop -> working tree. Nothing else mutates anything.

## Scaling Considerations

Scale here is *change size*, not users. Costs are reported by the vendors for *their* stacks; use as order-of-magnitude only.

| Scale | Architecture adjustments |
|-------|--------------------------|
| Trivial (<=10 lines) | 1 generalist finder + verifier on any Blocking/Required. Cloudflare avg $0.20 [L] |
| Lite (<=100 lines) | 3-4 lanes (Defects x2, Standards, Spec if spec found). Cloudflare avg $0.67 [L] |
| Full (>100 lines / >50 files / sensitive paths) | All applicable lanes + domain packs, deeper verify. Cloudflare avg $1.68 [L]; Anthropic managed service $15-25/review, ultrareview $5-25 [M] |
| Whole-repo / huge diff | Chunk by module; cap per-lane files; ultrareview refuses >500 files / 8,000 lines [M]. Do not silently truncate: report `lanes_skipped` |

### Token Budget and Cost Controls

1. **Filter before reading:** drop lockfiles, minified assets, source maps, generated files (keep DB migrations) - Cloudflare [L]; PR-Agent additionally prunes delete-only hunks, prioritizes by language then token count, and uses a safety buffer (3 context lines per hunk) [M: primary docs].
2. **Share, don't copy:** one `context.md`; per-file diffs in a directory; each lane reads only files in its domain.
3. **Multi-agent is ~15x chat tokens**; token use explains ~80% of performance variance on their eval; coding tasks with heavy shared context fit multi-agent poorly [M: Anthropic multi-agent research]. Hence tiering + caps, not "max lanes always".
4. **Cap the verified set** (e.g. top-N by severity, default ~15); overflow listed as `unverified`, not dropped.
5. **Per-lane bounds:** use agent `maxTurns` as the portable analogue of Cloudflare's 5-min/10-min per-reviewer and 25-min global limits [L for numbers; maxTurns M].
6. **Cost preview at G1** for full tier: lanes x est. tokens (diff + ledger). Anthropic's ultrareview shows scope + estimated cost before launch [M].
7. **Best value from repeated sampling:** SWR-Bench Self-Agg n=5 (5 runs + 1 merge call) reached F1 20.5% on Flash vs 19.4% for single-pass Pro at lower cost; gains flatten past n=5 [M: arXiv]. Suggests "deep" = N independent samples of the *same* lane then merge, as an alternative to adding more distinct lanes. Gain is recall; false positives remain the bottleneck -> verifier still required.

### Model Tiering per Stage

| Stage | Tier | Evidence |
|-------|------|----------|
| Eligibility, rules-file discovery, ledger/mechanical gathering | Haiku | Anthropic plugin steps 1-2 use Haiku [M] |
| PR summary, Standards/CLAUDE.md lane | Sonnet | Anthropic steps 3, agents 1-2 use Sonnet [M] |
| Defects lanes (bug/logic/security) | Sonnet default; Opus for security-sensitive paths in deep | Anthropic bug agents = Opus; Cloudflare specialists = Sonnet-class [M]; choose Sonnet for cost, escalate by tier |
| Verifier | Opus (different tier from finder) | Anthropic validators Opus for bugs; alexop Opus verifier over Sonnet finders [M/L] |
| Aggregator/judge | Top available (user's session model) | Cloudflare coordinator = top tier; downgraded to Sonnet on trivial tier [L] |

Aliases `haiku|sonnet|opus|inherit` are valid `model` values and may be blocked by org allowlists (substituted) [M: sub-agents docs]; the skill must tolerate substitution and record the *actual* model in `verification.model`.

## Anti-Patterns

### Anti-Pattern 1: Diff-only review
**What people do:** Feed the diff (Anthropic's bug agent does by design; PR-Agent compresses to the diff).
**Why it's wrong:** Misses callers/tests/config/doc divergence; 48% of sampled FPs trace to missing context (SWR-Bench) [M]; violates `docs/code-review.md`.
**Do this instead:** Mandatory ledger gate; finders flag only what they can cite.

### Anti-Pattern 2: Batch/self-verification ("verification theater")
**What people do:** One coordinator agent re-reads all findings and keeps them.
**Why it's wrong:** Kept 43 of 43 in alexop's first version [L]; same-model blind spots (Greptile model-inversion data) [L].
**Do this instead:** One finding per verifier, refute-by-default, different tier, sees nothing but the finding + code.

### Anti-Pattern 3: Debate/discourse as the precision mechanism
**What people do:** OCR phase 6 (AGREE/CHALLENGE/CONNECT/SURFACE among reviewers) [M: README]; CR-Agent multi-agent debate.
**Why it's wrong:** SWR-Bench: debate-style CR-Agent mean F1 9.2% vs 18.7% for a refined single-pass prompt, blamed on interaction overhead and error propagation [M: single benchmark paper, paper-level evidence]. Costs a full extra round of context per reviewer.
**Do this instead:** Make discourse optional and off by default (deep only), or drop. Spend the budget on the per-finding verifier.

### Anti-Pattern 4: Unbounded agentic exploration as the default
**What people do:** Greptile v3-style loop with very high tool-call limits.
**Why it's wrong (for a portable skill):** Works for Greptile because of prebuilt graph + very high cache hit rates (v3 uses ~3x context tokens yet cuts cost via caching) [L: vendor blog]; SWR-Agent (tool exploration) scored 12.6% F1 vs 18.7% for refined single-pass [M]. A skill has no shared index or controllable cache.
**Do this instead:** Bounded lanes (`maxTurns`), ledger pre-built by the gatherer, verifier does the targeted multi-hop.

### Anti-Pattern 5: Confirmation inside lanes / background fix mode
**What people do:** Let a subagent ask or edit.
**Why it's wrong:** Subagents can't ask the user; background subagent edits fall outside `/rewind` checkpoints [M: docs].
**Do this instead:** Foreground main-thread gates and fix loop; require clean tree (or auto-stash) first.

### Anti-Pattern 6: Reading rules/instructions from the PR head
**Why it's wrong:** Attacker-controlled; CSA documents agent hijack via PR-supplied agent config [M].
**Do this instead:** Base-branch only; mark everything from the PR as data.

### Anti-Pattern 7: Direct comment posting / APPROVE or REQUEST_CHANGES
**Why it's wrong:** Anthropic's review "never approves or blocks"; the check run never fails [M]. Cloudflare does approve/block but with a break-glass override and heavy bias toward approval [L]; far higher blast radius for a local skill.
**Do this instead:** Event omitted -> PENDING draft; only `COMMENT` if the user explicitly asks; verdict is advisory text.

### Anti-Pattern 8: Executing PR code during verification by default
**Why it's wrong:** Running a fork's tests is arbitrary code execution. Anthropic's managed review works "without building or running your code" [M]; ultrareview reproduces in an isolated cloud sandbox [M].
**Do this instead:** Default verification = static trace with citations. Dynamic repro (failing test) only on the user's own trusted checkout, or after explicit consent; in fix mode a repro is required before applying (project rule).

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| GitHub (PR read) | `gh pr view/diff`, `gh api` | No web fetch; link comments with full SHA + `#L<a>-L<b>` (Anthropic plugin rule) [M] |
| GitHub (PR write) | ONE `gh api repos/{o}/{r}/pulls/{n}/reviews` call, `commit_id` = reviewed HEAD SHA, `comments[]` with `path`, `line`, `side`, `start_line`, `body`; omit `event` -> PENDING [M: GitHub REST docs] | Batching avoids secondary rate limits ("creating content too quickly"); 422 can signal spam detection. Use `line`/`side`, not diff `position`. Pinning `commit_id` stops stale-line drift |
| Findings outside the diff | Cannot be inline comments; put in review body (Anthropic shows these only in the check-run table; annotations for line-bearing ones) [M] | Pre-existing findings go in body, never inline |
| GitLab | `glab` posting a single note (Claude Code does this) [M] | Defer; JSON mode covers it |
| Anthropic `/code-review` (built-in) | Coexists; our skill is namespaced by plugin | A same-named project/personal skill *replaces* the bundled one [M]; avoid name `code-review` to prevent accidental shadowing. Built-in local review ignores `REVIEW.md` (reads only `CLAUDE.md`) - our skill can read both |
| Optional accelerators | codegraph / Serena / LSP | Detect and use if present; never required |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Orchestrator <-> gatherer | Task message with paths in, files out | Gatherer may be a subagent or inline scripts; ledger is the contract |
| Orchestrator <-> finder/verifier | Task message (lens path, ctx path, out path); return = path + counts | Prompts include effort/stop rules; subagents are non-interactive, read-only |
| Finder -> verifier | Single finding JSON only (no sibling findings, no PR description) | Prevents anchoring; verifier gets `REVIEW.md` rules too (Anthropic gives both finders and verifiers the repo's review instructions [M]) |
| Aggregator <-> projections | `report.json` only | Projections never re-open lanes |
| Orchestrator <-> user | The only place gates exist | G1 cost, G2 post, G3 fix |

### Gate Design (confirmation points)

| Gate | Trigger | Shows | Default |
|------|---------|-------|---------|
| G1 scope/cost | tier=full and deep, or token estimate over threshold | lanes, files, est. cost | proceed on quick/standard without prompt |
| G2 post | `--post` | exact comment list (path:line, body, committable?), body-only items, target PR + HEAD SHA, "pending, not submitted" | no post without approval; user may drop individual comments |
| G3 fix | `--fix` | ordered plan: only `confirmed` findings with a fix; repro method for each; test command (derived at runtime from CI/config) | nothing applied without approval |
| G3a per-fix stop | test failure after a fix | failing test name + comment, revert of that fix | halt and ask; do not chain |

Fix loop rules (from `docs/code-review.md`, superpowers receiving-code-review [M], Anthropic `--fix` [M]): foreground main thread; clean tree or stash; order Blocking -> simple -> complex; per finding: reproduce (failing test/minimal repro) -> apply -> run full project suite -> next. A finding that cannot be reproduced goes to the user as *unconfirmed*, neither silently applied nor silently dropped. Fix-mode edits go through the user's permission mode.

### Serial Fallback (required)

Nesting depth is user-configurable (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`, 1 = off; default 3), concurrency default 20 [M]. If the skill itself runs inside a subagent (e.g. invoked by another workflow) or spawning errors, run lanes **serially in the main context** reading each lens file in turn, and use a Qodo-style batch self-score instead of per-finding verifiers. Report `mode: serial` and the lost guarantees (no context isolation, no tier-separated verification) in the output.

## Source Landscape: What Each System Contributes

| System | Shape | Takeaway for this design |
|--------|-------|--------------------------|
| Anthropic `code-review` plugin (command) | Haiku eligibility -> Haiku CLAUDE.md finder -> Sonnet summary -> 4 parallel finders (2 Sonnet CLAUDE.md, 2 Opus bug/security) -> 1 validator subagent per issue (Opus bugs, Sonnet CLAUDE.md) -> filter -> terminal -> inline comments, one per unique issue, committable suggestion only if it fully fixes [M] | Template for tiering, per-issue validation, false-positive list, posting rules. README version used 0-100 scores, threshold 80, plus a git-blame agent: an earlier iteration |
| Anthropic managed Code Review | Find (parallel) -> Verify -> Rank (dedupe+severity) -> Post; read-only, never approves, check run never fails; levels Quick/Standard/Deep; `REVIEW.md` from base branch; machine-readable severity counts in check-run text [M] | Output contract, severity vocabulary, base-branch rule, cap-the-nits config, re-review delta behavior |
| Anthropic ultrareview | Cloud fleet; every finding independently reproduced; 15-25 min; $5-25; diff limits 500 files / 8,000 lines [M] | Upper bound of "deep"; refusal behavior for oversized diffs |
| claude-code-action | Prompt + `--allowedTools` allowlist (inline-comment MCP tool + narrow `gh` prefixes); `confirmed:true` posts immediately, otherwise buffered then classified; `track_progress` single updating comment; `paths:` filters; minimal permissions [M] | Allowlist discipline; buffered-then-classified comments are an automated analogue of our G2 |
| PR-Agent / Qodo Merge | Single-call "PR compression" under token budget + 2nd-call self-reflection scoring 0-10 [M]. Qodo 2.0: specialist agents with dedicated contexts over a shared context engine, intent/alignment phase first, judge agent, evidence per finding [L: vendor blog; F1 60.1 figure is vendor/dev.to-reported, [L]] | Token-budget algorithm; batch self-score for the cheap path |
| Greptile | Prebuilt repo graph (files, functions, calls, imports) queried at review; v3 agent loop, high tool limits; model inversion (review with the other model) [M: docs for graph; L: v3/inversion numbers vendor-reported] | Why context matters; why verifier should differ from finder; not portable as-is |
| CodeRabbit | Pipeline not free agent: sandbox, codegraph, code index, 20+ linters, shell/ast-grep verification scripts ("receipts"), 7-8 model ensemble with cheap compressors + judge [L: Medium, LanceDB, vendor blogs conflicting on tiering] | Verification by generated grep/ast-grep checks = what our verifier does statically |
| Cloudflare | Coordinator + <=7 specialists, risk tiers, shared context file + diff dir, judge rubric biased to approval, circuit breakers, "what NOT to flag" prompts, stats (131k reviews, $1.19 avg) [L single primary source, detailed] | Best public production data on tiers, cost, and signal rules |
| OpenCodeReview (spencermarx) | Tech Lead orchestrator, personas with redundancy and per-instance model, 8 phases incl. discourse, SQLite session state, rounds, triage statuses [M: README] | Redundancy (n samples) idea; session artifacts layout; discourse is the weakly supported part |
| OCR delegate mode (alibaba) | Deterministic file selection + rule resolution (scripts) and the host agent reviews; per-file checklist; comment schema `path, content, start_line, end_line, category, severity` [M: local SKILL.md read] | Script/LLM split; category/severity enums |
| mattpocock code-review | Pin fixed point; spec source discovery order; standards sources + Fowler smell baseline; two parallel sub-agents <400 words each; no cross-axis reranking [M: local file read] | Spec + Standards lanes, fail-fast pinning, smell baseline |
| superpowers | `requesting-code-review`: curated context (SHAs, description, plan), never session history; severity-driven action table. `receiving-code-review`: verify before implementing, push back with evidence, YAGNI check, one item at a time [M: fetched] | Fix-mode discipline; reviewer gets curated context only |
| pr-review-toolkit (Anthropic plugin) | 6 narrow agents (comment accuracy, test coverage gaps, silent failures, type design, general, simplifier) with 0-100 / 1-10 ratings [M] | Ready-made lane inventory for tests/docs-vs-source/silent-failure lenses |
| Cursor Bugbot | Learned rules from reactions/replies; resolution rate metric (78%) [L]; architecture details not found | Out of scope (needs telemetry) |

## Suggested Build Order

Dependencies drive the order: schema is the contract for everything; context gate precedes any finder; verifier precedes aggregation (its output feeds ranking); mutation features last because they carry risk and depend on the verified report.

| # | Component | Depends on | Delivers / exit check |
|---|-----------|------------|------------------------|
| 1 | Finding/Verdict/Report schema + severity rubric + signal rules + validator script | - | `finding-schema.md`, JSON schema, `validate-findings` script; all later stages cite it |
| 2 | Scope: eligibility, ref pinning, diff split/filter, risk tier, lane selection, **context ledger** + untrusted-input rules | 1 | Ledger gate works; diff-only run is refused/labeled triage |
| 3 | ONE end-to-end lane (correctness, ported from ccr.md) + terminal report | 1, 2 | Replaces ccr.md baseline; all ccr rules kept or replaced with a log entry; SKILL.md skeleton under 500 lines |
| 4 | Verifier (per finding, refute-by-default, different tier) | 1, 3 | Refuted findings are dropped and counted; unconfirmed listed separately |
| 5 | Parallel lanes (security, tests, Spec, Standards, domain packs, language packs) + dedupe/judge aggregator + effort tiers + serial fallback | 3, 4 | Three-axis report; tier table; token caps; fallback tested by forcing depth 1 |
| 6 | JSON output mode (serialization of report.json) | 5 | Schema-valid output; stable for CI use |
| 7 | PR comments: line->diff mapping, batched PENDING review, G2 gate, dedupe vs existing comments, base-branch REVIEW.md | 5, 6 | Dry-run payload shown before any call; findings outside diff land in body |
| 8 | Fix mode: G3, clean-tree precondition, repro-first, per-fix test, revert, runtime test-command discovery | 4, 5 | Highest risk; ship last |

Notes: reference packs (lens files, language packs, severity rubric prose) can be authored in parallel from step 3 onward since they are loaded on demand and only need the schema. Attribution/license check for ccr.md must complete before step 3 publishes anything derived from it.

## Phase-Specific Research Flags

| Step | Flag | Why |
|------|------|-----|
| 2 | Needs research | Portable caller/test discovery without codegraph (grep heuristics per language, LSP availability); script language choice (git+gh+jq? Node? Python?) is undecided and affects "works on any machine" |
| 4 | Needs research | What a "static-trace" verification must contain to be accepted; when dynamic repro is safe |
| 5 | Needs measurement later | Lane-to-tier assignment and verified-set cap are starting points; no eval corpus (user decision), so rely on cost telemetry in the report, not accuracy claims |
| 7 | Needs research | Exact `line`/`side`/`start_line` semantics for multi-line and renamed files; fork PR permissions; existing-comment dedupe |
| 1, 3, 6, 8 | Standard patterns | Schema, single lane, JSON, fix loop are well covered by sources above |

## Confidence and Gaps

- **Overall MEDIUM.** The classifier rates plain WebFetch/WebSearch as LOW and cross-verified web as MEDIUM; most architecture claims here are corroborated across Anthropic docs + plugin source + independent write-ups (alexop, Cloudflare), so tagged [M]. Single-vendor performance numbers are [L].
- WebFetch content passed through a summarizer model; exact quoted prompt text from Anthropic's command and Cloudflare's rules should be re-fetched raw before being copied into the skill.
- **Not verified:** Greptile v3 "Claude Agent SDK" claim (secondary only); CodeRabbit model-ensemble and tiering details (Medium/LanceDB; CodeRabbit's own blog did not confirm tiering); Qodo 2.0 F1 60.1 / recall 56.7 (vendor + dev.to, and the launch post is internally inconsistent about exhaustive mode); Cursor Bugbot pipeline internals (page covered only learned rules); superpowers `code-reviewer.md` template body (only the skill page was read); claude-code-action `use_sticky_comment` input (not on fetched pages).
- **Evidence tension left open on purpose:** SWR-Bench (refined single-pass beats agentic/multi-agent debate; repeated sampling + merge is the best value) vs production reports from Anthropic, Cloudflare, Greptile (multi-agent + verifier raises action rate / lowers false positives). Resolution adopted: lanes for recall, per-finding verifier for precision, debate optional, "deep" may mean n-sample merge. No eval corpus means this is a reasoned position, not a measured one.
- Date check: Anthropic Code Review launched 2026-03-09 as research preview; the blog's related-links note says GA by 2026-10-15. Cloudflare post dated 2026-04-20; stats window 2026-03-10 to 2026-04-09.

## Sources

Anthropic / Claude Code (official)
- Code Review (managed): https://code.claude.com/docs/en/code-review
- Customize Code Review (REVIEW.md): https://code.claude.com/docs/en/code-review-customize
- PR reading, severity levels, check-run machine-readable output: https://code.claude.com/docs/en/code-review-pull-requests
- Costs: https://code.claude.com/docs/en/code-review-costs
- Local `/code-review` (effort, --comment, --fix): https://code.claude.com/docs/en/code-review-local
- Ultrareview: https://code.claude.com/docs/en/ultrareview
- Subagents (model resolution, depth, concurrency, tools, background): https://code.claude.com/docs/en/sub-agents
- Plugin manifest/layout/variables: https://code.claude.com/docs/en/plugins-reference
- Skill authoring best practices (500 lines, one level deep, scripts): https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- Blog: https://claude.com/blog/code-review
- `code-review` plugin command: https://github.com/anthropics/claude-code/blob/main/plugins/code-review/commands/code-review.md
- `code-review` plugin README (0-100, threshold 80): https://github.com/anthropics/claude-code/blob/main/plugins/code-review/README.md
- pr-review-toolkit: https://github.com/anthropics/claude-code/tree/main/plugins/pr-review-toolkit
- claude-code-action: https://github.com/anthropics/claude-code-action and docs/solutions.md https://raw.githubusercontent.com/anthropics/claude-code-action/main/docs/solutions.md
- Multi-agent research system (token multiples, effort rules, file handoff): https://www.anthropic.com/engineering/multi-agent-research-system

Other systems
- Cloudflare, Orchestrating AI Code Review at scale: https://blog.cloudflare.com/ai-code-review/
- alexop, Cloudflare design as a Claude Code workflow: https://alexop.dev/posts/ultracode-review-workflow-cloudflare/
- PR-Agent self-reflection: https://raw.githubusercontent.com/qodo-ai/pr-agent/main/docs/docs/core-abilities/self_reflection.md ; compression: https://raw.githubusercontent.com/qodo-ai/pr-agent/main/docs/docs/core-abilities/compression_strategy.md
- Qodo 2.0: https://www.qodo.ai/blog/introducing-qodo-2-0-agentic-code-review/
- Greptile graph context: https://www.greptile.com/docs/how-greptile-works/graph-based-codebase-context ; v3: https://www.greptile.com/blog/greptile-v3-agentic-code-review ; model inversion: https://www.greptile.com/blog/model-inversion
- CodeRabbit: https://www.coderabbit.ai/blog/the-art-and-science-of-context-engineering ; https://www.coderabbit.ai/blog/how-coderabbit-delivers-accurate-ai-code-reviews-on-massive-codebases ; (secondary) https://medium.com/data-science-collective/how-coderabbit-actually-works-331aeab55ec8
- OpenCodeReview (spencermarx): https://github.com/spencermarx/open-code-review ; OCR delegate mode: https://github.com/alibaba/open-code-review (local copy read: `~/.claude/plugins/marketplaces/open-code-review/skills/open-code-review-delegate/SKILL.md`)
- Cursor Bugbot learned rules: https://cursor.com/blog/bugbot-learning
- superpowers: https://github.com/obra/superpowers/blob/main/skills/requesting-code-review/SKILL.md ; https://github.com/obra/superpowers/blob/main/skills/receiving-code-review/SKILL.md
- mattpocock code-review, ccr, code-review rules: local `docs/matt.md`, `docs/ccr.md`, `docs/code-review.md`

Research / security
- SWR-Bench (agent vs pipeline, multi-review aggregation, FP causes): https://arxiv.org/html/2509.01494v2
- Code Review Agent Benchmark: https://arxiv.org/html/2603.23448v2
- Prompt injection against review agents (CSA): https://labs.cloudsecurityalliance.org/research/csa-research-note-comment-control-github-prompt-injection-20/
- GitHub REST, create a review: https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request

---
*Architecture research for: LLM-agent code-review skill (Claude Code plugin)*
*Researched: 2026-10-17*
