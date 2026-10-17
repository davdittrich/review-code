# Stack Research

**Domain:** Claude Code plugin shipping an LLM code-review skill (SKILL.md + references/ + subagent lanes + terminal / PR-comment / JSON / fix output)
**Researched:** 2026-10-17
**Confidence:** HIGH on plugin/skill/agent/hook/marketplace formats (read from official docs and the installed official plugins this session). MEDIUM on competitor internals (vendor docs and search summaries). LOW on the Ziad Hassan licence (unverified).

Confidence note: `gsd_run query classify-confidence` returned `LOW` for providers `websearch`, `webfetch` and `gh`. The seam only has tiers for package registries, so it cannot grade those providers. Tiers below are assigned by source class instead: official vendor docs fetched directly or files read from disk = HIGH; vendor docs via search summary = MEDIUM; third-party blogs = LOW.

## Version floor and runtime facts

- Tested against Claude Code **2.1.302**, `gh` **2.102.0** (both local, read this session).
- The manifest has **no engine or minimum-version field**. State the floor in the README and in SKILL.md `compatibility` (CC ignores it, the spec allows it).
- Provisional floor **2.1.246** (MEDIUM, confirm at build time). The recommended design uses only these: plugin skills, plugin agents (`tools`, `model`, `effort`, `maxTurns`), `hooks/hooks.json`, `claude plugin validate`. Avoid features with newer minimums in v1:
  - `onFailure: "block"` (2.1.295)
  - agent `autoCompactWindow` (2.1.296)
  - agent `omitClaudeMd` (2.1.271)
  - `--max-findings` (2.1.288)
  - `userConfig` `options` (2.1.271)
- Hook runtime is **not guaranteed**. Claude Code does not ship `jq`, `node` or `python` for hooks. Any hook that parses JSON must name its dependency, or be left out of v1.

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended | Confidence |
|------------|---------|---------|-----------------|------------|
| Claude Code plugin (`.claude-plugin/plugin.json`) | CC >= 2.1.246 | Packaging and distribution unit | The only unit that bundles skill + agents + hooks and installs by name. Manifest is optional, but write it for `displayName`, `version`, `license`, `repository`. Only `name` is required; kebab-case. Everything else sits at the plugin root, never inside `.claude-plugin/`. | HIGH |
| Agent Skill (`skills/<name>/SKILL.md`) | Agent Skills spec + CC extensions | The review skill: lean core + `references/` | Open standard (agentskills.io, Apache-2.0 spec repo). Progressive disclosure is the format's design: ~100 tokens of metadata at startup, body on activation, `references/` only when read. Body under 500 lines; spec recommends under 5,000 tokens. | HIGH |
| Plugin subagents (`agents/<lane>.md`) | CC >= 2.1.246 | Parallel reviewer lanes + verifier | Markdown + YAML frontmatter. Namespaced `review-code:<name>`. Each lane gets its own context and CLAUDE.md. Default cap is 20 concurrent subagents and nesting depth 3, so a 3 to 5 lane fan-out is safe. | HIGH |
| `hooks/hooks.json` (plugin-level) | CC >= 2.1.246 | Optional enforcement only | Plugin agents **ignore** `hooks`, `mcpServers` and `permissionMode` in their own frontmatter. Plugin-level hooks are the only enforcement point. Match lanes by scoped agent type (`^review-code:verifier$`). See Hooks policy below. | HIGH |
| `.claude-plugin/marketplace.json` in the same repo | CC >= 2.1.246 | Self-hosted marketplace | A repo can be plugin and marketplace at once: one entry with `"source": "./"`, `name` identical to `plugin.json`. Users run `claude plugin marketplace add <owner>/review-code`, then `claude plugin install review-code@<marketplace>`. No submission form. | HIGH |
| GitHub CLI `gh` | >= 2.x (tested 2.102.0) | PR read and review posting | Only external runtime dependency. `gh api --input payload.json` posts a whole review atomically. `gh pr diff` / `gh pr view --json` read the PR. Do not require the `gh-pr-review` extension. | HIGH |
| `claude plugin validate --strict` | ships with CC | Manifest, marketplace and path validation | The authoritative check. Run it in CI. `--strict` turns warnings (unknown field, missing `version`) into failures. | HIGH |

### Supporting Libraries and Formats

