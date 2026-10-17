# Phase 1 Gap Closure Summary

Date: 2026-10-18. Source: `01-VERIFICATION.md` (status `gaps_found`, 3/5). Tickets: review-code-qz4.16 (Gap A), review-code-k4u (Gap B), both closed with one evidence comment each.

## Gap A: SC4 trigger collision (T10)

Cause: T10 "give me a critical review of the current working tree" routed to the installed `critical-code-reviewer`.

Fix: reworded only the SKILL.md `description` (commit `039d0e0`, blob `3700f2cca4617802e2cdd7529e3c87a61a72dc33`). It now names the working tree and "a code review, a critical or adversarial review, a critique, an audit or a check of code". The exclusion sentence ("Not for writing, explaining or debugging code.") is unchanged. Third person, under 1,024 characters. `sh scripts/check-package.sh` gives `27 ok, 0 FAIL`; `shellcheck scripts/check-package.sh` is clean.

Re-run: all 20 prompts, 2 runs each (40 runs), after the description change. Method: `claude -p` 2.1.302, `--plugin-dir .` in a fresh clone of `039d0e0`, `--model sonnet --max-turns 8`, the forced-routing `--append-system-prompt`, stdin `/dev/null`, 4 in parallel, first non-task-observer Skill call recorded. The iteration budget (two further rewordings) was not needed; the first rewording passed.

| Group | Result | Runs |
|-------|--------|------|
| T01-T10 (should trigger) | all `review-code:review-code`, including T10 | 20 of 20 |
| N01-N10 (should not trigger) | none invoked `review-code`; N09 invoked no skill | 20 of 20 |

Per-prompt results are in `docs/trigger-prompts.md` (results table, blob id, quoted description, summary, dispositions). No run disagreed with its twin.

Unresolved: the D-06 interactive hand check (real session, own hooks, no forcing prompt) was not performed. Headless forced-choice wins show which skill wins when a skill is invoked, not the unforced trigger rate. `docs/trigger-prompts.md` says so in Method and Dispositions.

## Gap B: SC1 git-source install

`claude plugin marketplace add` rejects `file://` URLs ("Invalid marketplace source format. Try: owner/repo, https://..., or ./path") and treats a bare-repo path as a directory ("Marketplace file not found at ...review-code.git/.claude-plugin/marketplace.json"). So a true git clone was forced through the shorthand form with a git URL rewrite, all isolated:

- Fresh empty `CLAUDE_CONFIG_DIR` in the scratchpad.
- `GIT_CONFIG_GLOBAL` pointing at a temp gitconfig with `url."<scratch>/fake/".insteadOf = https://github.com/` (also `git@github.com:`), plus `GIT_CONFIG_NOSYSTEM=1`.
- `<scratch>/fake/davdittrich/review-code.git` is a bare clone of the repo at `039d0e0`.

Commands and output (abridged to the lines that matter):

```text
$ git ls-remote https://github.com/davdittrich/review-code.git
039d0e01fb9f78f95a6896b21bc231878f352e99	HEAD
$ claude plugin marketplace add davdittrich/review-code
Cloning repository (timeout: 120s): https://github.com/davdittrich/review-code.git
Clone complete, validating marketplace…
✔ Successfully added marketplace: review-code (declared in user settings)
$ claude plugin install review-code@review-code
✔ Successfully installed plugin: review-code@review-code (scope: user)
$ claude plugin list
  ❯ review-code@review-code   Version: 0.1.0   Scope: user   Status: ✔ enabled
$ claude plugin details review-code
  Skills (1)  review-code
  Agents (0)  Hooks (0)  MCP servers (0)  LSP servers (0)
  Always-on: ~102 tok
$ cat $CLAUDE_CONFIG_DIR/plugins/known_marketplaces.json
{"review-code":{"source":{"source":"github","repo":"davdittrich/review-code"},...}}
```

The plugin cache held `docs LICENSE NOTICE README.md scripts skills`. The real `~/.claude/plugins/{known_marketplaces,installed_plugins}.json` do not mention `review-code` and known_marketplaces.json was not modified (mtime predates the run).

This exercises the real git-clone code path for the `owner/repo` shorthand and the marketplace-name equals plugin-name resolution. Still unproven: a clone over the real network from github.com (the repo is not published, so existence, visibility and credentials are untested), and the SSH/HTTPS fallback behaviour against GitHub. README and the gate are unchanged: README line 16 already says the GitHub install form is not documented until publication, and a git-source check cannot run in the gate without a remote.

## Commits

| Hash | Message |
|------|---------|
| `039d0e0` | fix(01): name critical-review asks in the skill description |
| `62a7a1c` | docs(01): record all 20 trigger trials on the reworded description |
| (this file) | docs(01): add gap closure summary |

## Left unresolved

- D-06 interactive trigger hand check (human).
- Real GitHub clone after first publish (needs a user-approved remote and a `git ls-files` audit).
- `REQUIREMENTS.md` / `ROADMAP.md` / `STATE.md` were not edited, as instructed; PKG-01 and PKG-03 status there still needs the orchestrator's decision.
