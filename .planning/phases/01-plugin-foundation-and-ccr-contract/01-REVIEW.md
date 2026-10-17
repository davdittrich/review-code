---
phase: 01-plugin-foundation-and-ccr-contract
reviewed: 2026-10-18T00:00:00Z
depth: standard
files_reviewed: 10
files_reviewed_list:
  - .claude-plugin/plugin.json
  - .claude-plugin/marketplace.json
  - skills/review-code/SKILL.md
  - scripts/check-package.sh
  - LICENSE
  - NOTICE
  - README.md
  - .gitignore
  - docs/ccr-contract.md
  - docs/trigger-prompts.md
findings:
  critical: 0
  warning: 7
  info: 4
  total: 11
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-10-18
**Depth:** standard
**Files Reviewed:** 10
**Status:** issues_found

## Summary

Read all 10 files in full plus the five SUMMARY files and 01-CONTEXT.md. I also ran the gate and probed it.

- `sh scripts/check-package.sh` prints `27 ok, 0 FAIL` and exits 0. It runs from the repo root and from `scripts/`.
- A fresh `CLAUDE_CONFIG_DIR` install works.
- `NOTICE` reproduces Posit's MIT text exactly. I diffed it against `gh api repos/posit-dev/skills/contents/LICENSE`.
- `docs/ccr-contract.md` has 93 rows covering every non-blank, non-fence line L15 to L136 of `docs/ccr.md`. No duplicates, no gaps, and every row has a valid disposition and provenance.
- The `docs/trigger-prompts.md` blob id matches `git rev-parse HEAD:skills/review-code/SKILL.md`.

No security or data-loss defects found. The defects are in the gate's ability to fail, in what actually ships, and in places where the documents disagree with the source.

Not re-reported (known and accepted): D-08 licence assumption, T10 trigger collision (review-code-qz4.16), PKG-01 git-source install (review-code-k4u). Both tickets exist and are open, so their documentation is correct.

## Warnings

### WR-01: Gate `hygiene-tracked` reads the caller's repo, not the tree under test

**File:** `scripts/check-package.sh:203`
**Issue:** `tracked=$(git ls-files)` has no `-C`. The export branch uses `$root` (line 39), but when a directory argument is given, hygiene still inspects whatever repo the caller's cwd is in. I reproduced both failure modes:
- Plugin tree copied to a scratch dir with an added `AGENTS.md`, gate run from the repo root with that dir as the argument: `ok - hygiene-tracked`, `26 ok, 0 FAIL` (false pass).
- Same gate run from a non-git cwd: `FAIL - hygiene-tracked: git ls-files failed` on a clean tree (false fail).

The header comment says the argument form is used by the mutation probes, so hygiene mutation probes against a directory cannot fail. Even without an argument, line 39 uses cwd's repo, so running the script from another checkout gates that checkout.
**Fix:** Pin the repo to the script location and derive the file list from the tree under test:
```sh
# top of script, before any git call
cd "$(dirname "$0")/.." || exit 2
# line 203: list the exported tree, not the caller's index
tracked=$(cd "$tree" && find . -type f ! -path './.git/*' | sed 's|^\./||')
```
In the export branch `$tree` is already the tracked state, so this is also the correct subject.

### WR-02: Licence checks pass with the permission notice missing

**File:** `scripts/check-package.sh:167-174`, `:175-184`
**Issue:** `license-mit` only checks that line 1 is `MIT License` and that some `Copyright (c) 20xx <holder>` line exists. I replaced `LICENSE` with exactly those three lines (no permission grant, no disclaimer) and got `ok - license-mit`, `26 ok, 0 FAIL`. In `NOTICE`, only the first sentence of the permission text and the copyright line are grepped. The warranty disclaimer and the "above copyright notice shall be included" clause can be deleted without failing. The 01-02 SUMMARY and the NOTICE text state the upstream text is reproduced verbatim, but nothing gates that. MIT requires the full notice to travel with copies, so a truncation here is a licence-compliance regression the gate claims to guard (PKG-02).
**Fix:** Check the tail of the text as well, for example:
```sh
grep -qF 'THE SOFTWARE IS PROVIDED "AS IS"' "$lic" && grep -qF 'THE SOFTWARE IS PROVIDED "AS IS"' "$notice"
```
or pin a sha256 of the Posit block in `NOTICE` (lines 11-31 minus the D-08 paragraph).

### WR-03: `.claude/CLAUDE.md` is tracked and ships; gate and README say tooling pointer files cannot

