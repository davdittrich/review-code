# Feature Research

**Domain:** LLM-agent code review skills/tools (Claude Code skill + plugin, subagent lanes)
**Researched:** 2026-10-17
**Confidence:** MEDIUM overall. Product behavior (Anthropic, Codex, Cursor, Qodo, OCR, superpowers) read from vendor docs or repo files this session. Benchmark and acceptance numbers are mixed: arXiv abstracts fetched directly are the strongest; vendor benchmarks are self-reported and not comparable across corpora.

Confidence tags used below, via `gsd_run query classify-confidence`: web search or fetch alone = LOW; cross-checked against a second source = MEDIUM. The seam returned LOW for `webfetch` even when `--verified`. I upgraded to "MEDIUM" only where an official doc and an independent source agree, and I say so in the text. No claim here is HIGH; Context7 does not cover this domain.

## Baseline: what `docs/ccr.md` already has (so gaps are visible)

Keeps: iron law, artifact-only rule (ignore PR desc/TODOs), pattern detection (slop, adversarial lens, AI-authored code, Python/R/JS/SQL), cross-file consistency, doc-vs-source divergence, final-check list, 3-tier output (Blocking/Required/Suggestions) with Verdict. Docs `code-review.md` adds full-context rule, "Orphaned Coverage" smell, reproduce-before-fix. `matt.md` adds Standards/Spec two-axis parallel subagents with smell baseline. `gsd-code-reviewer.md` adds severity IDs, YAML frontmatter, fail-closed scope, prompt-injection-as-data rule. `cavecrew-reviewer.md` adds one-line-per-finding format.

**Gaps vs SOTA (what this project must add):** a verification stage, confidence/evidence model, introduced-vs-pre-existing attribution, machine-readable output, inline PR posting, fix mode, domain packs, review-rules file, effort tiers.

## Feature Landscape

