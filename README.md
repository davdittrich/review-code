# review-code

A Claude Code plugin with one skill, `review-code`, a critical code reviewer for diffs, branches and PRs. Findings are meant to carry `file:line` evidence and an evidence class.

Status: v0.1.0 is a stub. It states its own limits: no reviewer lanes, no context ledger and no independent verification yet. Do not rely on it for review results.

## Install

From a local checkout (works today):

```sh
claude plugin marketplace add /path/to/review-code
claude plugin install review-code@review-code
```

The repository is its own marketplace (`.claude-plugin/marketplace.json`, source `./`). A GitHub install form is not documented yet because the repository has not been published; it will be added at release time.

Invoke by asking for a code review (the skill auto-triggers) or with `/review-code`.

## Requirements

- Claude Code 2.1.246 or later. This floor is provisional; the plugin is tested on 2.1.302.
- `git`.
- `gh`, for PR targets in later versions.

## What ships

The plugin root is the repository root, so every tracked file ships to installers: `skills/`, `.claude-plugin/`, `LICENSE`, `NOTICE`, `README.md`, `docs/` (the ccr contract and trigger-prompt record) and `scripts/`. It also includes the GSD planning record under `.planning/` (`PROJECT.md`, `REQUIREMENTS.md`, `ROADMAP.md`, `STATE.md`, `config.json`, `research/` and `phases/`), which is shipped on purpose (decision D-03); it is project history, not loaded by the plugin. Tooling, agent pointer files (including `.claude/CLAUDE.md`) and third-party reference text are not tracked (see `.gitignore`) and never ship. `scripts/check-package.sh` fails if any of them become tracked.

## Releasing

1. Bump `version` in `.claude-plugin/plugin.json` (semver). Do not set a version in `marketplace.json`.
2. Run `sh scripts/check-package.sh`; it must exit 0.
3. On the primary branch with a clean tree, run `claude plugin tag --dry-run`, then `claude plugin tag --push`.
4. Before the first push to a new remote, audit `git ls-files` for tooling or private files.

## Development

`sh scripts/check-package.sh` needs `git`, `jq`, `tar` and the `claude` CLI. It checks the tracked state (the index and tracked modifications), never untracked working-tree files. It validates the manifests with `claude plugin validate --strict`, installs the plugin into a throwaway `CLAUDE_CONFIG_DIR`, and checks skill properties, licence files and tracked-file hygiene.

## Licence

MIT, see `LICENSE`. Upstream attribution and the accepted licence risk for the v1.3 additions are in `NOTICE`.
