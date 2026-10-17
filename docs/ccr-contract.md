# ccr.md Keep-or-Replace Contract

**Status:** draft, awaiting user approval

**Source:** `docs/ccr.md`, an intentionally untracked reference copy (critical-code-reviewer v1.3), sha256 `0d4daffe93e1e65f0bbbb6ba623a5f585cdac996d72ad0eb5c1a69c0bdbf8556`, 137 lines. Every `L<n>` below is a line number in that exact file; citations are valid only for that hash. Upstream comparison base: `posit-dev/skills` critical-code-reviewer v1.2 (commit `e32ae38e7489d9ad271c7c505c9a2c54ff72ef91`, MIT).

**Scope:** every non-blank body line from L15 to L136, except the two code-fence markers (L119, L137), has one row. Frontmatter (L1-L13) is metadata and is replaced by this plugin's own manifest and skill frontmatter.

**How to read it:** the disposition column holds `keep` (rule carried over, restated in our words), `adapt` (idea kept, scope, severity or mechanism changed), `replace` (idea superseded by a decision or requirement) or `drop` (not carried). The rule column paraphrases; no ccr.md sentence is reproduced (D-09). Provenance is `Posit v1.2` when an equivalent idea exists in the upstream v1.2 text, and `v1.3-only` when it does not or when v1.3 reverses it; a mixed line is marked `v1.3-only`, the conservative choice for D-08.

**Gate (D-10):** Phase 3 builds the Defects lane only from a contract whose Status line reads `approved` with a date.

## Contract table

