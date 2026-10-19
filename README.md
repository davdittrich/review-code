# review-code

A Claude Code plugin with one skill, `review-code`, a code reviewer for diffs, branches and PRs. It is based on Matt Pocock's two-axis `code-review` skill (Standards and Spec) and adds an evidence gate adapted from `critical-code-reviewer`. Findings are meant to carry `file:line` evidence and an evidence class.

Status: early version. It runs the Defects lane, a per-finding verifier, own-code reproduction, and text, JSON and SARIF output. The Standards and Spec lanes, PR posting and fix mode do not exist yet; the Standards and Spec inputs are prepared but not reviewed. Do not rely on it for review results.

## Install

From a local checkout (works today):

```sh
claude plugin marketplace add davdittrich/review-code
claude plugin install review-code@review-code
```

To install from a local checkout instead, use `claude plugin marketplace add /path/to/review-code`.

The repository is its own marketplace (`.claude-plugin/marketplace.json`, source `./`).

Invoke by asking for a code review (the skill auto-triggers) or with `/review-code`. Path targets and a `--spec` file are relative to the current directory (a `--spec` path that names no file there is tried from the repository top); `.` at the repository top level means the whole repository, and in a subdirectory that directory.

## Requirements

- Claude Code 2.1.246 or later. This floor is provisional; the plugin is tested on 2.1.302.
- `git`.
- `python3` 3.9 or later, for target pinning, the context ledger and the untrusted-text sanitizer. Without it, PR and MR targets are refused because their text cannot be sanitized; local diff targets are still reviewed, without commit messages or the Spec axis, and labelled triage.
- `gh`, for PR targets. PR text is read, sanitized and kept out of the main thread; posting comes in a later version.
- `glab`, for MR targets. MR reading is untested against a live glab.
- The Agent tool (subagents). The Defects lane and the verifier run as subagents; without it the skill reviews nothing.

## Output

The default is a terminal report. `--json` prints the schema v1 review document and `--sarif` prints SARIF 2.1.0. Both go to stdout and never to a file; in headless runs, redirect stdout to keep them. The schema is documented in the `skills/review-code/scripts/report.py` module docstring. Unverifiable items are listed in the text and JSON output but are not SARIF results.

## Reproduction

Reproduction covers your own code only. For a confirmed finding, the skill writes the smallest failing test and shows the exact command and the full test source before it asks to run it; the harness permission prompt follows on top. The test runs in a detached worktree under the run directory, which is removed afterwards. `PATH`, `LANG`, `LC_ALL` and `TERM` are kept, and `HOME` and `TMPDIR` move into the run directory. In-place targets get HEAD plus the tracked diff; untracked files are not carried. A run stops after 120 seconds.

Limits: there is no network or filesystem isolation (a test can read host files by absolute path), dependencies are not installed in the fresh worktree, and user-site packages and HOME-based configuration are not visible.

Known limit: in a headless `claude -p` run, the Claude Code 2.1.302 Bash guard denies a here-document body with `{` followed by a quote, so `report.py ingest` and `verdicts` are refused there. Interactive behaviour is unprobed.

## Run directory

Each run writes its pinned target, diff, context ledger and lane inputs to a mode 0700 directory under `git rev-parse --git-path review-code` inside the repository's git directory, never into the working tree. Run directories last written more than 14 days ago are removed when the next run in that repository pins a target. Each run also holds a stripped copy of the reviewed and referenced text files in `tree/`, so it takes about as much disk as those files (for `--repo`, every kept text file of the working copy) until it is removed; delete it sooner when the review is done. The skill asks once per run whether the code is your own and records the answer; every other outcome, including headless runs, records `external`. What is untrusted and what the defences do not cover: `skills/review-code/references/untrusted-input.md`.

## What ships

The plugin root is the repository root, so every tracked file ships to installers: `skills/`, `.claude-plugin/`, `LICENSE`, `NOTICE`, `README.md`, `docs/` (the matt.md and ccr.md keep-or-replace contracts and the trigger-prompt record) and `scripts/`. The GSD planning record under `.planning/` is gitignored and never ships. Tooling, agent pointer files (including `.claude/CLAUDE.md`) and third-party reference text are not tracked (see `.gitignore`) and never ship. `scripts/check-package.sh` fails if any of them become tracked.

## Releasing

1. Bump `version` in `.claude-plugin/plugin.json` (semver). Do not set a version in `marketplace.json`.
2. Run `sh scripts/check-package.sh`; it must exit 0.
3. On the primary branch with a clean tree, run `claude plugin tag --dry-run`, then `claude plugin tag --push`.
4. Before the first push to a new remote, audit `git ls-files` for tooling or private files.

## Development

`python3 -m unittest discover -s tests` runs the unit suite. `sh scripts/check-package.sh` needs `git`, `jq`, `tar` and the `claude` CLI. It checks the tracked state (the index and tracked modifications), never untracked working-tree files. It validates the manifests with `claude plugin validate --strict`, installs the plugin into a throwaway `CLAUDE_CONFIG_DIR`, and checks skill properties, licence files and tracked-file hygiene.

## Licence

MIT, see `LICENSE`. Upstream attribution (Matt Pocock; Posit, Garrick Aden-Buie and Ziad Hassan for the secondary source) and the owner's licence ruling for the v1.3 additions are in `NOTICE`.