| Library / format | Version | Purpose | When to Use | Confidence |
|------------------|---------|---------|-------------|------------|
| JSON findings schema (own) | v1 | Output mode "machine-readable JSON" | Always. One schema, three consumers: JSON mode, PR-comment builder, fix mode. Fields: `id, file, line, end_line, side, severity, category, title, evidence, confidence(0-100), verified(bool), fix, source_lane`. Mirrors claude-security's `Finding` (read on disk) and the official `ReportFindings` entry (file location + one-sentence summary + category). | HIGH (design), MEDIUM (field set) |
| `claude -p --json-schema <schema>` | CC flag (confirmed in `claude --help`) | Constrain headless JSON output | Document for CI users. In claude-code-action the same schema surfaces as the `structured_output` output. Do not make the skill depend on it. | HIGH |
| SARIF 2.1.0 | OASIS 2.1.0 | Optional export for GitHub code scanning | Defer to a later phase. Only when users ask for Security-tab upload. claude-security shows the mapping (`scripts/lib/sarif.py`, proprietary, so do not copy). | MEDIUM |
| GitHub REST `POST /repos/{o}/{r}/pulls/{n}/reviews` | API 2022-11-28 | Inline PR comments | See PR posting protocol. Use `line` / `side` / `start_line`, not diff `position`. | HIGH |
| `skills-ref validate` | agentskills repo | Spec-conformance check of SKILL.md | Optional CI step to catch spec drift. `name` must match the directory, lowercase-hyphen, at most 64 characters; `description` at most 1,024. | HIGH |
| `claude plugin eval` | ships with CC | Behavioural regression suite (`evals/<case>/prompt.md` + `graders/*.md`) | Not in v1 (PROJECT.md puts an eval corpus out of scope). Reserve the `evals/` directory name. It is the built-in route if quality proof is wanted later. | MEDIUM |
| Dynamic injection `` !`cmd` `` in SKILL.md | CC | Pre-load PR/diff metadata before the model runs | Avoid. A non-zero exit aborts the whole skill invocation, and anything not allowed outside auto mode aborts too. If used, append `|| true` and pre-approve via `allowed-tools`. | HIGH |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `claude --plugin-dir ./` | Load the plugin for one session while developing | `/reload-plugins` picks up edits. A relative-path marketplace added from a local path reads files in place, so version bumps are not needed during dev. |
| `claude plugin validate --strict .` | Manifest and marketplace gate | Run on the repo root. It validates `plugin.json` and `marketplace.json`. Fix warnings, do not suppress. |
| `claude plugin tag --push` | Release tag `{name}--v{version}` | Only needed if another plugin declares a semver range on this one. Otherwise a plain git tag is enough. |
| `claude plugin details review-code` | Component inventory and projected token cost | Use to prove "lean core": check the skill's always-on description cost. |
| `/skill-doctor` (CC >= 2.1.252) | Description cost and usage report | Catches an over-long description. |
| `shellcheck` | Lint any `scripts/*.sh` | Only if scripts ship. |

## Plugin layout (prescriptive)

```text
review-code/                        # repo root = plugin root = marketplace root
├── .claude-plugin/
│   ├── plugin.json                 # name, displayName, version, description, author, license, repository, homepage
│   └── marketplace.json            # one entry, source "./"
├── skills/
│   └── review/                     # name: review  -> /review-code:review
│       ├── SKILL.md                # lean core, iron law + verification gate in the first 5k tokens
│       └── references/             # one level deep from SKILL.md, each file focused
│           ├── severity-rubric.md
│           ├── output-terminal.md
│           ├── output-json.md        # schema
│           ├── output-pr-comments.md # gh protocol
│           ├── fix-mode.md
│           ├── verification.md
│           └── lang-*.md             # python, r, js-ts, sql, ... loaded by file type
├── agents/                         # review-code:<name>
│   ├── correctness-reviewer.md
│   ├── security-reviewer.md
│   ├── spec-standards-reviewer.md
│   └── verifier.md
├── hooks/hooks.json                # OPTIONAL, empty or absent in v1 (see Hooks policy)
├── scripts/                        # NOT bin/ (see What NOT to Use)
├── LICENSE
├── NOTICE                          # attribution: ccr authors, Apache-2.0 notices if borrowed
└── README.md
```

Plugin name `review-code` matches the repo. Skill `name: review` gives `/review-code:review`. Both are discretionary naming choices, not format requirements.

### SKILL.md frontmatter (recommended)

