# matt.md Keep-or-Replace Contract

**Status:** approved 2026-10-18

**Approval record:** "approve & push" (owner, 2026-10-18)

**Source:** `docs/matt.md`, an intentionally untracked reference copy (the `code-review` skill by Matt Pocock, MIT), sha256 `09eb147f793e4647949edd32dbad278f0554dfb56e0d46b699cd4c2c55bf0357`, 89 lines. Every `L<n>` is a line in that file. Rows group adjacent lines of one idea.

**Role:** matt.md is the base skill. `docs/ccr-contract.md` stays valid for the parts taken from ccr.md as a secondary source (iron law wording, evidence gate, severity tiers, language detection patterns).

**Dispositions:** `keep` (carried over), `adapt` (idea kept, mechanism changed), `replace` (superseded).

| matt.md lines | Rule (paraphrase) | Disposition | Rationale | Target |
|---|---|---|---|---|
| L1-L4 | Frontmatter: name `code-review`, trigger description | replace | The skill is named `review-code`; its description is already trial-tested (docs/trigger-prompts.md) and keeps its wording. | SKILL.md frontmatter |
| L6-L9 | Review the diff from a fixed point on two axes: Standards and Spec | keep | Core design. | SKILL.md "Two axes" |
| L11 | Both axes run as parallel sub-agents, then are aggregated | adapt | Sub-agent lanes arrive in a later phase; the v0.1 stub reviews both axes in one pass in the main thread. | later phases |
| L13 | Tell the user to run `/setup-matt-pocock-skills` when no tracker is given | replace | No dependency on another skill; ask the user for the spec instead. | SKILL.md |
| L17-L21 | Pin the fixed point (SHA, branch, tag, `main`, `HEAD~n`); ask if absent; use `git diff <fp>...HEAD` and the commit list | keep | Three-dot diff compares against the merge-base. Working-tree and PR targets are added by the target-resolution phase. | target resolution |
| L23 | Fail early on a bad ref or empty diff, before any sub-agent | keep | Cheap check that avoids wasted lanes. | target resolution |
| L25-L32 | Find the spec: issue refs in commits, user path, `docs/`/`specs/`/`.scratch/`, else ask; none means skip Spec | keep | PR body is added as a source when a PR is the target. | Spec axis |
| L34-L36 | Search for every standards document; `CODING_STANDARDS.md` and `CONTRIBUTING.md` must be listed | keep | Also read `CLAUDE.md` and `REVIEW.md` from the base branch, never the PR head. | Standards axis |
| L38-L41 | Fixed smell baseline; repo standards override it; smells are labelled judgement calls; skip what tooling enforces | keep | Matches the evidence rule: a smell is never a hard finding. | Standards axis |
| L43-L56 | Twelve Fowler smells, each as what it is and how to fix | keep | Carried as a reference list loaded on demand, not in the lean SKILL.md. | references/smells.md |
| L58-L60 | Issue both sub-agent calls together, in the foreground | adapt | Deferred with L11. | later phases |
| L62-L66 | Standards brief: cite the violated standard, quote the hunk, split hard violations from judgement calls, under 400 words | adapt | Every item also carries file:line, quoted code, a trigger and an evidence class (ccr contract gate). | Standards axis |
| L68-L72 | Spec brief: missing or partial, scope creep, implemented but wrong, quote the spec line | adapt | Same evidence fields as the Standards brief. | Spec axis |
| L74 | No spec: skip the Spec agent and say so | keep | | Spec axis |
| L76-L80 | Aggregate under `## Standards` and `## Spec`; never merge or rerank; one summary line per axis | keep | Defect findings from ccr-style severity tiers sit inside an axis, not across them. | output |
| L82-L89 | Why two axes: one axis can pass while the other fails | keep | Rationale for the separation. | SKILL.md |
