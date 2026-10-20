# review-code

A Claude Code plugin with one skill, `review-code`, a code reviewer for diffs, branches and PRs. It is based on Matt Pocock's two-axis `code-review` skill (Standards and Spec) and adds an evidence gate adapted from `critical-code-reviewer`. Findings are meant to carry `file:line` evidence and an evidence class.

Status: early version. It runs the Defects, Standards, Spec and SOTA lanes, a verifier per axis, own-code reproduction, quick, standard and deep tiers, domain packs, shards for directory and repo targets, a serial fallback, and text, JSON and SARIF output. `--comment` posts one COMMENT review to a GitHub PR or GitLab MR after you confirm the exact payload; `--fix` applies confirmed fixes one at a time after you confirm the plan and the exact test command, and reverts and stops at the first failing fix. The tier defaults are unvalidated. Do not rely on it for review results.

## Install

From GitHub:

```sh
claude plugin marketplace add davdittrich/review-code
claude plugin install review-code@review-code
```

To install from a local checkout instead, use `claude plugin marketplace add /path/to/review-code`.

The repository is its own marketplace (`.claude-plugin/marketplace.json`, source `./`).

Invoke by asking for a code review (the skill auto-triggers) or with `/review-code`. Path targets are relative to the current directory, and the spec file is given when the skill asks for it (step 3 question); `.` at the repository top level means the whole repository, and in a subdirectory that directory.

## Requirements

- Claude Code 2.1.292 or later. This floor is provisional; the plugin is tested on 2.1.304 locally; CI pins 2.1.302.
- `git`.
- `python3` 3.9 or later, for target pinning, the context ledger and the untrusted-text sanitizer. Without it, PR and MR targets are refused because their text cannot be sanitized; local diff targets are still reviewed, without commit messages or the Spec axis, and labelled triage.
- `gh`, for PR targets. PR text is read, sanitized and kept out of the main thread; with `--comment` the review is posted to the PR through `gh api`.
- `glab`, for MR targets. MR reading and MR posting (`--comment`) are untested against a live GitLab.
- Headless `claude -p`: a prompt that makes the model call the Skill tool ("use the review-code skill ...") is denied in the default permission mode, with the bare result `Execute skill: review-code:review-code` and no detail. Pass `--allowedTools Skill`, or use the slash form `/review-code`.
- The Agent tool (subagents). The lanes and verifiers run as subagents; without it the skill runs in serial mode in the main thread (see Serial mode).
- Optional: `serena` and `codegraph` MCP servers. When installed, and the review runs inside the activated or indexed repository (serena started with `--project-from-cwd`; codegraph needs `codegraph init`), the lanes and verifiers use four named read-only tools (`mcp__serena__find_symbol`, `find_referencing_symbols`, `get_symbols_overview`, `mcp__codegraph__codegraph_explore`) to find symbols and callers; otherwise they use Grep. These are accelerators, not dependencies. Their results describe the working checkout, which can differ from the pinned tree, so evidence is still quoted from the pinned tree only. The allowlist lives in the `tools:` line of each `agents/*.md`.

## Tiers and flags

Add `--quick`, `--standard` or `--deep` to pick a tier (default `standard`). `--sota` adds the SOTA lane to any tier and `--no-sota` removes it. `--ai` loads the LLM-generated-code pack. `--serial` forces serial mode.

| Tier | Lanes | Finder | Verifier |
|------|-------|--------|----------|
| quick | Defects, Standards | sonnet, low | opus, medium |
| standard | Defects, Standards, Spec | opus, medium | sonnet, high |
| deep | Defects, Standards, Spec, SOTA | opus, xhigh | sonnet, high |