**File:** `README.md:28`, `scripts/check-package.sh:202`, `.gitignore:16`
**Issue:** README line 28 states that tooling and agent pointer files "are not tracked ... and never ship. `scripts/check-package.sh` fails if any of them become tracked." `.claude/CLAUDE.md` (29 KB project instructions) is tracked (`git ls-files`) and is not in the `banned` regex. I installed `git archive HEAD` into a scratch `CLAUDE_CONFIG_DIR`. The cache `plugins/cache/review-code/review-code/0.1.0/.claude/CLAUDE.md` exists, next to `.planning/` (research, state, config) and `scripts/`. The README "What ships" list names `.planning/`, `docs/` and `scripts/` but not `.claude/CLAUDE.md`, so installers get an undocumented 29 KB instruction file. `.gitignore` also ignores root `/CLAUDE.md` while tracking the sibling `.claude/CLAUDE.md`, an inconsistent policy. `claude plugin tag --dry-run` already warns that CLAUDE.md at the plugin root is not loaded as project context. In addition, `.planning/PROJECT.md:37` ships an absolute personal path (`/home/dd/projects/review-code`), and the 01-03 SUMMARY justifies shipping `.planning/` by "D-03", but D-03 in 01-CONTEXT.md covers only semver and the self-hosted marketplace.
**Fix:** Either untrack and ignore `.claude/CLAUDE.md` and add `^\.claude/CLAUDE\.md$` to `banned`, or list it (and the rest of `.planning/`) in the README "What ships" section and reword line 28 so it does not claim the gate blocks it. Correct the D-03 citation in the 01-03 SUMMARY.

### WR-04: Verification gate admits `speculative` findings and leaves severity undefined

**File:** `skills/review-code/SKILL.md:24-32`, `:19-20`
**Issue:** The gate (line 24, "all four of") makes an evidence class the fourth requirement (line 29). Evidence class includes `inferred` and `speculative` (lines 40-41). Labelling a finding `speculative` therefore satisfies item 4, and items 1-3 are checkable by anyone. Result: an unverified finding is reportable. That contradicts line 19-20 ("Verified evidence decides what gets reported") and the project's core value (every reported finding is verified). Line 31-32 says only that Blocking needs `reproduced` or `traced`, and line 32-33 says a concern that "cannot meet the gate" becomes a question. A finding that passes the gate with class `inferred` or `speculative` has no defined severity or disposition (demote? question? report?). The contract (docs/ccr-contract.md L100 row) defines at least the accessibility demotion; the skill does not carry it.
**Fix:** State the mapping explicitly, for example: `speculative` is never reported as a finding (ask as a question); `inferred` is reportable only as Required or Suggestions; only `traced` and `reproduced` can be Blocking. Change item 4 to "an evidence class of at least `inferred`".

### WR-05: Shipped iron-law wording differs from the wording the user approved

**File:** `skills/review-code/SKILL.md:19-20` vs `docs/ccr-contract.md:121` and `:23`
**Issue:** The approved record (contract line 5 and "Resolved questions" item 3, line 121) fixes the iron-law wording as "Suspect to decide where to look; prove to decide what to report.", with target "SKILL.md iron law" on row L23. SKILL.md ships different text: "Skepticism chooses where to look. Verified evidence decides what gets reported. Do not assume the code is broken; read it closely, then report only what the evidence supports." The meaning is close, but the contract states it is approved and applied, and the 01-05 SUMMARY says the user approved "the 5 proposed defaults". Either the approval record is stale or the skill deviates without a recorded decision. The contract gate (D-10) is what Phase 3 is meant to trust.
**Fix:** Align one to the other. Either put the approved sentence in SKILL.md or amend contract row L23, line 121 and the approval record to the shipped wording (with a dated note).

### WR-06: trigger-prompts.md contradicts itself on method and misattributes the T10 acceptance

**File:** `docs/trigger-prompts.md:50-51`, `:60-63`, `:124`
**Issue:** Line 50-51 states that headless `claude -p` runs "are not usable" because routing "is never observed". Lines 60-63 then state all 20 trials were run headlessly. The deviation is disclosed, but the Method section still prescribes a procedure that was not performed, and a reader taking the first section as the method gets the wrong evidence strength. Line 124 reads "T10: accepted by user 2026-10-18, reason: delegated to the orchestrator". The 01-04 SUMMARY (deviation 2) says the user has not seen this specific flag. "Accepted by user" asserts an approval the user did not give; the user's global rule is that recorded state never authorises scope. This is a record-keeping defect only (the ticket review-code-qz4.16 itself is correct and open).
**Fix:** Retitle line 50 as "Planned method (not performed)" and move the as-run text above it, or merge the sections. Change line 124 to "accepted by orchestrator under user's no-interaction instruction; not yet seen by user" and have the user confirm T10 before the contract-style approval is relied on.

