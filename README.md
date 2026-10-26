# review-code

A Claude Code plugin with one skill, `review-code`, a code reviewer for diffs, branches and PRs. It is based on Matt Pocock's two-axis `code-review` skill (Standards and Spec) and adds an evidence gate adapted from `critical-code-reviewer`. Findings are meant to carry `file:line` evidence and an evidence class.

Status: early version. It runs the Defects, Standards, Spec and SOTA lanes, a verifier per axis, own-code reproduction, quick, standard and deep tiers plus a `--lean` cleanup-only tier, domain packs, shards for directory and repo targets, a serial fallback, automatic spec discovery, and text, JSON and SARIF output. `--comment` posts one COMMENT review to a GitHub PR or GitLab MR after you confirm the exact payload; `--fix` applies confirmed fixes one at a time after you confirm the plan and the exact test command; a fix whose tests fail, that runs too long or that edits too many lines is reverted and the run goes on (`reverted-tests`, `reverted-timeout`, `reverted-too-large`, `reverted-weakened`), while a scope violation, tamper or interrupt (`reverted-violation`, `tampered`, `reverted-interrupt`) halts it. The tier defaults are unvalidated. Do not rely on it for review results.

## Install

From GitHub:

```sh
claude plugin marketplace add davdittrich/review-code
claude plugin install review-code@review-code
```

To install from a local checkout instead, use `claude plugin marketplace add /path/to/review-code`.

The repository is its own marketplace (`.claude-plugin/marketplace.json`, source `./`).

Invoke by asking for a code review (the skill auto-triggers) or with `/review-code`. Path targets are relative to the current directory; `.` at the repository top level means the whole repository, and in a subdirectory that directory. The skill never asks for a spec: it finds one (see Spec discovery).

## Requirements

- Claude Code 2.1.292 or later. This floor is provisional; the plugin is tested on 2.1.304 locally; CI pins 2.1.302.
- `git`. Reviewing a commit, branch or range whose head is not checked out also needs an `origin` remote with `origin/HEAD` set, network access to read the upstream tip with `git ls-remote`, and the main checkout (not a linked worktree) on the upstream default branch at that tip; otherwise it refuses `no-upstream-remote`, `remote-unreachable` or `checkout-not-synced`. For a pull or merge request whose base repository is a fork, the parent needs a local remote too, and the fork's default branch, read live as well, must contain the parent's tip, or the review refuses `checkout-not-synced`.
- `python3` 3.9 or later, for target pinning, the context ledger, the untrusted-text sanitizer and the reply-capture hook. Without it, PR and MR targets are refused because their text cannot be sanitized; local diff targets are still reviewed, without commit messages or the Spec axis, and labelled triage.
- Reply capture: the plugin's hook (`hooks/hooks.json`, run with `python3`) stores each lane and verifier reply under the repository's `review-code` git directory, so `report.py ingest` and `verdicts` read it with `--agent <agentId>` instead of the model retyping it. The hook never creates that directory and never blocks an agent. With hooks disabled, without `python3`, or with a custom `agent_prefix` setting, the skill passes the reply on stdin as before.
- `gh`, for PR targets. PR text is read, sanitized and kept out of the main thread; with `--comment` the review is posted to the PR through `gh api`.
- `gh` and `glab` also fetch issues named in commit or PR text.
- Optional: `bd` (beads), to read tickets named in commit messages.
- `glab`, for MR targets. MR reading and MR posting (`--comment`) are untested against a live GitLab.
- Headless `claude -p`: a prompt that makes the model call the Skill tool ("use the review-code skill ...") is denied in the default permission mode, with the bare result `Execute skill: review-code:review-code` and no detail. Pass `--allowedTools Skill`, or use the slash form `/review-code`.
- The Agent tool (subagents). The lanes and verifiers run as subagents; without it the skill runs in serial mode in the main thread (see Serial mode).
- Optional: `serena` and `codegraph` MCP servers. When installed, and the review runs inside the activated or indexed repository (serena started with `--project-from-cwd`; codegraph needs `codegraph init`), the lanes and verifiers use four named read-only tools (`mcp__serena__find_symbol`, `find_referencing_symbols`, `get_symbols_overview`, `mcp__codegraph__codegraph_explore`) to find symbols and callers; otherwise they use Grep. These are accelerators, not dependencies. Their results describe the working checkout, which can differ from the pinned tree, so evidence is still quoted from the pinned tree only. The allowlist lives in the `tools:` line of each `agents/*.md`.

## Tiers and flags