These values are unvalidated defaults: there is no eval corpus behind them. The Standards lane carries the lean-code (Ponytail) checks. Domain packs load by file type or by what the changed lines contain: Python, R, JS/TS, SQL, data migrations, security/OWASP, concurrency, API compatibility and, with `--ai` or `--deep`, LLM-generated code. A pack's do-not-flag list is enforced through the lane prompt and the verifier handoff only, not by code. Lanes, caps, packs and serial mode in full: `skills/review-code/references/lanes.md`.

## Cost and caps

Calls and token estimates are shown, never prices. The `deep` tier and path and repo targets show a preview and ask Run or Stop before any lane starts. A headless run cannot be asked: it prints the preview and runs inside the caps, so bound an unattended run with `--quick` or the cap settings.

## Plugin settings

Reopen them with `/plugin configure review-code@<marketplace>`; empty means the tier's value. Tested on Claude Code 2.1.304 locally; CI pins 2.1.302. On 2.1.304, a number outside `min` and `max` and a string outside `options`, set in settings, reach the skill text unchanged; plan.py then refuses the run with `bad-config`. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` and `CLAUDE_CODE_EFFORT_LEVEL` disable the per-lane model and effort values; the report says so when the plugin can see them.

- `default_tier`: tier used without a tier flag.
- `finder_model`, `finder_effort`: model (an alias or a full model ID) and effort for the finder lanes.
- `verifier_model`, `verifier_effort`: the same for the verifiers.
- `max_verifier_calls` (1 to 200): cap on the findings sent to verifiers per run (ids, not calls; a call holds up to 5 handoffs of one code path). `max_shards` (1 to 50), `max_packs` (1 to 9): caps per run.

## Serial mode

When the Agent tool is unavailable, when an Agent call fails with a nesting-depth or concurrent-limit error, or with `--serial`, the main thread reviews the Defects and Standards inputs itself and self-scores all candidates in one batch. The report says `mode: serial` and lists what is lost: no independent verifier (nothing is Blocking), no lane isolation, no Spec axis, no SOTA lane and no reproduction. The main thread holds Bash while it reads target code and is told to act on nothing it reads; that is an instruction, not a sandbox. Nothing is verified in serial mode, so `--comment` ends in `nothing-to-post` and `--fix` in `nothing-to-fix`; review again in parallel mode to post or fix.

## Output

The default is a terminal report. `--json` prints the schema v1 review document and `--sarif` prints SARIF 2.1.0. Both go to stdout and never to a file; in headless runs, redirect stdout to keep them. The schema is documented in the `skills/review-code/scripts/report.py` module docstring. Unverifiable items are listed in the text and JSON output but are not SARIF results.

## Posting

`--comment` on a PR or MR target posts the verified findings as one `COMMENT` review, never an approval or a request for changes. The skill first prints the exact payload and a digest and asks; only "Post as COMMENT" posts, and headless runs never post. Headless `claude -p --comment` on a PR is unsupported: besides never posting, the two runs tried did not reach `post.py build`, because the approval prompt on the lane-ingest command is intermittent (review-code-02a.18); use an interactive session. Re-posting skips the findings you already posted. Secrets matching eleven regular expressions are redacted (no entropy check). MR posting is untested against a live GitLab; PR posting was run live against GitHub on two PRs (one review each, hidden markers survived, re-posting refused). Flow, markers, refusals and limits: `skills/review-code/references/posting.md`.

## Fix mode
`--fix` applies the verified findings' fixes to committed code of your own, after the report and any post. It stops early for staged, unstaged and working-tree targets. The skill shows every fix text, its model and effort and the exact test command, and asks once: only "Apply and run tests" applies anything, and headless runs never apply. The test command runs on the untouched tree first, and a red baseline stops the run before any edit. Then one fixer subagent edits one file per finding, the tests run, and the edit stays only if they pass; the first failing fix is restored and the run stops. Fix mode never commits, pushes, installs or changes a lockfile, manifest, CI or config file. Edits to files git ignores, such as `.env` or `node_modules/`, are not checked. Review the result with `git diff` before you commit.