```yaml
---
name: review                      # must equal directory name (spec)
description: >                    # <= 1,024 chars (spec). CC allows 1,536 incl. when_to_use. Front-load the use case.
  Adversarial, evidence-verified code review of diffs, branches and PRs ...
license: MIT
compatibility: Claude Code >= 2.1.246; git; gh (PR modes only)
metadata: { author: "...", upstream: "critical-code-reviewer 1.3 (Aden-Buie, Hassan)", version: "0.1.0" }
argument-hint: "[target] [--json] [--comment] [--fix]"
allowed-tools: Read Grep Glob Bash(git diff *) Bash(git log *) Bash(git show *) Bash(git blame *) Bash(gh pr view *) Bash(gh pr diff *)
---
```

`allowed-tools` pre-approves read-only commands only, for the invoking turn. It does not restrict tools. Leave the posting and fix commands out so they stay behind a prompt.

## Constraints that drive the architecture

Each constraint was read in the official docs this session.

1. **Do not set `context: fork` on the main skill.** `AskUserQuestion` is stripped from every subagent, so "confirm before posting" and "confirm before fixing" must run in the main thread. Lanes are spawned from the main thread via the `Agent` tool (depth 1).
2. **Permission prompts are not the confirmation gate.** `auto` / `bypassPermissions` skip them and `-p` has no user. The skill must ask explicitly (`AskUserQuestion`) before any external mutation. Headless runs need an explicit opt-in flag such as `--comment`, which must be passed in the invocation string.
3. **Plugin agents cannot carry `hooks`, `mcpServers` or `permissionMode`.** Copying the agent into `.claude/agents/` is the only way to use them. Read-only lanes come from a `tools:` allowlist (`Read, Grep, Glob`, plus a restricted Bash only for the verifier). `Agent(type)` parentheses inside a subagent definition are ignored.
4. **Compaction keeps only the first 5,000 tokens of each skill**, with a combined 25,000-token budget across skills. Put the iron law, the verification gate and the output-mode switch at the top of SKILL.md.
5. **Description at most 1,024 characters and `name` equals directory name**, so the skill stays spec-valid. Claude Code-only keys (`argument-hint`, `disable-model-invocation`, `context`, `paths`, `effort`, `hooks`) are fine in Claude Code, but **claude.ai / Skills API uploads hard-error on them**. Position this as a Claude Code plugin and do not promise claude.ai upload.
6. **Do not ship a top-level `bin/`.** claude.ai and Cowork refuse to install plugins that contain it. Use `scripts/` and write `${CLAUDE_PLUGIN_ROOT}/scripts/...` in the SKILL.md body (substituted inline). The variable is **not** in the Bash tool's environment, so scripts must take paths as arguments.
7. **Natural-language collision with the bundled `/code-review` and `/review`.** The plugin skill is namespaced, but description matching can still select the bundled skill. User-side fix, to document in the README: `"skillOverrides": {"code-review": "user-invocable-only"}`. The bundled `/code-review` reads CLAUDE.md and ignores REVIEW.md.
8. **Subagent model routing.** Resolution order is per-invocation `model`, then frontmatter `model`, then `CLAUDE_CODE_SUBAGENT_MODEL`, then the main model. Recommend lanes `model: sonnet`, `effort: high`; verifier and aggregation `model: inherit`. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` overrides everything, so document it.
9. **Lanes get CLAUDE.md by default.** Do not use `omitClaudeMd`. The standards lane depends on repo conventions, so full-context review requires it.
10. **`isolation: worktree`** on a fix-mode agent is available if fixes are delegated. Prefer applying fixes in the main thread after confirmation, through the user's permission mode.

## PR posting protocol (`gh`, no extensions)

Read:

```bash
gh pr view <n> --json number,title,body,baseRefName,headRefOid,isDraft,state,files
gh pr diff <n>                                  # unified diff: the anchor source
gh api repos/{o}/{r}/pulls/{n}/comments        # existing threads: dedupe, skip already-reviewed
```

Write, in this order:

1. **Validate every anchor against `gh pr diff`.** `line` + `side` (`RIGHT` for added/context lines, `LEFT` for deleted lines) must fall inside a hunk of that file. Any single bad anchor makes GitHub return **422 "Line could not be resolved"** and rejects the **entire** review. Findings with no valid anchor go into the review `body`, not `comments[]`.
2. Build `payload.json` with `commit_id` (= `headRefOid`, so comments are not outdated), `body` (summary), `comments[]` (`path, line, side, start_line, start_side, body`). **Omit `event`** to create a PENDING review. Suggestions use a fenced ` ```suggestion ` block in `body`. Suggestions are not applicable from a pending review in the UI.
3. `gh api --method POST repos/{o}/{r}/pulls/{n}/reviews --input payload.json`
4. Ask the user (main thread). On yes: `gh api --method POST .../reviews/{id}/events -f event=COMMENT` (default `COMMENT`; never default to `APPROVE` or `REQUEST_CHANGES`). On no: `gh api --method DELETE .../reviews/{id}`.
5. The endpoint triggers notifications and has secondary rate limits. Post once, in one request, and never in a loop.
6. Official-plugin comment convention worth keeping: link code with the **full 40-character SHA** and a line range (`blob/<sha>/path#L10-L14`), because `$(git rev-parse HEAD)` does not render in Markdown.

