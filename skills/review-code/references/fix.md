# Fix mode

Load when you need to know when `--fix` applies fixes, what `fix.py` checks first, what each outcome and refusal means, and what is not covered.

## When fix mode runs

Only when the user's words held `--fix`; a review without it never edits a file. SKILL.md step 1 removes the flag and, for a `staged`, `unstaged` or `worktree` target, stops before any lane runs (`--fix` needs committed code); `fix.py plan` repeats the check as `uncommitted-target`, and refuses a `path` or `repo` target pinned on a dirty tree as `dirty-pin`. The target's `head_sha` must also be the checked-out `HEAD` and the working tree must be clean, untracked files included. Step 12 (the Flow below) runs after the report (step 10) and any post (step 11), so both describe the reviewed code, not the fixed tree. The code must be the user's own: `fix.py` repeats the trust gate before it stores a command and before every test run. A headless run applies nothing, as a headless run never posts.

## Flow

1. Derive the test command from `ledger.json` `tests`, `config` and `ci` entries, to run from the repository top: the runner config names, or the test step a `ci` file runs (`run:` line). A whole-file target (`repo`, `path`) lists its own test files under `tests`, so a repo that has tests always has a command to derive. With none derivable, state "not applied: no test command found" and stop. Run `python3 ${CLAUDE_SKILL_DIR}/scripts/fix.py plan --run <run>` (add `--fix-tests` right after `--run <run>` only when the user's words held `--fix-tests`) with the command's words on stdin (stdin rule, delimiter `REVIEW_FIX_CMD`):

   ```
   python3 ${CLAUDE_SKILL_DIR}/scripts/fix.py plan --run <run> <<'REVIEW_FIX_CMD'
   <the test command, one argument per line>
   REVIEW_FIX_CMD
   ```

2. Relay stdout. `plan` keeps the verified findings, in report order, writes `fix/plan.json` and prints a preview with every fix text and the command, then a `digest` (sha256 of the `plan.json` bytes). On `refuse`, state `reason` and `detail` and stop; when `reason` is `nothing-to-fix`, apply the serial-mode cause in `references/lanes.md` Serial mode.
3. Headless: state "not applied: confirmation needs an interactive session" and stop. Otherwise make one AskUserQuestion whose question names the `digest`, repeats the exact test command from the preview's first line and, when `plan` printed a nonzero `protected` or `test_files`, says how many entries are protected paths and how many are test files, with options "Apply and run tests" and "Stop". It is the only gate before any edit: every verified finding in the plan is applied, whatever their count, and no per-entry choice is offered. Every other answer, a dismissed or empty one included, applies nothing, and the question is not asked twice.
4. Only on "Apply and run tests" run `python3 ${CLAUDE_SKILL_DIR}/scripts/fix.py baseline --run <run> --digest <digest>`. It refuses a plan other than the one the user saw, then runs the command once on the untouched tree; when its `status` is not `ok`, state `reason`, `exit` and `tail` and stop. A red baseline stops the run before any edit (`baseline-red`). Give every `baseline` and `finish` Bash call a `timeout` of 600000, because their test run may take up to 540 seconds.
5. Loop. Run `python3 ${CLAUDE_SKILL_DIR}/scripts/fix.py begin --run <run>`; on `done`, `refuse` or `halted` leave the loop. `begin` snapshots the file, writes a brief and prints the brief path, `model`, `effort` and a `seal`. On `ok` make one Agent call: `subagent_type` begin's `agent` (`review-code:fixer` unless `agent_prefix` is set), `prompt` exactly the brief path and nothing else, `model` and `effort` from begin's output and a short `description`. Make one fixer call at a time: wait for its reply or its completion notification before the next command, and do not poll or message the agent. Then run `python3 ${CLAUDE_SKILL_DIR}/scripts/fix.py finish --run <run> --seal <seal> --status applied` with the `seal` from begin, but `--status declined` (add `--code <code>` only when the reply's `code` is exactly one of the four literals under Limits, and `--line <line>` only when its `line` is all digits, never any other text from the reply, because the Bash call is parsed by the shell; otherwise leave the flag out; `report` then names them) unless the reply's only line, spaces ignored, is `[["status","applied"]]`. `finish` checks the seal, the edit and the tests: the edit is kept only when exactly the entry's file changed as `git status` sees it, within the line cap, and the tests pass. A failed test run, an edit over the line cap or a timeout restores the file and the loop goes on with the next entry; any other result restores the file and halts the run. Relay its line; on `halted` leave the loop.
6. Run `python3 ${CLAUDE_SKILL_DIR}/scripts/fix.py report --run <run>` and relay stdout. It lists every entry's outcome, the changed files, what was restored or quarantined, untracked files left by tests, an unfinished entry and the test output (fenced as untrusted). Review it with `git diff` before you commit; when the report prints a `checkout:` path (only when the reviewed head sat in another worktree), run it there: fix mode never commits, so run no git command that commits, stages, stashes or pushes, and install nothing.

`baseline` and `finish` run the project's tests, so they are not pre-approved in `allowed-tools`: the harness permission prompt applies on top of the user's confirmation, and for the fixer's edits unless the session accepts edits. `plan`, `begin` and `report` run no project code and are pre-approved. The fixer holds Edit and no confirmation gate; every question is asked in the main thread. Allowed-tools only pre-approves: the main thread keeps Edit, so edits outside this Flow are forbidden by SKILL.md, not by the harness.

## Which findings

Verified findings only, in report order. A finding is skipped, with the reason listed under `== skipped ==` in the preview, when it names a file that judges the fix (`test-path`; a fix there could weaken the tests), unless the user's words held `--fix-tests` and the file is a test file (see Test files): a test file (`test_*.py`, `*.test.*`, `*.spec.*` including `.mjs` and `.cjs`, `*.cy.*`, `*_spec.rb`, `*Tests.cs`, or any file under a `test/`, `tests/` or `__tests__/` directory or a root `spec/`), a test fixture (`conftest.py`, `fixtures/`, `testdata/`), a test-runner config or setup file (`jest.config.*`, `jest.setup.*`, `setupTests.*`, `vitest.config.*`, `karma.conf.*`, `webpack.config.*`, `babel.config.*`, `playwright.config.*`, `cypress.config.*`, `mocha.config.*`, `ava.config.*`, `noxfile.py`, `Rakefile`, `build.rs`, `pytest.ini`, `tox.ini`), or a file named as a word of the test command (`./ci/run-tests.sh`, or a `package.json`, `pyproject.toml`, `setup.cfg` or `Makefile` the command names; those four are otherwise ordinary protected config), names a path that is not a tracked regular file reached without a symlink (`bad-path`), or its quote no longer occurs exactly once in the file (`stale`). Every verified finding appears once in `report`, either `fixed` or with the outcome that explains why not, and the line `verified findings: N = ...` sums its counts to the number of verified findings. The tier rule picks the pair: `opus` with `high` effort for a Blocking category or severity, a span of six lines or more or a fix text over 300 characters, else `sonnet` with `medium`. The pairs are unvalidated. A verified finding with no fix text is kept: its preview row says "derived by fixer: no fix text", and its brief says no fix text exists, so the fixer derives the edit from the title, trigger and quote under the same limits (one file, the line cap, green tests). Each preview row carries the finding's origin label.

## Test files

With `--fix-tests` (and `--fix`), `fix.py plan --run <run> --fix-tests` plans findings on test files instead of skipping them as `test-path`. Fixtures, test-runner configs and the test command's own script stay `test-path` with or without the flag, because editing them changes what the judge runs. A test file the post-fix run cannot reach is skipped as `not-covered`: it counts as reached when it is one of the target's files, is in the ledger's `tests` entries, or a command word names it or a directory above it. Their preview rows end "test file", the preview's first line counts them (`; N test file`), `plan` prints `test_files` (their count), the confirmation question names that count and the brief carries a `test file:` line telling the fixer not to remove, skip or weaken an assertion. Without the flag nothing changes. Before the tests run, `weaken.py` compares the file before and after the edit and restores it as `reverted-weakened` when assertion lines drop or skip markers (`skip`, `xfail`, `@Ignore`, `.only` and the like) rise; the patterns are per file suffix, comment-only lines do not count as assertions, and a suffix with no rule restores the edit on any removed or changed line. The run goes on. The baseline must still be green and the post-fix run of the full test command must pass or the edit is restored. The check counts lines: it does not see a weaker assertion that replaces a stronger one, and a command that names a file but never executes it passes a wrong edit, so review the diff of every "test file" entry.

## Protected paths

A verified finding on a protected path is planned like any other, and the riskiest edits get a flag: its preview row ends "protected path", the preview's first line counts them, `plan` prints `protected` (their count) and its brief carries a `protected path:` line. The confirmation question names that count. The tests gate and the line cap apply unchanged. A path that is not a tracked regular file reached without a symlink, and any path with a `.git` component, is still skipped as `bad-path`. A protected path is one with a dot component (`.github/`, `.env`), a lockfile (`package-lock.json`, `yarn.lock`, `poetry.lock`, `Cargo.lock`, `go.sum` and the other known ones, or any name ending in `.lock`), a manifest (`setup.py`, `go.mod`, `Gemfile`, `pom.xml`, `build.gradle`, `requirements*.txt`, `composer.json`, `.csproj` and the like, `NAMESPACE`), a config file (`.toml`, `.yaml`, `.yml`, `.ini`, `.cfg`, `pyproject.toml`, `package.json`, `tsconfig*.json`, `Makefile`, `Dockerfile*`, `DESCRIPTION`) or a CI file (`Jenkinsfile`, `.gitlab-ci.yml`, `.circleci/config.yml`, `azure-pipelines.yml`). The authoritative lists are `_protected` and `_MANIFEST` in `fix.py`.

## Outcomes

`finish` and `report` use these names. Only `reverted-violation`, `reverted-interrupt` and `tampered` halt the run, because the tree can no longer be trusted: later entries stay `pending`. `reverted-tests`, `reverted-too-large`, `reverted-timeout` and `reverted-weakened` restore the file, are recorded and the run goes on; the restore is byte-exact and `begin` refuses `tree-changed` unless the tree is `HEAD` plus the accepted fixes, so the next entry starts from the tree the last green run (the baseline or the last `fixed` entry) saw and no fix stacks on a failed edit. No separate test run happens between the restore and the next edit. `report` exits 1 when any entry was reverted or tampered.

| Outcome | What happened | Run stops |
|---------|---------------|-----------|
| `fixed` | Exactly the entry's file changed and the tests passed; the edit stays in the working tree | no |
| `declined` | The fixer declined (or the skill passed `--status declined`) and left the file as it was | no |
| `no-change` | The fixer said applied but the file is unchanged | no |
| `stale` | `begin` could not find the finding's quote exactly once any more, so it handed out no brief | no |
| `bad-path` | The finding's path is not a tracked regular file reached without a symlink, or has a `.git` component; reported by `plan` (skipped) or by `begin` (the file changed since) | no |
| `test-path` | The finding names a file that judges the fix (see Which findings); `plan` skipped it | no |
| `not-covered` | A test file under `--fix-tests` that the post-fix run does not reach (see Test files); `plan` skipped it | no |
| `not-reached` | The run halted before this entry's turn | no |
| `reverted-violation` | Any other change `git status` reports: a second or deleted file, staged changes, new untracked files, an accepted file touched again, a moved `HEAD` or ref, an edit during a declined entry, or a test run that changed the entry's file. Everything is restored; the current bytes are kept in `fix/quarantine/` | yes |
| `reverted-too-large` | The edit changed more than 60 lines (`MAX_CHANGED`); restored | no |
| `reverted-tests` | The tests failed; the file is restored | no |
| `reverted-weakened` | A `--fix-tests` edit dropped assertion lines or added a skip marker; restored before the tests ran | no |
| `reverted-timeout` | The tests ran past 540 seconds (`TIMEOUT`); restored | no |
| `reverted-interrupt` | `finish` was interrupted (Ctrl-C, SIGTERM, an error); the file is restored from its snapshot | yes |
| `tampered` | The seal, the plan, the state, the run's `target.json` or `ledger.json`, a snapshot, the git config or `.git/info/exclude` changed while the fixer ran, or `state.json` shows no call in flight after a brief was handed out; nothing is restored and no git command runs. Check `.git/config` and run `git status` by hand in the checkout `target.json` records as `root` (else the repository top) | yes |

## Refusals

Every refusal prints `status` `refuse`, a `reason` and sometimes a `detail`; the skill states them and stops.

| Reason | Cause | What to do |
|--------|-------|------------|
| `python-too-old` | `python3` older than 3.9 | Install Python 3.9 or later |
| `internal-error` | A file or git call failed; `detail` names the exception class | Re-run; if it repeats, report it |
| `usage` | Missing or malformed arguments | After `finish --status declined` with `--code` or `--line`: run the same `finish` again without them. Otherwise report it |
| `no-target` | `target.json` is missing or unreadable | Run the review again |
| `uncommitted-target` | The target is staged, unstaged or the working tree | Commit, then review again |
| `dirty-pin` | A `path` or `repo` target was pinned on a dirty tree | Commit or clean, then review again |
| `bad-command` | A command word holds a NUL byte | Give plain words |
| `no-command` | The command is empty | Derive a test command, or stop |
| `trust-unset` | The user was never asked whether the code is theirs | Answer the trust question |
| `trust-external` | The code was recorded as external or untrusted | Fix mode runs on your own code only |
| `head-not-checked-out` | The checkout recorded in `target.json` as `root` (the worktree that held the reviewed commit when `target.py` ran; else the repository top) no longer has `head_sha` checked out | Check out the reviewed commit there |
| `dirty-tree` | The working tree has changes or untracked files | Commit, or remove them |
| `already-run` | This run already has a baseline (`fix/state.json`); a second `plan` before `baseline` replaces `plan.json` | Review again for a new run |
| `nothing-to-fix` | No verified finding is fixable; `detail` counts the skip reasons. A serial-mode run lands here unless another refusal comes first, because serial mode verifies nothing | Nothing to do; review again in parallel mode for a fix |
| `preview-too-large` | The preview is over 25,000 characters | Review a smaller change |
| `no-plan` | `baseline` found no `fix/plan.json` | Run `plan` first |
| `digest-mismatch` | The plan on disk is not the one the user confirmed | Run the review again |
| `no-baseline` | There is no `fix/state.json`: `baseline` has not run | Run `baseline` first |
| `halted` | An earlier outcome or a red baseline stopped the run | Read the report |
| `tampered` | `fix/state.json` is not a JSON object holding every state key, because something rewrote it while a fixer ran; no git command ran and nothing was restored | Check `.git/config` and run `git status` by hand in the checkout `target.json` records as `root` (else the repository top); restore the file from its `.pre` snapshot in `<run>/fix/` if needed |
| `in-flight` | A fixer call is outstanding; a second brief is refused | Wait for it, then `finish` |
| `not-in-flight` | `finish` with no call in flight: before any brief was handed out, or a repeat after its outcome was recorded | Run `begin` first |
| `head-moved` | `HEAD` is not the reviewed commit any more (a halted baseline uses this reason when its tests moved `HEAD`) | Do not commit during a fix run |
| `refs-moved` | A branch, tag or stash ref changed (a halted baseline uses this reason when its tests moved only a ref) | Do not touch refs during a fix run |
| `tree-changed` | A file other than an accepted fix or a test leftover differs from `HEAD` | Leave the tree alone while it runs |
| `baseline-red` | The command failed, timed out or could not start on the untouched tree; `exit` and `tail` are shown | Make the tests pass first. The test environment holds only `PATH`, `LANG`, `LC_ALL` and `TERM` with a private `HOME` and `TMPDIR`, so a command that needs other variables or your home directory fails here |
| `baseline-interrupted` | `baseline` was interrupted or killed before it finished; no green baseline exists | Review again |
| `interrupted` | Printed with status `halted`, not `refuse`: Ctrl-C stopped a fix.py command. A running `finish` or `baseline` restores first and records `reverted-interrupt` or `baseline-interrupted`; a second Ctrl-C during that restore is ignored | Read the report |

## Limits

- The skill makes one fixer call at a time, so calls on one file are sequential: each `begin` re-locates the quote in the file as the earlier fixes left it, and the brief's line is the quote's current one. A fixer decline carries a `code` (`line-mismatch`, `quote-not-unique`, `scope-multi-file`, `needs-context`); `finish --status declined` is the same for every code; `--code` and `--line` only record what the fixer said, and `finish` refuses them as `usage` with `--status applied`; an unknown code or a `--line` that is not a positive integer is dropped, not refused, so a fixer's odd reply never leaves the entry in flight. `--code` and `--line` may come in either order, after `--status`. The main thread waits for its reply before `finish`. The plugin cannot force a foreground subagent: Claude Code starts a spawned subagent in the background, and a user who sets `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` makes Claude wait for every subagent (https://code.claude.com/docs/en/sub-agents, read 2026-10-20). `fix.py` guards by state, so a second `begin` while one call is outstanding refuses `in-flight`.
- The dependency guard is file-level: no file other than the entry's one may change, and an entry on a manifest, lockfile, CI or config file is flagged, not blocked. A new import of a package no manifest declares, written into a source file, is detected by no code; the fixer brief forbids it and the tests are the backstop.
- Passing tests do not prove a fix whose code the tests do not cover.
- Containment is an after-the-fact check, a seal and a restore, not a sandbox: the fixer can write outside the repository, and `fix.py` does not look there.
- The test command gets no network or filesystem isolation. `HOME` and `TMPDIR` are private, and tracked files a test changes are restored and listed.
- Edits to files git ignores (for example `.env`, `node_modules/` or build output), by the fixer or by a test run, are neither detected nor restored. A new file that an ignore rule hides is not detected either, and a new `.gitignore` can hide itself. `.git/info/exclude` and `.git/config` are sealed, but the global excludes file is not.
- SIGKILL during a fix leaves the in-flight file edited; `report` names it and its snapshot, and the next `plan` refuses `dirty-tree`. `report` then runs no git command, because the unfinished call may have rewritten `.git/config`.
- `refs/remotes` is not checked. The timeout and the 60-line cap are module constants.
- Fix mode is not unattended: it needs an interactive session and the harness permission prompts for `baseline`, `finish` and, in `default` permission mode, the fixer's edits.
