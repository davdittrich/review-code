# Pitfalls Research

**Domain:** LLM-agent code-review skill/plugin for Claude Code (diff/branch/PR review, parallel lanes, verification pass, PR posting, fix mode)
**Researched:** 2026-10-17
**Confidence:** MEDIUM overall (see tag legend)

**Confidence legend.** Tiers come from `gsd_run query classify-confidence`: unverified websearch/webfetch = LOW, cross-checked websearch = MEDIUM. Tags used below:
- `[OBS]` = observed live this session (gh API, local files). Treat as fact, re-check before acting.
- `[M]` = MEDIUM: two or more independent sources, or a primary paper plus corroboration.
- `[L]` = LOW: single source, vendor blog, or secondhand figure. Direction is useful; do not quote numbers as authoritative.
- Vendor blogs (Augment, Cubic, CodeRabbit, Qodo, CodeAnt) sell review tooling. Their numbers are marked `[L]` unless a primary paper backs them.

Next-step phase names below are topical because no ROADMAP exists yet: **P-Skeleton** (SKILL.md, triggering, packaging), **P-Lanes** (parallel lanes + context retrieval), **P-Verify** (verification pass, confidence, severity), **P-Output** (terminal/JSON/PR posting), **P-Fix** (fix mode), **P-Legal** (licensing/attribution/NOTICE).

---

## Critical Pitfalls

### Pitfall 1: Verifier rubber-stamps (or manufactures) findings, so "verified" means nothing

**What goes wrong:** The verification pass is the project's Core Value ("every finding verified"). A verifier that sees the finder's reasoning, or that is asked to "explain and fix", agrees with it. LLMs reviewing for requirement conformance reject correct code systematically, and asking for explanations plus corrections makes this worse (arXiv 2603.00539). In CR-Bench, adding a Reflexion loop raised recall (27.0% to 32.8% for GPT-5.2) but cut signal-to-noise (5.11 to 1.95), and for GPT-5-mini noise outnumbered useful comments (SNR 0.91) (arXiv 2603.11078). Self-review is also weak: models showed self-preference and leniency on their own wrong code `[L]` (secondhand summary; primary Panickssery et al.).

**Why it happens:** Verification is implemented as "ask the same model again, same context, 'is this real?'". Chain-of-thought rationalizes the lean already present. More prompting to "find more" pushes weaker models to invent issues.

**How to avoid:**
- Verifier runs in **fresh context**, never receives the finder's rationale, only `{file:line, claim, claimed trigger input}`.
- Verifier's job is **refutation**: it must produce falsifiable evidence for one of three outcomes: CONFIRMED (cites the code path/line plus a concrete input that triggers it), REFUTED (cites the guard/caller/test that prevents it), or UNVERIFIABLE (kept only as a labeled low-tier note, never Blocking).
- Prefer binary prompts ("does input X at line N produce Y? yes/no + citation") over open "explain and fix".
- Where tests exist and execution is allowed (see Pitfall 8), use the fix-guided filter from 2603.00539: run tests on original and on proposed fix. Both pass = style, not blocking. Only fix passes = confirmed. Both fail = escalate to human `[M]` (paper + secondary writeup agree on the mechanism; paper abstract gives no numbers).
- Do not run self-reflection "find more issues" loops. Add lanes, not iterations.

**Warning signs:** Verifier confirmation rate near 100%. Verifier output restates the finder's words. Findings with no `file:line` and no concrete trigger input. Refuted rate near 0 in manual spot-checks.

**Phase to address:** P-Verify (design the refutation contract before any lane prompts are written).

---

### Pitfall 2: Fake-precision confidence scores with no eval corpus

**What goes wrong:** A 0-100 confidence number looks rigorous but is uncalibrated. Anthropic's own plugin drops findings under a score of 80 `[L]` (Cubic overview, secondhand); the official `code-review` command in `anthropics/claude-code` itself uses no numeric score, just a HIGH-SIGNAL bar plus independent validation `[OBS]` (fetched raw command file). The project has no seeded-bug corpus by user choice, so scores cannot be calibrated and will cluster at 85-95.

**Why it happens:** LLMs emit self-reported probabilities that are poorly calibrated; without ground truth nobody notices.

**How to avoid:** Anchor the score to **evidence type**, not feelings. Example ladder: `reproduced` (test/command failed as predicted) > `traced` (call path cited file:line, input constructed) > `inferred` (pattern match, no trace) > `speculative`. Only `reproduced`/`traced` may be Blocking. Report the evidence label alongside any number. Cheap in-scope mitigation: a small set of should-trigger / should-not-trigger prompts and ~10 hand-picked known-good/known-bad diffs as smoke tests. These are not the seeded-bug corpus the user ruled out; flag to user that this is a scope boundary decision.

**Warning signs:** All scores in a 10-point band. Score unrelated to evidence label. Threshold tuned by feel after one bad review.

**Phase to address:** P-Verify.

---

### Pitfall 3: Anchoring on PR description / commit message (and adversarial metadata)

**What goes wrong:** Framing in PR metadata changes verdicts. Saying a change is "bug-free" cut vulnerability detection by 16 to 93 points across five of six models (Opus 4.5 held, about 5 points); with LLM-crafted PR metadata and up to 10 refinement attempts, **17/17** vulnerability-reintroducing changes were accepted by real Claude Code review pipelines, within 5 iterations, total cost about $19 (arXiv 2603.18740) `[M]` (paper plus Augment summary agree). A second paper shows misleading task comments anchor judges even when the code is correct (arXiv 2505.16222) `[M]`. Prior-score anchoring is also not reduced by CoT or by "ignore the metadata" instructions (arXiv 2608.25869) `[L]` (search-snippet only).

