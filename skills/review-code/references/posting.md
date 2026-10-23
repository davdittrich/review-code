# Posting

Load this when you need to know how `--comment` posts a review to a pull request or merge request, what it posts, why it refuses, and what is not covered.

## When posting runs

Only with `--comment`, only for a PR or MR target, only in an interactive session. SKILL.md step 1 removes the flag and, for any other target, stops before a lane runs; `post.py build` repeats the check as `not-a-forge-target`. Step 11 (the Flow below) does the posting after the report. A headless run never posts: with no AskUserQuestion it states "not posted: confirmation needs an interactive session" and stops. Headless `--comment` on a PR is unsupported: the approval prompt on the lane-ingest command is intermittent.

## Flow

1. Run `python3 ${CLAUDE_SKILL_DIR}/scripts/post.py build --run <run>` and relay stdout. It is read-only on the forge and pre-approved in `allowed-tools`. It reads the pinned `target.json`, the live PR or MR and its diff, builds `post/payload.json`, prints a preview and prints the sha256 of the payload bytes as `digest`. When its last line has `status` `refuse`, state `reason` and `detail` and stop; when `reason` is `nothing-to-post`, apply the serial-mode cause in `references/lanes.md` Serial mode. On a re-review of a PR you already reviewed, the body opens with a `**Prior:**` line: see Re-reviews.
2. Headless: state "not posted: confirmation needs an interactive session" and stop. Otherwise make one AskUserQuestion whose question names the `digest`, with options "Post as COMMENT" and "Do not post". Every other answer, a dismissed or empty one included, posts nothing.
3. Only on "Post as COMMENT" run `python3 ${CLAUDE_SKILL_DIR}/scripts/post.py submit --run <run> --digest <digest>` with the `digest` from build's last line and state its result. It is not pre-approved, so the harness permission prompt also applies. It refuses unless the hash of `payload.json` equals the digest the user saw, re-scans every body, re-checks the head and then writes. Posting never happens through any other gh or glab command.
4. GitHub: create one pending review with `commit_id` pinned and its inline comments, read the stored comments back and compare them with the payload (count; per comment path, commit id, its finding marker line and its place, see Verify), re-check the head, count the review's comments once more (a comment started in the web UI joins your one pending review), send the one `COMMENT` event. GitLab: create a summary draft note and one positioned draft per inline finding, verify the user's drafts are exactly those, re-check the head, count the drafts once more, publish all drafts in one call that takes no parameters.
5. Any failure after a create deletes only the objects this call made: the review id its own create returned, while every comment in it carries one of this payload's finding markers as a whole line, or the drafts that carry this payload's marker lines. A GitHub review that also holds a comment of anyone else's making when cleanup lists it (one you started in the web UI joins your one pending review) is kept and refuses `review-kept`; see the GitHub race below for the one-request window after that listing. When a GitHub create fails or its answer is lost (a timeout, a dropped connection, unparsable JSON), submit lists your reviews and deletes a PENDING one of yours whose body holds this payload's review marker line; a pending review you start in the web UI meanwhile has no marker and is never touched. If a delete fails, `cleanup-failed` names what is left.

The forge calls go through `gh api` and `glab api` with an explicit `--hostname`, repository and number taken from `target.json`, never from the working directory's remote.

## Verify