Dedupe: check for an earlier review marker before posting. Put a hidden `<!-- review-code:v1 head=<sha> -->` in the body and skip or update when it is already present for that head. This mirrors the official `/code-review` "already reviewed" check.

Write the payload to the session scratchpad and delete it after use. Do not write into the repo.

## Hooks policy

**Recommendation: ship no hooks in v1.** `hooks/hooks.json` is optional, and a hook that parses JSON needs `jq`, `node` or `python`, which are not guaranteed on the user's machine. PROJECT.md requires portability.

If a phase later adds enforcement, this is what the docs support:

| Need | Mechanism | Dependency |
|------|-----------|------------|
| Reject a lane's malformed output | `SubagentStop` hook in `hooks/hooks.json`, `matcher: "^review-code:verifier$"`, exit 2 with stderr reason. The input carries `last_assistant_message`. | A JSON parser. Name it (`jq` or `node`) in the README. |
| Keep a lane read-only beyond `tools:` | `PreToolUse` on `Bash` with `agent_type` scoping, exit 2 | Same |
| Stop an infinite block loop | Confirm the `SubagentStop` loop-guard field in live docs. It was not in the part of the hooks page that was read. | Gap |

Prefer exec form (`"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/x.js"]`) over shell form. The `if` field filters only on `PreToolUse`, `PostToolUse` and their failure and permission variants, and it is best-effort, not enforcement.

## Existing public review tools and their formats