### Table Stakes (Users Expect These)

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Per-finding verification pass before reporting** (independent verifier tries to refute each candidate against code) | Every serious reviewer has one: Anthropic managed Code Review ("verification step checks findings against the code and filters false positives"), `/code-review` plugin (one validator subagent per bug issue; unvalidated dropped), Ultrareview (every finding "independently reproduced and verified"), Cursor Bugbot (validator model), CodeRabbit (generates grep/ast-grep scripts in sandbox to prove claims), Ellipsis (multistage filter), BitsAI-CR (ReviewFilter), Qodo (judge agent). Field data: 56.3% of CodeRabbit comments rejected, false positives top reason. | MED | Verifier gets finding + file refs, NOT the reviewer's reasoning (avoid anchoring). Verdicts: reproduced / evidenced (file:line cite) / refuted / unverifiable. Maps to PROJECT core value. Dep: lanes, evidence model. |
| **Precision bar / "do not flag" list** | Plugin: flag only code that won't compile, clearly wrong regardless of input, or exact quotable rule breach; "If you are not certain an issue is real, do not flag it." Codex prompt: 8 bar criteria (introduced by commit, discrete, provably affects other code, no unstated assumptions, author would fix). Anthropic default: correctness only, not style. | LOW | Encode as a shared bar in SKILL.md core. Include FP list: pre-existing, looks-like-bug-but-correct, linter-catchable, senior-wouldn't-raise, lint-ignored. |
| **Introduced-vs-pre-existing attribution** | Anthropic severity has a dedicated "Pre-existing" tier (purple); plugin has a git-blame/history agent; Codex requires the commit introduced it. Prevents blaming PR for old bugs and inflating counts. | LOW-MED | Use `git blame`/`git diff <base>...HEAD`. Report pre-existing separately, never in the blocking tier. |
| **Severity taxonomy, 3 tiers + verdict** | Convergent: Anthropic Important/Nit/Pre-existing; Codex P0-P3; superpowers Critical/Important/Minor; GSD BLOCKER/WARNING/INFO; ccr Blocking/Required/Suggestions; CodeRabbit 5 levels + category badge. | LOW | Keep ccr names (Blocking/Required/Suggestions) as the display taxonomy; add a rubric reference file with "what Blocking means here" (Anthropic's `REVIEW.md` calibration pattern: breaks behavior, leaks data, blocks rollback). Verdict advisory only. |
| **Confidence/evidence attached to every finding** | Plugin scores 0-100 and drops < 80 (earlier version); Codex JSON has `confidence_score` 0.0-1.0 per finding plus overall; CodeRabbit/Ellipsis/Qodo all filter on confidence. | LOW-MED | See Differentiators for the calibrated version. Minimum: every finding states how it was verified. |
| **file:line precision + concrete fix** | superpowers format: file:line, problem, why it matters, fix. cavecrew `path:line: severity: problem. fix.` Codex: minimal line ranges that overlap the diff. Research: comments that are brief, include code snippets, and are hunk-level are more likely to cause code changes (arXiv 2508.18771, 16 tools, 22k comments). | LOW | Fix text <= 3-line snippet; suggestion block only when it fully resolves the issue. |
| **Full-context review (callers, tests, config, CI, conventions), not diff-only** | Copilot "analyzes the whole repository, not just the diff"; Greptile indexes whole repo graph; Cursor moved to agentic dynamic context in fall 2025 and got its largest gains; CodeRabbit field study: 10.3% of FPs from missing visibility into other files. Also user rule in `code-review.md`. | MED | **Tension, see Pitfalls:** SWE-PRBench (arXiv 2603.26130) found all 8 models degrade as unstructured context grows. Implement as agent-driven retrieval with a recorded "context inspected" list, not by pasting files into prompts. |
| **Repo conventions honored (CLAUDE.md / AGENTS.md / CONTRIBUTING)** | Plugin (2 CLAUDE.md agents), Anthropic service (CLAUDE.md + REVIEW.md), Codex (AGENTS.md rules), Copilot (copilot-instructions.md, path instructions), CodeRabbit path instructions. | LOW | Rule: quote the exact rule violated; scope to files at or under the rule's directory (plugin rule). |
| **Parallel reviewer lanes with dedupe** | Plugin 4 agents; Qodo correctness/standards/architecture/risk agents + judge; Ellipsis parallel Comment Generators; RevAgent 5 commentators + critic (arXiv 2511.00517); matt two-axis; Ultrareview fleet; Anthropic scales agent count with PR size. | MED | Lanes: correctness/bugs, security, spec/requirements, standards/conventions, history/pre-existing, tests. Merge+dedupe before verification to cut verifier cost. |
| **Terminal report** | All tools. | LOW | Summary / Blocking / Required / Suggestions / Verdict (ccr format) + totals line. |
| **PR inline comments via `gh`, with consent gate** | Plugin `--comment`; claude-code-action; Codex; Anthropic service inline comments + check run. Plugin posts one comment per unique issue, full-SHA permalink with `#L<start>-L<end>`. Ultrareview posts only if user opts in per run. | MED | Draft comment list privately first, show, confirm, then post (PROJECT requires confirmation). Post as `COMMENT` review events only (no APPROVE/REQUEST_CHANGES). Handle lines not in diff (fallback to a summary comment; Anthropic's check run lists findings GitHub rejected). |
| **Machine-readable JSON output** | Codex review prompt schema (`findings[]`, `title`, `body`, `confidence_score`, `priority`, `code_location.line_range`, `overall_correctness`); OCR `--format json`; Anthropic `bugs.json` and check-run `bughunter-severity` counts; Claude Code `ReportFindings` tool (location, one-sentence summary, category tag); claude-code-security-review `findings-count`/results JSON. | LOW-MED | Define one versioned schema, validate it with a bundled script. Terminal and PR renderers derive from the JSON (single source of truth). |
| **Cap and rank findings** | PR-Agent `num_max_findings`, `max_suggestions_per_file`, `suggestions_score_threshold` ("values above 7-8 may clip relevant suggestions"); Anthropic `/code-review --max-findings` (default limit, 32 via findings tool) and nit caps in REVIEW.md. | LOW | Cap per tier; rank by severity then evidence class; report count of withheld findings (Anthropic re-review summaries do). |
| **Skip / eligibility checks** | Plugin skips closed, draft, trivial/automated, already-reviewed PRs. | LOW | Also fail closed on bad ref/empty diff before spawning lanes (matt step 1, GSD fail-closed scope). |
| **Prompt-injection hygiene** | claude-code-security-review: "not hardened against prompt injection... only review trusted PRs." Cloud Security Alliance note: PR titles/issue bodies/HTML comments treated as authoritative by Claude Code, Gemini CLI, Copilot agents. GSD reviewer already treats evidence text as data. | LOW | PR text, diffs, comments, fixtures are data. Read-only tools for reviewer lanes. Read review rules from base/default branch, not PR head (Anthropic `REVIEW.md` rule). |
| **Read-only reviewers** | Anthropic: review repo access is read-only; superpowers reviewer read-only; cavecrew Bash limited to `git diff/log/show`. | LOW | Subagent `tools` allowlist, `disallowedTools: Write, Edit`. Fix mode is a separate, explicit phase. |

### Differentiators (Competitive Advantage)

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Evidence-classed confidence instead of self-rated numbers** | LLM self-confidence and LLM-judge verdicts are weakly calibrated: LLM-as-judge on code had Cohen's kappa ~0.21 (Java), ~0.10 (Python) vs test results (arXiv 2507.16587 as summarized); LLMs falsely reject correct code, worse when asked to explain and fix (arXiv 2603.00539). A class tied to what was actually done (REPRODUCED by test/script, EVIDENCED by file:line cite, INFERRED, UNVERIFIED) is auditable. Derive a 0-1 number for JSON from the class. | MED | Opinion, MEDIUM: no vendor publishes this; synthesis of the calibration evidence. Report UNVERIFIED findings in a separate "unconfirmed" section (user rule in `code-review.md`), never drop silently, never mix into Blocking. |
| **Execution-based verification (reproduce)** | Highest-signal verification available: c-CRAB (arXiv 2603.23448) scores reviews by executable tests; fix-guided verification (run original + proposed fix against tests: orig fails + fix passes = confirmed; both pass = style only; both fail = escalate) from arXiv 2603.00539; Ultrareview "independently reproduced"; Cursor roadmap "running code to verify reports." | HIGH | Only where tests/runner exist; opt-in by effort tier. Needs a safe-execution policy (no network, no secrets). Falls back to evidence-by-citation. Anthropic's managed service explicitly does not build or run code. |
| **Spec/requirements axis with "declined to judge" list** | Most tools skip spec checking. Qodo flags "requirement gaps" vs linked spec; superpowers reviewer does plan alignment and lists behaviors set aside as out-of-spec; matt Spec agent finds missing, extra (scope creep), and wrong-looking requirements. Separate axes stop one masking another (matt's rationale). | MED | Spec source discovery order from matt (issue refs in commits, user path, `docs/`/`specs/`). If no spec, say so; do not invent one. |
| **Two-axis + lane output kept separate (no cross-axis rerank)** | matt: do not merge/rerank Standards and Spec findings. Prevents "followed all conventions, built the wrong thing" passing. | LOW | Already specified in docs; carry over. |
| **Review of AI-authored code lane** | Evidence the failure modes differ: 19.7% of package names in 576k LLM samples were hallucinated, 43% repeated on every re-run (USENIX Sec 2025, arXiv 2406.10279); Veracode 2025: 45% of 80 tasks had OWASP-class flaws, no improvement with newer models (vendor report); repo-level hallucinations: "project context conflicts" 24.56% (arXiv 2409.20550). Checks: dependency exists in registry and is the intended one (slopsquatting); API/method exists in the *installed* version; cross-file consistency (ccr constructor divergence); tests that assert nothing or mock the unit under test; copy-paste drift; doc-vs-source (ccr). | MED | Mostly already in ccr "AI-Authored" detection; add dependency-existence check (script, offline-safe fallback) and test-quality sub-lane. Trigger on commit trailers, branch names, user flag. |
| **Domain packs loaded on demand** | OCR ships built-in ruleset (NPE, thread-safety, XSS, SQL injection) matched per file by a deterministic template engine; claude-code-security-review has a category list (injection, authn/z, crypto, race/TOCTOU, supply chain, deserialization, XSS) and default exclusions (DoS, rate-limit, memory/CPU exhaustion, generic input validation, open redirects) to cut FPs; Anthropic customize doc shows migration-compat, PII-in-logs, tenant-scoping rules. | MED | Packs: security/OWASP, concurrency, API compat/breaking change, data+migrations, LLM-generated code, plus ccr Python/R/JS-TS/SQL. Selection is deterministic (file-type and path globs) so only relevant packs load (progressive disclosure; SKILL.md < 500 lines, references one level deep per Anthropic skill best practices). Each pack carries its own "do not flag" exclusions. |
| **Repo review-rules file (REVIEW.md-style), read from base ref** | Anthropic: `REVIEW.md` recalibrates severity, caps nits ("at most five"), excludes paths, adds "always check" rules, requires file:line evidence for behavior claims; read from default/base branch "never from the pull request's own changes." Codex: AGENTS.md "Code Review Rules"; CodeRabbit path instructions. | LOW-MED | Support nested rules by path. Local `/code-review` reads CLAUDE.md only, not REVIEW.md, so honoring both is a gap this skill can fill. |
| **Effort tiers (quick / standard / deep)** | Anthropic Quick/Standard/Deep; `/code-review` effort low (fewer FPs) to max (more coverage); Copilot Lite/Balanced; Qodo review-effort mode. Data justifies: reflection/aggregation raise recall but cost precision (CR-Bench Reflexion: recall 27.01% to 32.76% but SNR 5.11 to 1.95; SWR-Bench multi-review aggregation up to +43.67% F1). | MED | Tier = lanes count, verifier strictness, whether to reproduce, whether to emit nits/pre-existing. Default standard. Scale lanes with diff size. |
| **Fix mode with reproduce-first, one-at-a-time, test-gated apply** | `--fix` in `/code-review` (edits via permission mode), CodeRabbit one-click/Finishing Touches, Copilot handoff to coding agent, Cursor Autofix (beta), Greptile "Fix with your agent." superpowers *receiving-code-review*: verify before implementing, push back when wrong/YAGNI, one change at a time, test each. User rule: reproduce defect first, unreproducible goes to user, run suite after each fix, failure means finding may be misclassified intended behavior. Claude Code marks findings fixed / skipped / no-change-needed. | MED-HIGH | Only verified findings are eligible. Write failing test (or repro) -> fix -> run suite -> next. Status per finding in JSON. Hard no on auto-applying UNVERIFIED. |
| **Dedupe and re-review awareness** | Plugin dedupes; Bugbot dedupes against previously posted bugs; Anthropic later reviews focus on what changed since last review and resolve their own fixed threads; Ellipsis reviews only commits since last pass. | MED | Stable finding fingerprint (file + symbol + normalized claim) in JSON; stateless but works if prior JSON/comments supplied. |
| **Model routing per lane** | Plugin: haiku for eligibility/CLAUDE.md locator, sonnet for summary + CLAUDE.md compliance, opus for bug lanes and bug validators. Qodo benchmark: Haiku 4.5 F1 30.4% vs Sonnet/Opus ~55-57% (vendor, proprietary dataset). | LOW | Cheap models for triage/context gathering only; strong models for find+verify. Respect user routing (global rules). |
| **Hunk/file-bundle chunking for big diffs** | arXiv 2508.18771: hunk-level tools do especially well. OCR groups related files into bundles, one isolated sub-agent each. Diff-size study (secondary, LOW): Haiku F1 0.800 on 10-50 line diffs vs 0.043 on 150-600. | MED | Split by bundle (file + its tests + its direct callers). Cap lane fan-out well under the 20 concurrent subagent default. |
| **Orphaned Coverage and other user-specific smells** | Unique to this repo's `code-review.md`; no competitor has an equivalent for deleted tests that asserted unrelated live behavior. | LOW | Keep as named smell in the tests lane. |
| **Review-effort and risk estimate in the summary** | PR-Agent /review emits effort 1-5, risk low/med/high, `can_be_split`, merge recommendation (`no_concerns_found`/`needs_review`/`changes_required`), relevant-tests check, and ticket-compliance check. Helps humans triage where to look. | LOW | Optional summary fields; advisory only. Large-PR chunking merges conservatively (lowest score, highest effort, most conservative risk) which is a good rule for merging lane outputs. |
| **Optional external-model second opinion lane** | Same-vendor author+reviewer: roughly 5 in 6 attributed AI-reviewed PRs were same-product (Selvanayagam & Ghaleb, "AI-to-AI Code Reviews of GitHub Pull Requests", ESEM 2026 emerging results, via beri.net commentary; the source's own counts do not sum to its stated total, so no aggregate is derived here); no evidence yet that cross-vendor catches more (author's own caveat). | LOW | LOW confidence; ship as documented optional hook (any CLI reviewer), not a dependency (PROJECT: no local-tool dependence). |
| **SARIF export** | OCR emits SARIF for GitHub Code Scanning. | LOW | Cheap derivation from the JSON; defer unless asked (v1.x). |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| **"No mercy / guilty until proven exceptional" as a finding generator** (ccr iron law, taken literally) | Feels rigorous; maximizes recall. | LLM reviewers already overcorrect: systematic false rejection of correct code, worse with explain-and-fix prompts (arXiv 2603.00539; 4 categories = 87.2% of false rejections per AgentPatterns summary, MEDIUM). Recall-boosting without a gate drops SNR (CR-Bench). 56.3% rejection in the CodeRabbit field study. | Keep adversarial *search* (try to break it), but require a falsifiable counterexample or cited evidence before a finding survives verification. Keep ccr's "if code is genuinely elite, say so; don't manufacture issues." |
| **Auto-posting without confirmation / bot approvals** | Convenience; matches hosted tools. | PROJECT requires confirmation; Anthropic's service never approves or blocks; Copilot approvals are opt-in preview and dismissed on new commits. A skill running with user's `gh` token impersonates the user. | `--comment` drafts, shows, asks; `COMMENT` events only; verdict advisory. |
| **Dumping the whole repo or all docs into each lane prompt** | "Full context" taken literally. | SWE-PRBench: every model's score fell as context went diff -> +files -> full; a 2,000-token structured diff+summary prompt beat a 2,500-token full-context prompt. | Agentic retrieval on demand; context manifest in the report (what was read and why). |
| **Raw numeric confidence from the model as the sole filter** | Plugin did 0-100 / 80. | Self-rated numbers are weakly calibrated (LLM judges agree poorly with test outcomes, see Differentiators). Anthropic's current command text dropped the numeric scale for validators plus a "HIGH SIGNAL" bar (read this session). | Evidence class (see Differentiators) with a threshold per effort tier. |
| **Style/naming/nit flood by default** | Feels thorough (ccr Slop & Structure detections). | Nits drive rejection and trust loss; Anthropic caps nits and recommends REVIEW.md "at most five"; Codex defaults to P0/P1 only; Tricorder disabled analyzers above ~10% FP rate and Coverity said >30% FP gets tools ignored (both cited via Augment secondary summary, LOW). | Nits off in standard tier; on in deep tier or by repo rule; cap count. Skip anything a linter/CI enforces. |
| **Learning/memory from feedback (Greptile reactions, Bugbot learned rules, CodeRabbit Learnings, Qodo Rule Miner)** | Reduces repeat noise. | Needs persistent hosted state and telemetry; a portable stateless skill cannot do this honestly. PROJECT out of scope: hosted service/GitHub App. | Repo `REVIEW.md` rules maintained by humans; optionally a committed "dismissed findings" fingerprint list consumed by dedupe. Defer. |
| **PR walkthrough, sequence diagrams, poems, summaries of the PR** | CodeRabbit/Sourcery/Qodo pitch features. | Not a correctness feature; adds tokens; the Summary section already exists. | One-paragraph BLUF Summary only. |
| **Seeded-bug eval corpus / hosted leaderboard** | Proves quality. | PROJECT decision: research-grounded, no eval corpus. Vendor benchmarks are not comparable (proprietary sets, LLM judges, self-reported). | Cite benchmarks; use at most 3 smoke scenarios per Anthropic skill best practices. Revisit if quality needs proof. |
| **Dependence on Serena/codegraph/beads/claude-mem etc.** | Faster symbol lookup. | PROJECT constraint: portability. | Use `grep`/`git`/LSP-free tools by default; accelerators opt-in only. |
| **Reading review instructions from the PR branch** | Lets contributors tune the reviewer in the same PR (Copilot does this). | Prompt-injection / reviewer-weakening vector; Anthropic reads `REVIEW.md` only from base/default branch. | Base-ref only; warn if PR modifies review-rules files. |
| **Free-form prose reviews as the only output** | Easy. | Not machine-checkable; cannot feed fix mode, dedupe, CI gate. | JSON as source of truth, renderers on top. |
| **Auto-applying every suggestion in fix mode** | "Fix mode" literal reading. | LLM suggestions are wrong often enough (accepted: 36.4% CodeRabbit; RevMate 8.1% Mozilla, 7.2% Ubisoft). | Verified-only, one at a time, tests between. |

## Feature Dependencies

```
Context gathering (agentic retrieval + manifest)
    └──requires──> Scope pinning (fixed point, diff, fail-closed)
    └──feeds──> Reviewer lanes (correctness, security, spec, standards, history, tests)
                    └──requires──> Domain pack selection (deterministic by file/path)
                    └──emits──> Candidate findings (schema: id, file, line, claim, lane, severity, introduced?)
                                    └──requires──> Merge + dedupe
                                    └──feeds──> Verification pass (refute / reproduce / cite)
                                                    └──emits──> Evidence class -> confidence
                                                    └──feeds──> Severity calibration (rubric + repo rules)
                                                    └──feeds──> JSON (source of truth)
                                                                    ├──> Terminal renderer
                                                                    ├──> PR inline-comment renderer (requires gh + consent + full SHA)
                                                                    └──> Fix mode (requires VERIFIED status + test runner)

Repo rules file (REVIEW.md-style, base ref) ──enhances──> Lanes, Severity, Verification bar
Effort tiers ──configures──> Lane count, verifier strictness, reproduce on/off, nits on/off
Spec axis ──requires──> Spec source discovery (issue/PR/commit refs, docs/)
Execution-based reproduction ──requires──> Test runner detected + safe-exec policy
AI-authored lane ──requires──> Dependency registry check (network optional) + cross-file grep
Adversarial iron-law tone ──conflicts──> Precision bar unless gated by verification
Full-context-in-prompt ──conflicts──> Context-dilution evidence (use retrieval)
Auto-post ──conflicts──> Consent gate
```

### Dependency Notes

- **Verification requires a structured candidate schema:** the verifier must receive claim + locations, not the finder's narrative, to avoid anchoring (Turpin et al. per AgentPatterns; MEDIUM).
- **Fix mode requires verification status:** user rule: reproduce before applying; unreproducible findings go to the user as unconfirmed.
- **PR comments require JSON locations to map to diff lines:** lines outside the diff cannot be inline comments; Anthropic's check run falls back to a table.
- **Effort tiers conflict with fixed cost expectations:** reflection and more passes raise recall and lower SNR; therefore tiers must change the verifier bar together with lane count.
- **Domain packs enhance lanes but need path/type routing,** else all packs load and context dilutes.
- **Execution-based verification enhances (not replaces) citation-based verification** because many targets (naming, docs, design) are not executable.

## MVP Definition

### Launch With (v1)

- [ ] SKILL.md core + `references/` (severity rubric, precision bar and FP list, output schema, packs) — lean per skill best practices (< 500 lines, one level deep)
- [ ] Scope pinning with fail-closed refs and eligibility skip — avoids wasted lanes
- [ ] Parallel read-only lanes: correctness, security, spec/requirements, standards/conventions, history (pre-existing attribution), tests — the matt + plugin + Qodo common core
- [ ] Verification pass per finding with evidence classes; unverified reported separately — core value
- [ ] Full-context retrieval with context manifest (callers, tests, config, CI, docs) — user's gate
- [ ] Severity rubric + repo review-rules file from base ref — calibration
- [ ] Output: terminal report, versioned JSON, PR inline comments with consent via `gh` — all four modes minus fix
- [ ] Domain packs: security/OWASP, concurrency, API compat, migrations/data, LLM-generated code, plus ccr Python/R/JS-TS/SQL
- [ ] Prompt-injection hygiene and read-only tools
- [ ] Fix mode, minimal: verified findings only, one at a time, repro/failing test first, suite after each — PROJECT lists it as active

### Add After Validation (v1.x)

- [ ] Execution-based reproduction by default in deep tier — after safe-exec policy proven
- [ ] Effort tiers beyond a simple quick/standard/deep switch (e.g. per-path overrides)
- [ ] Re-review mode keyed on finding fingerprints and prior comments
- [ ] SARIF export
- [ ] Optional external second-opinion lane hook

### Future Consideration (v2+)

- [ ] Committed dismissed-finding list as lightweight "learning"
- [ ] GitLab MR posting via `glab` (Claude Code's `/code-review --comment` already does)
- [ ] Eval/seeded-bug corpus (explicitly out of scope today)

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Verification pass | HIGH | MEDIUM | P1 |
| Precision bar / FP list | HIGH | LOW | P1 |
| Evidence-class confidence | HIGH | MEDIUM | P1 |
| Pre-existing attribution | HIGH | LOW | P1 |
| Parallel lanes + dedupe | HIGH | MEDIUM | P1 |
| Full-context retrieval + manifest | HIGH | MEDIUM | P1 |
| JSON schema | HIGH | LOW | P1 |
| PR inline comments (consent) | HIGH | MEDIUM | P1 |
| Domain packs | MEDIUM-HIGH | MEDIUM | P1 |
| Spec axis | HIGH | MEDIUM | P1 |
| Review-rules file (base ref) | MEDIUM | LOW | P1 |
| AI-authored code lane | MEDIUM-HIGH | MEDIUM | P1 (already in ccr) |
| Fix mode (verified-only) | MEDIUM-HIGH | MEDIUM-HIGH | P1/P2 |
| Effort tiers | MEDIUM | MEDIUM | P2 |
| Execution-based reproduction | HIGH | HIGH | P2 |
| Re-review/dedupe vs prior | MEDIUM | MEDIUM | P2 |
| SARIF | LOW | LOW | P3 |
| Second-opinion external lane | LOW | LOW | P3 |

## Competitor Feature Analysis

**Caveat:** cells say what a fetched doc states. `?` means not documented in the pages I read, NOT a verified absence. Several vendors (Graphite, Greptile, Sourcery, Ellipsis, Copilot internals) were read via search summaries or landing pages only.

| Feature | Anthropic managed Code Review / `/code-review` | Codex review | Cursor Bugbot | CodeRabbit | Qodo / PR-Agent | Greptile | Copilot | Graphite Agent | OCR (Alibaba) | superpowers / matt | Our approach |
|---------|------|------|------|------|------|------|------|------|------|------|------|
| Multi-agent lanes | Yes (scales with size); plugin 4 + validators | ? | 8 passes (v1), now agentic | ? | Parallel agents + judge (Qodo); PR-Agent single call per tool | ? (repo graph index) | agentic tool calls | ? | file bundles, one sub-agent each | superpowers: reviewer must not spawn subagents; matt: 2 | 6 lanes, scale by tier |
| Verification | Verify stage drops FPs; Ultrareview reproduces | prompt bar only | validator model + voting (v1) | sandbox grep/ast-grep scripts (secondary sources) | judge agent filters low confidence; PR-Agent `suggestions_score_threshold` | ? | ? | ? | reflection module (comment content check) | superpowers: receiving-side verify-before-implement | Per-finding verifier + evidence class |
| Severity | Important/Nit/Pre-existing | P0-P3 | ? | 5 levels + category | High/Low thresholds; PR-Agent risk low/med/high + effort 1-5 | ? | ? | ? | adjustable (per search summary) | Critical/Important/Minor | Blocking/Required/Suggestion + pre-existing |
| Confidence | Not exposed | `confidence_score` 0-1 | ? | internal | judge filter; PR-Agent importance score + threshold | ? | ? | ? | ? | ? | Evidence class + derived number |
| Spec check | ? | ? | ? | Pre-merge checks (custom) | Requirement gaps; PR-Agent ticket compliance (GitHub/GitLab/Jira/Azure/Asana) | ? | ? | ? | ? | Plan alignment / Spec axis | Dedicated lane |
| Rules file | REVIEW.md, CLAUDE.md | AGENTS.md | Bugbot Rules | path instructions (YAML config) | Rule Miner, `.pr_agent.toml`, `extra_instructions` | ? | copilot-instructions.md, AGENTS.md, path `*.instructions.md` | ? | custom rules (path-filtered) | n/a | REVIEW.md-style from base ref |
| JSON output | check-run counts, `bugs.json`, `ReportFindings` | schema in prompt | ? | ? | PR-Agent `--json-output` | ? | ? | ? | `--format json` | ? | Versioned schema |
| PR inline comments | Yes | Yes | Yes | Yes | Yes (`inline_key_issues`) | Yes | Yes | Yes | CI recipes | ? | `gh`, consent-gated |
| Fix | `--fix` | ? | Autofix beta | one-click, Finishing Touches | committable suggestions | Fix with agent | handoff to cloud agent | ? | viewer marks fixed/ignored | receiving-code-review (skill) | Verified-only, test-gated |
| Learns from feedback | ? (replies read by next review) | ? | Yes | Yes | Yes | Yes | Memory (preview) | Yes (per search summary) | ? | ? | No (defer) |

## Research Evidence on Effectiveness (informs the thresholds above)

| Finding | Number | Source | Type | Confidence |
|---------|--------|--------|------|------------|
| Best PR-level review F1 on 1000 verified PRs | 19.38% (Gemini-2.5-Pro); some combos > 7 FPs per PR | SWR-Bench, arXiv 2509.01494 | Academic | MEDIUM |
| Multi-review aggregation | up to +43.67% F1 | arXiv 2509.01494 | Academic | MEDIUM |
| Human-flagged issues found by 8 frontier models (diff only) | 15-31%; more context lowered scores | SWE-PRBench, arXiv 2603.26130 | Academic | MEDIUM |
| Reflexion loop | recall 27.01% to 32.76%; SNR 5.11 to ~1.9 | CR-Bench, arXiv 2603.11078 | Academic | MEDIUM |
| Agents solve ~40% of c-CRAB tasks; reviews differ from human focus | ~40% | arXiv 2603.23448 | Academic | MEDIUM |
| LLM reviewers falsely reject correct code; explain+fix prompts worsen | qualitative (no rates in abstract) | arXiv 2603.00539 | Academic | MEDIUM |
| Plausible-but-invalid review comments hard to detect; repo-grounded agentic judge reaches 76.60% | 76.60% | CRJudgeBench, arXiv 2609.37216 | Academic (under review) | LOW-MEDIUM |
| Hallucination detector for review comments | F1 0.85, $0.009 per assessment, 67% agree with developers | HalluJudge, arXiv 2601.19072 (Atlassian) | Academic | MEDIUM |
| CodeRabbit comments in the wild: accepted / discussed / rejected | 36.4% / 7.3% / 56.3% (31,073 comments, 239 repos) | arXiv 2607.03316 | Academic | MEDIUM |
| LLM comments at Mozilla / Ubisoft accepted | 8.1% / 7.2%; median 42.9 s reading per patch | RevMate, arXiv 2411.07091 | Academic | MEDIUM |
| Atlassian RovoDev: resolution rate LLM vs human; cycle time | 38.70% vs 44.45%; PR cycle time -30.8% (1,900 repos, 54k comments) | arXiv 2601.01129 (ICSE-SEIP 2026) | Academic/industry | MEDIUM |
| Review-agent-only PRs merged vs human-only | 45.20% vs 68.37%; 12 of 13 agents average signal < 60% | arXiv 2604.03196 (MSR 2026) | Academic | MEDIUM |
| Comment features that lead to code changes | brief, with snippets, hunk-level, manually triggered | arXiv 2508.18771 (16 tools, 22k comments) | Academic | MEDIUM |
| BitsAI-CR precision / Outdated Rate (Go) | 75.0% / 26.7%; 12k weekly users | arXiv 2501.15134 | Industry paper | MEDIUM |
| Package hallucination | 19.7% of packages (576k samples, 16 models); 43% repeat each run | arXiv 2406.10279 (USENIX Sec 2025) | Academic | MEDIUM |
| AI code with OWASP-class flaws | 45% of 80 tasks, 100+ LLMs | Veracode 2025 report (via press/Help Net Security) | Vendor | LOW-MEDIUM |
| Best vendor-benchmark F1 | Opus 5.5 high 56.8% F1 (60.2 P / 53.7 R), $1.00/PR; Haiku 4.5 30.4% | Qodo blog (proprietary 125 PR / 703 bug set, LLM judge Sonnet; internal text/table inconsistencies) | Vendor | LOW |
| Martian Code Review Bench online leaderboard | top F1 ~63%; Greptile P 80.9 / R 52.1 | withmartian.com via search summary; offline understates precision | Third-party | LOW-MEDIUM |
| Anthropic internal | PRs with substantive comments 16% to 54%; < 1% findings marked incorrect | https://www.helpnetsecurity.com/2026/03/10/anthropic-claude-code-review/ and https://www.infoq.com/news/2026/04/claude-code-review/ (search summaries; vendor self-report; anthropic.com/news/code-review returned 404) | Vendor | LOW |
| Graphite negative-comment rate | < 5% (own figure; own guide says 5-8%); Macroscope reported 18.3% detection | graphite.com, macroscope.com (competitor) | Vendor | LOW |
| Cursor Bugbot resolution rate | 52% to over 70% (78% after learned rules) | cursor.com/blog/building-bugbot (primary read) | Vendor | MEDIUM |
| OCR (AACR-Bench) recall deliberately lower; ~1/9 tokens of general agent | top config ~20% of annotated issues | github.com/alibaba/open-code-review; third-party summary | Vendor | LOW-MEDIUM |

**Reading the table:** academic precision/F1 on real PRs is far lower than vendor numbers; benchmarks differ in corpus, golden set, and judge, so no cross-row comparison is valid. The consistent direction is: recall is cheap to raise, precision is expensive, and 55-90% of AI review comments are rejected or not acted on even after vendor-side filtering (CodeRabbit runs sandbox verification; RevMate used an LLM-judge filter). Filtering alone is not enough, which argues for evidence-class verification with a stated bar.

## Sources

Primary (read this session):
- Claude Code `/code-review` plugin README and command text — https://github.com/anthropics/claude-code/blob/main/plugins/code-review/README.md and https://raw.githubusercontent.com/anthropics/claude-code/main/plugins/code-review/commands/code-review.md (MEDIUM; README and command text differ: README describes 0-100 scoring/80 threshold, current command uses validators and a HIGH SIGNAL bar)
- Anthropic Code Review docs: https://code.claude.com/docs/en/code-review ; https://code.claude.com/docs/en/code-review-pull-requests ; https://code.claude.com/docs/en/code-review-customize ; https://code.claude.com/docs/en/code-review-local ; https://code.claude.com/docs/en/ultrareview (MEDIUM)
- Claude Code subagents doc (nesting depth, 20-subagent cap, read-only tools, worktree): https://code.claude.com/docs/en/sub-agents
- Agent Skills best practices (under 500 lines, one-level references): https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- claude-code-action: https://github.com/anthropics/claude-code-action ; solutions guide https://raw.githubusercontent.com/anthropics/claude-code-action/main/docs/solutions.md (path-specific, OWASP-prompt, checklist, inline-comment tool with `confirmed: true`; `docs/usage.md` structured-outputs page not read)
- PR-Agent tool docs: https://docs.pr-agent.ai/tools/review/ and https://docs.pr-agent.ai/tools/improve/ (the improve page shows an importance-score threshold, not a documented "self-reflection" mechanism)
- Research-plan/research-store caching seam was not used; see handback.
- claude-code-security-review: https://github.com/anthropics/claude-code-security-review
- OpenAI Codex review prompt (community gist of `codex-rs/core/review_prompt.md`): https://gist.github.com/cbh123/ce4893a10ed2b87a89d9114b08118a08 ; Codex GitHub review use case: https://developers.openai.com/codex/use-cases/github-code-reviews
- superpowers code-reviewer template and receiving-code-review: https://raw.githubusercontent.com/obra/superpowers/main/skills/requesting-code-review/code-reviewer.md ; https://raw.githubusercontent.com/obra/superpowers/main/skills/receiving-code-review/SKILL.md
- Open Code Review (Alibaba): https://github.com/alibaba/open-code-review
- Cursor Bugbot: https://cursor.com/blog/building-bugbot
- CodeRabbit overview: https://docs.coderabbit.ai/guides/code-review-overview ; verification and profile details from search summaries (https://www.coderabbit.ai/blog/context-engineering-ai-code-reviews, https://foojay.io/today/how-coderabbits-agentic-code-validation-helps-with-code-reviews/) — LOW-MEDIUM
- Qodo: https://docs.qodo.ai/code-review ; PR-Agent https://github.com/qodo-ai/pr-agent ; benchmark https://www.qodo.ai/blog/which-llm-should-review-your-code/
- Greptile intro: https://www.greptile.com/docs/introduction
- GitHub Copilot code review: https://docs.github.com/en/copilot/concepts/agents/code-review
- Graphite (search summary only): https://graphite.com/features/ai-reviews , https://graphite.com/guides/ai-code-review-false-positives — LOW
- Ellipsis architecture (search summary only): https://www.nsbradford.com/blog/how-we-built-ellipsis , https://www.zenml.io/llmops-database/building-and-deploying-production-llm-code-review-agents-architecture-and-best-practices — LOW-MEDIUM
- Sourcery (third-party summaries only, no vendor doc read): https://dev.to/rahulxsingh/sourcery-github-integration-pr-review-setup-ej — LOW. mattpocock code-review, ccr, GSD reviewer, cavecrew: local `docs/`.

Academic (abstract pages read or search-confirmed):
- SWR-Bench https://arxiv.org/abs/2509.01494 ; SWE-PRBench https://arxiv.org/abs/2603.26130 ; CR-Bench https://arxiv.org/abs/2603.11078 ; c-CRAB https://arxiv.org/abs/2603.23448 ; CRJudgeBench https://arxiv.org/abs/2609.37216
- "Are LLMs Reliable Code Reviewers? Systematic Overcorrection" https://arxiv.org/abs/2603.00539 ; summary https://agentpatterns.ai/patterns/anti-patterns/llm-review-overcorrection/
- HalluJudge https://arxiv.org/abs/2601.19072 ; RevAgent https://arxiv.org/abs/2511.00517 ; BitsAI-CR https://arxiv.org/abs/2501.15134
- RovoDev https://arxiv.org/abs/2601.01129 ; RevMate https://arxiv.org/abs/2411.07091 ; CodeRabbit in the wild https://arxiv.org/abs/2607.03316 ; Code review agents in PRs https://arxiv.org/abs/2604.03196 ; AI review and code changes https://arxiv.org/abs/2508.18771
- Package hallucination https://arxiv.org/abs/2406.10279 ; LLM code hallucinations in practice https://arxiv.org/abs/2409.20550 ; bug taxonomy https://arxiv.org/abs/2403.08937
- Veracode 2025 GenAI Code Security Report: https://www.veracode.com/resources/analyst-reports/2025-genai-code-security-report/
- Secondary aggregators (verify before reuse): https://www.augmentcode.com/guides/ai-code-review-accuracy (Tricorder 10%, Coverity 30%, diff-size study, SAST numbers); https://www.beri.net/article/ai-to-ai-code-review-same-vendor-reviewer-independence-gap ; https://codereview.withmartian.com/ and https://withmartian.com/post/code-review-bench-v0 ; Cloud Security Alliance prompt-injection note https://labs.cloudsecurityalliance.org/research/csa-research-note-claude-code-github-action-prompt-injection/