| ccr.md line | Rule (paraphrase) | Disposition | Rationale and source | Target | Provenance |
|---|---|---|---|---|---|
| L15 | Skill title | replace | The plugin and skill are named review-code (D-01); the old title is not reused. | SKILL.md heading | Posit v1.2 |
| L17 | Iron Law section heading | keep | The skill states one core principle near the top; PKG-03 requires the iron law inside the first 5,000 tokens. | SKILL.md | v1.3-only |
| L19 | Iron law text: skepticism required, code presumed broken until shown excellent, terse, merciless | replace | D-11: keep skepticism as a guide to where to look; drop the presumption of guilt, which research ties to false positives (REQUIREMENTS Out of Scope). Verified evidence decides what is reported. "No mercy" is theatrical (D-14). | SKILL.md iron law | v1.3-only |
| L21 | Mindset section heading | adapt | Section kept as the stance block, holding the three attitudes after their rewrite. | SKILL.md | Posit v1.2 |
| L23 | Guilty-until-exceptional: treat all code as lazy or broken by default | replace | D-11: a default verdict of guilt generates findings without evidence. Replaced by "suspect to locate, prove to report". | SKILL.md iron law | Posit v1.2 |
| L24 | Artifact only: ignore PR text, comments and TODOs; an unhandled case is a bug; flag stale or misleading comments | adapt | D-12 and CTX-03: Defects and Standards lanes never read PR description or commit messages; only the Spec lane sees them, labelled untrusted. The stale-comment flag stays. Upstream v1.2 takes the opposite view (use them as claims to verify); this reversal exists only in v1.3. | Defects + Standards (artifact only); Spec lane (untrusted PR text) | v1.3-only |
| L25 | Constructive brutality: direct, specific, actionable, no theatre, praise only for elite work | adapt | D-14: keep direct, specific, constructive; drop the praise-only-if-elite clause and brutality framing. | SKILL.md tone | Posit v1.2 |
| L27 | Detection Patterns section heading | adapt | Groups are redistributed by lane ownership (Defects, Standards, Ponytail, domain packs) instead of one flat list. | lane prompts (Phase 3) | Posit v1.2 |
| L29 | Slop and structure group heading | adapt | D-14: "slop" is an internal pejorative, never used in output; contents go to Standards and Ponytail, severity capped at Required. | Standards + Ponytail lanes | Posit v1.2 |
| L30 | Comments that only restate the next line | adapt | D-14: demote to Suggestions; wording must not insult the reader of the finding. | Standards lane | Posit v1.2 |
| L31 | Meaningless names (generic data, temp, single letters) | adapt | D-14: Suggestions by default, Required only when a name misleads or breaks the repo's own convention; linter-catchable cases excluded (VERI-05). | Standards lane | Posit v1.2 |
| L32 | Repeated logic across branches should be hoisted | adapt | D-14: Ponytail input at Suggestions; reported only when the duplicate sits in changed lines. | Ponytail lane | Posit v1.2 |
| L33 | Cargo-cult idioms: wrong effect dependencies, blocking work inside async wrappers, row-wise apply where a vector form exists | adapt | Behavioural misuse is a Defects finding when a failing path is traced; style-only cases go to Standards at Suggestions (D-14). Language examples are repeated in the packs. | Defects (behavioural) + Standards | Posit v1.2 |
| L34 | Structural smells: junk-drawer files, very large components, import disorder, dependency sprawl | adapt | D-14: size thresholds are heuristics, not defects; sprawl and sizing flow to Ponytail, conventions to Standards. | Standards + Ponytail lanes | Posit v1.2 |
| L35 | Dead code: unused imports and variables, unreachable branches, commented-out blocks | adapt | Unused symbols are linter-catchable and excluded (VERI-05) unless tooling is absent; unreachable and commented-out code stays as Ponytail input. | Ponytail lane | Posit v1.2 |
| L37 | Adversarial lens group heading | keep | D-11: skepticism is a search strategy, not a verdict. | Defects lane | Posit v1.2 |
| L38 | Silent failures: errors caught and discarded, promises never awaited, detached background calls | keep | Reportable when a concrete lost error signal is traced. | Defects lane | Posit v1.2 |
| L39 | Missing-value inputs will occur, so guard them | adapt | VERI-05: input-dependent maybes are excluded; report only when changed code makes the failing input reachable. | Defects lane | Posit v1.2 |
| L40 | Hostile input: injection, XSS, type coercion; treat every user as an attacker | adapt | Keep injection and coercion checks; drop the slogan. Require a traced path from untrusted source to sink. | Defects lane + security pack | Posit v1.2 |
| L41 | Temporary hacks that become permanent | keep | Report with evidence (marker, missing removal condition); Suggestions. | Defects + Ponytail lanes | Posit v1.2 |
| L42 | AI transcription: a diff that mirrors its plan item by item deserves a harder look | adapt | Heuristic that prompts a check of unplanned paths, never a finding by itself; needs a path trace before reporting. | Defects lane + LLM-generated-code pack (Phase 4) | v1.3-only |
| L44 | Extra scrutiny for AI-authored code (heading) | adapt | Authorship is not knowable from a diff; trigger the scrutiny from the change's claims and shape instead. | Defects lane + LLM-generated-code pack | v1.3-only |
| L45 | Trace every path, not only the optimised one; regressions hide in unmentioned paths | keep | Produces `traced` evidence (VERI-03) and feeds the verifier. | Defects lane + verifier | v1.3-only |
| L46 | Reject unproven claims such as zero overhead or no behaviour change | adapt | An unproven claim is at most `inferred` evidence until a path trace exists (VERI-03). | Defects lane + verifier | v1.3-only |
| L47 | Calls duplicated across branches should be hoisted | adapt | Same idea as L32; Ponytail input at Suggestions. | Ponytail lane | v1.3-only |
| L49 | Language red flags section heading | adapt | D-15: becomes content of the Phase 4 domain packs; no pack files are written in Phase 1. | Phase 4 packs (LANE-06) | v1.3-only |
| L51 | Python sub-heading | adapt | D-15: one pack per language. | Python pack | v1.3-only |
| L52 | Bare or over-broad exception handlers swallow errors | adapt | D-15; also a Defects silent-failure case. Linter-catchable, so excluded when a linter is configured (VERI-05). | Python pack | v1.3-only |
| L53 | Handler tuple mixing a specific and a generic class makes the specific one dead | adapt | D-15; kept as a pack check, excluded when a linter already flags it (VERI-05). | Python pack | v1.3-only |
| L54 | Mutable default arguments | adapt | D-15; common linter rule, excluded when enforced (VERI-05). | Python pack | v1.3-only |
| L55 | Wildcard imports pollute the namespace | adapt | D-15; linter-catchable, excluded when enforced (VERI-05). | Python pack | v1.3-only |
| L56 | Serialisation with a string fallback hides failures | keep | D-15; a silent-failure pattern a linter does not catch. | Python pack | v1.3-only |
| L58 | R sub-heading | adapt | D-15: one pack per language. | R pack | v1.3-only |
| L59 | Spell out TRUE and FALSE instead of T and F | adapt | D-15; linter-catchable, excluded when enforced (VERI-05). | R pack | v1.3-only |
| L60 | Reliance on partial argument matching | keep | D-15; not flagged by default linters. | R pack | v1.3-only |
| L61 | Explicit loops where a vectorised form exists | adapt | D-15; performance claims need evidence, so Suggestions and Ponytail input only. | R pack + Ponytail lane | v1.3-only |
| L62 | Converting a factor to integer yields level codes, not labels | keep | D-15; a real correctness trap. | R pack | v1.3-only |
| L63 | Binding data frames with mismatched columns silently reorders | adapt | D-15; the exact R behaviour must be confirmed against R documentation before the pack ships (Evidence constraint). | R pack | v1.3-only |
| L64 | NaN spreading through aggregates | keep | D-15; convert to NA or drop missing values explicitly. | R pack | v1.3-only |
| L65 | Several identical foreign-function call sites across branches | adapt | D-15; duplication is Ponytail input at Suggestions. | R pack + Ponytail lane | v1.3-only |
| L66 | Repeated compound conditions should be bound once | adapt | D-15; duplication is Ponytail input at Suggestions. | R pack + Ponytail lane | v1.3-only |
| L68 | JS/TS and front-end sub-heading | adapt | D-15: one pack for JS/TS and front-end. | JS/TS pack | v1.3-only |
| L69 | Strict equality instead of loose equality | adapt | D-15; linter-catchable, excluded when enforced (VERI-05). | JS/TS pack | v1.3-only |
| L70 | The `any` type is a bug | adapt | D-15; excluded when the type checker or linter forbids it (VERI-05); otherwise Suggestions, not a defect. | JS/TS pack | v1.3-only |
| L71 | React issues: missing memoisation, unstable references, index keys, stale effect dependencies | adapt | D-15; keep keys, references and dependencies; drop missing-memo, which needs profiling evidence. | JS/TS pack | v1.3-only |
| L72 | Accessibility: missing alt text, unlabeled inputs, low contrast | keep | D-13 keeps accessibility as a Blocking category; D-15 puts the checks in the pack. Upstream has a fuller accessibility section. | Defects lane + JS/TS pack | Posit v1.2 |
| L73 | Request-per-item in loops or render paths | keep | D-15; report when the loop and the call are both in the change. | JS/TS pack | v1.3-only |
| L74 | Styling scattered across inline, module and global without a rule | adapt | D-14: convention matter, Suggestions or Noted. | Standards lane + JS/TS pack | Posit v1.2 |
| L76 | SQL and ORM sub-heading | adapt | D-15: one pack for SQL and ORM. | SQL pack | v1.3-only |
| L77 | Build queries with parameter binding, not string interpolation | keep | D-15; overlaps the security pack; Blocking only with a traced path from untrusted input. | SQL pack + security pack | v1.3-only |
| L78 | Lazy loading inside loops | keep | D-15; report when the loop is in changed code. | SQL pack | v1.3-only |
| L79 | Unbounded queries and missing indexes | adapt | D-15; index need cannot be proven from a diff, so at most `inferred`; Suggestions. | SQL pack | v1.3-only |
| L81 | Cross-file and documentation integrity section heading | keep | D-15 and LANE-01: these checks need full repository context. | Defects lane | v1.3-only |
| L83 | Code consistency sub-heading | keep | D-15: part of the Defects lane. | Defects lane | v1.3-only |
| L84 | Constructor divergence: construction sites with different defaults | keep | D-13 keeps it as a Blocking category; evidence is a search of all construction sites (`traced`). | Defects lane | v1.3-only |
| L85 | Removing an import breaks tests that patch that name | keep | Blocking when a test target is shown to reference the removed name (`traced`). | Defects lane | v1.3-only |
| L87 | Doc versus source divergence sub-heading | keep | D-15: part of the Defects lane. | Defects lane | v1.3-only |
| L88 | Trust the source over comments; check algorithms, constants, thresholds and symbols | keep | D-13 keeps doc/source mismatch as a Blocking category; the verifier compares the claim to the body (VERI-01). | Defects lane + verifier | v1.3-only |
| L89 | Do not present compile-time guards as runtime alternatives | keep | Same category as L88. | Defects lane | v1.3-only |
| L90 | Search every doc surface (reference docs, vignettes, README) for the same claim | keep | Same category as L88; Blocking when the contradiction is traced. | Defects lane | v1.3-only |
| L92 | Operating protocols section heading | adapt | Rules about uncertainty move into the skill's verification gate. | SKILL.md | Posit v1.2 |
| L94 | Constraints and uncertainty sub-heading | adapt | Uncertain items become the `unverifiable` bucket (VERI-02), listed rather than dropped. | SKILL.md + verifier | Posit v1.2 |
| L95 | With partial code, say what cannot be verified and flag risk without assuming failure | keep | Maps to verifier outcome `unverifiable` (VERI-02). | SKILL.md + verifier | Posit v1.2 |
| L96 | On repeat reviews focus on the delta and do not reopen settled items | keep | Fits change targeting (CTX-07) and origin tagging (VERI-06). | SKILL.md | Posit v1.2 |
| L97 | When a pattern is unfamiliar, note it, defer to team convention and ask | keep | Aligns with honouring repo guidance files (CTX-06). | SKILL.md | Posit v1.2 |
| L99 | Severity tiers sub-heading | adapt | D-13: tiers stay as severity; evidence class is a second, separate axis. | severity rubric (OUT-01) | Posit v1.2 |
| L100 | Blocking tier: security, data loss, races, accessibility, doc/source mismatch, constructor divergence | adapt | D-13: category list kept, including constructor divergence; Blocking additionally requires `reproduced` or `traced` evidence (VERI-03). Tier core exists upstream; the two cross-file categories are v1.3. | severity rubric | v1.3-only |
| L101 | Required tier: style items, weak naming, missing edge-case handling, plan-mirroring diffs | adapt | D-14 demotes style items; plan transcription (L42) is a heuristic, not a tier trigger. | severity rubric | v1.3-only |
| L102 | Suggestions tier: intent, performance, tests | keep | D-13; performance items need evidence. | severity rubric | Posit v1.2 |
| L103 | Noted tier: minor style, mention once | keep | D-13 keeps Noted for style. | severity rubric | Posit v1.2 |
| L105 | Tone and delivery sub-heading | adapt | Tone block is kept, minus theatre (D-14). | SKILL.md tone | Posit v1.2 |
| L106 | Explain why: state the failure mode | keep | Becomes the `evidence` field of a finding. | finding schema (OUT-02) | Posit v1.2 |
| L107 | Be specific: quote lines and give a fix | keep | Becomes the `file`, `line` and `fix` fields. | finding schema (OUT-02) | Posit v1.2 |
| L108 | Exit rule: say so when the code is genuinely strong; do not invent issues | keep | An empty verified result is valid output; supports the no-false-positive core value. | SKILL.md | Posit v1.2 |
| L110 | Final review check heading | adapt | Becomes the verifier checklist. | verifier prompt (VERI-01) | Posit v1.2 |
| L111 | Is a production incident likely? | adapt | Used by the verifier as an impact question. | verifier prompt | Posit v1.2 |
| L112 | Which assumptions are unvalidated? | adapt | Used by the verifier; unvalidated assumptions yield `inferred` evidence. | verifier prompt | Posit v1.2 |
| L113 | Is there path-trace proof for performance claims? | adapt | Maps to evidence classes (VERI-03). | verifier prompt | v1.3-only |
| L114 | Is there overhead on paths that are not the optimised one? | adapt | Part of selective path analysis (L45). | verifier prompt | v1.3-only |
| L115 | Are technical claims checked against the function body? | keep | Core of independent verification (VERI-01). | verifier prompt | v1.3-only |
| L117 | Response format heading | replace | Terminal report follows OUT-01 and is rendered from the JSON schema (OUT-02). | report renderer (Phase 5) | Posit v1.2 |
| L120 | Summary section | adapt | Kept as the report opener (OUT-01). | OUT-01 terminal report | Posit v1.2 |
| L121 | Bottom-line-up-front assessment | keep | Lead with the conclusion. | OUT-01 terminal report | Posit v1.2 |
| L123 | Blocking issues section | adapt | Kept; each entry carries evidence class (VERI-03). | OUT-01 terminal report | Posit v1.2 |
| L124 | Entry shape: location, problem, fix, reason | keep | Maps to finding fields. | finding schema (OUT-02) | Posit v1.2 |
| L126 | Required changes section | adapt | Kept as the Required tier (OUT-01). | OUT-01 terminal report | Posit v1.2 |
| L127 | Placeholder describing required items as slop or laziness | replace | D-14: internal labels such as slop or laziness never appear in output. | OUT-01 terminal report | Posit v1.2 |
| L129 | Suggestions section | keep | OUT-01. | OUT-01 terminal report | Posit v1.2 |
| L130 | Placeholder for better patterns | keep | Suggestions carry alternatives. | OUT-01 terminal report | Posit v1.2 |
| L132 | Verdict section | adapt | Advisory only; never maps to a GitHub review event (OUT-04). | OUT-01 terminal report | Posit v1.2 |
| L133 | Three verdict labels, including an approval label | replace | OUT-04: the skill never approves or requests changes on GitHub; labels are renamed so none reads as an approval. | OUT-01 terminal report | Posit v1.2 |
| L135 | Next steps section | adapt | Shown only in interactive main-thread runs. | OUT-01 terminal report | Posit v1.2 |
| L136 | Offer follow-up options; skip when run as a subagent | adapt | Lane subagents return findings only and never offer options. | SKILL.md + lane prompts | Posit v1.2 |

## Accepted risk (D-08)

The licence of the v1.3 additions credited to Ziad Hassan has not been verified. The project assumes MIT and credits him in NOTICE and SKILL.md. Every row marked `v1.3-only` rests on that assumption. Wording is rewritten in our own words (D-09), which lowers but does not remove the risk. Revisit before any public release; verification (comparison with the posit-dev text or asking the author) was declined for now.

## Open questions for the approval checkpoint

1. **L24, stale-comment flag.** Proposed: the Standards lane owns it (artifact-only lane that checks documented conventions). Alternative: Defects, if the comment contradicts source (already covered by L88).
2. **L100, accessibility as Blocking.** Accessibility barriers are often only `inferred` without a rendered page. Proposed: stay in the Blocking category list but demote to Required when evidence is below `traced`.
3. **L19, iron law wording.** Proposed: "Suspect to decide where to look; prove to decide what to report." Alternative: keep ccr's terse imperative style with the presumption removed.
4. **L133, verdict labels.** Proposed: Needs changes / Needs discussion / No blocking findings, advisory text only. Alternative: keep the ccr labels.
5. **L47 and L32 merge.** Proposed: one Ponytail check for repeated expressions. Alternative: keep as two rows in the lane prompt.