| Tool | Version / licence | Packaging / config format | Output shape | Reuse for us |
|------|-------------------|---------------------------|--------------|--------------|
| **Anthropic `code-review` plugin** (read on disk) | Apache-2.0, official marketplace | `.claude-plugin/plugin.json` (name, description, author only) + a single `commands/code-review.md` with `allowed-tools: Bash(gh ...)`. No agents or skills dir. | Posts one `gh pr comment`. 5 parallel Sonnet agents (CLAUDE.md x2, shallow bugs, git blame, prior-PR comments), Haiku scorers, 0/25/50/75/100 rubric, **threshold 80**. "### Code review / Found N issues" with full-SHA links. | Pattern ideas (eligibility pre-check, scoring rubric, false-positive list) are borrowable with Apache-2.0 notice. Its "diff-only" bug agent contradicts our full-context gate. |
| **Anthropic `pr-review-toolkit`** (read on disk) | Apache-2.0 | `agents/*.md` (code-reviewer, silent-failure-hunter, pr-test-analyzer, type-design-analyzer, comment-analyzer, code-simplifier) + `commands/review-pr.md` | Per-aspect text reports; agent confidence 0-100, report at least 80; `model: opus` on code-reviewer. | Best model for lane agent frontmatter, `<When to invoke>` scenarios in the body. |
| **Anthropic `claude-security`** (read on disk) | **All rights reserved** | 9 agents (inventory, researcher, 3-voter `scan-verifier` that defaults to FALSE_POSITIVE, redactor...), `workflows/scan.js`, Python `scripts/lib` (finding, sarif, cwe) | `findings.json`, SARIF 2.1.0, rendered report. Severity CRITICAL..LOW, confidence low/medium/high. | **Do not copy prompts or code.** Conceptual lesson only: independent refutation verifier with "default FALSE_POSITIVE". |
| **Bundled `/code-review`** (`/review` alias) | Built into CC | Not on disk. A project skill named `code-review` replaces it. Reads CLAUDE.md, ignores REVIEW.md. | Terminal text, or `ReportFindings` list in hosts (file location + one-sentence summary + category). Effort low..max, `ultra` in cloud. `--comment` (inline on GitHub, one note on GitLab via `glab`), `--fix`, limit 32 findings. | Our flags (`--comment`, `--fix`, `--json`) should feel the same. Differentiate on verification evidence and full-context gate. |
| **Managed Code Review service + REVIEW.md** | Anthropic service | Repo-root `REVIEW.md` (+ subdir copies), read from the base branch. Freeform Markdown. | Inline comments with severity 🔴 Important / 🟡 Nit / 🟣 Pre-existing, check run "Claude Code Review", hidden `bughunter-severity: {"normal":n,"nit":n,"pre_existing":n}` JSON line. | Adopt the 3-level taxonomy and the Pre-existing class. Honour a repo `REVIEW.md` if present. |
| **anthropics/claude-code-action** | `@v1` (tag `v1`), MIT | GitHub Action. Inputs `prompt`, `claude_args`, `plugins`, `plugin_marketplaces`, `use_sticky_comment`, `classify_inline_comments` (default true), `include_fix_links` (default true). | Inline comments via MCP tool, sticky comment, output `structured_output` when `--json-schema` is passed through `claude_args`. | Our plugin must install and run in it: `plugins: review-code@<mkt>`. Test headless (`-p`). |
| **anthropics/claude-code-security-review** | MIT, no releases (use `@main`) | Action + `.claude/commands/security-review.md`. Inputs `custom-security-scan-instructions`, `false-positive-filtering-instructions` (text file with HARD EXCLUSIONS). | `findings-count` and `results-file` outputs. Fields: file, line, severity, category, description, exploit scenario, recommendation. | Hard-exclusion list idea (DoS, rate-limit, theoretical input validation, open redirect). |
| **CodeRabbit** (`coderabbitai/skills`) | v1.1.1, MIT | `.claude-plugin/`, `.cursor-plugin/`, `.codex-plugin/`, `skills/{code-review,autofix}/SKILL.md`, `agents/{code-reviewer,autofix}.md`. Wraps the local `cr` CLI. Repo config is `.coderabbit.yaml` (`path_instructions: [{path, instructions}]`, profiles chill/assertive). | `cr review --agent` emits structured JSON for agents. Severity critical..none, fix-and-re-review loop. | Layout is the closest analogue to ours (skills + agents in one repo). Multi-harness manifests are a nice-to-have. Skip in v1. |
| **Qodo PR-Agent** | v0.48.0, MIT | `.pr_agent.toml`, `[pr_reviewer] extra_instructions`, `num_max_findings` (3), `persistent_comment`. Settings cascade global, repo, CLI. | Persistent edited PR comment, `/review`, `/improve`, `/describe`. No custom output format. Newer Qodo agent uses `[review_agent]`. | Idea: persistent single comment edited in place, `num_max_findings` cap. Note the security warning: never take the config branch from PR-controlled input. |
| **Greptile** | SaaS | `greptile.json` or `.greptile/{config.json,rules.md,files.json}`. Strictness 1-3, structured rules with `id`, `scope`, `severity`, `disabledRules`. | PR summary, 0-5 confidence score, severity tags (P1...), suggestions, sequence diagrams. | Cascading per-directory rules with disable-by-id is a good model for our `references/` overrides. |
| **GitHub Copilot code review** | SaaS | `.github/copilot-instructions.md`, `.github/instructions/*.instructions.md` with `applyTo` glob, `excludeAgent: "code-review"\|"cloud-agent"`. The 4,000-character cap was removed 2026-06-12. | Inline comments with suggestion blocks. | We should read these files as repo-convention sources for the standards lane (search result, MEDIUM). |
| **Cursor Bugbot** | SaaS | `.cursor/BUGBOT.md` (walks up from changed files; 30k chars per file, 100k total), `bugbot.yaml`. | PR comments, Autofix via a cloud agent. Pipeline: 8 parallel passes with shuffled diff order, majority vote, validator model. | Supports our design: parallel lanes + vote + validator. Read `BUGBOT.md` as a convention source. |
| **OpenCodeReview (OCR)** (`alibaba/open-code-review`, read on disk) | v1.13.1, Apache-2.0 | `.claude-plugin/marketplace.json` at repo root → `plugins/open-code-review/claude-code` (`plugin.json` with `commands`) + two portable `skills/*/SKILL.md` (spec fields: `license`, `compatibility`, `metadata`). Needs the external Go `ocr` CLI. | `ocr review --audience agent`, line-level comments. `delegate` mode: the CLI only selects files and rules, and the host agent reviews. | **Rejected as a dependency** (external binary breaks portability). Its delegate mode proves the "host does the review" shape. Its SKILL.md frontmatter is a good spec-compliant example. |
| **superpowers `requesting-code-review`** | v7.0.0, MIT | `skills/requesting-code-review/SKILL.md` (frontmatter name + description only) + `code-reviewer.md` template with `{DESCRIPTION} {PLAN_OR_REQUIREMENTS} {BASE_SHA} {HEAD_SHA}`. | Strengths / Issues by Critical, Important, Minor / Assessment. | Template-with-placeholders handoff keeps the reviewer's context clean. Same idea as matt.md's Spec axis. |
| **posit-dev `critical-code-reviewer`** (upstream of ccr.md) | v1.2, **MIT** (verified: SPDX MIT on repo, `license: MIT` in frontmatter) | Single `SKILL.md`, no `references/`. Adds guided walkthrough and pending-review publishing via the `gh-pr-review` extension. | Severity tiers, accepted-findings-only publishing, "Keep unverified concerns phrased as questions". | The base we extend. Its pending-review publishing flow is the right idea; we use plain `gh api` instead of the extension. |