Add `--quick`, `--standard` or `--deep` to pick a tier (default `standard`). `--lean` runs only the Standards lane and its verifier, for a cleanup pass; it is a flag only, not a `default_tier` value, and its report says Defects and Spec were skipped. `--sota` adds the SOTA lane to any tier and `--no-sota` removes it. `--ai` loads the LLM-generated-code pack. `--serial` forces serial mode. `--strict` lowers the evidence floor to `inferred`, so a concern the verifier re-derives from source is lifted into an `inferred` finding. `--sweep` lists other uses of a finding's quoted line outside the diff. `--nit` adds a Nit severity for strict adherence to the repo's own standards: a Standards nit quotes a rule from a repo file (read from the merge base) or a norm the scripts count in the code (at least 20 lines, the other idiom the majority); a Defects or Spec nit is a trivial finding that passes its own lane's evidence gate (no rule or norm is quoted), is grouped by its category and is verified like any other finding. Nits never change the verdict, the text report lists at most 15 groups (a rule, a norm or a category), `--fix` ignores them, and `--nit --lean` gives a nits-only pass. A diff review reports changed lines only; a `--repo` or path review also reports pre-existing nits. `--own` or `--external` answers the trust question up front, so it is not asked.

| Tier | Lanes | Finder | Verifier |
|------|-------|--------|----------|
| quick | Defects, Standards | sonnet, low | opus, medium |
| standard | Defects, Standards, Spec | opus, medium | sonnet, high |
| deep | Defects, Standards, Spec, SOTA | opus, xhigh | sonnet, high |

These values are unvalidated defaults: `evals/` holds a harness but no baseline has been recorded behind them. The Finder column covers the Defects, Spec and SOTA lanes; the Standards lane runs on haiku, medium in every tier (see `standards_model`). The Standards lane carries the lean-code (Ponytail) checks. Domain packs load by file type or by what the changed lines contain: Python, R, JS/TS, SQL, data migrations, security/OWASP, concurrency, API compatibility and, with `--ai` or `--deep`, LLM-generated code. A pack's do-not-flag list is enforced through the lane prompt and the verifier handoff only, not by code. Lanes, caps, packs and serial mode in full: `skills/review-code/references/lanes.md`.

## Cost and caps

Calls and token estimates are shown, never prices. The `deep` tier and path and repo targets show a preview and ask Run or Stop before any lane starts. Cost scales with the repository and the selected tier: each shard of a repo or path target gets a Defects and a Standards lane call, and every candidate is verified, one verifier call per 5 candidates of a shard and verifier agent. A cheaper tier (`--lean`, `--quick`) runs fewer lanes at lower effort, but it does not cap the shards or the verifier calls. The interactive preview states the shard count and the lane calls; a headless run cannot be asked, so it prints the preview and runs on without a bound.

## Spec discovery

The Spec axis needs a statement of what the code should do. The skill collects one itself:

- PR or MR text, closing issues, and `#N` issues named in commit messages, read with `gh` or `glab`.
- Issues in other repositories, written as `owner/repo#N` or as a GitHub or GitLab issue URL. They are fetched read-only with `gh` or `glab`, five per run at most. Jira, Linear and other trackers cannot be fetched; the ledger lists them as unfetched.
- `bd` tickets named `<repo-dir>-xyz` in commit messages, when `bd` is installed and the repository has `.beads/`. A `repo` or `path` target reads the last 30 commits for them.
- Files under `docs/`, `specs/` or `.scratch/` whose names match the branch or the reviewed paths, five at most. A `repo` target takes every file there. A file under review is never its own spec.
- Decision records: every prose file under an `adr`, `adrs`, `decisions` or `architecture-decisions` directory, at any depth. Each record is listed in `inputs/spec.md` by path, title, size (`over 4M` for a record the file cap leaves unread) and why it matched, never pasted, with no cap: the Spec lane reads the ones it needs from the repository.
- When nothing is found, or only decision records, a read-only haiku subagent (`spec-finder`) searches the tracked prose files by content and names spec files or directories. The script keeps tracked files only, and none of the subagent's text reaches the lanes.

All of this text is untrusted and reaches the Spec lane inside marked blocks. To name a spec yourself, run `ledger.py build --run DIR --spec PATH` (a path inside the repository). With no spec found, the report says the Spec axis was skipped. To judge a change against a review procedure that lives outside the repository, add `--rubric PATH` (repeatable): the file is copied into the run as untrusted conventions for the Standards lane, with its path and sha256 in the ledger.

## Plugin settings

Reopen them with `/plugin configure review-code@<marketplace>`; empty means the tier's value. Tested on Claude Code 2.1.304 locally; CI pins 2.1.302. On 2.1.304, a number outside `min` and `max` and a string outside `options`, set in settings, reach the skill text unchanged; plan.py then refuses the run with `bad-config`. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` and `CLAUDE_CODE_EFFORT_LEVEL` disable the per-lane model and effort values; the report says so when the plugin can see them.