After the create, submit lists the pending review's comments and refuses `verify-failed` unless they are exactly the payload's: same count, and each stored comment matches one payload comment on path, `commit_id` (the pinned head), the finding's marker as a whole line of its body, and its place. GitHub lists the comments of a PENDING review with `line`, `side` and `original_line` null and a diff `position` instead (observed live on a real PR, 2026-10-20). So when `line` and `side` are null the place is the `position`, compared with the position of the finding's row in the live `gh pr diff` (it returns the current head's diff; check (d) right after verify confirms the head is still the pinned one): rows below the file's first `@@` header, a later `@@` header and a `\ No newline` line each counting one. When a stored comment does carry `line` and `side`, those are compared instead. `payload.json` holds no position; it is derived at verify time, so the digest does not depend on it.

## What is posted

- Verified findings (a confirmed verdict), in report order: axis, severity, path, line, id. Unverifiable items are not posted; the body says how many ("Not posted: N unverifiable items").
- One review, event `COMMENT`, and never `APPROVE` or `REQUEST_CHANGES`: `COMMENT` is the only event in `post.py`. The verdict line is labelled advisory.
- Severity as a word label (Blocking, Required, Suggestion, Noted; a nit opens `nit: **Nit**`), no emoji.
- After the verdict and the axis counts, one `Areas inspected:` line lists the inspected areas, at most 20, then `(+N more)`. It is part of the previewed payload.
- At most 32 inline comments. A finding without a diff anchor, or beyond the 32nd, goes into the review body with its location. Each body stays under 60000 characters (`too-large` otherwise); the whole preview stays under 25000 characters (`preview-too-large` otherwise), so the confirmation never covers text the harness cut.
- No disclosure or attribution line, and no option to add one.

## Anchors

A finding is inline only when its `path:line` is a context or added row of the forge's own diff (`gh pr diff`, or the GitLab diffs endpoint). GitHub gets `line` and `side` RIGHT. GitLab gets `new_line`, plus `old_line` for an unchanged row (a context row needs both). A finding on a line outside every hunk goes into the body. GitLab files reported `collapsed` or `too_large` are not anchorable, so their findings go into the body. There are no multi-line range comments.

## Head pin

The head sha in `target.json` is the head that was reviewed. It is compared with the live head at four points: (a) in build before the diff is read, (b) in build after the reads, (c) in submit before the first write, (d) in submit before the event or publish call. A difference at any of them refuses `head-moved`. GitHub also sends the pinned head as `commit_id`; on GitLab the three `diff_refs` (base, start, head) read at (a) must be unchanged at each later check and are sent in every position.

## Markers

Every posted finding ends with the line `<!-- review-code:finding F-xxxxxxxxxx v1 -->`, and the review body ends with `<!-- review-code:review v1 head=<sha> -->`. Finding ids do not change when the head moves, so a finding posted for one head is skipped for a later one. The review marker may also carry the ids earlier runs found fixed or outdated: `<!-- review-code:review v1 head=<sha> fixed=F-a,F-b outdated=F-c -->` (both lists optional, in that order; an old marker with the head alone still parses; any other field makes the marker void).

Re-posting counts a marker only when it is a whole line (CRLF normalised) in a review body, review comment or note written by the authenticated user: the exact login on GitHub, the user id on GitLab. A marker in anyone else's text, or inside a quote line (which carries a `N: ` or `| ` prefix), is not counted. Build drops the findings already posted and reports them as "already posted"; when nothing is left it refuses `already-posted`. Submit repeats the check before its first write.

## Re-reviews

The forge is the ledger: there is no local state file. `prior.py` reads only reviews, review comments and notes written by the authenticated login, as whole lines, and keeps a record only when its fields validate (`F-` and 10 hex, a 40 or 64 hex sha, a closed state word). A finding's path is the REST comment `path`, or the `**Location:**` line of an own review body; if neither is readable the id is `unknown`. GitHub only: GitLab posts as before, and `--since-last` refuses `since-last-unsupported` there and for any non-PR target.

With P the prior ids and R this run's verified ids: `new` = R minus P (plus an id that was fixed and is back); `open` = R and P. A prior id absent from R is `outdated` when a new id sits on its path (the quote moved or changed, so the hash did; not claimed fixed); `fixed` when the run is complete (parallel, every Defects, Standards and Spec lane complete, nothing lost or rejected, none of those lanes skipped except "no spec"), its path was in this run's scope (the diff, or the `--since-last` range), no new id sits on that path and the file changed since the prior review head (`git diff --name-only <prior head> <head>`; a missing or unreachable prior head, or a git failure, counts as unchanged); else `unknown`, and an id left unknown only because its file did not change is listed as `unknown (F-a: file unchanged since the last review)`. `fixed` means only "no longer found by this review, in a file that changed": never proven. Fixed and outdated ids are carried in the review marker, so the next run reports each once. A prior nit (its heading opens `nit: **Nit** (`) is `unknown` unless the run is `--nit`: only a nit run looks for nits. Spec findings cannot be told apart from others by id, so a run without a spec reads their absence as fixed.

The body gets `**Prior:** 1 open, 1 fixed (F-a), 1 outdated (F-b -> may be F-c), 1 unknown, 1 unknown (F-d: file unchanged since the last review)` (ids and counts only), each new finding a `**State:** new` line, and `**Not run:** ...` (the lanes and checks that did not run: a lane name with optional `@shard`, `shard N`, `repro`, `serial`). `already-posted` now means no finding is new and no prior state changed; when only the states changed, a body-only status review is posted, still `COMMENT`; submit refuses it as `already-posted` when the newest own review marker equals its marker line. Resolved threads (GraphQL `isResolved`) are not read: nothing here fakes GraphQL yet, so a resolved thread still counts as open.

`--since-last`: target.py reads the head of your newest review; when it is an ancestor of the PR head, the diff range starts at that head (`last..head`; target.json `diff_base`) while the merge base, review rules and sibling hints stay on the true merge base, so a rule file pushed earlier is never trusted; findings on lines added before `last` are outside the range and read as pre-existing. Otherwise the review is full and the `since_last` line says why (no readable review, force-push or rebase).

## Secret scan

Eleven regular expressions adapted from gitleaks (MIT, see NOTICE) run on every outgoing body: `aws-access-token`, `github-pat`, `github-oauth`, `github-fine-grained-pat`, `gitlab-pat`, `gcp-api-key`, `slack-bot-token`, `stripe-access-token`, `jwt`, `private-key` and `generic-secret` (a `password`, `secret`, `token` or `api-key` assignment whose value sits in `"`, `'` or backticks; prose turns a backtick into `'`, so the backtick form is redacted before that). A match is replaced by `[REDACTED:<rule>]` in each raw field (title, trigger, quote, fix) before that field is escaped, so a redaction can never remove an escape character. `private-key` matches only a `BEGIN` or `END ... PRIVATE KEY` line, because a key body has no reliable shape (PGP armor, CRLF, string literals); a hit anywhere in a finding replaces all four fields of that finding, and of every other posted finding in the same file, with `[REDACTED:private-key]` (another finding may quote only the key body); each such finding is listed in the preview's scan. The scan then runs again on every rendered body before the payload is written. The preview lists each rule and the finding's `path:line`, never the matched text. Submit scans again, so an edited `payload.json` with a recomputed digest still refuses `secret-found`; a scanner that raises refuses `scan-failed` rather than posting unscanned.

Limits: regex only. There is no entropy check and no decoding, so an encoded, split or unusual secret passes, and so does a private key body quoted without its `BEGIN` or `END` line when no posted finding in that file quotes either line. The scan runs on posted text, not on the repository.

## Text rules

Repository-derived text can ping people, trigger forge commands or hide markup, so every field is rewritten.

- Fixed labels (`**Trigger:**`, `**Evidence:**`, `**Fix:**`) start each field, so no posted line starts with `/` or `<!--`.
- Prose is one line. Every reference GitHub or GitLab links or pings goes in a code span, which their reference filters skip; they match rendered text, so an escape alone would not stop one. That covers `@name`, `@org/team`, `#123`, `!123`, and the GitLab forms in docs.gitlab.com/user/markdown: `&123` (epic), `~bug` and `~"two words"` (label), `%v1.2` and `%"two words"` (milestone), `$123` (snippet), `^alert#1`, `*iteration:x`, `[issue:1]`-style bracket references, `[[Page]]`, `GL-123` (and GitHub's `GH-123`, also inside a word or right after another reference) and `namespace/project>`. Everything else has `&` turned into `&amp;` (so `&commat;x` cannot render `@x`), `\` doubled, `<` turned into `&lt;` and `[` into `\[`. A dollar amount, `x^2` or `%d` also ends up in a code span; that is cosmetic.
- The quoted code sits in a fence longer than any backtick run inside it. Each line is prefixed with its line number only when the quote fits exactly one place in the finding's rows of the reviewed file (inner lines equal to their row after whitespace folding, the first a row's tail, the last a row's head). Otherwise each line is prefixed `| ` under a `**Lines:** L-E (quoted without line numbers)` label, because a lane chooses where the quote's line breaks go. A CRLF in the quote becomes LF and a lone CR becomes the visible text `<U+000D>`, so no quote text can start a line of its own.
- The line guard refuses `unsafe-text` for any CR in a body, and for any line, outside the fence prefix, that starts with `/` or `<!--` and is not one of the marker lines.
- The guard is an allowlist of the line shapes build writes. Outside the quote fence a line must be empty, `---`, a marker line, the fence opener (backticks then `text`), or start with one of the fixed labels (`**Verdict:**`, a severity label and ` (` (a nit opens `nit: **Nit** (`), `**Basis:**`, `**Location:**`, `**Lines:**`, `**Trigger:**`, `**Evidence:**`, `**Fix:**`), an axis count (`Defects: 2 findings`), the exact `**Prior:**`, `**State:** new` and `**Not run:**` shapes (Re-reviews), `Areas inspected: ` or `Not posted: N unverifiable items`. Any other line refuses, so a `~~~` fence or an indented block cannot hide text from the checks. Inside the fence every line must start with `N: ` or `| `, and only the exact opener's backticks close it; a fence that never closes refuses.
- A labelled line must also keep the prose rules: no raw `<`, `&` (other than `&amp;` and `&lt;`) or backtick, no `[` behind an even backslash run, and no reference of the forms above outside a code span in the text the forge renders (entities undone, and a backslash dropped only before ASCII punctuation, as CommonMark does).
- Build and submit run the same guard, so an edited `payload.json` with a recomputed digest is held to every rule above and refuses `unsafe-text`.

Bare URLs and commit SHAs still autolink (links, not pings).

## Refusals

`build` and `submit` print one JSON line; `plan.py`, `report.py ingest` and `report.py verdicts` raise `no-pin`, `pin-pruned` and `bad-ref` too, with the same meaning. `plan.py`, a `report.py ingest --lane spec` and a `report.py verdicts` call on spec findings also raise `no-ledger` and `bad-ledger`; the two `report.py` calls also raise `record-unreadable`. On a refusal `status` is `refuse` and `reason` is one of these (`detail` carries at most `HTTP nnn`, `timeout`, a handle or a count, never forge text).

| Reason | Meaning | What to do |
|--------|---------|------------|
| `usage` | Wrong arguments, or a digest that is not 64 hex digits | Run the two commands as Flow gives them |
| `python-too-old` | Python below 3.9 | Use Python 3.9 or later |
| `internal-error` | An unexpected file or data error (`detail` is the exception type) | Re-run the review; report it if it repeats |
| `no-target` | `target.json` is missing or unreadable | Re-run the review from step 1 |
| `no-pin` | `target.json` has no `pinned_tree`: the run predates the pin | Review again |
| `pin-pruned` | The pinned tree is gone (a `git gc` pruned the synthetic tree of an in-place pin) | Review again |
| `bad-ref` | A git command that reads the pinned tree failed (`ls-tree` or `cat-file`), or the tree lists a missing blob | Re-run; report it if it repeats |
| `no-ledger` | The run has no `ledger.json` (`plan.py`, spec ingest and spec verdicts) | Run `ledger.py build` for the run, or review again |
| `bad-ledger` | `ledger.json` cannot be read or has the wrong shape, or `inputs/spec.md` cannot be read; the spec copy guard refuses rather than pass text it cannot check | Run `ledger.py build` again, or review again |
| `record-unreadable` | A decision record the ledger lists is over the 4,000,000-byte file cap (`MAX_FILE`) or not text (a NUL byte), so the spec copy guard cannot read it whole; a lane could still read it in parts, so the guard refuses rather than count it as no words (spec ingest and spec verdicts) | Shrink the record, save it as plain text (UTF-8) or move it out of the record directory, then review again |
| `not-a-forge-target` | The target is not a PR or MR | Run without `--comment` |
| `bad-target-url` | The URL, host, repository, number or head in `target.json` is malformed | Re-run the review |
| `gh-missing` | `gh` is not installed | Install `gh` |
| `glab-missing` | `glab` is not installed | Install `glab` |
| `gh-failed` | A `gh` call exited non-zero, ran over 120 seconds (`detail` `timeout`) or returned unusable JSON | Check `gh auth status` and network; `detail` may hold `HTTP nnn` |
| `glab-failed` | The same for `glab` (a call over 120 seconds gives `detail` `timeout`) | Check `glab auth status` and network |
| `no-identity` | `GET /user` did not return a login or id (a GitHub App token has none) | Use a user token |
| `closed` | The PR or MR is closed | Reopen it or stop |
| `merged` | The PR or MR is merged | Stop |
| `locked` | The MR is locked | Wait until it is unlocked |
| `draft` | The PR or MR is a draft | Mark it ready for review |
| `diff-unavailable` | The forge could not return the diff (for example a very large PR) | Nothing is posted with unchecked anchors, and there is no body-only fallback |
| `diff-refs-pending` | GitLab has not computed the diff refs yet | Wait and re-run |
| `draft-notes-unavailable` | The GitLab instance has no draft notes endpoint (HTTP 404) | Post is not possible on this instance |
| `too-many-comments` | More than 20 pages of reviews, comments, notes or drafts | Post by hand |
| `nothing-to-post` | The run has no verified finding. A serial-mode run lands here unless another refusal comes first, because serial mode verifies nothing | Nothing to do; review again in parallel mode to post |
| `head-moved` | The PR or MR head (or GitLab diff refs) changed after the review | Review the new head |
| `already-posted` | Every verified finding already carries your marker on this PR or MR and no prior finding changed state | Nothing to do; a retry after a timeout also ends here |
| `pending-review-exists` | You already have a pending review on this PR (never deleted: it may hold your work) | Submit or delete it, then re-run |
| `pending-drafts-exist` | You already have draft notes on this MR (the publish call would send them too; never deleted) | Publish or delete them, then re-run |
| `too-large` | A body is over 60000 characters | Review a smaller change |
| `preview-too-large` | The preview is over 25000 characters. Claude Code saves Bash output over 30000 characters (its default `BASH_MAX_OUTPUT_LENGTH`) to a file, so you would confirm text you never saw | Review a smaller change; nothing was written |
| `non-utf8-name` | `ledger.py build` (the run directory path), `report.py ingest` (a path, title or trigger in a lane reply; the whole reply is refused, not the one line), `post.py build` (a finding's path) or `post.py submit` (a payload comment path) holds a byte that is not valid UTF-8; no file is written and no forge call is made | Review a checkout whose names are valid UTF-8 |
| `unsafe-text` | A body holds a CR, or a line would run as a command or hide as markup | Report it; nothing was posted |
| `secret-found` | A secret rule matches in an outgoing body: at submit time, or at build time in text the per-field redaction did not cover (a path) | At submit: re-run build, which redacts. At build: report it; nothing was posted |
| `scan-failed` | The secret scanner raised | Report it; nothing was posted |
| `no-payload` | `post/payload.json` is missing | Run build first |
| `digest-mismatch` | The payload hash is not the digest given | Run build again and confirm the new preview |
| `payload-mismatch` | The payload does not belong to this target and head | Run build again |
| `post-failed` | The forge refused the create, event or publish call, or the create's answer was lost. When a GitHub event call fails, submit reads the review's state first: if GitHub applied the event anyway (its answer was lost), submit reports `ok` instead | Check `detail`; nothing was left pending unless `cleanup-failed` follows |
| `post-partial` | GitLab's publish call failed after publishing some drafts: `detail` is `published n of m` (your new notes that carry this payload's markers, of the drafts made), or `published unknown` when the notes could not be listed. On GitHub: the event call failed and the review's state could not be read, `detail` `published unknown`, and the review is not deleted; or the run was interrupted after GitHub applied the event, `detail` `published` (cleanup found the review already submitted) | GitLab: the review is public but incomplete; the drafts left are deleted. GitHub: open the PR; the review is either submitted or still pending as yours. Re-run build either way: it skips the findings already posted |
| `verify-failed` | What the forge stored differs from the payload: count, path, commit id, marker line, or position (line and side on GitLab) | Nothing is posted; the plugin's own pending objects are deleted |
| `review-kept` | After a failure, the GitHub pending review this run created also holds a comment without this payload's marker line (for example one you started in the web UI), so it was not deleted; `detail` names it | Nothing was published. Open the PR and delete that pending review, or first remove the plugin's comments from it (they may be ones verify rejected) and then submit your own |
| `cleanup-failed` | A delete after a failure did not work; `detail` names the review or drafts left, or is `pending review unknown` when the listing after a failed GitHub create did not work | Delete them by hand; for `pending review unknown`, check the PR for a pending review of yours that ends with the review marker |

## GitLab specifics and not covered

- Whether GitLab's publish call is atomic is not known. After a failed publish, submit lists your notes and refuses `post-partial` when any note of this payload is public, so the outcome is never reported as not posted.
- MR posting is untested against a live GitLab. `glab` was not available, so every GitLab path is proven only against a fake forge in the test suite. A first live run may find differences; `verify-failed` and `post-failed` refuse safely.
- The reviewer state after publish is undocumented (OQ7). No approval or request-changes call exists; whether publishing marks you as having reviewed is not known.
- Race: a draft you add in the GitLab UI during submit is published with the review if it lands after the last draft count and before the publish call. The pre-check refuses drafts that exist before submit, and verify and the count right before publish refuse `verify-failed` for one added earlier; the window left is one request.
- Race, GitHub: you have at most one pending review per pull request, so a review comment you start in the web UI while submit runs joins the plugin's pending review and would be published inside its COMMENT review, unconfirmed. Verify and a second count right before the event refuse for one added before them, and the review is then kept (`review-kept`) rather than deleted with your comment in it; one added after the count and before the event is published. After a failure, cleanup keeps the review only for a comment already in it when cleanup lists it: a comment you start in the one request between cleanup's listing and its delete is deleted with the review, because GitHub's delete takes no precondition. Each window left is one request (inferred from GitHub's one-pending-review model, not checked live).
- Race: the head can still move between check (d) and the forge call. The window is one request.
- The lost-create cleanup on GitHub assumes the reviews listing shows your own PENDING review with its body, as the fake forge does; this path is not checked live (the plain delete of a pending review was observed live, below).
- `glab api` is called with a query string in the endpoint (`...?per_page=100&page=N`); that is assumed to work and is unchecked. The fallback is `--method GET -F page=N`.
- Observed live on GitHub (one real PR, 2026-10-20; the first live submit refused `verify-failed` and was fixed, see Verify): a pending review's stored comments have null `line`, `side` and `original_line` and a `position`; the plugin's own delete of that pending review worked; a failing `gh api` call exits non-zero with `HTTP nnn` on stderr (A3, a 404 read); the second submit posted one `COMMENTED` review whose `commit_id` is the pinned head, with the inline comment on the expected line and side RIGHT and the out-of-hunk finding in the body.
- A7 holds on GitHub. Re-checked live on GitHub (2026-10-20, plugin commit ff63928, review 5587337735 on a second real PR): one `COMMENTED` review by the posting login with `commit_id` equal to the pinned head; both marker lines are stored whole-line in the review body and the review comment body, and the rendered `body_html` shows neither marker nor escaped marker text; the one inline comment is on the expected path, and the submitted comments listing again returns null `line` and `side` with `position` 1 and a diff hunk ending on the expected line, so the anchor is checked by `position`, not by `line`. Re-building the same run then refuses `already-posted`. GitLab is not checked: its markers are assumed to survive the same way.
- Not covered at all: editing or replacing an earlier review, multi-line range comments, resolving threads or reading their resolved state, SARIF upload, and any forge other than GitHub and GitLab.
- A published review cannot be deleted by this plugin; on GitHub its comments can be removed in the web UI.

## Current behaviour choices

Each is one line to change if the decision changes.

- OQ1: `--comment` on a non-PR/MR target stops before any lane, so the user does not pay for a review that cannot be posted.
- OQ2: no disclosure line; this plugin adds no AI attribution trailer to PR text.
- OQ3: an unreadable diff refuses `diff-unavailable`; unvalidated anchors are never posted.
- OQ4: unverifiable items post as a count only; their text may be speculative.
- OQ5: any pending review or draft the user already has refuses; nothing of theirs is deleted.