**Why it happens:** The model treats PR text as context to believe. ccr.md says "treat as claims to verify" (good) but also instructs reading the description first, which primes the detector.

**How to avoid:**
- **Architecture rule:** detection lanes (correctness, security, concurrency, etc.) **never see the PR description, linked issue text, or commit messages**. Only the Spec lane receives them, and its output is treated as claims-vs-code.
- Programmatic redaction beats instruction: in the paper, redacting the description restored detection in 12/17 cases; redaction plus "ignore commit metadata" gave 16/17. Instructions alone did not suffice.
- Treat "trusted contributor" as a user-declared input, not something inferred from the PR text.

**Warning signs:** Lane output echoes PR-description wording. Finding count drops sharply when description says "security fix"/"refactor only". Review of the same diff differs with/without description.

**Phase to address:** P-Lanes (context partitioning is an architectural, not a prompt, decision).

---

### Pitfall 4: Prompt injection from reviewed content, including exfiltration through the output channel

**What goes wrong:** Code, comments, PR titles, issue text, file names, hidden HTML comments are all attacker-controlled input to a review agent. "Comment and Control" (disclosed April 2026, Aonan Guan et al.) showed the PR title in Anthropic's Claude Code Security Review prompted `whoami`/env dumping, and a variant read `ANTHROPIC_API_KEY`/`GITHUB_TOKEN` and **posted them into a public PR comment**; Gemini CLI Action and Copilot Agent (hidden HTML comments, base64 to defeat masking) fell similarly `[M]` (SecurityWeek, VentureBeat, CSA, Vectara case study agree). Anthropic's README says the security-review action is "not hardened against prompt injection" and for trusted PRs only; `claude-code-action` strips HTML comments/invisible chars/alt text but says new bypasses may emerge `[M]`. A Cline triage workflow lost an npm publish token to an issue-title injection `[L]` (one news report). A third-party audit flagged a CodeRabbit autofix skill that executes the comment's agent prompt as a direct instruction `[L]`.

**Why it happens:** Skill instructions and untrusted data share one context window. The agent holds tools (Bash, `gh`) and the output channel (PR comment) is also a write path to a public place.

**How to avoid:**
- Wrap all reviewed content in explicit data delimiters and state in the lane prompt that nothing inside is an instruction. This is necessary but **not sufficient**.
- Reviewer lanes are **read-only**: Read/Grep/Glob only. No Bash, no network, no `gh` in detection or verification lanes. Only the orchestrator, post-confirmation, may call `gh`.
- Strip hidden HTML comments, zero-width/invisible characters, and image alt text from PR text before any lane sees it.
- **Outbound check:** show the exact payload before posting; scan it for secret-shaped strings and env values; never include raw tool output in a posted comment.
- Do not ship GitHub-Actions/headless auto-trigger mode in v1. If ever added: `pull_request` not `pull_request_target`, no head checkout into workspace root, minimal token scope (`contents: read`, `pull-requests: write`), approval gate for external contributors `[M]` (Anthropic docs + 3 hardening guides).
- Treat file names and branch names as untrusted too.

**Warning signs:** A lane calling a tool the review never needed. Review text containing commands or "as instructed". Posted body containing env-like strings. Reviews of attacker-controlled forks run with the user's credentials.

**Phase to address:** P-Lanes (tool allowlists), P-Output (outbound scan), P-Skeleton (red-team fixture: a diff containing an injection comment).

---

### Pitfall 5: False positives and hallucinated findings erode trust faster than misses

**What goes wrong:** Untuned LLM reviewers produce many wrong comments. Evidence: reviewers accepted only 8.1% (Mozilla) and 7.2% (Ubisoft) of generated comments (RevMate preprint, via Augment) `[L]`; Google disabled analyzers whose "not useful" rate exceeded 10% (Tricorder, CACM 2018) and Coverity found >~30% false positives makes users ignore the tool (CACM 2010) `[M]`; up to 24.8% of correct code blocks drew incorrect suggestions from GPT-4o/Gemini 2.0 Flash, roughly double without a problem description (arXiv 2505.20206, via Augment) `[L]`. CR-Bench best-case precision was about 5%, but it counts only the benchmark's target bug as a hit, so it overstates noise `[M]`. Atlassian (ASE 2025): 18.2% of LLM comments were bug-related vs 6.5% for humans; design comments had 28.6% resolution `[L]` (secondhand).

**Why it happens:** Directional bias toward "find something". The prompt voice ("ruthlessly identify every flaw") rewards output volume. Reviewers lack context and cannot execute.