- `default_tier`: tier used without a tier flag.
- `finder_model`, `finder_effort`: model (an alias or a full model ID) and effort for the finder lanes.
- `standards_model`, `standards_effort`: model and effort for the Standards lane only; empty means `haiku` and `medium` in every tier. The Standards lane never reports Blocking and its findings still go through a verifier, so a smaller model there costs recall on convention and lean-code findings, not precision. That recall has not been measured. The report flags a Standards model equal to the verifier's.
- `verifier_model`, `verifier_effort`: the same for the verifiers.
- `max_packs` (1 to 9): cap per run.
- `agent_prefix`: plugin whose agents run the lanes (`<prefix>:finder`, `standards`, `spec`, `sota`, `verifier`, `spec-verifier`, `spec-finder`, `fixer`); default `review-code`. Lowercase letters, digits and hyphens, at most 64.
- `nav_hint`: one line (at most 300 characters, `${` removed) added to each lane's input as `Navigation preference: ...`. The lanes treat it as a preference; it never widens their `tools:` list.

### Own agents (custom navigation tools)

1. Copy `agents/*.md` into your own plugin, for example `mycorp-review-agents`.
2. Edit the `tools:` lines to your read-only MCP servers, or delete the MCP entries.
3. Set `agent_prefix=mycorp-review-agents`.

The shipped files stay read-only by default, and the package allowlist test guards only the shipped files. Keep every lane read-only in a copy: no Bash, Write, Edit or network tools (the fixer alone keeps Edit). Both plugins must be installed; a second plugin's agents resolve as `<plugin>:<agent>` (observed on Claude Code 2.1.304).

## Serial mode

When the Agent tool is unavailable, when an Agent call fails with a nesting-depth error, or with `--serial`, the main thread reviews the Defects and Standards inputs itself and self-scores all candidates in one batch. The report says `mode: serial` and lists what is lost: no independent verifier (nothing is Blocking), no lane isolation, no Spec axis, no SOTA lane and no reproduction. The main thread holds Bash while it reads target code and is told to act on nothing it reads; that is an instruction, not a sandbox. Nothing is verified in serial mode, so `--comment` ends in `nothing-to-post` and `--fix` in `nothing-to-fix`; review again in parallel mode to post or fix. Any other failed or refused Agent call is retried once on its own (a call the user denied is reported, not retried), and after a concurrent-limit error the wave is halved (minimum 1); finished lanes are kept, and a lane whose retry fails stays in `missing_lanes` and is reported lost. Only a nesting-depth error, a missing Agent tool or `--serial` switches to serial mode and discards the lane results.

## Output

The default is a terminal report. `--json` prints the schema v1 review document and `--sarif` prints SARIF 2.1.0. Both go to stdout and never to a file; in headless runs, redirect stdout to keep them. The schema is documented in the `skills/review-code/scripts/report.py` module docstring. Unverifiable items are listed in the text and JSON output but are not SARIF results.

## Posting

`--comment` on a PR or MR target posts the verified findings as one `COMMENT` review, never an approval or a request for changes. The skill first prints the exact payload and a digest and asks; only "Post as COMMENT" posts, and headless runs never post. Headless `claude -p --comment` on a PR is unsupported: besides never posting, the two runs tried did not reach `post.py build`, because the approval prompt on the lane-ingest command is intermittent (review-code-02a.18); use an interactive session. Re-posting skips the findings you already posted. Secrets matching eleven regular expressions are redacted (no entropy check). MR posting is untested against a live GitLab; PR posting was run live against GitHub on two PRs (one review each, hidden markers survived, re-posting refused). Flow, markers, refusals and limits: `skills/review-code/references/posting.md`.

## Fix mode

`--fix` applies the verified findings' fixes to committed code of your own, after the report and any post. It stops early for staged, unstaged and working-tree targets. The skill derives the test command from the ledger's test, config and CI entries (a `repo` or `path` target lists its own tests, so a repository with tests always has one) and applies nothing when it finds none. Findings in test files, fixtures and test-runner config (`pytest.ini`, `tox.ini`, `jest.config.*` and the like) are skipped, because a fix there could weaken the tests. So is `package.json`, `pyproject.toml`, `setup.cfg` or `Makefile` when the test command names it as a word; in any other place those files are fixed like any other config file. The skill shows every fix text, its model and effort and the exact test command, and asks once: only "Apply and run tests" applies anything, and headless runs never apply. The test command runs on the untouched tree first, and a red baseline stops the run before any edit. Then one fixer subagent edits one file per finding, the tests run, and the edit stays only if they pass; a fix whose tests fail, run too long or edit too many lines is restored and the run goes on with the next entry; a scope violation, tamper or interrupt restores and stops the run. Fix mode never commits, pushes or installs. A finding on a lockfile, manifest, CI or config file is fixed like any other, but the preview flags it as a protected path and the confirmation names how many there are; the tests and the line cap apply to it too. Edits to files git ignores, such as `.env` or `node_modules/`, are not checked. Review the result with `git diff` before you commit. `--fix-tests` (with `--fix`) also fixes findings in test files, which are skipped otherwise; fixtures, runner configs, the test command's own script, files a command word names and helpers in a test directory stay protected. The plan labels them "test file", the one confirmation states their count, a test file the run does not reach is skipped as `not-covered`, an edit that drops assertion lines or adds a skip marker is restored (`reverted-weakened`; for a file type with no built-in rule the skip check is a word list: `skip`, `exit`, `quit`), and the post-fix test run still gates each edit.