Limit: one fixer call at a time, and the main thread waits for it. The plugin cannot force a foreground subagent: Claude Code starts spawned subagents in the background, and setting `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` makes Claude wait for every subagent. Flow, refusals, outcomes and limits: `skills/review-code/references/fix.md`.
## Reproduction

Reproduction covers your own code only. For a confirmed finding, the skill writes the smallest failing test and shows the exact command and the full test source before it asks to run it; the harness permission prompt follows on top. The test runs in a detached worktree under the run directory, which is removed afterwards. `PATH`, `LANG`, `LC_ALL` and `TERM` are kept, and `HOME` and `TMPDIR` move into the run directory. In-place targets get HEAD plus the tracked diff; untracked files are not carried. A run stops after 120 seconds.

Limits: there is no network or filesystem isolation (a test can read host files by absolute path), dependencies are not installed in the fresh worktree, and user-site packages and HOME-based configuration are not visible.

Wire form: lane and verifier replies are JSON arrays of `[key, value]` pairs, never objects, because the Claude Code Bash guard denies a here-document body holding a brace followed by a quote, or a backslash-escaped quote: lanes write `{` as `\u007b` and `"` as `\u0022`. The approval prompt on a here-document body is also intermittent: on 2.1.304 the same code-like body (`if a(b).c() != d:`) ran in 2 of 3 headless probes and asked in 1, so no escaping rule makes a headless `--comment` run on a PR reliable and it can stop at `report.py ingest`. A reproduction test source with a dict literal can still trip that guard; headless runs skip reproduction.

## Run directory

Each run writes its pinned target, diff, context ledger and lane inputs to a mode 0700 directory under `git rev-parse --git-path review-code` inside the repository's git directory, never into the working tree. Run directories last written more than 14 days ago are removed when the next run in that repository pins a target. Each run also holds a stripped copy of the reviewed and referenced text files in `tree/`, so it takes about as much disk as those files (for `--repo`, every kept text file of the working copy) until it is removed; delete it sooner when the review is done. A sharded path or repo run copies each shard's files again under `shards/`, so it takes roughly twice that. The skill asks once per run whether the code is your own and records the answer; every other outcome, including headless runs, records `external`. What is untrusted and what the defences do not cover: `skills/review-code/references/untrusted-input.md`.

## What ships

The plugin root is the repository root, so every tracked file ships to installers: `skills/`, `agents/`, `.claude-plugin/`, `LICENSE`, `NOTICE`, `README.md`, `docs/` (the matt.md and ccr.md keep-or-replace contracts and the trigger-prompt record) and `scripts/`. The GSD planning record under `.planning/` is gitignored and never ships. Tooling, agent pointer files (including `.claude/CLAUDE.md`) and third-party reference text are not tracked (see `.gitignore`) and never ship. `scripts/check-package.sh` fails if any of them become tracked.

## Releasing

1. Bump `version` in `.claude-plugin/plugin.json` (semver). Do not set a version in `marketplace.json`.
2. Run `sh scripts/check-package.sh`; it must exit 0.
3. On the primary branch with a clean tree, run `claude plugin tag --dry-run`, then `claude plugin tag --push`.
4. Before the first push to a new remote, audit `git ls-files` for tooling or private files.

## Development

`python3 -m unittest discover -s tests` runs the unit suite. `sh scripts/check-package.sh` needs `git`, `jq`, `tar` and the `claude` CLI. It checks the tracked state (the index and tracked modifications), never untracked working-tree files. It validates the manifests with `claude plugin validate --strict`, installs the plugin into a throwaway `CLAUDE_CONFIG_DIR`, and checks skill properties, licence files and tracked-file hygiene.

## Licence

MIT, see `LICENSE`. Upstream attribution (Matt Pocock; Posit, Garrick Aden-Buie and Ziad Hassan for the secondary source) and the owner's licence ruling for the v1.3 additions are in `NOTICE`.