**How to avoid:**
- Every Blocking/Required finding must carry: `file:line`, quoted code, concrete trigger input or scenario, and what guard was checked and absent. No evidence = demote or drop.
- Explicit **do-not-flag** list (adapted, not pasted, from the Anthropic command's list): pre-existing issues, things a linter/compiler catches, deliberate lint-ignores, unchanged lines, input-dependent maybes, style opinions without a repo rule. Idea reuse is fine; the text is under Anthropic's all-rights-reserved terms (see Pitfall 13).
- Hard cap on reported findings per tier (for example 10 Blocking+Required, Suggestions collapsed). Report counts of what was dropped by verification so the user can calibrate.
- Track acceptance rate by asking the user at the end of fix/post flow ("which findings were wrong?") and write to a local, user-owned log. Monitor refuted-by-human rate.
- Ship a "dropped findings" appendix in JSON output so false-negative audits are possible.

**Warning signs:** More than ~10 findings on a 100-line diff. Findings that begin "consider..." or "might". Many findings on lines the PR did not touch.

**Phase to address:** P-Verify (evidence contract + caps), P-Output (dropped-findings appendix).

---

### Pitfall 6: Comment fatigue, nitpick noise, and severity inflation

**What goes wrong:** Volume trains humans to ignore the bot. An industrial study found 26.2% of automated comments ended "Won't Fix"/"Closed" and reviewers sometimes skipped issues because the bot would flag them `[L]` (arXiv 2412.18531 via search snippet). Controlled experiments report automated reviews do not save time and can bias reviewers toward lower-severity issues `[L]` (arXiv 2405.18216 snippet). Defect-focused work found raw LLM output is dominated by nitpicks and hallucinations and had to filter by nitpick/fake-problem/criticality scores (arXiv 2505.17928) `[L]` (abstract-level; PDF body not read). Greptile reportedly could not prompt away nits without losing critical findings `[L]` (Cubic, vendor). No study measures "severity inflation" by name; treat it as practitioner-reported, not proven.

**Why it happens:** ccr.md's tier labels (Blocking/Required/Suggestions) are assigned by the same model that found the issue, with a "guilty until proven" stance, so inflation is built in. Nit rules in ccr.md ("lazy naming: `data`, `temp`, `result`", "obvious comments") are exactly what experienced reviewers skip.

**How to avoid:**
- **Severity is a rubric over evidence, not a mood.** Put the rubric in `references/severity.md`: Blocking = demonstrable wrong behavior/security/data loss with a trace; Required = likely defect or violated documented repo rule, quoted; Suggestion = everything else. Verifier, not finder, assigns the final tier.
- Demote ccr "slop detector" items (naming, obvious comments, structure contempt) to opt-in "style pass", off by default, and only when backed by a repo-documented convention.
- Cap and collapse Suggestions ("N more low-priority notes; ask to expand").
- Praise/positive notes limited to at most one line; do not add filler sections.
- In PR mode, one grouped review, not one comment per nit.

**Warning signs:** Blocking share above ~20% of findings. Same finding class repeated across files (should be one grouped finding). User replies "ok" to everything.

**Phase to address:** P-Verify (rubric), P-Output (grouping, caps).

---

### Pitfall 7: Diff-only blindness, and its mirror image, context dilution

**What goes wrong:** Two opposing failures.
- *Diff-only:* with only the diff, eight frontier models found 15-31% of what human reviewers flagged (SWE-PRBench, 350 PRs, arXiv 2603.26130) `[M]`. Missing-authorization-style bugs depend on code outside the method `[L]`. CodeRabbit attributes 10.3% of false positives to missing cross-file visibility `[L]`. The user's own rule (docs/code-review.md) says diff-only never satisfies a gate.
- *Dilution:* the same SWE-PRBench found scores **declined** as context grew from diff to diff+files to full context, and a compact ~2,000-token diff-plus-summary beat a ~2,500-token full-context prompt for all eight models, attributed to attention dilution `[L]` (single preprint, abstract-level, benchmark of its own design). Do not read this as contradicting full-context review; read it as "dumping files hurts, targeted retrieval helps".

**Why it happens:** Teams equate "full context" with "stuff the repo into the prompt".

**How to avoid:**
- Full context = **retrieval by the lane agent**: callers of changed symbols, tests touching them, config/CI, convention docs. Lane gets the diff as primary text plus a compact per-lane context brief, and Reads more on demand.
- Require each finding to **cite what was inspected beyond the diff** (call path, test file). The gate is "evidence of inspected context", which the project's own "diff-only never satisfies gate" rule needs in checkable form. Output includes an "Inspected" list.
- Per-lane context budget; diffs above a size threshold are split by file/module before lanes run (Haiku-class F1 reportedly collapsed on 150-600-line diffs `[L]`, source not cited by Augment; do not quote the figure).
- Add file path, module and language to each lane prompt (one practitioner reported ~40% fewer false positives `[L]`).

**Warning signs:** Findings that never cite a file outside the diff. Lane transcripts with 20+ whole-file Reads and then generic output. Misses a bug whose caller is in another file.

**Phase to address:** P-Lanes.

---

### Pitfall 8: Verification and fix mode execute attacker-controlled code

**What goes wrong:** "Reproduce the finding" and "run tests after fix" mean running the PR's test files, conftest, `package.json` scripts, build hooks, git hooks. On an untrusted branch that is arbitrary code execution on the user's machine with the user's credentials. This is distinct from prompt injection and is not covered by "read-only lanes".

**Why it happens:** Reproduction is the project's headline quality signal, so it gets built first, without a trust model.

**How to avoid:**
- Ask the user once per run to declare the code **trusted** (own branch) or **untrusted** (external PR). Untrusted: verification is static-only; execution only after explicit confirmation naming the exact command, ideally in a sandbox/worktree with no secrets in env.
- Reproduction commands are generated by the orchestrator, shown, then run; lanes never execute.
- Never run a PR's install/build scripts as part of review.
- Fix mode runs only in a throwaway worktree or on a clean tree after user confirmation.

**Warning signs:** `npm install`/`pip install -e`/`make` appearing in a review transcript. Tests run before the user has been asked.

**Phase to address:** P-Verify (trust model), P-Fix.

---

### Pitfall 9: Unsafe PR posting (422s, duplicates, unconfirmed or auto-approving writes)

**What goes wrong:**
- GitHub rejects the **whole** review with 422 if any inline comment targets a line outside a diff hunk (documented in several tool bug reports: fullsend-ai/agents#430, pingdotgg/t3code#12549, cli/cli#13358). 422 is also returned when the endpoint is "spammed". `position` (diff offset) is being retired; use `line` + `side` `[M]`.
- Posting comments one-by-one triggers notification storms and secondary rate limits `[M]` (GitHub docs).
- Re-running the skill duplicates comments.
- An automated `APPROVE` or `REQUEST_CHANGES` event is a policy decision, not a tool decision. Anthropic's managed Code Review never approves `[M]`.

**Why it happens:** Findings are generated against the working tree and posted without mapping to the PR diff; posting is bolted on last.

**How to avoid:**
- Fetch the diff via the API, validate every `(path, line, side)` against hunks, move out-of-hunk findings to the review body.
- Use one `POST /pulls/{n}/reviews` with a `comments[]` array and `commit_id` pinned to the reviewed SHA; event is always `COMMENT`. Never send approve/request-changes.
- Draft first, show the exact payload, post only on explicit confirmation (the Anthropic command uses a private planned-comment list and `confirmed: true`; copy the pattern, not the text).
- Idempotency: stamp a hidden marker per finding (hash of path+anchor+claim) and skip those already present; refuse to post if HEAD SHA differs from reviewed SHA.
- Cap inline comments per review; the rest go to the summary.

**Warning signs:** `gh api ... 422`. Two identical comments after a re-run. Comments anchored to the wrong line after a force-push.

**Phase to address:** P-Output.

---

### Pitfall 10: Auto-fix mode introduces regressions or amplifies injected text

**What goes wrong:** No rigorous independent regression study was found `[L]`. Reported failure shapes: fixes confined to the changed file that miss downstream consumers; SQL-injection fix that patches one path and misses a parallel path; fixes that add dependencies; build verification fails but changes are still delivered (vendor docs) `[L]`. A reviewed comment executed as an agent instruction in autofix mode is the injection vector `[L]`.

**Why it happens:** Fix generation shares the same weak verification as detection, then "tests pass" is mistaken for correctness on thinly tested code.

**How to avoid:**
- "Applies verified fixes one time" (PROJECT.md) means: only CONFIRMED findings; fix text generated from the verified claim, **never** from PR comments or reviewed text; one pass, no loop; run tests before and after; if tests were already red, say so and do not auto-claim improvement.
- Show a diff of the fix and require confirmation per finding or batch; no commits, no pushes by the skill.
- Refuse fixes that add dependencies, touch lockfiles/CI/config, or exceed N lines; those become suggestions.
- Report "fix applied, tests unchanged/not covering this path" honestly when coverage is absent.

**Warning signs:** Fix touches files the finding did not cite. Test count unchanged but "all passing". Fix diff larger than the original issue.

**Phase to address:** P-Fix (depends on P-Verify).

---

### Pitfall 11: Parallel lanes blow up cost and add redundancy without adding coverage

**What goes wrong:** Anthropic reports about 4x tokens for agents and about 15x for its multi-agent research system, with token usage explaining ~80% of BrowseComp variance (Anthropic multi-agent research post) `[M]`. That is a research system, not code review; applying it is an extrapolation. Teams budgeting 3 agents at 3x report 5-15x bills `[L]`. Anthropic's managed Code Review averages $15-25/PR `[M]` (support docs plus several repeats; first-party figure). Lanes also share the same model and context so their misses correlate; N lanes may be one opinion counted N times `[L]`.

**Why it happens:** Every lane re-reads the same files; every finding triggers a verifier; no triage gate.

**How to avoid:**
- Triage gate (cheap model) as in Anthropic's flow: skip trivial/draft/already-reviewed PRs; scale lane count to diff size and risk (security lane only when diff touches auth/input/crypto/deps/SQL/shell).
- Tier models per lane (cheap model for compliance/triage, strongest for correctness/security and for verification).
- Shared read-only repo map produced once and passed as a compact brief, not re-derived per lane.
- Dedupe findings by `(file, anchor, class)` before verification so each unique claim is verified once.
- Per-run cap (max lanes, max verifier calls, max turns) and printed cost estimate before launch for large diffs. Lanes must not spawn subagents.
- Do not run Reflexion-style iteration (Pitfall 1).

**Warning signs:** Same file Read by every lane. Verifier invoked for each of 60 raw findings. Review of a 20-line change runs 6 lanes.

**Phase to address:** P-Lanes.

---

### Pitfall 12: Non-determinism makes reviews unrepeatable

**What goes wrong:** Same input, different findings. 70 Java commits, 5 runs at temperature 0: consistency 0.53 (GPT-4o mini) to 0.85 (Claude 3.5 Sonnet) (arXiv 2502.20747) `[M]` (paper plus Augment). Cross-task studies show similar run-to-run variance `[M]`. No study of finding-level stability on real PRs was found. Claude Code skills cannot set temperature.

**How to avoid:**
- Do not promise determinism. Make the **process** repeatable: fixed checklist per lane, structured JSON output with stable finding IDs (hash of path + normalized anchor + class), deterministic ordering (severity, path, line).
- Re-run stability check as a manual smoke test (run twice, diff finding IDs) in the verification checklist; track Blocking-set Jaccard.
- Idempotent posting (Pitfall 9) so re-runs do not spam.
- Report "not found in this run" as non-proof of absence.

**Warning signs:** Blocking set differs between two runs. User loses trust because a "fixed" issue reappears under a new name.

**Phase to address:** P-Output (IDs and ordering), P-Verify.

---

### Pitfall 13: Licensing and attribution of borrowed material (ccr.md and others)

**What goes wrong:** Borrowed text ships without the required notices, or with the wrong license.

**Facts found `[OBS]` unless noted:**
- Upstream: `posit-dev/skills/posit-dev/critical-code-reviewer` is **MIT**, `Copyright (c) 2025 Posit PBC`, SKILL.md frontmatter `license: MIT`, `author: Garrick Aden-Buie (@gadenbuie)`, `version: "1.2"` on main. First added 2026-01-09 (commit 59f7eb2e, originally under `open-source/`). Latest commits 2026-08-05 and 2026-10-07 (adds accessibility review and an external PR-review workflow, neutral AI-disclosure wording).
- Local `docs/ccr.md` is labeled `version: "1.3"`, `author: Garrick Aden-Buie (@gadenbuie), Ziad Hassan (@ziad-hsn)`, has `category`/`triggers` metadata, and has **no `license:` field**. It lacks the opening paragraph that posit main has, so it diverges from upstream in structure; I did not complete a full diff.
- A separate listing at ClawHub by `@ziad-hsn` (v0.1.0) is labeled **MIT-0** by the registry `[M]` (ClawHub page and secure.software mirror agree). The listing's own text asks reviews to begin with "Review feedback assisted by the critical-code-reviewer skill" linking to posit-dev. The Ziad GitHub account has no repo for this skill, so the relationship (copy, fork, or contributor) is **unestablished**. The name "Ziad Hassan" matches the GitHub profile `ziad-hsn` (display name "Ziad Hassan") `[OBS]`.
- Other docs/ licenses: `mattpocock/skills` MIT `[OBS]`; ponytail plugin MIT, `Copyright (c) 2026 DietrichGebert` `[OBS]` (local marketplace copy); caveman plugin Apache-2.0 `[OBS]` (local marketplace copy); `@opengsd/gsd-core` (source of gsd-code-reviewer) MIT per npm `[OBS]`; `cavecrew-reviewer` is in the caveman plugin, so presumed Apache-2.0 but not verified. I did not verify that each `docs/*.md` file is the exact upstream file.
- `anthropics/claude-code` (home of the `code-review` plugin command) has **no SPDX license**; LICENSE.md reads "(c) Anthropic PBC. All rights reserved. Use is subject to Anthropic's Commercial Terms of Service". `anthropics/skills` also shows NONE. `anthropics/claude-code-security-review` and `claude-code-action` are MIT.

**Pitfalls and how to avoid them:**
1. **MIT-0 label does not wash the notice away.** Any derivative of Posit's MIT work must carry Posit's copyright and permission notice; a downstream MIT-0 relabel by a third party does not remove that obligation `[M]` (MIT terms; this is analysis, not legal advice). Ship `THIRD_PARTY_NOTICES.md` (or `NOTICE`) containing the full Posit MIT text with the copyright line, plus the MIT text for mattpocock/ponytail and Apache-2.0 NOTICE/state-changes for anything taken from caveman.
2. Restore `license:` in the skill frontmatter, keep **both** author credits (as in ccr.md v1.3), and add `derived-from: posit-dev/skills (MIT)` in README plus provenance (upstream commit/version at fork time).
3. Decide the project's own license compatibly (MIT is simplest). Do not choose a copyleft or proprietary license for files that contain MIT-derived text without keeping the notices.
4. **Do not paste text from `anthropics/claude-code` plugin commands or from any all-rights-reserved source.** Reimplement the ideas (false-positive list, posting protocol) in your own words. Facts and ideas are not copyrightable; verbatim prose is.
5. Verify upstream drift in P-Legal: do a real diff of ccr.md against posit main (accessibility, PR-feedback workflow added after the local copy was taken) and decide which changes to adopt.
6. **User decision needed (do not resolve in research):** posit's v1.2 and the ClawHub listing instruct reviews posted to PRs to begin with a "review assisted by..." disclosure line. That is a runtime-behavior choice, not an MIT term, and it conflicts with the user's global rule to omit AI attribution from PR text. Credit goes in README/NOTICE/frontmatter; whether posted reviews carry a disclosure line should be a configurable, explicit choice.
7. Name collision, see Pitfall 14.

**Warning signs:** `LICENSE` file absent or only the project's own text. Frontmatter missing `license`. Verbatim paragraphs from non-MIT sources (grep for distinctive phrases before release).

**Phase to address:** P-Legal (start at P-Skeleton: put NOTICE in the first commit).

---

### Pitfall 14: Skill never triggers, collides with others, or wastes context

**What goes wrong:**
- Claude Code gives the skill listing a budget (default 1% of context, characters; per-entry description+when_to_use truncated around 1,536 chars). When over budget, **descriptions are dropped, least-used skills first**, so a brand-new skill is first to lose its description and never fires `[M]` (GitHub issue anthropics/claude-code#68135 plus several practitioner guides agree; exact figures vary by source and version, re-check before relying on them). Warning is easy to miss; `--debug`, `/doctor` reveal it.
- Anthropic says Claude undertriggers, so descriptions should be a little pushy and put triggers first; SKILL.md body under 500 lines; references one level deep; reference files over 300 lines need a table of contents `[M]` (official best-practices doc plus skill-creator).
- **Collision in this user's environment `[OBS]`:** the skill list already contains `critical-code-reviewer` (identical ccr description), built-in `code-review`, `caveman:caveman-review`, `ponytail:ponytail-review`, `mattpocock-skills:code-review`, `gsd-code-review`, `open-code-review:review`, `security-review`, and others. A same-named plugin shadows or loses ambiguously, and many near-identical descriptions make routing random.
- Upstream issue posit-dev/skills#19 criticizes ccr itself: language sections bloat SKILL.md (no progressive disclosure), theatrical wording ("Guilty Until Proven Exceptional", "Structural Contempt") is token waste, and negative framing dominates `[L]` (single reviewer, but consistent with Anthropic's guidance).

**How to avoid:**
- **Rename** (do not reuse `critical-code-reviewer`); pick a distinctive name and namespace; confirm with `claude plugin list` / `/doctor`.
- Description: third person, lead with distinct triggers ("review this PR/branch/diff", "verify findings", "post PR review") and add exclusions ("not for writing new code"); keep under ~500 chars so budget pressure is small.
- Core SKILL.md: workflow, lane dispatch, severity pointer, output selection. Language packs, OWASP, severity rubric, templates in `references/`, each linked with "read when X". Target well under 500 lines; aim for a few hundred.
- Delete voice theatre; keep decision criteria. Positive directives ("cite file:line for every finding") over "reject slop".
- Test triggering: ~10 should-trigger and ~10 should-not-trigger prompts; check the skill fires without being named, in a session with the user's real skill set loaded.

**Warning signs:** Needing to type `/skillname` for it to run. Skill missing from `/context` listing. Two skills answer the same "review this" prompt in different sessions.

**Phase to address:** P-Skeleton.

---

### Pitfall 15: ccr.md's persona and "iron law" drive overcorrection; keeping every rule verbatim backfires

**What goes wrong:** PROJECT.md says every ccr rule is "kept or deliberately replaced". Some rules actively work against the Core Value:
- "Assume every line is broken", "zero tolerance", "ruthlessly identify every flaw" instruct the directional bias measured in 2603.00539.
- Evaluate-the-artifact-not-intent is good (anti-anchoring) but is undercut if the description is read first (Pitfall 3).
- Slop-detector/structural-contempt items generate nitpicks (Pitfall 6).
- Sycophancy in the other direction: when the author pushes back in a follow-up turn, models tend to cave. The "AI-authored code needs extra scrutiny" rule is sound but cannot be done by the same model that wrote the code (Pitfall 16).

**How to avoid:** Write a keep/replace table in P-Skeleton with the reason for each ccr rule. Replace the persona with an **evidence contract** ("skeptical about claims, demanding about proof"). Keep: artifact-over-intent, cross-file checks for AI-authored code, doc-vs-source checks, tiered output. Replace: guilty-until-proven, nit rules (opt-in), emphatic voice. Add a follow-up-turn rule: a finding is retracted only by new evidence (code/test), never by author assertion.

**Warning signs:** Review verdict is "Request changes" on code that passes tests and the repo's CI. Findings phrased as character judgments.

**Phase to address:** P-Skeleton (decision table), P-Verify (anti-sycophancy in re-review).

---

### Pitfall 16: Reviewing AI-authored code with the same model family that wrote it

**What goes wrong:** Self-review misses: 31.7% of behavior-changing outputs missed in a Python 2-to-3 study, sometimes after the model articulated the distinguishing semantic (arXiv 2605.21537, via AgentPatterns) `[L]`; same-family testers pass generated code 9-17 points more often `[L]` (CodeRabbit blog, vendor with a stake); "Self-Correction Blind Spot" 64.5% failure `[L]`. Directionally consistent, numbers unreliable.

**How to avoid:** Run the verifier and detection in fresh contexts; offer an optional cross-model lane (a different model tier or vendor CLI) as an *optional accelerator*, not a hard dependency (portability constraint). Prefer execution-based evidence (tests) over agreement. If the session just wrote the code, warn that this is a same-context self-review and recommend spawning from a fresh session.

**Warning signs:** Review runs in the session that authored the diff. Zero Blocking findings on large AI-generated changes.

**Phase to address:** P-Lanes / P-Verify.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Copy ccr.md as SKILL.md and add sections | Fast start | 500+ line body, persona bias, license gaps, name collision | Never |
| Single verifier prompt, no refutation contract | Less prompt work | Rubber-stamp, Core Value void | Never |
| Hard-code language rules in SKILL.md | One file | Context waste every review | Never; use `references/` |
| One review comment per finding via `gh pr comment` | Simple | Spam, 422s, rate limits | Only for single summary comment |
| Skip NOTICE until release | Faster v0 | Easy to forget; legally required for MIT-derived text | Never (first commit) |
| Self-reported 0-100 confidence | Looks scientific | Uncalibrated, false trust | Only paired with evidence label |
| No trigger tests | Saves an afternoon | Skill silently never fires | Never |
| Fix mode loops until tests pass | "Self-healing" | Test-gaming, scope creep, hack-to-green | Never |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| GitHub reviews API | Inline comment on line outside hunk fails whole review (422) | Validate `(path,line,side)` against fetched diff; leftovers go in review body; one `POST /reviews` with `comments[]`, `commit_id` pinned |
| GitHub `gh` | Using `position` offsets | Use `line` + `side`; fetch with diff media type |
| `gh` posting | Re-run duplicates comments | Hidden per-finding marker; skip existing; refuse on SHA mismatch |
| GitHub Actions (if ever added) | `pull_request_target` + head checkout + secrets | `pull_request`, no head checkout at root, minimal token, human approval for externals |
| Claude Code skill loader | Long/overlapping description, new skill | Short third-person description, distinct triggers, test in real skill set, check `--debug` for listing overflow |
| Subagents | Lanes inherit Bash and `gh` | Explicit read-only tool allowlist per lane |
| Optional accelerators (Serena/codegraph/beads) | Hard dependency in instructions | "If available" branch with plain Grep/Read fallback, tested without them |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| N lanes re-read same files | Cost scales with lanes x files | Shared repo brief; dedupe before verify | Any diff over a few hundred lines |
| Verifier per raw finding | Verifier calls > lanes | Dedupe and cap pre-verification | Large PRs (dozens of raw findings) |
| Whole-diff in one lane | Attention dilution, misses | Split by file/module | Reportedly well above ~150 changed lines `[L]` |
| Long SKILL.md + large references loaded eagerly | Context burn before work starts | Link references conditionally | Every invocation |
| Lanes spawn subagents | Runaway cost | Forbid nested spawning, per-run cap | Any |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Lanes have Bash/`gh` | Injection leads to secret exfil into PR comment | Read-only lanes; orchestrator-only `gh` after confirmation |
| Reviewing untrusted PR then running its tests | Arbitrary code execution | Trust prompt; static-only for untrusted; sandbox/worktree |
| Feeding raw PR title/body/comments to detection lanes | Injection and anchoring | Redact hidden HTML/invisible chars; withhold from detection lanes |
| Posting raw agent output | Secrets or env in public comment | Outbound payload preview plus secret-pattern scan |
| Fix text derived from reviewed comments | Injection becomes file edits | Fix from verified claim only |
| Trusting "ignore previous instructions" refusal in delimiter | Bypassed (documented bypasses exist) | Defense in depth: tools, scopes, human confirmation |
| Secrets in diff echoed into findings | Leak into logs/JSON/comments | Redact secrets in quotes (show prefix only) |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| 40-finding wall of text | Bot-wallpaper, real bug missed | Cap tiers, collapse Suggestions, lead with verdict and count |
| Voice theatre ("brutal") | Alienates author, no signal | Neutral, evidence-first tone |
| Confidence number only | False authority | Evidence label plus how to re-check |
| Silent skips (files too large, binary, generated) | False sense of completeness | State coverage: files reviewed, skipped, why |
| PR post without preview | Public mistakes | Draft, preview payload, confirm |
| Four output modes with different content | Confusing | One canonical JSON; terminal/PR are renderings of it |

## "Looks Done But Isn't" Checklist

- [ ] **Verification pass:** often a second prompt that agrees; verify the verifier can output REFUTED and does so on a planted false finding.
- [ ] **Full-context claim:** often only the diff was read; verify output lists inspected non-diff files and each finding cites one.
- [ ] **Injection resistance:** often just a "ignore instructions" sentence; verify with a fixture diff containing an injected comment plus hidden HTML in a PR body, and confirm no tool call or comment leak.
- [ ] **PR posting:** often works on the demo PR; verify an out-of-hunk finding, a deleted-line finding, a force-pushed head, and a re-run.
- [ ] **Triggering:** often works when named; verify it fires unprompted next to the user's real skill set, and the listing budget warning is absent.
- [ ] **Licensing:** often LICENSE exists but Posit notice and authors are missing; verify NOTICE contains Posit MIT text, `license:` field present, both authors credited.
- [ ] **Fix mode:** often "tests pass"; verify baseline-vs-after test results, no unrelated file edits, no new deps.
- [ ] **Portability:** often silently uses Serena/codegraph/beads; verify run in a clean environment without them.
- [ ] **Cost:** often untested on a big diff; verify lane count and token estimate on a 1,000-line diff.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Verifier rubber-stamp discovered late | HIGH | Redesign verifier contract; re-run on saved findings; re-baseline severity |
| Injection or leak in a posted comment | HIGH | Delete comment, rotate exposed credentials, add outbound scan and read-only lanes, disclose |
| License notice missing after publish | MEDIUM | Add NOTICE and frontmatter, release patch, note in changelog |
| Skill name collision / non-triggering after release | MEDIUM | Rename in a minor release with alias note; shorten description; re-run trigger tests |
| Noisy reviews drive users away | MEDIUM | Tighten caps, raise evidence bar, move nits to opt-in; ship dropped-findings log |
| Duplicate PR comments | LOW | Delete duplicates by marker; add idempotency |
| Cost overrun | LOW | Lower lane caps; add triage gate; print estimate |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| 1 Verifier rubber-stamp | P-Verify | Planted false finding is REFUTED; fresh-context contract documented |
| 2 Fake-precision confidence | P-Verify | Score maps to evidence label; no Blocking without trace |
| 3 Anchoring on PR text | P-Lanes | Detection lanes receive no PR description (inspect lane inputs); A/B same diff with/without description |
| 4 Prompt injection / exfil | P-Lanes, P-Output | Injection fixture produces no tool call; outbound scan blocks planted secret |
| 5 False positives | P-Verify | Evidence fields mandatory; do-not-flag list present; spot-check 10 reviews |
| 6 Fatigue / severity inflation | P-Verify, P-Output | Rubric in references; caps enforced; Blocking share sane on sample diffs |
| 7 Diff-only / dilution | P-Lanes | "Inspected" list non-empty; per-lane context budget set |
| 8 Executing PR code | P-Verify, P-Fix | Trust prompt exists; untrusted run executes nothing |
| 9 PR posting safety | P-Output | Out-of-hunk, rerun, SHA-mismatch tests pass; event always COMMENT |
| 10 Auto-fix regressions | P-Fix | Baseline vs after tests; fix limited to cited files |
| 11 Cost blowup | P-Lanes | Triage gate; caps; 1,000-line diff estimate recorded |
| 12 Non-determinism | P-Output, P-Verify | Stable finding IDs; two-run Blocking-set comparison documented |
| 13 Licensing/attribution | P-Legal (NOTICE in first commit) | NOTICE, `license:`, both authors; grep for non-MIT verbatim text |
| 14 Triggering / collision / bloat | P-Skeleton | Trigger tests; unique name; body line count; `--debug` clean |
| 15 ccr persona/overcorrection | P-Skeleton | Keep/replace table reviewed by user |
| 16 Same-family self-review | P-Lanes, P-Verify | Fresh-context spawn; self-review warning |

## Open Questions (for planning)

- Whether the posted-review "assisted by" disclosure line should exist, default off or on (conflicts with user's global no-attribution rule; credit via NOTICE regardless). Needs user decision.
- Relationship between Ziad Hassan's ClawHub variant and Posit's skill (copy, fork, contributor); affects only attribution wording, not the Posit notice requirement.
- How much of upstream posit main (accessibility, external-PR feedback workflow) to adopt; requires a real diff against ccr.md v1.3.
- Whether to add a minimal trigger/smoke fixture set despite "no eval corpus" scope decision (recommended; not a seeded-bug corpus).

## Sources

Academic and primary (fetched this session):
- CR-Bench: https://arxiv.org/html/2603.11078v1
- Overcorrection in requirement-conformance review (Jin and Chen): https://arxiv.org/abs/2603.00539 ; summary https://agentpatterns.ai/patterns/anti-patterns/llm-review-overcorrection/
- Contextual bias in LLM-assisted security code review: https://arxiv.org/html/2603.18740v2
- SWE-PRBench: https://arxiv.org/abs/2603.26130
- Biases in LLM code judges: https://arxiv.org/html/2505.16222v2 ; anchoring in LLM-as-judge: https://arxiv.org/html/2608.25869v1 (snippet only)
- Determinism in LLM code review: https://arxiv.org/html/2502.20747
- Defect-focused automated review: https://arxiv.org/pdf/2505.17928 (metadata only read)
- Automated Code Review in Practice: https://arxiv.org/pdf/2412.18531 (snippet only); Modern Code Review roadmap: https://arxiv.org/pdf/2405.18216 (snippet only)
- Rethinking Code Review Workflows with LLM Assistance: https://arxiv.org/pdf/2505.16339

Security and incidents:
- Comment and Control (SecurityWeek): https://www.securityweek.com/claude-code-gemini-cli-github-copilot-agents-vulnerable-to-prompt-injection-via-comments/
- VentureBeat: https://venturebeat.com/security/ai-agent-runtime-security-system-card-audit-comment-and-control-2026
- CSA research notes: https://labs.cloudsecurityalliance.org/research/csa-research-note-comment-control-github-prompt-injection-20/ and https://labs.cloudsecurityalliance.org/research/csa-research-note-claude-code-github-action-prompt-injection/
- Vectara case study: https://github.com/vectara/awesome-agent-failures/blob/main/docs/case-studies/comment-and-control-prompt-injection.md
- claude-code-action security doc: https://github.com/anthropics/claude-code-action/blob/main/docs/security.md
- claude-code-security-review README: https://github.com/anthropics/claude-code-security-review
- The Hacker News on the GitHub Action flaw: https://thehackernews.com/2026/06/claude-code-github-action-flaw-let-one.html

Vendor and practitioner (treat numbers as LOW):
- Augment, AI code review accuracy (secondary aggregator): https://www.augmentcode.com/guides/ai-code-review-accuracy
- Cubic: https://www.cubic.dev/blog/the-false-positive-problem-why-most-ai-code-reviewers-fail-and-how-cubic-solved-it and https://www.cubic.dev/blog/how-ai-code-review-works
- CodeRabbit independence: https://www.coderabbit.ai/blog/code-review-needs-independence ; Augment adversarial review: https://www.augmentcode.com/guides/adversarial-code-review
- Self-review failure: https://agentpatterns.ai/patterns/anti-patterns/self-review-modernization-failure/
- Anthropic code review cost and design: https://support.claude.com/en/articles/14233555-set-up-code-review-for-claude-code ; https://www.helpnetsecurity.com/2026/03/10/anthropic-claude-code-review/
- Anthropic code-review plugin command (read for ideas only; all rights reserved): https://github.com/anthropics/claude-code/tree/main/plugins/code-review
- Multi-agent cost: https://www.augmentcode.com/guides/multi-agent-cost-compounding ; Anthropic multi-agent research summary https://theaiengineer.substack.com/p/how-anthropic-built-multi-agent-deep
- Autofix: https://docs.github.com/en/code-security/code-scanning/managing-code-scanning-alerts/responsible-use-autofix-code-scanning ; https://www.systemshardening.com/articles/cicd/ai-autofix-security-review/

GitHub API behavior:
- https://docs.github.com/en/rest/pulls/reviews ; https://docs.github.com/en/rest/pulls/comments ; https://github.com/fullsend-ai/agents/issues/430 ; https://github.com/pingdotgg/t3code/issues/12549 ; https://github.com/cli/cli/issues/13358

Skill authoring and triggering:
- Official best practices: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- skill-creator: https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md
- Listing budget: https://github.com/anthropics/claude-code/issues/68135 ; https://primeline.cc/blog/claude-code-skills-not-triggering ; https://dev.to/rulestack/too-many-claude-code-skills-how-the-listing-budget-decides-which-descriptions-claude-sees-4a6m

Licensing and provenance:
- Upstream skill and license: https://github.com/posit-dev/skills/blob/main/posit-dev/critical-code-reviewer/SKILL.md ; https://github.com/posit-dev/skills/blob/main/LICENSE
- Upstream critique issue #19: https://github.com/posit-dev/skills/issues/19
- ClawHub variant: https://clawhub.ai/ziad-hsn/critical-code-reviewer ; https://secure.software/clawhub/packages/@ziad-hsn/critical-code-reviewer
- Anthropic claude-code LICENSE.md: https://github.com/anthropics/claude-code/blob/main/LICENSE.md
- Local: `/home/dd/projects/review-code/docs/ccr.md` frontmatter; local marketplace LICENSE files for ponytail and caveman

---
*Pitfalls research for: LLM-agent code-review skill/plugin*
*Researched: 2026-10-17*