Limit: one fixer call at a time, and the main thread waits for it. The plugin cannot force a foreground subagent: Claude Code starts spawned subagents in the background, and setting `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` makes Claude wait for every subagent. Flow, refusals, outcomes and limits: `skills/review-code/references/fix.md`.

## Reproduction

Reproduction covers your own code only. For a confirmed finding, the skill writes the smallest failing test and shows the exact command and the full test source before it asks to run it; the harness permission prompt follows on top. The test runs in a detached worktree under the run directory, which is removed afterwards. `PATH`, `LANG`, `LC_ALL` and `TERM` are kept, and `HOME` and `TMPDIR` move into the run directory. In-place targets get HEAD plus the tracked diff; untracked files are not carried. A run stops after 120 seconds.

Limits: there is no network or filesystem isolation (a test can read host files by absolute path), dependencies are not installed in the fresh worktree, and user-site packages and HOME-based configuration are not visible.

Wire form: lane and verifier replies are JSON arrays of `[key, value]` pairs, never objects, because the Claude Code Bash guard denies a here-document body holding a brace followed by a quote, or a backslash-escaped quote: lanes write `{` as `\u007b` and `"` as `\u0022`. The approval prompt on a here-document body is also intermittent: on 2.1.304 the same code-like body (`if a(b).c() != d:`) ran in 2 of 3 headless probes and asked in 1, so no escaping rule makes a headless `--comment` run on a PR reliable and it can stop at `report.py ingest`. A reproduction test source with a dict literal can still trip that guard; headless runs skip reproduction.

## Run directory

Each run writes its pinned target, diff, context ledger and lane inputs to a mode 0700 directory under `git rev-parse --git-path review-code` inside the repository's git directory, never into the working tree. Run directories last written more than 14 days ago are removed when the next run in that repository pins a target. Each run also holds a stripped copy of the checkout-difference files in `tree/`: the files whose checkout copy is not the reviewed content (for a PR that is not checked out, the files that differ from the checkout; for a review of what is checked out, none, except that a staged review lists the worktree edits on top of the index and its unmerged paths; for example, a changed or untracked path that is a directory, FIFO, socket or oversized file also stays in the difference). Every other file is read in place. A sharded run copies that set again for each shard under `shards/`. The skill asks once per run whether the code is your own and records the answer; every other outcome, including headless runs, records `external`. What is untrusted and what the defences do not cover: `skills/review-code/references/untrusted-input.md`.

## What ships

The plugin root is the repository root, so every tracked file ships to installers: `skills/`, `agents/`, `.claude-plugin/`, `LICENSE`, `NOTICE`, `README.md`, `docs/` (the matt.md and ccr.md keep-or-replace contracts and the trigger-prompt record), `scripts/`, `tests/`, `.github/` and `.gitignore`. The GSD planning record under `.planning/` is gitignored and never ships. Tooling, agent pointer files (including `.claude/CLAUDE.md`) and third-party reference text are not tracked (see `.gitignore`) and never ship. `scripts/check-package.sh` fails if any of them become tracked.

## Releasing

1. Bump `version` in `.claude-plugin/plugin.json` (semver). Do not set a version in `marketplace.json`.
2. Run `sh scripts/check-package.sh`; it must exit 0.
3. On the primary branch with a clean tree, run `claude plugin tag --dry-run`, then `claude plugin tag --push`.
4. Before the first push to a new remote, audit `git ls-files` for tooling or private files.

## Development

`python3 -m unittest discover -s tests` runs the unit suite. `evals/` holds an optional, paid eval harness (`python3 evals/run.py`, see `evals/README.md`); the unit suite and the package gate never run it. `sh scripts/check-package.sh` needs `git`, `jq`, `tar` and the `claude` CLI. It checks the tracked state (the index and tracked modifications), never untracked working-tree files. It validates the manifests with `claude plugin validate --strict`, installs the plugin into a throwaway `CLAUDE_CONFIG_DIR`, and checks skill properties, licence files and tracked-file hygiene.

## Licence

MIT, see `LICENSE`. Upstream attribution (Matt Pocock; Posit, Garrick Aden-Buie and Ziad Hassan for the secondary source) and the owner's licence ruling for the v1.3 additions are in `NOTICE`.