## Licensing and attribution (decision needed from roadmap)

| Source | Licence | Status | Action |
|--------|---------|--------|--------|
| posit-dev/skills `critical-code-reviewer` v1.2 (Garrick Aden-Buie) | MIT | HIGH (repo SPDX and frontmatter read) | Keep copyright and permission notice in `NOTICE` / `LICENSE`. |
| ccr.md v1.3 additions credited to Ziad Hassan | **Unknown** | **LOW** (not verified; his published copy's licence not found) | Gap. Confirm before shipping v1.3-only text. Do not assume MIT. |
| Anthropic `code-review`, `pr-review-toolkit`, `security-guidance` | Apache-2.0 | HIGH (LICENSE files read) | If prompts are adapted: ship the Apache-2.0 text, keep notices, and state changes. |
| Anthropic `claude-security` | All rights reserved | HIGH | **Do not borrow.** |
| superpowers, claude-code-action, PR-Agent, coderabbit/skills | MIT | HIGH (GitHub API SPDX) | Attribution if text is borrowed. |
| The plugin's own licence | **Open decision** | n/a | MIT is simplest and matches the MIT upstream. Apache-2.0 only if substantial Apache text is incorporated. |

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Skill + plugin subagents (no external binary) | Wrap an external CLI (`ocr`, `cr`) | Only if the user already runs that CLI. It breaks the portability constraint in PROJECT.md and adds an install and credential step. |
| Plain `gh api --input` for review posting | `gh-pr-review` extension | If users want GraphQL thread resolution. An extra install and a moving API. Can be an optional accelerator. |
| One plugin repo that is also its own marketplace (`source: "./"`) | Submit to Anthropic's directory (`claude.ai/directory/manage`, paid claude.ai plan) | After v1 is stable. The portal applies extra rules `validate` does not check. The official marketplace takes no direct submissions. |
| `commands/` not used; skill-only | Slash-command markdown (`commands/review.md`) | Docs say prefer `skills/` for new plugins. Official `code-review` still uses `commands/`, so that is legacy style. |
| Fan-out from the main-thread skill (lanes at depth 1) | `context: fork` skill that runs the review as one subagent | When no user confirmation is needed (pure report mode). Forking removes `AskUserQuestion`, so it cannot gate posting. |
| Own JSON schema + optional SARIF later | SARIF as the primary format | Only if the main consumer is GitHub code scanning. SARIF is verbose and loses `confidence` / `verified` unless placed in a property bag. |
| No hooks in v1 | `SubagentStop` schema validator | When lanes demonstrably emit malformed JSON. Then add it and name the JSON-parser dependency. |
| Single-language plugin manifest (`.claude-plugin`) | Multi-harness manifests (`.codex-plugin`, `.cursor-plugin`) like CodeRabbit and OCR | If portability beyond Claude Code becomes a goal. Out of scope: PROJECT.md targets Claude Code. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `context: fork` on the main review skill | Strips `AskUserQuestion`, so confirm-before-post is impossible. It also runs in the background by default. | Main-thread skill that spawns lane agents. |
| Permission prompts as the only "confirmation first" gate | `auto` / `bypassPermissions` skip them, and `-p` has no user. | Explicit `AskUserQuestion` in the main thread, plus opt-in flags for headless runs. |
| Top-level `bin/` | claude.ai and Cowork refuse to install plugins with it. | `scripts/` plus `${CLAUDE_PLUGIN_ROOT}` in the skill body. |
| `hooks`, `mcpServers`, `permissionMode` in plugin agent frontmatter | Silently ignored for plugin agents. | `tools:` allowlist, plus plugin-level `hooks/hooks.json` if needed. |
| `Agent(type)` allowlist inside subagent definitions | Parentheses are ignored. Only works for `claude --agent` main-thread agents. | Plain `Agent`, or omit `Agent` from lane `tools`. Lanes need no nested spawning. |
| `` !`cmd` `` injection without `\|\| true` | A non-zero exit aborts the whole skill. | Let the skill run `gh` / `git` through Bash tool calls with error handling. |
| Diff `position` in review comments | Counting offsets from `@@` is error-prone. | `line` + `side` (+ `start_line`), validated against `gh pr diff`. |
| One `gh pr comment` per finding, or a posting loop | Notification spam and secondary rate limits; 422 on spam. | One pending review, one request, then submit or delete. |
| Defaulting the review `event` to `APPROVE` / `REQUEST_CHANGES` | An LLM must not gate merges. Even the managed service never approves or blocks. | `COMMENT` only. |
| Copying `claude-security` prompts or `scripts/lib` | Proprietary ("All rights reserved"). | Re-derive the refutation-verifier idea independently. |
| Depending on Serena / codegraph / beads / OCR / CodeRabbit CLI | PROJECT.md forbids it. | Built-in Read / Grep / Glob / Bash. Optional accelerators only, detected at runtime. |
| Plugin or skill names starting `claude-`, `anthropic-`, or containing whole-word `claude` / `anthropic` | `plugin validate` errors or warns that it "passes as one of Anthropic's own". | `review-code`. |
| Setting `version` in `plugin.json` and pushing without bumping | `claude plugin update` says "already at latest" and users keep the old copy. | Bump on every release, or omit `version` and use the commit SHA. Pick one and say so in the README. Prefer explicit semver plus `claude plugin tag`. |
| Reading config (REVIEW.md / instructions) from the PR's head branch for trust decisions | A PR author could inject instructions. Both Anthropic's service and Qodo read from the base or default branch. | Read repo review rules from the base branch (`git show <base>:REVIEW.md`). |

## Stack Patterns by Variant

**If the target is a local diff or branch (no PR):**
- Use `git diff <merge-base>...HEAD` plus the working tree. Emit terminal or JSON only. `--comment` is inapplicable; say so.
- Because there is nothing to post to and `gh` is not needed.

**If the target is a GitHub PR:**
- Use `gh pr view/diff`, check `isDraft` / `state` / existing review marker first. Use the pending-review protocol above.
- Because it is the only flow that is atomic, previewable and reversible.

**If the target is a GitLab MR:**
- Out of scope for v1. The bundled `/code-review` posts a single note via `glab`.
- Because there is no equivalent anchor-validation path researched.

**If running headless (CI, `claude -p`, claude-code-action):**
- No `AskUserQuestion`. Require `--comment` in the invocation to post, and default to report-only. Use `--json-schema` for structured output.
- Because the confirm step cannot run.

**If fix mode:**
- Apply only verified findings, once, in the main thread, then run the repo's tests. Edits go through the user's permission mode. A background subagent's edits fall outside `/rewind`, so do not delegate edits to a background lane.
- Because the rewind caveat is documented for the bundled `/code-review --fix`.

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| Claude Code 2.1.302 (tested) | plugin manifest fields `name, version, description, author, license, repository, homepage, keywords, dependencies, userConfig` | `displayName` and `metadata` need >= 2.1.222. Unknown top-level keys are stripped with a validate warning. `userConfig` entries are strict, so an unknown key there breaks the load. |
| Agent Skills spec | Claude Code skills | CC ignores `license`, `compatibility`, `metadata`. It uses `allowed-tools` as a pre-approval grant (not a restriction). The spec marks `allowed-tools` experimental. |
| `gh` 2.102.0 | REST reviews API 2022-11-28 | `gh api --input` and `--method` are stable. `{owner}/{repo}` placeholders are filled from the current repo. |
| Subagent `model:` aliases | `sonnet`, `opus`, `haiku`, `fable`, full IDs, `inherit` | Aliases resolve to the main model's exact version when the same family. Blocked values fall back per `availableModels`. |
| claude-code-action `@v1` | plugins from a git marketplace | Use `plugin_marketplaces` + `plugins` inputs. `code-review@claude-code-plugins` is the documented example. |
| Hooks `if` field | only `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `PermissionDenied` | On other events a hook with `if` never runs. |

## Installation

Not an npm or pip project. There is no build step; the plugin is Markdown + JSON.

```bash
# Develop
claude --plugin-dir ./                       # then /reload-plugins after edits
claude plugin validate --strict .            # CI gate

# User install (after publishing to GitHub)
claude plugin marketplace add <owner>/review-code
claude plugin install review-code@<marketplace-name>
# or in one step (CC >= 2.1.292):
claude plugin install review-code --marketplace <owner>/review-code

# Runtime prerequisites for PR modes only
gh auth status
```

## Deviations from the prescribed method

- The `research-plan` seam was **skipped**. All provider flags in `.planning/config.json` are false and no `research-store` cache was written. Facts came from direct reads of official docs (WebFetch), files on disk (installed official plugins), GitHub API metadata (`gh api`) and WebSearch.
- **Context7 MCP was unavailable** in this session. Versions were verified from the docs pages, local `--help` output and GitHub release metadata instead.
- Not verified: the `SubagentStop` loop-guard field, SARIF property-bag mapping, the licence of the Ziad Hassan v1.3 additions, `claude plugin eval` minimum CC version, and the exact per-feature minimum versions beyond those quoted from the docs.

## Sources

- https://code.claude.com/docs/en/plugins-reference (plugin.json fields, layout, env vars, path rules, validate) — HIGH
- https://code.claude.com/docs/en/plugin-marketplaces and https://code.claude.com/docs/en/plugins/marketplace-reference (marketplace.json, sources, strict mode, reserved names) — HIGH
- https://code.claude.com/docs/en/plugins/publish (release, versioning, tag, directory submission) — HIGH
- https://code.claude.com/docs/en/skills (SKILL.md frontmatter, substitutions, 500-line and 1,536-char limits, compaction 5k/25k) — HIGH
- https://code.claude.com/docs/en/sub-agents (agent frontmatter, plugin-agent restrictions, nesting, model routing, 20 concurrent) — HIGH
- https://code.claude.com/docs/en/hooks (events, exit codes, `if`, SubagentStop, exec form) — HIGH (page truncated at about 100k characters)
- https://code.claude.com/docs/en/code-review-local, /code-review-customize, /code-review-pull-requests (bundled `/code-review`, REVIEW.md, severity levels, `bughunter-severity` line) — HIGH
- https://code.claude.com/docs/en/plugin-evals (`claude plugin eval`) — MEDIUM (only the section headers were read)
- https://agentskills.io/specification (open skill spec) — HIGH
- https://docs.github.com/en/rest/pulls/reviews (create-a-review parameters, pending state) — HIGH
- https://github.com/pingdotgg/t3code/issues/12549 ("Line could not be resolved" 422 rejects the whole review) — MEDIUM
- On disk: `~/.claude/plugins/marketplaces/claude-plugins-official/plugins/{code-review,pr-review-toolkit,claude-security,security-guidance}` and `~/.claude/plugins/marketplaces/open-code-review` — HIGH
- https://raw.githubusercontent.com/anthropics/claude-code-action/main/action.yml and docs/usage.md — HIGH
- GitHub API (`gh api repos/<r>`): licences and latest tags for claude-code-action (v1, MIT), pr-agent (v0.48.0, MIT), open-code-review (v1.13.1, Apache-2.0), superpowers (v7.0.0, MIT), coderabbitai/skills (v1.1.1, MIT), posit-dev/skills (MIT) — HIGH
- https://github.com/coderabbitai/skills and https://docs.coderabbit.ai/cli/ — MEDIUM
- https://docs.qodo.ai/v1/configuration/configuration-file and https://github.com/qodo-ai/pr-agent/blob/main/docs/docs/tools/review.md — MEDIUM
- https://www.greptile.com/docs/code-review/greptile-config-reference — MEDIUM (search summary)
- https://github.blog/changelog/2026-06-12-copilot-code-review-new-configurations-and-controls/ and https://docs.github.com/copilot/customizing-copilot/adding-custom-instructions-for-github-copilot — MEDIUM (search summary)
- https://cursor.com/blog/building-bugbot — MEDIUM (search summary)
- https://github.com/anthropics/claude-code-security-review — MEDIUM (search summary)
- https://github.com/obra/superpowers — MEDIUM

---
*Stack research for: Claude Code code-review plugin*
*Researched: 2026-10-17*