### WR-07: Description and `compatibility` advertise PR support the stub and README disclaim

**File:** `skills/review-code/SKILL.md:4,9`, `README.md:24`
**Issue:** The description lists "pull requests" as a supported target and `compatibility` says `gh for pull request targets`. README line 24 says `gh` is needed "for PR targets in later versions". The Status section (SKILL.md lines 50-52) lists missing features (lanes, ledger, verifier, JSON, PR posting, fix mode) but does not say PR reading is absent, and the body has no procedure for fetching a PR diff (only `argument-hint: "[target]"`). Trigger test T05 ("review PR #123") counts as a WIN for a description that promises a capability the body does not define. Users who trigger on a PR get unscripted behaviour, with `gh` calls outside any `allowed-tools` grant.
**Fix:** Pick one. Either describe PR reading in the Status section and give a one-line fetch instruction (`gh pr diff`, `gh pr view`), or drop "pull requests" from the description and `compatibility` until the PR phase lands. Keep README consistent with the choice.

## Info

### IN-01: Collision surface lists 9 skills but the executing session had more review-shaped ones

**File:** `docs/trigger-prompts.md:31-45`
**Issue:** Line 33 says names were "confirmed against the skill list of the executing session". The listed surface omits skills with overlapping triggers that appear in this session's skill list: `ponytail:ponytail-audit` ("audit this codebase", relevant to T03 "audit src/auth"), `caveman:caveman-evidence-review`, `agy-delegate:agy-review` (adversarial critique, relevant to T10), `security-audit`, `co-researcher:review`, `open-code-review:delegate-review`. The trial itself still used the real skill list, so results are unaffected; the documented surface is just incomplete. Confidence 60: the trial session's list may have differed from mine.
**Fix:** Add the missing rows, or reword line 33 to say the table lists the main competitors only.

### IN-02: Small gate defects that cannot affect the current tree

**File:** `scripts/check-package.sh:132`, `:79`, `:149`, `:159`
**Issue:**
- `grep -qiwE 'i|you|your|my|we'` matches `i` in "i.e." (`.` is a non-word char), so a legitimate third-person description fails `skill-description-person`.
- The semver regex rejects pre-releases (`0.2.0-rc.1`), while README "Releasing" says only "semver".
- `head -c` (lines 149, 154) is not POSIX though the header says POSIX sh. It works on GNU and BSD userland.
- `grep -q '!\`'` (line 159) only catches inline injection. I believe Claude Code also runs fenced blocks opened with three backticks followed by `!` (confidence 55, unverified here), which this check would miss.
**Fix:** Use `\<i\>` with a non-"." guard or drop `i`; allow `(-[0-9A-Za-z.]+)?` in the regex; add `grep -q '^```!'`; replace `head -c` with `dd bs=15000 count=1` or note the dependency.

### IN-03: Contract rows cite line numbers of an untracked, unshipped file

**File:** `docs/ccr-contract.md:7`
**Issue:** The contract is pinned to `docs/ccr.md` by sha256, but that file is gitignored (`.gitignore:30`), so no clone or installer can re-run the coverage check the 01-05 SUMMARY used (`coverage-ok`, `pin-ok`). The pin is verifiable only on the author's machine. This is deliberate (licence uncertainty), but the contract says nothing about how to restore the file.
**Fix:** Add one line telling readers where to obtain the pinned copy (or that Phase 3 must receive it from the author), or keep a script that re-verifies coverage and ships it.

### IN-04: Personal environment details ship in `docs/trigger-prompts.md`

**File:** `docs/trigger-prompts.md:60-76`, `:124`
**Issue:** The doc records the author's installed skills, hook behaviour and delegation decision. It ships to every installer under `source: "./"`. Not a secret, but it is not installer-facing content and will be stale.
**Fix:** Acceptable for v0.1.0; consider moving `docs/trigger-prompts.md` out of the shipped tree (or ignoring it) before public release, together with the WR-03 decision.

---

_Reviewed: 2026-10-18_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
